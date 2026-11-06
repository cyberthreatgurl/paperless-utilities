#!/usr/bin/env python3
"""
Paperless-ngx PDF Sync and Validation Tool.

Scans directories for PDF files, validates them, checks for duplicates via the
Paperless-ngx API, and stages new files in the consume folder for ingestion.

Key Features:
  - PDF validation (corruption, encryption, size limits)
  - Duplicate detection via Paperless API checksum lookup
  - Consume folder pre-check to avoid re-staging files
  - Per-directory state tracking to skip unchanged directories
  - Quarantine for invalid PDFs (oversized, corrupt, password-protected)

Usage:
  python paperless_sync.py [options] [scan_directory]

  Examples:
    python paperless_sync.py ~/Documents
    python paperless_sync.py --verbose --dry-run /data/archive
    python paperless_sync.py --force ~/Documents  # ignore saved state
    paperless-sync --clean-consume --dry-run

Inputs:
  - CLI Arguments:
      [scan_directory]  Root folder to scan (default: current directory)
      -v, --verbose     Show detailed file information
      -n, --dry-run     Scan without copying files
      -f, --force       Ignore .paperless_check_state files, re-scan all
      --no-state        Do not write or update .paperless_check_state files
    --clean-consume   Quarantine invalid PDFs already in the consume folder
    --clean-consume   Quarantine invalid PDFs already in the consume folder

  - Environment Variables (from .env):
      PAPERLESS_URL           Base URL of Paperless-ngx instance
      PAPERLESS_TOKEN         API authentication token
      CONSUME_DIR             Target folder for new documents
      MAXIMUM_FILE_SIZE_MB    Max file size in MB (default: 25)
      LOG_FILE                Log file path (default: logs/paperless_sync.log)
      CHECK_DIR               Quarantine folder (default: check/)

Outputs:
  - Files:
      {CONSUME_DIR}/*.pdf              Staged PDFs ready for ingestion
      {CHECK_DIR}/*.pdf                Quarantined invalid PDFs
      {LOG_FILE}                       Structured log of all operations
      {scan_dir}/.paperless_check_state  Per-directory state cache (JSON)

  - Exit Codes:
      0   Success
      1   Missing environment variables or critical errors

  - State File Format (.paperless_check_state):
      Written to each scanned directory after successful processing.
      Skipped during --dry-run. Contains:
      {
        "last_checked": "<ISO-8601 timestamp>",
        "pdf_count": <int>,
        "dir_fingerprint": "<md5 hex string>"
      }
      Fingerprint is MD5 of sorted "filename:mtime_ns" entries for all PDFs.

Dependencies:
  - requests  (Paperless API communication)
  - pypdf     (PDF validation)
  - dotenv    (environment configuration)

Operational Assumptions:
  - Paperless API is reachable and token is valid
  - Consume folder is mounted and writable
  - MD5 checksums are unique per document
  - File modification times are reliable for change detection
  - State files are not manually edited
"""

import argparse
import hashlib
import json
import os
import re
import requests
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import pypdf
from pypdf.errors import DependencyError, FileNotDecryptedError, PyPdfError, WrongPasswordError
from paperless_file_utils import copy_file_data_atomic, is_appledouble_file

load_dotenv()

# -----------------------------
# CONFIGURATION (from .env)
# -----------------------------
_required = ("PAPERLESS_URL", "PAPERLESS_TOKEN", "CONSUME_DIR")
_missing  = [v for v in _required if not os.getenv(v)]
if _missing:
    print(f"ERROR: Missing required environment variables: {', '.join(_missing)}")
    print("Copy .env.example to .env and fill in your values.")
    sys.exit(1)

PAPERLESS_URL       = os.environ["PAPERLESS_URL"].rstrip("/")
PAPERLESS_TOKEN     = os.environ["PAPERLESS_TOKEN"]
CONSUME_DIR         = Path(os.environ["CONSUME_DIR"])
MAXIMUM_FILE_SIZE   = int(os.getenv("MAXIMUM_FILE_SIZE_MB", "25")) * 1024 * 1024
LOG_FILE            = Path(os.getenv("LOG_FILE", "logs/paperless_sync.log"))
CHECK_DIR           = Path(os.getenv("CHECK_DIR", "check"))

# -----------------------------
# ANSI COLORS
# -----------------------------
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"

def ok(s):    return f"{C.GREEN}{s}{C.RESET}"
def err(s):   return f"{C.RED}{C.BOLD}{s}{C.RESET}"
def warn(s):  return f"{C.YELLOW}{s}{C.RESET}"
def info(s):  return f"{C.CYAN}{s}{C.RESET}"
def dim(s):   return f"{C.DIM}{s}{C.RESET}"
def bold(s):  return f"{C.BOLD}{s}{C.RESET}"
def hi(s):    return f"{C.MAGENTA}{C.BOLD}{s}{C.RESET}"

# -----------------------------
# LOGGING
# -----------------------------
def log(msg):
    with LOG_FILE.open("a", encoding="utf-8") as f:
        clean = re.sub(r"\033\[[0-9;]*m", "", msg)
        f.write(clean + "\n")

# -----------------------------
# HELPERS
# -----------------------------
def md5sum(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def paperless_api_ok():
    try:
        r = requests.get(f"{PAPERLESS_URL}/api/", timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False

def paperless_token_ok():
    try:
        r = requests.get(
            f"{PAPERLESS_URL}/api/documents/",
            headers={"Authorization": f"Token {PAPERLESS_TOKEN}"},
            timeout=5
        )
        return r.status_code == 200
    except requests.RequestException:
        return False

def paperless_document_exists(file_hash):
    """
    Query Paperless for a document matching this MD5 checksum.
    Returns (exists: bool, matched_count: int).
    The checksum filter is scoped so a non-empty library never causes
    false positives — only an exact checksum match returns count > 0.
    """
    headers = {"Authorization": f"Token {PAPERLESS_TOKEN}"}
    # First call: filtered by checksum
    url_filtered = f"{PAPERLESS_URL}/api/documents/?checksum={file_hash}"
    r = requests.get(url_filtered, headers=headers, timeout=10)
    r.raise_for_status()
    filtered_count = r.json().get("count", 0)

    # Sanity-check: fetch total document count (no filter)
    url_all = f"{PAPERLESS_URL}/api/documents/?page_size=1"
    r2 = requests.get(url_all, headers=headers, timeout=10)
    r2.raise_for_status()
    total_count = r2.json().get("count", 0)

    # If the filtered result equals the total, the filter was ignored — treat as new
    if total_count > 0 and filtered_count == total_count:
        return False, 0  # filter not honoured; assume new

    return filtered_count > 0, filtered_count

def consume_folder_ok():
    return CONSUME_DIR.exists() and CONSUME_DIR.is_dir()

def consume_folder_writable():
    try:
        test_file = CONSUME_DIR / "paperless_test_write.tmp"
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("ok")
        test_file.unlink()
        return True
    except OSError:
        return False

def validate_pdf(path: Path) -> tuple[bool, str]:
    """
    Returns (is_valid: bool, reason: str).
    Checks file size, corruption, and password protection.
    """
    if path.stat().st_size > MAXIMUM_FILE_SIZE:
        size_mb = path.stat().st_size / 1024 / 1024
        return False, f"oversized ({size_mb:.2f} MB)"

    with path.open("rb") as pdf_file:
        if b"%PDF-" not in pdf_file.read(1024):
            return False, "corrupt"

    try:
        reader = pypdf.PdfReader(str(path))
    except PyPdfError:
        return False, "corrupt"

    if reader.is_encrypted:
        try:
            reader.decrypt("")
            # Probe pages to confirm decryption succeeded
            _ = len(reader.pages)
        except (WrongPasswordError, FileNotDecryptedError):
            return False, "password-protected"
        except DependencyError:
            return False, "password-protected (AES — install cryptography)"
        except PyPdfError:
            return False, "corrupt"
    else:
        # Probe pages on unencrypted files too to catch structural corruption
        try:
            _ = len(reader.pages)
        except PyPdfError:
            return False, "corrupt"

    return True, "ok"

def quarantine_invalid_consume_files(
    consume_dir: Path,
    check_dir: Path,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Move invalid PDFs from the consume folder to quarantine, returning counts."""
    quarantined = 0
    failed = 0

    if consume_dir.resolve() == check_dir.resolve():
        raise ValueError("Consume and quarantine directories must be different")

    for path in sorted(consume_dir.glob("*.pdf")):
        if not path.is_file() or is_appledouble_file(path):
            continue

        is_valid, reason = validate_pdf(path)
        if is_valid:
            continue

        marker = "corrupt" if reason == "corrupt" else "invalid"
        destination = check_dir / f"{path.stem}.{marker}{path.suffix}"
        suffix = 1
        while destination.exists():
            destination = check_dir / f"{path.stem}.{marker}.{suffix}{path.suffix}"
            suffix += 1

        if dry_run:
            print(f"[QUARANTINE|DRY] {path.name}  reason={reason}")
        else:
            try:
                shutil.move(str(path), str(destination))
            except OSError as error:
                print(f"[ERROR] Could not quarantine {path}: {error}", file=sys.stderr)
                log(f"[ERROR] Could not quarantine {path}: {error}")
                failed += 1
                continue
            print(f"[QUARANTINE] {path.name}  reason={reason}  saved_as={destination.name}")

        log(f"[QUARANTINE{'|DRY' if dry_run else ''}] {path.name}  reason={reason}")
        quarantined += 1

    return quarantined, failed

def file_in_consume_folder(filename: str) -> bool:
    """
    Returns True if the file already exists in the consume folder.
    Used to avoid re-staging files that are queued but not yet ingested.
    """
    return (CONSUME_DIR / filename).is_file()

def compute_dir_fingerprint(directory: Path) -> str:
    """
    Compute MD5 fingerprint of all PDFs in a directory.
    Fingerprint is based on sorted filename:mtime_ns pairs.
    Returns MD5 hex digest.
    """
    pdfs = sorted(
        path for path in directory.glob("*.pdf")
        if not is_appledouble_file(path)
    )
    if not pdfs:
        # Empty directory gets fingerprint of empty string
        return hashlib.md5(b"").hexdigest()
    
    entries = [f"{p.name}:{p.stat().st_mtime_ns}" for p in pdfs]
    fingerprint_str = "|".join(entries)
    return hashlib.md5(fingerprint_str.encode("utf-8")).hexdigest()

def read_dir_state(directory: Path) -> dict | None:
    """
    Read .paperless_check_state file from directory.
    Returns dict with keys: last_checked, pdf_count, dir_fingerprint.
    Returns None if file missing or invalid JSON.
    """
    state_file = directory / ".paperless_check_state"
    if not state_file.exists():
        return None
    
    try:
        with state_file.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

def write_dir_state(directory: Path, fingerprint: str, pdf_count: int) -> None:
    """
    Write .paperless_check_state file to directory.
    Does not raise on permission errors; logs warning instead.
    """
    state_file = directory / ".paperless_check_state"
    state_data = {
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "pdf_count": pdf_count,
        "dir_fingerprint": fingerprint
    }
    
    try:
        with state_file.open("w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)
    except OSError as e:
        log(f"[WARN] Could not write state file to {directory}: {e}")

def dir_is_unchanged(directory: Path, current_fingerprint: str) -> bool:
    """
    Returns True if saved directory fingerprint matches current fingerprint.
    """
    state = read_dir_state(directory)
    if state is None:
        return False
    
    saved_fingerprint = state.get("dir_fingerprint")
    return saved_fingerprint == current_fingerprint

# -----------------------------
# MAIN LOGIC
# -----------------------------
def main():
    parser = argparse.ArgumentParser(description="Sync PDFs to Paperless-ngx")
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="Root folder to scan for PDFs (default: current directory)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show filename, MD5 hash, and size for every PDF checked",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Scan and validate without changing any files",
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Ignore saved directory state and re-scan all directories",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="Do not write or update .paperless_check_state files",
    )
    parser.add_argument(
        "--clean-consume",
        action="store_true",
        help="Quarantine invalid PDFs already in the consume folder",
    )
    args = parser.parse_args()
    root_scan_dir = Path(args.root).resolve() if args.root else Path.cwd()
    verbose = args.verbose
    dry_run = args.dry_run
    force = args.force
    no_state = args.no_state

    # Ensure runtime directories exist
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECK_DIR.mkdir(parents=True, exist_ok=True)

    if args.clean_consume:
        if not CONSUME_DIR.is_dir():
            print(err(f"[ERROR] Consume folder not found: {CONSUME_DIR}"))
            return
        quarantined, failed = quarantine_invalid_consume_files(
            CONSUME_DIR,
            CHECK_DIR,
            dry_run=dry_run,
        )
        print(f"Consume cleanup complete: quarantined={quarantined}, failed={failed}")
        return

    log(f"=== Starting Paperless Sync {'(DRY RUN) ' if dry_run else ''}===")
    log(f"[INFO] Scanning: {root_scan_dir}")

    # ------------------------------------------------------------------
    # Environment status block
    # ------------------------------------------------------------------
    print(bold(f"\n{'─'*54}"))
    print(bold("  Paperless Sync") + (warn("  [DRY RUN — no files will be copied]") if dry_run else ""))
    print(bold(f"{'─'*54}"))

    scan_ok        = root_scan_dir.is_dir()
    consume_exists = consume_folder_ok()
    consume_write  = consume_exists and consume_folder_writable()
    api_ok         = paperless_api_ok()
    token_ok       = api_ok and paperless_token_ok()
    state_enabled  = not (force or no_state)

    def status_line(label, value, good, good_msg, bad_msg):
        badge = ok("  OK  ") if good else err(" FAIL ")
        detail = ok(good_msg) if good else err(bad_msg)
        print(f"  [{badge}]  {info(label):<30} {dim(str(value))}  {detail}")

    print(info("  Environment"))
    status_line("Scan directory", root_scan_dir,  scan_ok,        "readable",         "not found / not a directory")
    status_line("Consume folder", CONSUME_DIR,    consume_write,  "mounted & writable","not writable / not mounted")
    status_line("Paperless API",  PAPERLESS_URL,  api_ok,         "reachable",        "unreachable")
    status_line("API token",      "•••••••••",    token_ok,       "valid",            "invalid" if api_ok else "skipped")
    
    # State tracking status
    if force:
        state_value = "disabled"
        state_msg = "disabled (--force)"
    elif no_state:
        state_value = "disabled"
        state_msg = "disabled (--no-state)"
    else:
        state_value = "enabled"
        state_msg = "active"
    status_line("State tracking", state_value, state_enabled, "active", state_msg)
    
    print(bold(f"{'─'*54}\n"))

    status_log = (
        f"Scan directory: {root_scan_dir} [{'OK' if scan_ok else 'FAIL'}]  "
        f"Consume: {CONSUME_DIR} [{'OK' if consume_write else 'FAIL'}]  "
        f"API [{'OK' if api_ok else 'FAIL'}]  Token [{'OK' if token_ok else 'FAIL'}]"
    )
    log(status_log)

    if not api_ok:
        print(err("[ERROR] Paperless API is not responding. Aborting."))
        log("[ERROR] Paperless API is not responding.")
        return
    if not token_ok:
        print(err("[ERROR] API token invalid. Aborting."))
        log("[ERROR] API token invalid.")
        return
    if not consume_exists:
        print(err(f"[ERROR] Consume folder not mounted: {CONSUME_DIR}"))
        log(f"[ERROR] Consume folder missing: {CONSUME_DIR}")
        return
    if not consume_write:
        print(err(f"[ERROR] Consume folder not writable: {CONSUME_DIR}"))
        log(f"[ERROR] Cannot write to consume folder: {CONSUME_DIR}")
        return
    if not scan_ok:
        print(err(f"[ERROR] Scan directory not found: {root_scan_dir}"))
        log(f"[ERROR] Scan directory not found: {root_scan_dir}")
        return

    # ------------------------------------------------------------------
    # Collect directories containing PDFs
    # ------------------------------------------------------------------
    print(info(f"  Scanning for PDFs in {root_scan_dir} ..."), end="", flush=True)
    all_pdfs = [
        path for path in root_scan_dir.rglob("*.pdf")
        if not is_appledouble_file(path)
    ]
    pdf_total = len(all_pdfs)
    
    # Group PDFs by parent directory
    dirs_with_pdfs = {}
    for pdf_path in all_pdfs:
        parent = pdf_path.parent
        if parent not in dirs_with_pdfs:
            dirs_with_pdfs[parent] = []
        dirs_with_pdfs[parent].append(pdf_path)
    
    # Sort directories for deterministic output
    sorted_dirs = sorted(dirs_with_pdfs.keys())
    
    print(f"\r  {bold(str(pdf_total))} PDF{'s' if pdf_total != 1 else ''} found in {bold(str(len(sorted_dirs)))} director{'ies' if len(sorted_dirs) != 1 else 'y'} under {dim(str(root_scan_dir))}")
    log(f"[INFO] {pdf_total} PDFs found in {len(sorted_dirs)} directories under {root_scan_dir}")

    if pdf_total == 0:
        print(warn("  No PDFs to process."))
        return

    # ------------------------------------------------------------------
    # Process directories
    # ------------------------------------------------------------------
    total_docs          = 0
    total_skipped       = 0
    total_added         = 0
    total_quarantine    = 0
    total_dirs_skipped  = 0

    def progress(n):
        pct = int(n / pdf_total * 100)
        return f"{C.DIM}[{n:{len(str(pdf_total))}}/{pdf_total}  {pct:3}%]{C.RESET}"

    if verbose:
        pad = len(str(pdf_total)) * 2 + 8  # width of progress prefix
        print(f"  {'':<{pad}}  {'FILE':<43} {'SIZE':>8}  {'MD5 HASH':<34}  STATUS")
        print(f"  {'':<{pad}}  {'-'*43} {'-'*8}  {'-'*34}  {'------'}")

    for directory in sorted_dirs:
        pdfs_in_dir = sorted(dirs_with_pdfs[directory])
        current_fingerprint = compute_dir_fingerprint(directory)
        
        # Check if directory is unchanged since last scan
        if not force and dir_is_unchanged(directory, current_fingerprint):
            state = read_dir_state(directory)
            last_checked = state.get("last_checked", "unknown") if state else "unknown"
            actual_count = len(pdfs_in_dir)
            
            log(f"[SKIP-DIR] Unchanged since last check: {directory}")
            if verbose:
                print(f"  {dim(f'[skip-dir] {directory.relative_to(root_scan_dir)} ({actual_count} PDFs, last checked: {last_checked})')}")
            else:
                print(dim(f"  [skip-dir] {directory.relative_to(root_scan_dir)} ({actual_count} PDFs)"))
            
            total_skipped += actual_count
            total_dirs_skipped += 1
            continue
        
        # Process each PDF in this directory
        for full_path in pdfs_in_dir:
            filename = full_path.name
            total_docs += 1
            size_mb = full_path.stat().st_size / 1024 / 1024
            prog = progress(total_docs)

            # --- PDF validation ---
            is_valid, reason = validate_pdf(full_path)
            if not is_valid:
                if not dry_run:
                    quarantine_dest = CHECK_DIR / filename
                    copy_file_data_atomic(full_path, quarantine_dest)
                action = dim("(would quarantine)") if dry_run else ""
                log(f"[QUARANTINE{'|DRY' if dry_run else ''}] {filename}  reason={reason}")
                if verbose:
                    print(f"  {prog}  {warn(filename):<43} {size_mb:>7.2f}M  {dim('—'):34}  {warn(reason)} → check/ {action}")
                else:
                    to = f" → check/ {action}" if not dry_run else f" {action}"
                    print(f"{prog} " + warn(f"[!] {filename}  ({reason}){to}"))
                total_quarantine += 1
                total_skipped += 1
                continue

            file_hash = md5sum(full_path)
            log(f"[CHECK] {full_path}  md5={file_hash}  size={size_mb:.2f}MB")

            # Check if file is already staged in consume folder
            if file_in_consume_folder(filename):
                log(f"[SKIP] Already staged in consume folder: {filename}")
                if verbose:
                    print(f"  {prog}  {dim(filename):<43} {size_mb:>7.2f}M  {dim(file_hash)}  {dim('already staged')}")
                else:
                    print(f"{prog} " + dim(f"[skip] {filename}  (already in consume folder)"))
                total_skipped += 1
                continue

            exists, _ = paperless_document_exists(file_hash)

            if exists:
                log(f"[SKIP] Duplicate in Paperless: {filename}")
                if verbose:
                    print(f"  {prog}  {dim(filename):<43} {size_mb:>7.2f}M  {dim(file_hash)}  {dim('duplicate')}")
                else:
                    print(f"{prog} " + dim(f"[skip] {filename}"))
                total_skipped += 1
                continue

            if not dry_run:
                dest = CONSUME_DIR / filename
                copy_file_data_atomic(full_path, dest)
            log(f"[INGEST{'|DRY' if dry_run else ''}] {'Would add' if dry_run else 'Added'}: {filename}")

            if verbose:
                status = ok(C.BOLD + ('WOULD ADD' if dry_run else 'ADDED') + C.RESET)
                print(f"  {prog}  {ok(C.BOLD + '+' + C.RESET + C.GREEN + ' ' + filename):<52} {size_mb:>7.2f}M  {ok(file_hash)}  {status}")
            else:
                label = "[~]" if dry_run else "[+]"
                print(f"{prog} " + ok(f"{label} {filename}  ({size_mb:.2f} MB)"))

            total_added += 1
        
        # Write directory state after processing all files in this directory
        if not dry_run and not no_state:
            write_dir_state(directory, current_fingerprint, len(pdfs_in_dir))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    # Show completion message if we scanned fewer files than total
    if total_docs < pdf_total:
        remaining = pdf_total - total_docs
        print(f"\n{info(f'  Remaining {remaining} PDFs were in {total_dirs_skipped} unchanged directories (skipped)')}")
    
    print(bold(f"\n{'─'*54}"))
    print(f"  {info('PDFs scanned:      ')}  {bold(str(total_docs))}")
    print(f"  {info('Dirs skipped:      ')}  {bold(str(total_dirs_skipped))} {dim('(unchanged)')}")
    print(f"  {warn('Files skipped:     ')}  {bold(str(total_skipped))}")
    print(f"  {warn('→ quarantined:     ')}  {bold(str(total_quarantine))} {dim('(check/ folder)')}")
    print(f"  {ok(  'Added:             ')}  {bold(str(total_added))}")
    print(bold(f"{'─'*54}\n"))

    log(f"Summary — scanned:{total_docs}  dirs_skipped:{total_dirs_skipped}  files_skipped:{total_skipped}  quarantined:{total_quarantine}  added:{total_added}")
    log("=== Sync Complete ===")

if __name__ == "__main__":
    main()


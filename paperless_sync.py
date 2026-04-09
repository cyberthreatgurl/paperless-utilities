#!/usr/bin/env python3
import argparse
import hashlib
import re
import requests
import shutil
from pathlib import Path

import pypdf
from pypdf.errors import DependencyError, FileNotDecryptedError, PyPdfError, WrongPasswordError

# -----------------------------
# CONFIGURATION
# -----------------------------
PAPERLESS_URL = "http://192.168.1.12:8000"   # Change to your server hostname/IP
PAPERLESS_TOKEN = "ca60a3e9d5c7977d03f57b57be26f0bf4c8169da"

# Path to your mounted consume folder
CONSUME_DIR = Path("/Volumes/consume")

# Maximum allowed file size (example: 25 MB)
MAXIMUM_FILE_SIZE = 25 * 1024 * 1024

# Log file
LOG_FILE = Path("logs/paperless_sync.log")

# Quarantine folder for corrupt / password-protected / oversized PDFs
CHECK_DIR = Path("check")

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

def validate_pdf(path):
    """
    Returns (is_valid: bool, reason: str).
    Checks file size, corruption, and password protection.
    """
    if path.stat().st_size > MAXIMUM_FILE_SIZE:
        size_mb = path.stat().st_size / 1024 / 1024
        return False, f"oversized ({size_mb:.2f} MB)"

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
        help="Scan and validate without copying any files",
    )
    args = parser.parse_args()
    root_scan_dir = Path(args.root).resolve() if args.root else Path.cwd()
    verbose = args.verbose
    dry_run = args.dry_run

    # Ensure runtime directories exist
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECK_DIR.mkdir(parents=True, exist_ok=True)

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

    def status_line(label, value, good, good_msg, bad_msg):
        badge = ok("  OK  ") if good else err(" FAIL ")
        detail = ok(good_msg) if good else err(bad_msg)
        print(f"  [{badge}]  {info(label):<30} {dim(str(value))}  {detail}")

    print(info("  Environment"))
    status_line("Scan directory", root_scan_dir,  scan_ok,        "readable",         "not found / not a directory")
    status_line("Consume folder", CONSUME_DIR,    consume_write,  "mounted & writable","not writable / not mounted")
    status_line("Paperless API",  PAPERLESS_URL,  api_ok,         "reachable",        "unreachable")
    status_line("API token",      "•••••••••",    token_ok,       "valid",            "invalid" if api_ok else "skipped")
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
    # Pre-count PDFs
    # ------------------------------------------------------------------
    print(info(f"  Counting PDFs in {root_scan_dir} ..."), end="", flush=True)
    all_pdfs = sorted(root_scan_dir.rglob("*.pdf"))
    pdf_total = len(all_pdfs)
    print(f"\r  {bold(str(pdf_total))} PDF{'s' if pdf_total != 1 else ''} found in {dim(str(root_scan_dir))}")
    log(f"[INFO] {pdf_total} PDFs found in {root_scan_dir}")

    if pdf_total == 0:
        print(warn("  No PDFs to process."))
        return

    # ------------------------------------------------------------------
    # Scan for PDFs
    # ------------------------------------------------------------------
    total_docs      = 0
    total_skipped   = 0
    total_added     = 0
    total_quarantine = 0

    def progress(n):
        pct = int(n / pdf_total * 100)
        return f"{C.DIM}[{n:{len(str(pdf_total))}}/{pdf_total}  {pct:3}%]{C.RESET}"

    if verbose:
        pad = len(str(pdf_total)) * 2 + 8  # width of progress prefix
        print(f"  {'':<{pad}}  {'FILE':<43} {'SIZE':>8}  {'MD5 HASH':<34}  STATUS")
        print(f"  {'':<{pad}}  {'-'*43} {'-'*8}  {'-'*34}  {'------'}")

    for full_path in all_pdfs:
        filename = full_path.name
        total_docs += 1
        size_mb = full_path.stat().st_size / 1024 / 1024
        prog = progress(total_docs)

        # --- PDF validation ---
        is_valid, reason = validate_pdf(full_path)
        if not is_valid:
            if not dry_run:
                quarantine_dest = CHECK_DIR / filename
                shutil.copy(full_path, quarantine_dest)
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
            shutil.copy(full_path, dest)
        log(f"[INGEST{'|DRY' if dry_run else ''}] {'Would add' if dry_run else 'Added'}: {filename}")

        if verbose:
            status = ok(C.BOLD + ('WOULD ADD' if dry_run else 'ADDED') + C.RESET)
            print(f"  {prog}  {ok(C.BOLD + '+' + C.RESET + C.GREEN + ' ' + filename):<52} {size_mb:>7.2f}M  {ok(file_hash)}  {status}")
        else:
            label = "[~]" if dry_run else "[+]"
            print(f"{prog} " + ok(f"{label} {filename}  ({size_mb:.2f} MB)"))

        total_added += 1

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(bold(f"\n{'─'*54}"))
    print(f"  {info('PDFs scanned:  ')}  {bold(str(total_docs))}")
    print(f"  {warn('Skipped:       ')}  {bold(str(total_skipped))}")
    print(f"  {warn('→ quarantined: ')}  {bold(str(total_quarantine))} {dim('(check/ folder)')}")
    print(f"  {ok(  'Added:         ')}  {bold(str(total_added))}")
    print(bold(f"{'─'*54}\n"))

    log(f"Summary — scanned:{total_docs}  skipped:{total_skipped}  quarantined:{total_quarantine}  added:{total_added}")
    log("=== Sync Complete ===")

if __name__ == "__main__":
    main()


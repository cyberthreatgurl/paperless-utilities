#!/usr/bin/env python3
"""
Mark directories as scanned by creating .paperless_check_state files.

Recursively finds all directories containing PDFs under the specified root
and creates a state file in each. This allows paperless_sync.py to skip
unchanged directories on future runs.

Usage:
  python mark_scanned.py <root_directory>

The script will:
  1. Find all directories containing PDFs (recursively)
  2. Compute a fingerprint for each directory based on PDF names and mtimes
  3. Create a .paperless_check_state file in each directory
  4. Display a summary of directories marked

Inputs:
  - root_directory: Path to scan (expands ~ and resolves to absolute path)

Outputs:
  - .paperless_check_state file in each directory containing PDFs
  - Summary showing total directories marked and total PDFs found
"""
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

def compute_dir_fingerprint(directory: Path) -> str:
    """
    Compute MD5 fingerprint of all PDFs in a directory.
    Fingerprint is based on sorted filename:mtime_ns pairs.
    """
    pdfs = sorted(directory.glob("*.pdf"))
    if not pdfs:
        return hashlib.md5(b"").hexdigest()
    
    entries = [f"{p.name}:{p.stat().st_mtime_ns}" for p in pdfs]
    fingerprint_str = "|".join(entries)
    return hashlib.md5(fingerprint_str.encode("utf-8")).hexdigest()

def mark_directory_scanned(directory: Path) -> int:
    """
    Create a .paperless_check_state file in the specified directory.
    Returns the number of PDFs found in this directory.
    """
    pdfs = sorted(directory.glob("*.pdf"))
    pdf_count = len(pdfs)
    
    if pdf_count == 0:
        return 0
    
    fingerprint = compute_dir_fingerprint(directory)
    
    state_file = directory / ".paperless_check_state"
    state_data = {
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "pdf_count": pdf_count,
        "dir_fingerprint": fingerprint
    }
    
    try:
        with state_file.open("w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)
        return pdf_count
    except OSError as e:
        print(f"  Warning: Could not write state file to {directory}: {e}")
        return 0

def mark_tree_scanned(root_directory: str) -> None:
    """
    Recursively find all directories containing PDFs and mark them as scanned.
    """
    root_dir = Path(root_directory).expanduser().resolve()
    
    if not root_dir.exists() or not root_dir.is_dir():
        print(f"Error: {root_dir} is not a valid directory")
        sys.exit(1)
    
    print(f"Scanning for PDFs under: {root_dir}")
    
    # Find all PDFs recursively
    all_pdfs = list(root_dir.rglob("*.pdf"))
    
    if not all_pdfs:
        print("  No PDFs found.")
        return
    
    # Group PDFs by parent directory
    dirs_with_pdfs = {}
    for pdf_path in all_pdfs:
        parent = pdf_path.parent
        if parent not in dirs_with_pdfs:
            dirs_with_pdfs[parent] = []
        dirs_with_pdfs[parent].append(pdf_path)
    
    # Sort directories for consistent output
    sorted_dirs = sorted(dirs_with_pdfs.keys())
    
    print(f"  Found {len(all_pdfs)} PDFs in {len(sorted_dirs)} directories\n")
    
    # Mark each directory
    total_marked = 0
    total_pdfs = 0
    
    for directory in sorted_dirs:
        pdf_count = mark_directory_scanned(directory)
        if pdf_count > 0:
            rel_path = directory.relative_to(root_dir) if directory != root_dir else "."
            print(f"  ✓ {rel_path}  ({pdf_count} PDFs)")
            total_marked += 1
            total_pdfs += pdf_count
    
    print(f"\n{'─'*50}")
    print(f"  Directories marked: {total_marked}")
    print(f"  Total PDFs:         {total_pdfs}")
    print(f"{'─'*50}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python mark_scanned.py <directory>")
        print("\nMarks all subdirectories containing PDFs as already scanned.")
        print("This allows paperless_sync.py to skip unchanged directories.")
        sys.exit(1)
    
    mark_tree_scanned(sys.argv[1])

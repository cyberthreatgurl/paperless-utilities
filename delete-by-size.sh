#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false

usage() {
    echo "Usage: $0 [--dry-run] [directory] <size_in_bytes>"
    echo "Examples:"
    echo "  $0 --dry-run . 5000000"
    echo "  $0 5000000              # uses current directory"
    exit 1
}

# Parse --dry-run
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    shift
fi

# If only one argument remains, assume current directory
if [[ $# -eq 1 ]]; then
    TARGET_DIR="."
    MIN_SIZE_BYTES="$1"
elif [[ $# -eq 2 ]]; then
    TARGET_DIR="$1"
    MIN_SIZE_BYTES="$2"
else
    usage
fi

# Normalize directory
if [[ "$TARGET_DIR" == "." ]]; then
    TARGET_DIR="$(pwd)"
fi

# Validate directory
if [[ ! -d "$TARGET_DIR" ]]; then
    echo "Error: '$TARGET_DIR' is not a directory"
    exit 1
fi

# Validate size
if ! [[ "$MIN_SIZE_BYTES" =~ ^[0-9]+$ ]]; then
    echo "Error: size must be an integer number of bytes"
    exit 1
fi

echo "Scanning '$TARGET_DIR' for files >= $MIN_SIZE_BYTES bytes"
$DRY_RUN && echo "[DRY RUN] No files will be deleted"

# Core logic
find "$TARGET_DIR" -type f -size +"$((MIN_SIZE_BYTES - 1))"c | while read -r file; do
    FILE_SIZE=$(stat -c%s "$file")

    if $DRY_RUN; then
        echo "[DRY RUN] Would delete: $file ($FILE_SIZE bytes)"
    else
        echo "Deleting: $file ($FILE_SIZE bytes)"
        rm -f -- "$file"
    fi
done

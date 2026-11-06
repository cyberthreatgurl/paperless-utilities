#!/usr/bin/env bash
set -euo pipefail

# Recursively copy PDFs to a Paperless consume folder without exposing partial files.

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <source_folder> <destination_folder>"
    echo "Example: $0 /path/to/archive /path/to/consume"
    exit 1
fi

SRC_DIR="$1"
DEST_DIR="$2"

if [ ! -d "$SRC_DIR" ]; then
    echo "Error: Source directory '$SRC_DIR' does not exist." >&2
    exit 1
fi

if [ ! -d "$DEST_DIR" ]; then
    echo "Error: Destination directory '$DEST_DIR' does not exist." >&2
    exit 1
fi

echo "Scanning '$SRC_DIR' for PDF files..."

pdf_files=()
while IFS= read -r -d '' file; do
    pdf_files+=("$file")
done < <(find "$SRC_DIR" -type f -iname "*.pdf" ! -name '._*' -print0)
total_files=${#pdf_files[@]}

if [ "$total_files" -eq 0 ]; then
    echo "No PDF files found in '$SRC_DIR'."
    exit 0
fi

file_size() {
    if stat -c%s "$1" >/dev/null 2>&1; then
        stat -c%s "$1"
    else
        stat -f%z "$1"
    fi
}

echo "Calculating total size..."
total_size_bytes=0
for file in "${pdf_files[@]}"; do
    size=$(file_size "$file" 2>/dev/null || echo 0)
    total_size_bytes=$((total_size_bytes + size))
done

total_gb=$(awk "BEGIN {printf \"%.2f\", $total_size_bytes / 1024 / 1024 / 1024}")
echo "Found $total_files PDF files totaling ${total_gb} GB."
echo "Starting import..."

count=0
for file in "${pdf_files[@]}"; do
    filename=$(basename "$file")
    base="${filename%.*}"
    extension="${filename##*.}"
    target="$DEST_DIR/$filename"
    skip_file=false

    while [ -e "$target" ]; do
        hash_src=$(shasum -a 256 "$file" | awk '{print $1}')
        hash_dst=$(shasum -a 256 "$target" | awk '{print $1}')
        if [ "$hash_src" = "$hash_dst" ]; then
            skip_file=true
            break
        fi

        suffix=$(printf "%03d" "$((RANDOM % 1000))")
        target="$DEST_DIR/${base}-${suffix}.${extension}"
    done

    if [ "$skip_file" = false ]; then
        temporary_file=$(mktemp "$DEST_DIR/.paperless-import.XXXXXX")
        if ! cat "$file" > "$temporary_file"; then
            rm -f "$temporary_file"
            echo "Error: Failed to copy '$file'." >&2
            exit 1
        fi
        if ! mv "$temporary_file" "$target"; then
            rm -f "$temporary_file"
            echo "Error: Failed to stage '$file' as '$target'." >&2
            exit 1
        fi
    fi

    count=$((count + 1))
    percent=$((count * 100 / total_files))
    completed=$((percent / 2))
    remaining=$((50 - completed))
    bar=$(printf "%${completed}s" | tr ' ' '#')
    spaces=$(printf "%${remaining}s" | tr ' ' '-')
    printf "\r[%s%s] %d%% (%d/%d)" "$bar" "$spaces" "$percent" "$count" "$total_files"
done

echo
echo "Done! Processed $count PDFs."
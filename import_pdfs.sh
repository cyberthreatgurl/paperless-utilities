#!/usr/bin/env bash
set -euo pipefail

# Convert Word documents and copy PDFs to consume without exposing partial files.

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

echo "Scanning '$SRC_DIR' for PDF and Word files..."

files=()
while IFS= read -r -d '' file; do
    files+=("$file")
done < <(find "$SRC_DIR" -type f \( -iname "*.pdf" -o -iname "*.doc" -o -iname "*.docx" \) ! -name '._*' -print0)
total_files=${#files[@]}

if [ "$total_files" -eq 0 ]; then
    echo "No PDF or Word files found in '$SRC_DIR'."
    exit 0
fi

needs_conversion=false
for file in "${files[@]}"; do
    extension=$(printf '%s' "${file##*.}" | tr '[:upper:]' '[:lower:]')
    if [ "$extension" = "doc" ] || [ "$extension" = "docx" ]; then
        needs_conversion=true
        break
    fi
done

if [ "$needs_conversion" = true ] && ! command -v soffice >/dev/null 2>&1; then
    echo "Error: LibreOffice 'soffice' is required to convert .doc and .docx files." >&2
    exit 1
fi

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/paperless-import.XXXXXX")
cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

file_size() {
    if stat -c%s "$1" >/dev/null 2>&1; then
        stat -c%s "$1"
    else
        stat -f%z "$1"
    fi
}

echo "Calculating total size..."
total_size_bytes=0
for file in "${files[@]}"; do
    size=$(file_size "$file" 2>/dev/null || echo 0)
    total_size_bytes=$((total_size_bytes + size))
done

total_gb=$(awk "BEGIN {printf \"%.2f\", $total_size_bytes / 1024 / 1024 / 1024}")
echo "Found $total_files documents totaling ${total_gb} GB."
echo "Starting import..."

count=0
for file in "${files[@]}"; do
    filename=$(basename "$file")
    extension=$(printf '%s' "${filename##*.}" | tr '[:upper:]' '[:lower:]')
    staged_file="$file"
    if [ "$extension" = "doc" ] || [ "$extension" = "docx" ]; then
        conversion_dir="$TEMP_DIR/$count"
        mkdir -p "$conversion_dir/profile"
        if ! soffice \
            "-env:UserInstallation=file://$conversion_dir/profile" \
            --headless \
            --convert-to pdf \
            --outdir "$conversion_dir" \
            "$file" >/dev/null 2>&1; then
            echo "Error: Failed to convert '$file' to PDF." >&2
            exit 1
        fi
        staged_file="$conversion_dir/${filename%.*}.pdf"
        if [ ! -s "$staged_file" ]; then
            echo "Error: Conversion did not produce a PDF for '$file'." >&2
            exit 1
        fi
        filename="${filename%.*}.pdf"
    fi

    base="${filename%.*}"
    extension="${filename##*.}"
    target="$DEST_DIR/$filename"
    skip_file=false

    while [ -e "$target" ]; do
        hash_src=$(shasum -a 256 "$staged_file" | awk '{print $1}')
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
        if ! cat "$staged_file" > "$temporary_file"; then
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
echo "Done! Processed $count documents as PDFs."
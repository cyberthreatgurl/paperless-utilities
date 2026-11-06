#!/usr/bin/env bash
set -euo pipefail

# Paperless pre-consume hook for stable-size checks, form flattening, and OCR.

FILE="${DOCUMENT_WORKING_PATH:-${1:-}}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "PRECONSUME_QUARANTINE missing-file"
  exit 1
fi

FILE="$(cd "$(dirname "$FILE")" && pwd -P)/$(basename "$FILE")"
FILENAME="$(basename "$FILE")"
QUARANTINE_DIR="${PRECONSUME_QUARANTINE_DIR:-/usr/src/paperless/export/.quarantine}"
ORIGINALS_DIR="${PRECONSUME_ORIGINALS_DIR:-/usr/src/paperless/export/originals}"
PASSWORD_PROTECTED_PDFS_DIR="${PRECONSUME_PASSWORD_PROTECTED_DIR:-/usr/src/paperless/export/password_protected_pdfs}"
LOG_FILE="${PRECONSUME_LOG_FILE:-/usr/src/paperless/export/preconsume.log}"
DEBUG_LOG="${PRECONSUME_DEBUG_LOG:-/usr/src/paperless/export/preconsume-debug.log}"
LOG_DATE="$(date +%F)"
LOG_FILE="${LOG_FILE%.log}-${LOG_DATE}.log"
DEBUG_LOG="${DEBUG_LOG%.log}-${LOG_DATE}.log"

mkdir -p "$QUARANTINE_DIR" "$ORIGINALS_DIR"
touch "$LOG_FILE" "$DEBUG_LOG"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/paperless-preconsume.XXXXXX")"
TMP_OUT="$TMP_DIR/preconsume-ocr.pdf"
FLAT_BASE="$TMP_DIR/preconsume-flat"
FLATTENED="${FLAT_BASE}.pdf"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

timestamp() { date '+%Y-%m-%dT%H:%M:%S%z'; }

log() { echo "[$(timestamp)] $1: $FILENAME - $2" >> "$LOG_FILE"; }
debug() { echo "[$(timestamp)] $1: $FILENAME - $2" >> "$DEBUG_LOG"; }

if command -v file >/dev/null 2>&1; then
  MIME_TYPE="$(file -b --mime-type "$FILE" 2>/dev/null || echo unknown)"
  if [ "$MIME_TYPE" != "application/pdf" ]; then
    debug "SKIP" "not a PDF (mime=$MIME_TYPE)"
    log "SKIP" "not a PDF (mime=$MIME_TYPE)"
    exit 0
  fi
fi

quarantine_and_exit() {
  local reason="$1"
  local classification="${2:-invalid}"
  local base="${FILENAME%.*}"
  local destination="$QUARANTINE_DIR/${base}.${classification}.pdf"
  local suffix=1
  while [ -e "$destination" ]; do
    destination="$QUARANTINE_DIR/${base}.${classification}.${suffix}.pdf"
    suffix=$((suffix + 1))
  done
  cp "$FILE" "$destination" 2>/dev/null || true
  echo "PRECONSUME_QUARANTINE $FILENAME"
  log "QUARANTINE" "$reason (saved as ${destination##*/})"
  debug "QUARANTINE" "$reason (saved as ${destination##*/})"
  exit 1
}

move_encrypted_and_exit() {
  local reason="$1"
  local source_file="${DOCUMENT_SOURCE_PATH:-$FILE}"
  local base="${FILENAME%.*}"
  local destination="$PASSWORD_PROTECTED_PDFS_DIR/${base}.enc.pdf"
  local suffix=1

  if ! mkdir -p "$PASSWORD_PROTECTED_PDFS_DIR"; then
    echo "PRECONSUME_QUARANTINE $FILENAME"
    log "ERROR" "$reason; could not create $PASSWORD_PROTECTED_PDFS_DIR"
    debug "ERROR" "$reason; could not create $PASSWORD_PROTECTED_PDFS_DIR"
    exit 1
  fi
  while [ -e "$destination" ]; do
    destination="$PASSWORD_PROTECTED_PDFS_DIR/${base}.enc.${suffix}.pdf"
    suffix=$((suffix + 1))
  done

  if [ ! -f "$source_file" ] || ! mv "$source_file" "$destination"; then
    echo "PRECONSUME_QUARANTINE $FILENAME"
    log "ERROR" "$reason; could not move source file to $destination"
    debug "ERROR" "$reason; could not move source file to $destination"
    exit 1
  fi

  echo "PRECONSUME_QUARANTINE $FILENAME"
  log "QUARANTINE" "$reason; moved source to ${destination##*/}"
  debug "QUARANTINE" "$reason; moved source to ${destination##*/}"
  exit 1
}

for command_name in pdfinfo pdftocairo ocrmypdf file stat mv cp head grep; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    quarantine_and_exit "missing dependency: $command_name"
  fi
done

get_file_size() {
  if stat -c%s "$1" >/dev/null 2>&1; then
    stat -c%s "$1"
  else
    stat -f%z "$1" 2>/dev/null || echo 0
  fi
}

debug "START" "initial size=$(get_file_size "$FILE") mime=$(file -b "$FILE" 2>/dev/null || echo unknown)"

previous_size=-1
stable=0
for iteration in 1 2 3 4 5; do
  size=$(get_file_size "$FILE")
  debug "SIZE_CHECK" "iter=$iteration size=$size prev=$previous_size stable=$stable"
  if [ "$size" -eq "$previous_size" ] && [ "$size" -ne 0 ]; then
    stable=$((stable + 1))
  else
    stable=0
  fi
  previous_size=$size
  [ "$stable" -ge 2 ] && break
  sleep 1
done

if [ "$previous_size" -eq 0 ]; then
  quarantine_and_exit "zero-byte file after wait"
fi

if ! head -c 1024 "$FILE" | grep "%PDF-" >/dev/null; then
  quarantine_and_exit "missing %PDF- header"
fi

if pdfinfo "$FILE" 2>/dev/null | grep '^Encrypted:[[:space:]]*yes' >/dev/null; then
  move_encrypted_and_exit "encrypted PDF"
fi

cp "$FILE" "$ORIGINALS_DIR/$FILENAME" 2>/dev/null || true

PROCESS_FILE="$FILE"
if pdfinfo "$FILE" 2>/dev/null | grep "Form: AcroForm" >/dev/null; then
  debug "FLATTEN" "AcroForm detected, flattening"
  if ! pdftocairo -pdf "$FILE" "$FLAT_BASE" >/dev/null 2>>"$DEBUG_LOG"; then
    quarantine_and_exit "pdftocairo flattening failed"
  fi
  if [ ! -s "$FLATTENED" ]; then
    quarantine_and_exit "pdftocairo produced no output"
  fi
  PROCESS_FILE="$FLATTENED"
fi

debug "OCR" "running ocrmypdf on $PROCESS_FILE"
OCR_EXIT=0
ocrmypdf \
  --skip-text \
  --fast-web-view 0 \
  --jobs 2 \
  --deskew \
  --output-type pdf \
  -v 1 \
  "$PROCESS_FILE" "$TMP_OUT" 2>>"$DEBUG_LOG" || OCR_EXIT=$?
debug "OCR_EXIT" "code=$OCR_EXIT tmp_out_size=$(get_file_size "$TMP_OUT")"

case "$OCR_EXIT" in
  0) OCR_REASON="success" ;;
  2) OCR_REASON="input file error or format warning (soft fail)" ;;
  6) OCR_REASON="prior OCR found (soft fail)" ;;
  15) OCR_REASON="unexpected error (soft fail, output exists)" ;;
  1) OCR_REASON="bad arguments" ;;
  3) OCR_REASON="missing dependency" ;;
  4) OCR_REASON="invalid output PDF" ;;
  5) OCR_REASON="output file access error" ;;
  7) OCR_REASON="subprocess error" ;;
  8) OCR_REASON="encrypted PDF" ;;
  9) OCR_REASON="Tesseract config error" ;;
  10) OCR_REASON="PDF/A conversion failed" ;;
  *) OCR_REASON="unrecognized exit code $OCR_EXIT" ;;
esac

if [ "$OCR_EXIT" -eq 8 ]; then
  move_encrypted_and_exit "encrypted PDF"
fi

if [ "$OCR_EXIT" -ne 0 ]; then
  if { [ "$OCR_EXIT" -eq 2 ] || [ "$OCR_EXIT" -eq 6 ] || [ "$OCR_EXIT" -eq 15 ]; } && [ -s "$TMP_OUT" ]; then
    debug "OCR_SOFT_OK" "exit=$OCR_EXIT reason=$OCR_REASON output present"
  else
    quarantine_and_exit "ocrmypdf failure ($OCR_REASON)"
  fi
else
  debug "OCR_OK" "ocr succeeded"
fi

if [ -s "$TMP_OUT" ]; then
  if cat "$TMP_OUT" > "$FILE" 2>>"$DEBUG_LOG"; then
    debug "REPLACE" "replaced original with OCR output tmp_out_size=$(get_file_size "$TMP_OUT")"
    echo "PRECONSUME_REPAIRED $FILENAME"
    log "OK" "passed and replaced"
    exit 0
  fi
  quarantine_and_exit "failed to overwrite original file contents"
fi

quarantine_and_exit "final output missing after OCR"
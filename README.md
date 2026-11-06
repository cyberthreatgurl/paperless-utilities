# paperless-utilities

Tools to scan, validate, and sync PDF files to Paperless-ngx with intelligent duplicate detection and directory state tracking.

## Features

- **PDF Validation** - Detects corrupt, oversized, and password-protected files
- **Consume Cleanup** - Finds invalid PDFs already in the consume folder and moves them to quarantine
- **Duplicate Detection** - Checks Paperless API via MD5 checksums to avoid re-ingestion
- **Consume Folder Pre-check** - Skips files already staged but not yet ingested
- **Directory State Tracking** - Remembers which folders have been scanned to avoid redundant processing
- **Quarantine System** - Invalid PDFs moved to check folder for review
- **Progress Tracking** - Real-time console output with file counts and percentages
- **Dry-run Mode** - Test without making any changes
- **PDF Importer** - Copy PDF archives into the consume folder with duplicate handling
- **Pre-consume Hook** - Validate, flatten, and OCR incoming PDFs

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/cyberthreatgurl/paperless-utilities.git
cd paperless-utilities

# Install as a global command
pip install .
```

### Configuration

```bash
# Copy example config
cp .env.example .env

# Edit with your values
nano .env
```

Required settings:
- `PAPERLESS_URL` - Your Paperless-ngx URL (e.g., http://localhost:8000)
- `PAPERLESS_TOKEN` - API token from Paperless → Settings → API Tokens  
- `CONSUME_DIR` - Path to Paperless consume folder (e.g., /mnt/consume)

### Usage

```bash
# Sync PDFs from a directory
paperless-sync ~/Documents

# Dry-run to see what would happen
paperless-sync --dry-run ~/Documents

# Mark directories as already processed (skip on future runs)
paperless-mark-scanned ~/Documents

# Force re-scan ignoring saved state
paperless-sync --force ~/Documents

# Show detailed progress
paperless-sync --verbose ~/Documents

# Preview and quarantine invalid PDFs already in the consume folder
paperless-sync --clean-consume --dry-run
paperless-sync --clean-consume
```

## Requirements

- Python 3.10 or higher
- Paperless-ngx instance (reachable over network)
- Consume folder mounted locally (Samba/NFS/local filesystem)

The pre-consume hook additionally requires `poppler` (`pdfinfo` and `pdftocairo`),
`ocrmypdf`, and `tesseract`. On macOS, install them with:

```bash
brew install poppler tesseract ocrmypdf jbig2enc pngquant
```

`import_pdfs.sh` also imports `.doc` and `.docx` files by converting them with
LibreOffice's `soffice` command before placing the generated PDFs in consume.
Install LibreOffice on macOS with `brew install --cask libreoffice` and ensure
`soffice` is available on `PATH`.

## Pre-consume Tools

`pre-consume.sh` is intended to be configured as Paperless-ngx's pre-consume
hook. It checks for a stable, valid PDF, flattens AcroForms when detected, runs
OCRmyPDF, and replaces the input contents only after successful output
validation. Non-PDF inputs are left unchanged for Paperless to handle. Configure
`PRECONSUME_QUARANTINE_DIR`, `PRECONSUME_ORIGINALS_DIR`,
`PRECONSUME_LOG_FILE`, and `PRECONSUME_DEBUG_LOG` to change its output paths.
It creates separate `preconsume-YYYY-MM-DD.log` and
`preconsume-debug-YYYY-MM-DD.log` files for each local calendar day.
Encrypted PDFs are moved out of the watched source path into
`password_protected_pdfs/filename.enc.pdf` and reported as failed consumption.
Numbered suffixes are used if a destination already exists. In Docker, the hook
uses `/usr/src/paperless/export/password_protected_pdfs`, which should map to
the host path configured by `PASSWORD_PROTECTED_PDFS_DIR` in `.env`.

To recursively import PDFs and Word documents into an existing consume folder:

```bash
import_pdfs.sh /path/to/archive /path/to/consume
```

Word files are converted to PDFs in a temporary directory; source files remain
unchanged. Identical PDFs with the same name are skipped, and different files
with that name are staged under a suffixed filename. Files are copied to a
temporary file before being moved into the consume folder.

Generate a read-only reconciliation report for PDFs in the consume and
pre-consume quarantine folders:

```bash
paperless-reconcile
```

The command writes a CSV under `logs/` and prints grouped counts. It checks
Paperless using exact SHA-256 matches and correlates task history with saved
pre-OCR originals. By default it is read-only. Use `--apply` to move confirmed
ingests to `export/already_ingested/`, hook failures to quarantine, encrypted
files to `PASSWORD_PROTECTED_PDFS_DIR`, and AppleDouble sidecars to
`export/appledouble_sidecars/`. Add `--retry-unmatched` with `--apply` to
retrigger consume files that have no exact Paperless match or recorded hook
failure. The action is recorded in the CSV. Use `--no-api` for a local-only
report, or `--output /path/to/report.csv` to choose the CSV path.

To inspect PDFs already in the consume folder before Paperless retries them, use
`paperless-sync --clean-consume --dry-run`. Remove `--dry-run` to move invalid
files into `CHECK_DIR`: malformed PDFs receive a `.corrupt.pdf` suffix, while
other validation failures receive `.invalid.pdf`. Valid PDFs remain in consume.

The Python sync tools and shell importer ignore macOS `._*` AppleDouble
sidecars. `paperless-sync` stages file bytes without copying extended
attributes, which helps avoid creating new sidecars on SMB consume mounts.

## Documentation

- **[Installation Guide](INSTALL.md)** - Detailed installation options including standalone executables
- **[Changelog](CHANGELOG.md)** - Release history
- **[Module Docstrings](paperless_sync.py)** - Technical documentation in code

## How It Works

1. **Scan Phase** - Recursively finds all PDFs under the specified root directory
2. **State Check** - Reads `.paperless_check_state` files to skip unchanged directories  
3. **Validation** - Checks each PDF for corruption, encryption, and size limits
4. **Duplicate Detection** - Queries Paperless API by MD5 checksum
5. **Consume Check** - Verifies file isn't already in consume folder waiting for ingestion
6. **Copy Phase** - Stages new valid PDFs to consume folder
7. **State Recording** - Writes state files to skip unchanged directories on next run

Invalid PDFs are quarantined to the `check/` folder for manual review.

## Command Reference

### paperless-sync

```
Usage: paperless-sync [options] [directory]

Options:
  -v, --verbose     Show detailed file information (MD5, size)
  -n, --dry-run     Scan without copying files
  -f, --force       Ignore saved state, re-scan all directories
  --no-state        Don't write .paperless_check_state files
  --clean-consume   Quarantine invalid PDFs already in the consume folder

Arguments:
  directory         Root folder to scan (default: current directory)
```

### paperless-mark-scanned

```
Usage: paperless-mark-scanned <directory>

Recursively marks all subdirectories containing PDFs as already scanned
by creating .paperless_check_state files. Use this to skip directories
on future paperless-sync runs.
```

## State Files

`.paperless_check_state` files are created in each processed directory:

```json
{
  "last_checked": "2026-04-09T19:45:24.572471+00:00",
  "pdf_count": 16,
  "dir_fingerprint": "26bcb101fc814bbfe0e79e6e95efc690"
}
```

The fingerprint is an MD5 hash of all PDF filenames and modification times. If any PDF in the directory changes, the fingerprint changes and the directory is re-scanned.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PAPERLESS_URL` | Paperless-ngx base URL | *Required* |
| `PAPERLESS_TOKEN` | API authentication token | *Required* |
| `CONSUME_DIR` | Consume folder path | *Required* |
| `MAXIMUM_FILE_SIZE_MB` | Max PDF size in MB | 25 |
| `LOG_FILE` | Log file path | logs/paperless_sync.log |
| `CHECK_DIR` | Quarantine folder | check/ |

## Exit Codes

- `0` - Success
- `1` - Missing required environment variables or critical errors

## Security

- **Never commit `.env`** - Contains your API token (already in .gitignore)
- API tokens should be read-only if possible
- Use environment variables instead of hardcoded credentials
- Validate all file paths to prevent directory traversal

## Contributing

See [.github/copilot-instructions.md](.github/copilot-instructions.md) for coding standards.

## License

GPL-3.0-only - See LICENSE file for details

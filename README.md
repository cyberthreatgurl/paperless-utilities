<<<<<<< HEAD
# paperless-utilities
Tools to clean-up local directories before ingestion into Paperless-NGX
=======
# paperless-scripts

A command-line tool that scans a folder for PDF files and ingests new ones into [Paperless-ngx](https://docs.paperless-ngx.com/) via its consume folder. Duplicate detection uses the Paperless API to avoid re-ingesting files that already exist in the archive.

---

## Requirements

- Python 3.10+
- Paperless-ngx instance reachable over the network
- Consume folder mounted locally (e.g. via Samba/SMB)

### Python dependencies

```
pip install -r requirements.txt
```

Or manually:

```
pip install requests pypdf python-dotenv cryptography
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `PAPERLESS_URL` | Base URL of your Paperless-ngx instance, e.g. `http://192.168.1.12:8000` |
| `PAPERLESS_TOKEN` | API token from Paperless-ngx → Settings → API Tokens |
| `CONSUME_DIR` | Local path to the mounted consume folder, e.g. `/Volumes/consume` |

The maximum file size limit (default 25 MB) can be changed by editing `MAXIMUM_FILE_SIZE` at the top of `paperless_sync.py`.

> **Never commit `.env`** — it is listed in `.gitignore` and contains your API token.

---

## Usage

```
python paperless_sync.py [ROOT_DIR] [--dry-run] [--verbose]
```

### Arguments

| Argument | Description |
|---|---|
| `ROOT_DIR` | Directory to scan recursively for PDFs. Defaults to the current working directory if omitted. |
| `-n`, `--dry-run` | Scan and validate without copying any files. Reports what *would* be ingested or quarantined. |
| `-v`, `--verbose` | Print a table showing filename, size, MD5 hash, and status for every PDF found. |

### Examples

Sync your Downloads folder:
```
python paperless_sync.py ~/Downloads
```

Preview what would be synced from a OneDrive folder without touching anything:
```
python paperless_sync.py ~/Library/CloudStorage/OneDrive-Personal --dry-run
```

Verbose scan of the current directory:
```
python paperless_sync.py --verbose
```

Dry run with verbose output:
```
python paperless_sync.py ~/Documents -n -v
```

---

## What it does

1. **Environment check** — validates the scan directory, consume folder (Samba mount), Paperless API, and API token before doing any work. Results are printed as a colour-coded status block.

2. **PDF validation** — each PDF is checked before ingestion:
   - **Oversized** — larger than `MAXIMUM_FILE_SIZE`
   - **Corrupt** — cannot be parsed by pypdf
   - **Password-protected** — encrypted and not openable with a blank password

   Invalid files are copied to the `check/` folder for manual review and are never sent to Paperless.

3. **Duplicate detection** — computes the MD5 checksum of each valid PDF and queries the Paperless API. Files already in the archive are skipped.

4. **Ingestion** — new, valid PDFs are copied to the consume folder. Paperless picks them up automatically from there.

---

## Output

### Status indicators

| Symbol | Meaning |
|---|---|
| `[+] filename` | File is new and was ingested |
| `[~] filename` | Dry-run: file would be ingested |
| `[skip] filename` | Already exists in Paperless (duplicate) |
| `[!] filename (reason)` | Invalid PDF — copied to `check/` |

### Verbose table columns

```
  FILE                                          SIZE     MD5 HASH                           STATUS
  + invoice.pdf                                0.42M  d41d8cd98f00b204e9800998ecf8427e  ADDED
    old-doc.pdf                                1.10M  9e107d9d372bb6826bd81d3542a419d8  duplicate
```

---

## Folder structure

```
paperless-scripts/
├── paperless_sync.py     # Main script
├── requirements.txt
├── logs/
│   └── paperless_sync.log   # Created automatically on first run
└── check/
    └── ...                  # Quarantined PDFs land here
```

`logs/` and `check/` are created automatically on startup if they do not exist.

---

## Logging

All activity is written to `logs/paperless_sync.log`. ANSI colour codes are stripped from log output so the file remains readable with any text editor.
>>>>>>> cd91d75 (Fixes to include file count, dry-run and file-size)

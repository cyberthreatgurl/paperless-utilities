# Installation Guide

This guide covers different ways to install and use the Paperless utilities.

## Prerequisites

- Python 3.10 or higher
- pip (Python package installer)
- A running Paperless-ngx instance
- LibreOffice (`soffice`) to import `.doc` and `.docx` files
- For the pre-consume hook: `poppler`, `ocrmypdf`, and `tesseract`

## Installation Methods

### Option 1: Install as System Command (Recommended)

This installs the scripts as global commands you can run from anywhere.

#### Step 1: Install the package

From the project directory:

```bash
pip install .
```

Or for development (changes to code take effect immediately):

```bash
pip install -e .
```

#### Step 2: Set up environment variables

Create a `.env` file in your working directory or set environment variables:

```bash
# Copy the example file
cp .env.example .env

# Edit with your values
nano .env
```

Required variables:
- `PAPERLESS_URL` - Your Paperless-ngx URL (e.g., http://localhost:8000)
- `PAPERLESS_TOKEN` - Your API authentication token
- `CONSUME_DIR` - Path to the Paperless consume folder

Optional variables:
- `MAXIMUM_FILE_SIZE_MB` - Max PDF size in MB (default: 25)
- `LOG_FILE` - Log file path (default: logs/paperless_sync.log)
- `CHECK_DIR` - Quarantine folder (default: check/)

#### Step 3: Run the commands

```bash
# Sync PDFs to Paperless
paperless-sync ~/Documents

# Preview and quarantine invalid PDFs already in the consume folder
paperless-sync --clean-consume --dry-run
paperless-sync --clean-consume

# Mark directories as already scanned
paperless-mark-scanned ~/Documents

# Import an archive into the consume folder
./import_pdfs.sh ~/Documents/archive /path/to/consume

# Show help
paperless-sync --help
```

### Option 2: Run Directly with Python

If you don't want to install globally, you can run the scripts directly:

```bash
# Activate virtual environment
source venv/bin/activate

# Run scripts
python paperless_sync.py ~/Documents
python mark_scanned.py ~/Documents

# Clean invalid PDFs out of the configured consume folder
python paperless_sync.py --clean-consume --dry-run
```

### Option 3: Create Standalone Executables (Advanced)

For distribution to users without Python, create standalone executables:

#### Install PyInstaller

```bash
pip install pyinstaller
```

#### Build executables

```bash
# Build paperless-sync
pyinstaller --onefile \
  --name paperless-sync \
  --add-data ".env.example:." \
  paperless_sync.py

# Build paperless-mark-scanned
pyinstaller --onefile \
  --name paperless-mark-scanned \
  mark_scanned.py
```

Executables will be in `dist/`:
- `dist/paperless-sync`
- `dist/paperless-mark-scanned`

Copy these to `/usr/local/bin` or another directory in your PATH.

## Uninstallation

```bash
pip uninstall paperless-utilities
```

## Updating

```bash
# Pull latest changes
git pull

# Reinstall
pip install --upgrade --force-reinstall .
```

## Troubleshooting

### Command not found after installation

The installation location may not be in your PATH. Find where pip installed it:

```bash
pip show paperless-utilities
```

Add the location to your PATH in `~/.zshrc` or `~/.bashrc`:

```bash
export PATH="$PATH:$HOME/.local/bin"
```

### Permission denied

If you get permission errors, install in user mode:

```bash
pip install --user .
```

### Dependencies not found

Make sure you're in the project directory with `requirements.txt`, or install manually:

```bash
pip install requests pypdf python-dotenv
```

The pre-consume hook uses system tools rather than Python packages. On macOS,
install them with `brew install poppler tesseract ocrmypdf jbig2enc pngquant`.

The shell tools are installed by `pip install .` and are also available directly
from the repository as `./import_pdfs.sh` and `./pre-consume.sh`.
The importer requires LibreOffice's `soffice` executable only when the source
archive contains `.doc` or `.docx` files. On macOS, install it with
`brew install --cask libreoffice`.

## Configuration Files

The scripts look for `.env` in these locations (in order):
1. Current working directory
2. Script installation directory
3. User home directory (`~/.paperless-utilities.env`)

You can also set environment variables directly:

```bash
export PAPERLESS_URL="http://localhost:8000"
export PAPERLESS_TOKEN="your-token-here"
export CONSUME_DIR="/path/to/consume"
paperless-sync ~/Documents
```

## Running as a Cron Job

To run automatically on a schedule:

```bash
# Edit crontab
crontab -e

# Add entry (runs daily at 2 AM)
0 2 * * * /usr/local/bin/paperless-sync /path/to/documents >> /var/log/paperless-sync.log 2>&1
```

For user installations:

```bash
0 2 * * * $HOME/.local/bin/paperless-sync /path/to/documents >> $HOME/paperless-sync.log 2>&1
```

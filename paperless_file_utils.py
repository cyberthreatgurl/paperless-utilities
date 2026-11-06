"""Helpers for copying Paperless PDFs without importing AppleDouble metadata.

Key features:
  - Identify macOS AppleDouble sidecar filenames.
  - Copy only file bytes and permission bits using an atomic destination replace.

Usage:
  Import `is_appledouble_file` when discovering PDFs and
  `copy_file_data_atomic` when staging a document.

Inputs:
  - Paths to source and destination files.

Outputs:
  - A boolean sidecar classification or an atomically replaced destination file.

Dependencies:
  - Python standard library only.

Operational assumptions:
  - The destination directory exists and supports temporary files and replace.
  - The source remains available until the copy completes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def is_appledouble_file(path: Path) -> bool:
    """Return whether a file uses macOS's AppleDouble sidecar naming pattern."""
    return path.name.startswith("._")


def copy_file_data_atomic(source: Path, destination: Path) -> None:
    """Copy bytes and mode without extended attributes, then atomically replace."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".paperless-stage-",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary_path)
        shutil.copymode(source, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)

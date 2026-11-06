"""Tests for PDF validation and consume-folder quarantine.

Usage:
  python -m unittest tests.test_paperless_sync

Inputs:
  Temporary PDF and HTML files created by the test cases.

Outputs:
  Assertions for corrupt-file classification and quarantine behavior.

Dependencies:
  unittest, pypdf, and paperless_sync.

Operational assumptions:
  Paperless environment variables are required when importing paperless_sync;
  tests provide harmless local defaults and do not call the Paperless API.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pypdf

os.environ.setdefault("PAPERLESS_URL", "http://localhost")
os.environ.setdefault("PAPERLESS_TOKEN", "test-token")
os.environ.setdefault("CONSUME_DIR", "/tmp/paperless-test-consume")

from paperless_sync import quarantine_invalid_consume_files


class ConsumeQuarantineTests(unittest.TestCase):
    """Verify invalid PDFs are quarantined while valid files remain staged."""

    def test_quarantines_html_as_corrupt(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            consume_dir = root / "consume"
            check_dir = root / "check"
            consume_dir.mkdir()
            check_dir.mkdir()
            source = consume_dir / "report.pdf"
            source.write_text("<html>not a PDF</html>", encoding="utf-8")

            result = quarantine_invalid_consume_files(consume_dir, check_dir)

            self.assertEqual(result, (1, 0))
            self.assertFalse(source.exists())
            self.assertEqual(
                (check_dir / "report.corrupt.pdf").read_text(encoding="utf-8"),
                "<html>not a PDF</html>",
            )

    def test_dry_run_leaves_corrupt_file_in_consume(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            consume_dir = root / "consume"
            check_dir = root / "check"
            consume_dir.mkdir()
            check_dir.mkdir()
            source = consume_dir / "report.pdf"
            source.write_text("<html>not a PDF</html>", encoding="utf-8")

            result = quarantine_invalid_consume_files(
                consume_dir,
                check_dir,
                dry_run=True,
            )

            self.assertEqual(result, (1, 0))
            self.assertTrue(source.exists())
            self.assertEqual(list(check_dir.iterdir()), [])

    def test_leaves_valid_pdf_in_consume(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            consume_dir = root / "consume"
            check_dir = root / "check"
            consume_dir.mkdir()
            check_dir.mkdir()
            source = consume_dir / "report.pdf"
            writer = pypdf.PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with source.open("wb") as pdf_file:
                writer.write(pdf_file)

            result = quarantine_invalid_consume_files(consume_dir, check_dir)

            self.assertEqual(result, (0, 0))
            self.assertTrue(source.exists())
            self.assertEqual(list(check_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
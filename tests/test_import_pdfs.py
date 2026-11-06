"""Tests for the archive importer’s Word-to-PDF staging behavior.

Functional description:
  Verify that Office documents are converted before they are exposed to
  Paperless and that missing conversion tooling fails without staging them.

Usage:
  python -m pytest tests/test_import_pdfs.py

Inputs:
  Temporary source documents and destination folders.

Outputs:
  Assertions over conversion results and importer exit status.

Dependencies:
  Python standard library and Bash. LibreOffice is replaced by a test stub.

Operational assumptions:
  The repository importer is executable through Bash, and tests do not alter
  files outside their temporary directories.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


IMPORTER = Path(__file__).resolve().parents[1] / "import_pdfs.sh"


class ImportPdfsTests(unittest.TestCase):
    """Exercise Word conversion at the importer boundary."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source"
        self.destination = self.root / "consume"
        self.bin_directory = self.root / "bin"
        self.source.mkdir()
        self.destination.mkdir()
        self.bin_directory.mkdir()

        soffice = self.bin_directory / "soffice"
        soffice.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "output_dir=\n"
            "input_file=\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    --outdir) output_dir=\"$2\"; shift 2 ;;\n"
            "    -*) shift ;;\n"
            "    *) input_file=\"$1\"; shift ;;\n"
            "  esac\n"
            "done\n"
            "mkdir -p \"$output_dir\"\n"
            "output_name=$(basename \"${input_file%.*}\").pdf\n"
            "printf '%s\\n' '%PDF-1.4' 'mock converted file' > \"$output_dir/$output_name\"\n",
            encoding="utf-8",
        )
        soffice.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_import(self, *, include_converter: bool) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        path_entries = ["/usr/bin", "/bin"]
        if include_converter:
            path_entries.insert(0, str(self.bin_directory))
        environment["PATH"] = os.pathsep.join(path_entries)
        return subprocess.run(
            ["bash", str(IMPORTER), str(self.source), str(self.destination)],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )

    def test_converts_doc_and_docx_to_consume_pdfs(self) -> None:
        (self.source / "Quarterly Report.docx").write_text("docx", encoding="utf-8")
        (self.source / "Meeting Notes.doc").write_text("doc", encoding="utf-8")

        result = self.run_import(include_converter=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            {path.name for path in self.destination.iterdir()},
            {"Quarterly Report.pdf", "Meeting Notes.pdf"},
        )
        self.assertTrue((self.source / "Quarterly Report.docx").is_file())
        self.assertTrue((self.source / "Meeting Notes.doc").is_file())
        for pdf_path in self.destination.glob("*.pdf"):
            self.assertTrue(pdf_path.read_bytes().startswith(b"%PDF-1.4"))

    def test_missing_converter_does_not_stage_word_document(self) -> None:
        (self.source / "Quarterly Report.docx").write_text("docx", encoding="utf-8")

        result = self.run_import(include_converter=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'soffice' is required", result.stderr)
        self.assertEqual(list(self.destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
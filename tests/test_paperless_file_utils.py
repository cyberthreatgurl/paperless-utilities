"""Tests for sidecar filtering and metadata-free PDF staging."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paperless_file_utils import copy_file_data_atomic, is_appledouble_file


class AppleDoubleTests(unittest.TestCase):
    """Ensure AppleDouble files are identified and never staged as PDFs."""

    def test_detects_appledouble_name(self) -> None:
        self.assertTrue(is_appledouble_file(Path("._document.pdf")))
        self.assertFalse(is_appledouble_file(Path("document.pdf")))

    def test_atomic_copy_preserves_bytes_and_mode_without_sidecar(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.pdf"
            destination = root / "destination.pdf"
            source.write_bytes(b"%PDF-1.4\nfixture\n")
            source.chmod(0o640)

            copy_file_data_atomic(source, destination)

            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o640)
            self.assertEqual(sorted(path.name for path in root.iterdir()), [
                "destination.pdf",
                "source.pdf",
            ])


if __name__ == "__main__":
    unittest.main()

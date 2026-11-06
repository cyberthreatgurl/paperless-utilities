"""Tests for read-only Paperless disposition reconciliation."""

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from paperless_reconcile import (
    HookEvent,
    PdfFile,
    _hook_disposition,
    _is_later_event,
    apply_safe_actions,
    matching_successful_task,
    parse_hook_event,
    source_filename_for_quarantine,
)


class HookLogParsingTests(unittest.TestCase):
    """Verify hook log formats and source/destination correlation."""

    def test_parses_current_event_and_saved_destination(self) -> None:
        line = (
            "[2026-11-05T15:00:00-05:00] QUARANTINE: report.pdf - "
            "encrypted PDF (saved as report.enc.pdf)"
        )

        parsed = parse_hook_event(line)

        self.assertIsNotNone(parsed)
        filename, event = parsed
        self.assertEqual(filename, "report.pdf")
        self.assertEqual(event.event, "QUARANTINE")
        self.assertEqual(event.reason, "encrypted PDF (saved as report.enc.pdf)")
        self.assertEqual(event.destination_name, "report.enc.pdf")
        self.assertEqual(event.timestamp.isoformat(), "2026-11-05T15:00:00-05:00")

    def test_parses_legacy_corrupt_event(self) -> None:
        parsed = parse_hook_event("[CORRUPT] report.pdf — encrypted PDF")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], "report.pdf")
        self.assertEqual(parsed[1].event, "QUARANTINE")
        self.assertEqual(parsed[1].reason, "encrypted PDF")

    def test_undated_legacy_event_does_not_override_dated_event(self) -> None:
        dated = HookEvent(
            "OK",
            "passed",
            datetime(2026, 11, 5, tzinfo=timezone.utc),
        )
        undated = HookEvent("QUARANTINE", "old error")

        self.assertFalse(_is_later_event(undated, dated))
        self.assertTrue(_is_later_event(dated, undated))


class QuarantineNameTests(unittest.TestCase):
    """Verify quarantine copies map back to source basenames conservatively."""

    def test_maps_encrypted_names_with_collision_suffixes(self) -> None:
        self.assertEqual(
            source_filename_for_quarantine("report.enc.2.pdf"),
            "report.pdf",
        )

    def test_maps_invalid_and_legacy_quarantine_suffixes(self) -> None:
        self.assertEqual(
            source_filename_for_quarantine("report.invalid.1.pdf"),
            "report.pdf",
        )
        self.assertEqual(
            source_filename_for_quarantine("report.quarantine.pdf"),
            "report.pdf",
        )

    def test_later_success_is_not_treated_as_content_match(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "report.invalid.pdf"
            path.touch()
            pdf_file = PdfFile(path, "quarantine", 0)
            source_events = {"report.pdf": HookEvent("OK", "passed")}

            disposition, _ = _hook_disposition(
                pdf_file,
                source_events,
                {},
                {},
            )

        self.assertEqual(
            disposition,
            "Same basename later hook pass; content unverified",
        )


class SuccessfulTaskCorrelationTests(unittest.TestCase):
    """Require matching saved original bytes before trusting a successful task."""

    def test_requires_exact_saved_original_hash(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            originals = root / "originals"
            originals.mkdir()
            original = originals / "document.pdf"
            original.write_bytes(b"first PDF content")
            pdf_file = PdfFile(original, "consume", original.stat().st_size)
            tasks = {
                "document.pdf": {
                    "task_id": "task-1",
                    "status": "success",
                    "result_data": {"document_id": 12},
                },
            }
            from paperless_reconcile import sha256_file

            pdf_file = PdfFile(
                original,
                "consume",
                original.stat().st_size,
                sha256_file(original),
            )

            self.assertIsNotNone(matching_successful_task(pdf_file, tasks, originals))

            pdf_file.path.write_bytes(b"different PDF content")
            self.assertIsNone(matching_successful_task(pdf_file, tasks, originals))


class ApplyActionTests(unittest.TestCase):
    """Verify opt-in file organization preserves data and never overwrites."""

    def test_organizes_confirmed_failures_and_retriggers_unmatched_files(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            consume = root / "consume"
            protected = root / "protected"
            quarantine = root / "quarantine"
            already_ingested = root / "already_ingested"
            sidecars = root / "sidecars"
            consume.mkdir()
            records = [
                ("done.pdf", "prior_success", "", ""),
                ("locked.pdf", "not_found", "QUARANTINE", "encrypted PDF"),
                ("broken.pdf", "not_found", "QUARANTINE", "invalid output PDF"),
                ("._sidecar.pdf", "not_checked", "", ""),
                ("retry.pdf", "not_found", "OK", ""),
            ]
            rows = []
            for filename, api_status, event, reason in records:
                path = consume / filename
                path.write_bytes(filename.encode("ascii"))
                rows.append(
                    {
                        "location": "consume",
                        "path": str(path),
                        "paperless_status": api_status,
                        "hook_event": event,
                        "hook_reason": reason,
                        "execution_action": "",
                    },
                )

            apply_safe_actions(
                rows,
                protected_dir=protected,
                quarantine_dir=quarantine,
                already_ingested_dir=already_ingested,
                sidecar_dir=sidecars,
                retry_staging_dir=root / "retry_staging",
                retry_unmatched=True,
            )

            self.assertTrue((already_ingested / "done.pdf").is_file())
            self.assertTrue((protected / "locked.enc.pdf").is_file())
            self.assertTrue((quarantine / "broken.pdf").is_file())
            self.assertTrue((sidecars / "._sidecar.pdf").is_file())
            self.assertTrue((consume / "retry.pdf").is_file())
            self.assertFalse(any((root / "retry_staging").glob("*")))
            self.assertIn("retriggered", rows[-1]["execution_action"])


if __name__ == "__main__":
    unittest.main()
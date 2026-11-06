#!/usr/bin/env python3
"""Reconcile local Paperless PDFs with hook history and Paperless by checksum.

Key features:
  - Reports consume and pre-consume quarantine PDFs without moving or uploading them.
  - Correlates dated and archived hook logs and the legacy quarantine log.
  - Checks exact SHA-256 matches through the Paperless documents API.
  - Writes a CSV inventory and a grouped console summary.

Usage:
  paperless-reconcile [options]
  paperless-reconcile --no-api --output /tmp/reconciliation.csv

Inputs:
  - PAPERLESS_URL and PAPERLESS_TOKEN from the environment or .env, unless --no-api.
  - CONSUME_DIR from the environment or .env.
  - PAPERLESS_PRECONSUME_LOG_DIR from the environment or .env; defaults to ./export.
  - Optional CLI paths can override consume, export, quarantine, and CSV output.

Outputs:
  - A CSV report with file path, SHA-256, API match state, hook result, disposition,
    and suggested next action.
  - A console summary grouped by location, API result, and disposition.
  - Exit status 0 when a report is generated, 1 for configuration or report errors.

Dependencies:
  - Python 3.10 or later.
  - requests and python-dotenv (already required by this project).

Operational assumptions:
  - Paperless exposes its documents API and supports the checksum__iexact filter.
  - The API token can view the documents relevant to the report; a missing match
    means no exact match visible to that token, not proof of global absence.
  - Hook filenames in the available logs are matched by basename. This is evidence,
    not proof that same-named files have identical contents.
    - The default mode is report-only. File organization and consume retriggering
        require explicit --apply and --retry-unmatched flags.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import os
import re
import shutil
import threading
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

LOGGER = logging.getLogger("paperless_reconcile")
CHUNK_SIZE = 1024 * 1024
DEFAULT_API_WORKERS = 4
REQUEST_TIMEOUT_SECONDS = 10
HOOK_EVENT_PATTERN = re.compile(
    r"^\[(?P<timestamp>[^]]+)\]\s+(?P<event>OK|QUARANTINE):\s+(?P<payload>.*)$",
)
LEGACY_EVENT_PATTERN = re.compile(
    r"^\[(?P<event>CORRUPT|QUARANTINE|OK)\]\s+(?P<payload>.*)$",
)
QUARANTINE_EVENT_PATTERN = re.compile(
    r"^\S+\s+(?P<event>quarantined|corrupt):\s+(?P<payload>.+)$",
    re.IGNORECASE,
)
ENC_FILENAME_PATTERN = re.compile(r"^(?P<base>.+)\.enc(?:\.\d+)?\.pdf$", re.IGNORECASE)
INVALID_FILENAME_PATTERN = re.compile(
    r"^(?P<base>.+)\.invalid(?:\.\d+)?\.pdf$",
    re.IGNORECASE,
)
QUARANTINE_FILENAME_PATTERN = re.compile(
    r"^(?P<base>.+)\.quarantine(?:\.\d+)?\.pdf$",
    re.IGNORECASE,
)
CSV_FIELDS = (
    "filename",
    "location",
    "path",
    "size_bytes",
    "sha256",
    "paperless_status",
    "paperless_match_count",
    "paperless_task_id",
    "paperless_task_status",
    "paperless_task_error",
    "hook_timestamp",
    "hook_event",
    "hook_reason",
    "disposition",
    "recommended_action",
    "execution_action",
)
_THREAD_STATE = threading.local()


@dataclass(frozen=True)
class HookEvent:
    """One parsed result from a pre-consume log."""

    event: str
    reason: str
    timestamp: datetime | None = None
    destination_name: str | None = None


@dataclass(frozen=True)
class PdfFile:
    """A local PDF candidate and its computed checksum, if readable."""

    path: Path
    location: str
    size_bytes: int
    sha256: str = ""
    error: str = ""


def parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO timestamp from hook logs when possible."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_hook_event(line: str) -> tuple[str, HookEvent] | None:
    """Parse current and legacy pre-consume log event formats."""
    match = HOOK_EVENT_PATTERN.match(line.rstrip())
    timestamp: datetime | None = None
    if match:
        timestamp = parse_timestamp(match.group("timestamp"))
        event = match.group("event")
        payload = match.group("payload")
    else:
        match = LEGACY_EVENT_PATTERN.match(line.rstrip())
        if not match:
            return None
        event = match.group("event")
        event = "QUARANTINE" if event == "CORRUPT" else event
        payload = match.group("payload")

    for delimiter in (" — ", " - "):
        if delimiter in payload:
            filename, reason = payload.rsplit(delimiter, 1)
            break
    else:
        return None

    saved_match = re.search(r"\(saved as (.+)\)$", reason)
    destination_name = saved_match.group(1) if saved_match else None
    hook_event = HookEvent(event, reason, timestamp, destination_name)
    return filename, hook_event


def load_hook_events(export_dir: Path) -> tuple[dict[str, HookEvent], dict[str, HookEvent]]:
    """Load source- and destination-name events from current and archived logs."""
    log_paths = [
        export_dir / "preconsume.log",
        *export_dir.glob("preconsume-????-??-??.log"),
        *export_dir.glob("archive/preconsume-legacy-*/*.log"),
    ]
    source_events: dict[str, HookEvent] = {}
    destination_events: dict[str, HookEvent] = {}

    for log_path in sorted(set(log_paths)):
        if not log_path.is_file():
            continue
        try:
            with log_path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    parsed = parse_hook_event(line)
                    if not parsed:
                        continue
                    filename, event = parsed
                    previous = source_events.get(filename)
                    if previous is None or _is_later_event(event, previous):
                        source_events[filename] = event
                    if event.destination_name:
                        previous_destination = destination_events.get(event.destination_name)
                        if previous_destination is None or _is_later_event(
                            event,
                            previous_destination,
                        ):
                            destination_events[event.destination_name] = event
        except OSError as error:
            LOGGER.warning("Could not read hook log %s: %s", log_path, error)

    return source_events, destination_events


def _is_later_event(candidate: HookEvent, previous: HookEvent) -> bool:
    """Prefer the event with the later timestamp, falling back to log order."""
    if candidate.timestamp is None:
        return previous.timestamp is None
    if previous.timestamp is None:
        return True
    candidate_time = candidate.timestamp.timestamp()
    previous_time = previous.timestamp.timestamp()
    return candidate_time >= previous_time


def load_quarantine_log(export_dir: Path) -> dict[str, str]:
    """Load legacy quarantine reasons indexed by source filename."""
    reasons: dict[str, str] = {}
    log_path = export_dir / "quarantine.log"
    if not log_path.is_file():
        return reasons

    try:
        with log_path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                match = QUARANTINE_EVENT_PATTERN.match(line.rstrip())
                if not match:
                    continue
                payload = match.group("payload")
                if " (" not in payload or not payload.endswith(")"):
                    continue
                filename, reason = payload.rsplit(" (", 1)
                reasons[filename] = reason[:-1]
    except OSError as error:
        LOGGER.warning("Could not read quarantine log %s: %s", log_path, error)
    return reasons


def source_filename_for_quarantine(name: str) -> str | None:
    """Recover a likely source basename from a quarantine filename."""
    for pattern in (
        ENC_FILENAME_PATTERN,
        INVALID_FILENAME_PATTERN,
        QUARANTINE_FILENAME_PATTERN,
    ):
        match = pattern.match(name)
        if match:
            return f"{match.group('base')}.pdf"
    return None


def sha256_file(path: Path) -> str:
    """Compute a file's SHA-256 digest without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def collect_pdfs(
    directory: Path,
    location: str,
    *,
    calculate_hashes: bool = True,
) -> list[PdfFile]:
    """Collect top-level PDFs and optionally hash them for API reconciliation."""
    if not directory.is_dir():
        raise NotADirectoryError(f"PDF directory not found: {directory}")

    files: list[PdfFile] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.casefold() != ".pdf":
            continue
        try:
            size_bytes = path.stat().st_size
            checksum = (
                sha256_file(path)
                if calculate_hashes and not path.name.startswith("._")
                else ""
            )
            files.append(PdfFile(path, location, size_bytes, checksum))
        except OSError as error:
            try:
                size_bytes = path.stat().st_size
            except OSError:
                size_bytes = 0
            files.append(PdfFile(path, location, size_bytes, error=str(error)))
    return files


def _thread_session(base_url: str, token: str) -> requests.Session:
    """Return a reusable API session scoped to the current worker thread."""
    session = getattr(_THREAD_STATE, "session", None)
    session_config = getattr(_THREAD_STATE, "session_config", None)
    current_config = (base_url, token)
    if session is None or session_config != current_config:
        session = requests.Session()
        session.headers.update({"Authorization": f"Token {token}"})
        _THREAD_STATE.session = session
        _THREAD_STATE.session_config = current_config
    return session


def _api_count(response: requests.Response) -> int:
    """Return the paginated document count or raise for an unexpected API shape."""
    response.raise_for_status()
    payload: Any = response.json()
    count = payload.get("count") if isinstance(payload, dict) else None
    if not isinstance(count, int):
        raise ValueError("Paperless API response did not contain an integer count")
    return count


def verify_checksum_filter(base_url: str, token: str) -> None:
    """Fail closed if the API ignores the exact checksum filter."""
    session = _thread_session(base_url, token)
    response = session.get(
        f"{base_url}/api/documents/",
        params={"checksum__iexact": "0" * 64, "page_size": 1},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    count = _api_count(response)
    if count != 0:
        raise ValueError(
            "Paperless did not apply checksum__iexact; refusing to infer duplicate status",
        )


def check_paperless_checksum(
    base_url: str,
    token: str,
    checksum: str,
) -> int:
    """Return the number of visible Paperless documents with this exact SHA-256."""
    session = _thread_session(base_url, token)
    response = session.get(
        f"{base_url}/api/documents/",
        params={"checksum__iexact": checksum, "page_size": 1},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _api_count(response)


def load_successful_consume_tasks(
    base_url: str,
    token: str,
) -> dict[str, dict[str, Any]]:
    """Fetch all retained successful consume tasks, following API pagination."""
    session = _thread_session(base_url, token)
    url = f"{base_url}/api/tasks/"
    params: dict[str, str | int] = {
        "task_type": "consume_file",
        "status": "success",
        "page_size": 100,
    }
    tasks_by_filename: dict[str, dict[str, Any]] = {}
    while url:
        response = session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload: Any = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("Paperless task API response did not contain results")
        for task in payload["results"]:
            input_data = task.get("input_data") or {}
            result_data = task.get("result_data") or {}
            filename = input_data.get("filename")
            if not filename or not result_data.get("document_id"):
                continue
            previous = tasks_by_filename.get(filename)
            if previous is None or str(task.get("date_created", "")) > str(
                previous.get("date_created", ""),
            ):
                tasks_by_filename[filename] = task
        url = payload.get("next") or ""
        params = {}
    return tasks_by_filename


def load_latest_consume_tasks(
    base_url: str,
    token: str,
    filenames: set[str],
    workers: int,
) -> dict[str, dict[str, Any]]:
    """Fetch the latest consume task for each current filename."""

    def fetch_filename(filename: str) -> tuple[str, dict[str, Any] | None]:
        session = _thread_session(base_url, token)
        url = f"{base_url}/api/tasks/"
        params: dict[str, str | int] = {
            "name": filename,
            "task_type": "consume_file",
            "page_size": 100,
        }
        latest_task = None
        while url:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload: Any = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("Paperless task API response did not contain results")
            for task in payload["results"]:
                if (task.get("input_data") or {}).get("filename") != filename:
                    continue
                if latest_task is None or str(task.get("date_created", "")) > str(
                    latest_task.get("date_created", ""),
                ):
                    latest_task = task
            url = payload.get("next") or ""
            params = {}
        return filename, latest_task

    if not filenames:
        return {}
    latest_tasks: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_filename, filename) for filename in filenames]
        for future in as_completed(futures):
            filename, task = future.result()
            if task:
                latest_tasks[filename] = task
    return latest_tasks


def matching_successful_task(
    pdf_file: PdfFile,
    successful_tasks: dict[str, dict[str, Any]],
    originals_dir: Path,
) -> dict[str, Any] | None:
    """Match a successful task only when its saved pre-OCR original hashes identically."""
    source_name = (
        source_filename_for_quarantine(pdf_file.path.name)
        if pdf_file.location == "quarantine"
        else pdf_file.path.name
    )
    if not source_name or not pdf_file.sha256:
        return None
    task = successful_tasks.get(source_name)
    original_path = originals_dir / source_name
    if task is None or not original_path.is_file():
        return None
    try:
        return task if sha256_file(original_path) == pdf_file.sha256 else None
    except OSError as error:
        LOGGER.warning("Could not compare saved original %s: %s", original_path, error)
        return None


def lookup_paperless_checksums(
    checksums: set[str],
    base_url: str,
    token: str,
    workers: int,
) -> tuple[dict[str, tuple[str, int | None]], str | None]:
    """Look up unique checksums concurrently and fail closed on API errors."""
    try:
        verify_checksum_filter(base_url, token)
    except (requests.RequestException, ValueError) as error:
        message = f"API checksum filter unavailable: {error}"
        LOGGER.error("%s", message)
        return {checksum: ("error", None) for checksum in checksums}, message

    results: dict[str, tuple[str, int | None]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(check_paperless_checksum, base_url, token, checksum): checksum
            for checksum in checksums
        }
        for future in as_completed(futures):
            checksum = futures[future]
            try:
                count = future.result()
            except (requests.RequestException, ValueError) as error:
                LOGGER.warning("Paperless checksum lookup failed: %s", error)
                results[checksum] = ("error", None)
            else:
                results[checksum] = ("found" if count else "not_found", count)
    return results, None


def _hook_disposition(
    pdf_file: PdfFile,
    source_events: dict[str, HookEvent],
    destination_events: dict[str, HookEvent],
    quarantine_reasons: dict[str, str],
) -> tuple[str, HookEvent | None]:
    """Infer disposition from exact destination and source-basename log matches."""
    if pdf_file.path.name.startswith("._"):
        return "AppleDouble metadata sidecar; not a document", None

    event = destination_events.get(pdf_file.path.name)
    if event:
        return f"Hook quarantine: {event.reason}", event

    if pdf_file.location == "consume":
        event = source_events.get(pdf_file.path.name)
        if event is None:
            return "No matching hook record; pending or outside log window", None
        if event.event == "OK":
            return "Hook passed; still in consume", event
        return f"Hook rejected: {event.reason}", event

    if ENC_FILENAME_PATTERN.match(pdf_file.path.name):
        return "Encrypted PDF", source_events.get(
            source_filename_for_quarantine(pdf_file.path.name) or "",
        )

    source_name = source_filename_for_quarantine(pdf_file.path.name)
    if source_name:
        event = source_events.get(source_name)
        if event:
            if event.event == "OK":
                return "Same basename later hook pass; content unverified", event
            return f"Hook quarantine: {event.reason}", event
        reason = quarantine_reasons.get(source_name)
        if reason:
            return f"Quarantine log: {reason}", None

    return "No matching reason in available logs", None


def _disposition_and_action(
    pdf_file: PdfFile,
    api_state: str,
    hook_disposition: str,
    task_error: str = "",
) -> tuple[str, str]:
    """Combine local evidence with API status into a conservative recommendation."""
    if pdf_file.error:
        return "Unreadable file", "Investigate file access before retrying"
    if pdf_file.path.name.startswith("._"):
        return "AppleDouble metadata sidecar", "Ignore; this is not a document"
    if api_state == "found":
        return "Exact SHA-256 match visible in Paperless", "Do not reingest"
    if api_state == "prior_success":
        return (
            "Successful Paperless task; pre-OCR original hash matches",
            "Archive as already consumed; do not reingest",
        )
    if api_state == "in_progress":
        return "Paperless task is pending or started", "Leave in consume until task completes"
    if api_state == "task_failure":
        return f"Paperless task failed: {task_error or 'see task history'}", "Move to quarantine for review"
    if api_state == "error":
        return hook_disposition, "Review manually; Paperless checksum lookup failed"
    if api_state == "not_checked":
        return hook_disposition, "Run with API checks before considering a retry"
    if "Encrypted PDF" in hook_disposition or "encrypted PDF" in hook_disposition.casefold():
        return hook_disposition, "Decrypt or remove the password before retrying"
    if hook_disposition.startswith("Hook rejected:") or hook_disposition.startswith("Hook quarantine:"):
        return hook_disposition, "Resolve the recorded hook failure before retrying"
    if hook_disposition == "Hook passed; still in consume":
        return hook_disposition, "Inspect Paperless queue and parser logs"
    if hook_disposition.startswith("Same basename later hook pass"):
        return hook_disposition, "Compare content hashes; basename alone is inconclusive"
    if hook_disposition.startswith("No matching"):
        return hook_disposition, "Inspect Paperless task history before retrying"
    return hook_disposition, "Review before retrying"


def build_report_rows(
    files: list[PdfFile],
    api_results: dict[str, tuple[str, int | None]],
    source_events: dict[str, HookEvent],
    destination_events: dict[str, HookEvent],
    quarantine_reasons: dict[str, str],
    successful_tasks: dict[str, dict[str, Any]],
    latest_tasks: dict[str, dict[str, Any]],
    originals_dir: Path,
    api_enabled: bool,
) -> list[dict[str, str | int]]:
    """Create CSV-ready per-file dispositions from local and API evidence."""
    rows: list[dict[str, str | int]] = []
    for pdf_file in files:
        hook_reason, event = _hook_disposition(
            pdf_file,
            source_events,
            destination_events,
            quarantine_reasons,
        )
        api_state, match_count = api_results.get(
            pdf_file.sha256,
            ("not_checked", None),
        ) if pdf_file.sha256 else ("not_checked", None)
        source_name = (
            source_filename_for_quarantine(pdf_file.path.name)
            if pdf_file.location == "quarantine"
            else pdf_file.path.name
        )
        successful_task = None
        task = latest_tasks.get(source_name or "")
        if api_state == "not_found":
            successful_task = matching_successful_task(
                pdf_file,
                successful_tasks,
                originals_dir,
            )
            if successful_task:
                api_state, match_count = "prior_success", 1
            elif task and task.get("status") in {"pending", "started"}:
                api_state = "in_progress"
            elif task and task.get("status") in {"failure", "revoked"}:
                api_state = "task_failure"
        if not api_enabled:
            api_state, match_count = "not_checked", None
        task_error = str(
            (task.get("result_data") or {}).get("error_message", ""),
        ) if task else ""
        disposition, action = _disposition_and_action(
            pdf_file,
            api_state,
            hook_reason,
            task_error,
        )
        rows.append(
            {
                "filename": pdf_file.path.name,
                "location": pdf_file.location,
                "path": str(pdf_file.path),
                "size_bytes": pdf_file.size_bytes,
                "sha256": pdf_file.sha256,
                "paperless_status": api_state,
                "paperless_match_count": "" if match_count is None else match_count,
                "paperless_task_id": task.get("task_id", "") if task else "",
                "paperless_task_status": task.get("status", "") if task else "",
                "paperless_task_error": task_error,
                "hook_timestamp": event.timestamp.isoformat() if event and event.timestamp else "",
                "hook_event": event.event if event else "",
                "hook_reason": hook_reason if not pdf_file.error else pdf_file.error,
                "disposition": disposition,
                "recommended_action": action,
            },
        )
    return rows


def write_csv_report(output_path: Path, rows: list[dict[str, str | int]]) -> None:
    """Write the report as CSV, refusing to overwrite an existing report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"Report already exists: {output_path}")
    with output_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _unique_destination(directory: Path, filename: str) -> Path:
    """Choose a destination without overwriting an existing file."""
    destination = directory / filename
    if not destination.exists():
        return destination
    source_name = Path(filename)
    suffix = 1
    while True:
        destination = directory / f"{source_name.stem}.{suffix}{source_name.suffix}"
        if not destination.exists():
            return destination
        suffix += 1


def move_preserving_file(source: Path, directory: Path, filename: str | None = None) -> Path:
    """Move across volumes safely, verify copied bytes, and never overwrite."""
    directory.mkdir(parents=True, exist_ok=True)
    requested_name = filename or source.name
    while True:
        destination = _unique_destination(directory, requested_name)
        try:
            destination_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            continue

        try:
            with source.open("rb") as source_stream, os.fdopen(
                destination_fd,
                "wb",
            ) as destination_stream:
                shutil.copyfileobj(source_stream, destination_stream, CHUNK_SIZE)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
            if sha256_file(source) != sha256_file(destination):
                raise OSError(f"checksum mismatch while copying {source}")
            try:
                shutil.copystat(source, destination)
            except OSError:
                LOGGER.warning("Could not preserve file metadata for %s", source)
            source.unlink()
            return destination
        except OSError:
            destination.unlink(missing_ok=True)
            raise


def retrigger_consume_file(path: Path, staging_dir: Path) -> Path:
    """Stage outside the watched folder, then create the PDF there exactly once."""
    staged = move_preserving_file(path, staging_dir)
    try:
        return move_preserving_file(staged, path.parent, path.name)
    except OSError:
        if staged.exists() and not path.exists():
            move_preserving_file(staged, path.parent, path.name)
        raise


def apply_safe_actions(
    rows: list[dict[str, str | int]],
    *,
    protected_dir: Path,
    quarantine_dir: Path,
    already_ingested_dir: Path,
    sidecar_dir: Path,
    retry_staging_dir: Path,
    retry_unmatched: bool,
) -> None:
    """Organize consume files using only checksum, task, and hook evidence."""
    for row in rows:
        if row["location"] != "consume":
            continue
        source = Path(str(row["path"]))
        if not source.is_file():
            row["execution_action"] = "not moved; source no longer exists"
            continue

        api_status = str(row["paperless_status"])
        hook_event = str(row["hook_event"])
        hook_reason = str(row["hook_reason"])

        try:
            if source.name.startswith("._"):
                destination = move_preserving_file(source, sidecar_dir)
                row["execution_action"] = f"moved sidecar to {destination}"
            elif api_status in {"found", "prior_success"}:
                destination = move_preserving_file(source, already_ingested_dir)
                row["execution_action"] = f"archived confirmed ingest at {destination}"
            elif api_status == "task_failure":
                failure_text = str(row.get("paperless_task_error", "")).casefold()
                if "encrypted pdf" in failure_text or "password" in failure_text:
                    base = source.stem.removesuffix(".enc")
                    destination = move_preserving_file(
                        source,
                        protected_dir,
                        f"{base}.enc.pdf",
                    )
                    row["execution_action"] = f"moved failed encrypted source to {destination}"
                else:
                    destination = move_preserving_file(source, quarantine_dir)
                    row["execution_action"] = f"moved Paperless task failure to {destination}"
            elif api_status == "not_found" and hook_event == "QUARANTINE":
                if "encrypted pdf" in hook_reason.casefold():
                    base = source.stem.removesuffix(".enc")
                    destination = move_preserving_file(
                        source,
                        protected_dir,
                        f"{base}.enc.pdf",
                    )
                    row["execution_action"] = f"moved encrypted source to {destination}"
                else:
                    destination = move_preserving_file(source, quarantine_dir)
                    row["execution_action"] = f"moved hook failure to {destination}"
            elif retry_unmatched and api_status == "not_found" and hook_event in {"", "OK"}:
                retry_path = retrigger_consume_file(source, retry_staging_dir)
                row["execution_action"] = (
                    f"retriggered once at {retry_path.name}; awaiting Paperless task"
                )
            else:
                row["execution_action"] = "left in consume for manual review"
        except OSError as error:
            row["execution_action"] = f"action failed: {error}"
            LOGGER.error("Could not organize %s: %s", source, error)


def print_summary(rows: list[dict[str, str | int]]) -> None:
    """Print per-location counts grouped by API result and disposition."""
    groups: Counter[tuple[str, str, str]] = Counter()
    for row in rows:
        groups[(str(row["location"]), str(row["paperless_status"]), str(row["disposition"]))] += 1

    print(f'{"Location":12} {"API":12} {"Count":>6}  Disposition')
    print("-" * 100)
    for (location, api_state, disposition), count in sorted(
        groups.items(),
        key=lambda item: (item[0][0], -item[1], item[0][2]),
    ):
        print(f"{location:12} {api_state:12} {count:6}  {disposition}")
    print(f"{'TOTAL':12} {'':12} {len(rows):6}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Report local Paperless PDF dispositions; never moves or ingests files.",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="dotenv file (default: .env)")
    parser.add_argument("--consume-dir", type=Path, help="consume directory (default: CONSUME_DIR)")
    parser.add_argument("--export-dir", type=Path, help="export/log directory (default: PAPERLESS_PRECONSUME_LOG_DIR or ./export)")
    parser.add_argument("--quarantine-dir", type=Path, help="quarantine directory (default: <export-dir>/.quarantine)")
    parser.add_argument("--output", type=Path, help="CSV output path (default: logs/paperless-reconciliation-TIMESTAMP.csv)")
    parser.add_argument("--no-api", action="store_true", help="skip Paperless checksum lookups")
    parser.add_argument("--workers", type=int, default=DEFAULT_API_WORKERS, help="concurrent read-only API lookups (default: 4)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="organize confirmed ingests, hook failures, encrypted files, and sidecars",
    )
    parser.add_argument(
        "--retry-unmatched",
        action="store_true",
        help="with --apply, retrigger unmatched consume PDFs without a hook rejection",
    )
    parser.add_argument(
        "--protected-dir",
        type=Path,
        help="encrypted-file destination (default: PASSWORD_PROTECTED_PDFS_DIR)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Reconcile files, optionally organize clear dispositions, and write a CSV."""
    arguments = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    load_dotenv(arguments.env_file)

    if not 1 <= arguments.workers <= 16:
        LOGGER.error("--workers must be between 1 and 16")
        return 1
    if arguments.retry_unmatched and not arguments.apply:
        LOGGER.error("--retry-unmatched requires --apply")
        return 1

    consume_dir = arguments.consume_dir or (
        Path(os.environ["CONSUME_DIR"]) if os.getenv("CONSUME_DIR") else None
    )
    export_dir = arguments.export_dir or Path(
        os.getenv("PAPERLESS_PRECONSUME_LOG_DIR", "export"),
    )
    quarantine_dir = arguments.quarantine_dir or Path(
        os.getenv("PRECONSUME_QUARANTINE_DIR", str(export_dir / ".quarantine")),
    )
    protected_dir = arguments.protected_dir or Path(
        os.getenv(
            "PASSWORD_PROTECTED_PDFS_DIR",
            str(export_dir / "password_protected_pdfs"),
        ),
    )
    if consume_dir is not None:
        consume_dir = consume_dir.expanduser()
    export_dir = export_dir.expanduser()
    quarantine_dir = quarantine_dir.expanduser()
    protected_dir = protected_dir.expanduser()
    if consume_dir is None:
        LOGGER.error("Set CONSUME_DIR or pass --consume-dir")
        return 1

    try:
        files = collect_pdfs(consume_dir, "consume")
        files.extend(
            collect_pdfs(
                quarantine_dir,
                "quarantine",
                calculate_hashes=not arguments.apply,
            ),
        )
    except (NotADirectoryError, OSError) as error:
        LOGGER.error("Could not inventory PDF directories: %s", error)
        return 1

    source_events, destination_events = load_hook_events(export_dir)
    quarantine_reasons = load_quarantine_log(export_dir)
    api_enabled = not arguments.no_api
    api_results: dict[str, tuple[str, int | None]] = {}
    successful_tasks: dict[str, dict[str, Any]] = {}
    latest_tasks: dict[str, dict[str, Any]] = {}
    task_history_error: str | None = None
    if api_enabled:
        base_url = os.getenv("PAPERLESS_URL", "").rstrip("/")
        token = os.getenv("PAPERLESS_TOKEN", "")
        if not base_url or not token:
            LOGGER.error("Set PAPERLESS_URL and PAPERLESS_TOKEN or pass --no-api")
            return 1
        checksums = {
            pdf_file.sha256
            for pdf_file in files
            if pdf_file.sha256 and not pdf_file.path.name.startswith("._")
        }
        api_results, api_error = lookup_paperless_checksums(
            checksums,
            base_url,
            token,
            arguments.workers,
        )
        if api_error:
            LOGGER.warning("Paperless statuses will be marked as API errors")
        else:
            try:
                successful_tasks = load_successful_consume_tasks(base_url, token)
                consume_names = {
                    pdf_file.path.name
                    for pdf_file in files
                    if pdf_file.location == "consume" and pdf_file.sha256
                }
                latest_tasks = load_latest_consume_tasks(
                    base_url,
                    token,
                    consume_names,
                    arguments.workers,
                )
            except (requests.RequestException, ValueError) as error:
                task_history_error = str(error)
                LOGGER.warning("Could not load successful Paperless tasks: %s", error)

    rows = build_report_rows(
        files,
        api_results,
        source_events,
        destination_events,
        quarantine_reasons,
        successful_tasks,
        latest_tasks,
        export_dir / "originals",
        api_enabled,
    )
    output_path = (arguments.output or Path("logs") / (
        f"paperless-reconciliation-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}.csv"
    )).expanduser()
    if arguments.apply:
        if not api_enabled or api_error or task_history_error:
            LOGGER.error("--apply requires successful checksum and task-history API checks")
            return 1
        if output_path.exists():
            LOGGER.error("Report already exists: %s", output_path)
            return 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(dir=output_path.parent, prefix=".reconcile-check-"):
                pass
        except OSError as error:
            LOGGER.error("Report directory is not writable: %s", error)
            return 1
        sidecar_dir = export_dir / "appledouble_sidecars"
        already_ingested_dir = export_dir / "already_ingested"
        apply_safe_actions(
            rows,
            protected_dir=protected_dir,
            quarantine_dir=quarantine_dir,
            already_ingested_dir=already_ingested_dir,
            sidecar_dir=sidecar_dir,
            retry_staging_dir=export_dir / ".retry_staging",
            retry_unmatched=arguments.retry_unmatched,
        )
    try:
        write_csv_report(output_path, rows)
    except (FileExistsError, OSError) as error:
        LOGGER.error("Could not write CSV report: %s", error)
        return 1

    print_summary(rows)
    print(f"\nCSV report: {output_path.resolve()}")
    if arguments.apply:
        counts: Counter[str] = Counter()
        for row in rows:
            action = str(row.get("execution_action", ""))
            if not action:
                continue
            if action.startswith("action failed:"):
                counts["failed"] += 1
            elif action.startswith("retriggered"):
                counts["retriggered"] += 1
            elif action.startswith("left in consume"):
                counts["left_in_consume"] += 1
            else:
                counts["organized"] += 1
        print(f"Applied actions: {dict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

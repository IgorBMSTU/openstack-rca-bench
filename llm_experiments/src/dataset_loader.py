"""Dataset loader for OpenStack RCA incidents."""

from __future__ import annotations

import gzip
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_INCIDENTS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "rca-framework" / "incidents"
)


@dataclass
class Incident:
    """Structured representation of a single RCA incident."""

    incident_id: str
    metadata: dict[str, Any]
    logs: list[dict[str, Any]]
    ground_truth: dict[str, Any] = field(default_factory=dict)

    def get_logs_reduced(
        self,
        strategy: str = "full",
        window_seconds: int = 120,
        last_n: int = 500,
    ) -> list[dict[str, Any]]:
        """Return logs filtered by the specified reduction strategy.

        Strategies:
            - full: all logs
            - error_only: lines with ERROR/CRITICAL/FATAL severity
            - around_injection: window around injection_time
            - truncated: last N log entries
            - hybrid: ERROR lines + lines around injection_time, deduplicated
        """
        if strategy == "full":
            return list(self.logs)

        if strategy == "error_only":
            return [
                entry
                for entry in self.logs
                if _is_error_line(entry.get("line", ""))
            ]

        if strategy == "truncated":
            return self.logs[-last_n:] if len(self.logs) > last_n else list(self.logs)

        injection_time_str = self.metadata.get("injection", {}).get("injection_time")
        if not injection_time_str:
            logger.warning(
                "Incident %s has no injection_time; falling back to full logs",
                self.incident_id,
            )
            return list(self.logs)

        try:
            injection_time = _parse_iso8601(injection_time_str)
        except ValueError as exc:
            logger.warning(
                "Incident %s has unparseable injection_time (%s): %s",
                self.incident_id,
                injection_time_str,
                exc,
            )
            return list(self.logs)

        window = timedelta(seconds=window_seconds)
        start_time = injection_time - window
        end_time = injection_time + window

        if strategy == "around_injection":
            return [
                entry
                for entry in self.logs
                if _entry_in_window(entry, start_time, end_time)
            ]

        if strategy == "hybrid":
            seen_ids: set[int] = set()
            result: list[dict[str, Any]] = []
            for entry in self.logs:
                eid = id(entry)
                if eid in seen_ids:
                    continue
                if _is_error_line(entry.get("line", "")) or _entry_in_window(
                    entry, start_time, end_time
                ):
                    result.append(entry)
                    seen_ids.add(eid)
            return result

        logger.warning("Unknown strategy '%s'; returning full logs", strategy)
        return list(self.logs)


def _is_error_line(line: str) -> bool:
    """Check if a log line indicates an error severity."""
    line_upper = line.upper()
    return any(level in line_upper for level in ("ERROR", "CRITICAL", "FATAL"))


def _parse_iso8601(value: str) -> datetime:
    """Parse an ISO 8601 timestamp string to a timezone-aware datetime.

    Always returns offset-aware datetime (UTC for naive timestamps).
    """
    value = value.strip()
    # Handle Z suffix
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    # Python 3.11+ supports most formats directly; fallback for older versions
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        # Try common format without explicit timezone
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise


def _entry_in_window(
    entry: dict[str, Any], start: datetime, end: datetime
) -> bool:
    """Check whether a log entry's timestamp falls within the given window."""
    ts_raw = entry.get("timestamp")
    if not ts_raw:
        return False
    try:
        ts = _parse_iso8601(str(ts_raw))
        return start <= ts <= end
    except ValueError:
        return False


def load_incident(incident_dir: Path) -> Incident | None:
    """Load a single incident from its directory.

    Args:
        incident_dir: Path to the incident directory (e.g., .../INC-2026-075).

    Returns:
        An Incident object or None if loading fails.
    """
    incident_id = incident_dir.name
    metadata_path = incident_dir / "metadata.json"
    logs_path = incident_dir / "raw_logs.json.gz"

    if not metadata_path.exists():
        logger.warning("Skipping %s: metadata.json not found", incident_id)
        return None
    if not logs_path.exists():
        logger.warning("Skipping %s: raw_logs.json.gz not found", incident_id)
        return None

    try:
        with metadata_path.open("r", encoding="utf-8") as f:
            metadata = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Skipping %s: failed to read metadata.json: %s", incident_id, exc)
        return None

    try:
        with gzip.open(logs_path, "rt", encoding="utf-8") as f:
            logs_data = json.load(f)
    except (gzip.BadGzipFile, json.JSONDecodeError, OSError) as exc:
        logger.warning("Skipping %s: failed to read raw_logs.json.gz: %s", incident_id, exc)
        return None

    logs: list[dict[str, Any]] = []
    if isinstance(logs_data, dict):
        logs = logs_data.get("logs", [])
    elif isinstance(logs_data, list):
        logs = logs_data
    else:
        logger.warning("Skipping %s: unexpected logs data type", incident_id)
        return None

    injection = metadata.get("injection", {})
    ground_truth = {
        "true_service": injection.get("service"),
        "true_scenario": injection.get("scenario"),
        "true_host": metadata.get("target_host"),
        "true_command": injection.get("command"),
    }

    return Incident(
        incident_id=incident_id,
        metadata=metadata,
        logs=logs,
        ground_truth=ground_truth,
    )


def load_all_incidents(
    incidents_dir: Path | str | None = None,
) -> list[Incident]:
    """Load all valid incidents from the incidents directory.

    Args:
        incidents_dir: Root directory containing incident folders.
            Defaults to the project's rca-framework/incidents path.

    Returns:
        A list of successfully loaded Incident objects.
    """
    if incidents_dir is None:
        incidents_dir = DEFAULT_INCIDENTS_DIR
    else:
        incidents_dir = Path(incidents_dir)

    if not incidents_dir.exists():
        logger.error("Incidents directory does not exist: %s", incidents_dir)
        return []

    incidents: list[Incident] = []
    for entry in sorted(incidents_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not entry.name.startswith("INC-2026-"):
            continue
        incident = load_incident(entry)
        if incident is not None:
            incidents.append(incident)

    logger.info("Loaded %d valid incidents from %s", len(incidents), incidents_dir)
    return incidents

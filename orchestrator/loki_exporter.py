#!/usr/bin/env python3
"""
Loki Chunk Exporter
Pulls logs from Loki API via undercloud SSH and saves to compressed JSON.
Loki is the single source of truth for logs.
"""

import json
import gzip
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from urllib.parse import quote

logger = logging.getLogger(__name__)

LOKI_ENDPOINT = "http://10.197.76.10:3100"
JUMP_HOST = "stack@10.197.75.10"
SSH_KEY = "~/.ssh/standkey"

DEFAULT_QUERY = '{job="systemd-journal"}'


def _run_via_undercloud(cmd: str, timeout: int = 120) -> Tuple[bool, str, str]:
    """Run a shell command on undercloud via jump-host."""
    ssh_key = Path(SSH_KEY).expanduser()
    ssh_cmd = [
        "ssh",
        "-i", str(ssh_key),
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-J", JUMP_HOST,
        "stack@10.197.75.10",
        cmd,
    ]
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
        success = result.returncode == 0
        if not success:
            logger.warning(f"SSH command failed: {result.stderr[:500]}")
        return success, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Timeout"
    except Exception as e:
        return False, "", str(e)


def loki_query_range(
    query: str,
    start: datetime,
    end: datetime,
    limit: int = 5000,
    direction: str = "forward",
) -> List[Dict]:
    """
    Query Loki /loki/api/v1/query_range and return list of log entries.
    Each entry: {"timestamp": "ISO", "line": str, "labels": dict}
    """
    start_ns = int(start.timestamp() * 1_000_000_000)
    end_ns = int(end.timestamp() * 1_000_000_000)

    # Use quote for URL parameter
    query_escaped = quote(query, safe="")
    url = (
        f"{LOKI_ENDPOINT}/loki/api/v1/query_range"
        f"?query={query_escaped}"
        f"&start={start_ns}"
        f"&end={end_ns}"
        f"&limit={limit}"
        f"&direction={direction}"
    )

    cmd = f"curl -s -G '{url}'"
    logger.info(f"Loki query: {query} from {start.isoformat()} to {end.isoformat()}")

    success, stdout, stderr = _run_via_undercloud(cmd, timeout=120)
    if not success:
        logger.error(f"Loki query failed: {stderr}")
        return []
    if "max entries limit per query exceeded" in stdout:
        logger.warning("Loki limit exceeded, retrying with limit=2000")
        return loki_query_range(query, start, end, limit=2000, direction=direction)

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        logger.error(f"Loki returned invalid JSON: {e}. Raw: {stdout[:500]}")
        return []

    if data.get("status") != "success":
        logger.error(f"Loki error response: {data}")
        return []

    results = data.get("data", {}).get("result", [])
    logs = []
    for stream in results:
        labels = stream.get("stream", {})
        values = stream.get("values", [])
        for ts_ns, line in values:
            ts_sec = int(ts_ns) / 1_000_000_000
            ts_dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            logs.append({
                "timestamp": ts_dt.isoformat(),
                "line": line,
                "labels": labels,
            })

    logs.sort(key=lambda x: x["timestamp"])
    logger.info(f"Retrieved {len(logs)} log lines from Loki")
    return logs


def export_logs_to_gzip(
    logs: List[Dict],
    output_path: Path,
    incident_id: str,
    query: str,
    start: datetime,
    end: datetime,
) -> Path:
    """Save logs to a gzipped JSON file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "incident_id": incident_id,
        "source": "loki",
        "query": query,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "count": len(logs),
        "logs": logs,
    }

    with gzip.open(output_path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    size = output_path.stat().st_size
    logger.info(f"Exported {len(logs)} logs to {output_path} ({size} bytes gzipped)")
    return output_path


def extract_logs_for_incident(
    incident_id: str,
    incident_dir: Path,
    injection_start: datetime,
    injection_end: datetime,
    query: str = DEFAULT_QUERY,
    padding_seconds: int = 120,
) -> dict:
    """
    Full pipeline: query Loki for [injection_start - padding, injection_end + padding]
    and save to incident_dir/raw_logs.json.gz
    """
    start = injection_start - timedelta(seconds=padding_seconds)
    end = injection_end + timedelta(seconds=padding_seconds)

    logs = loki_query_range(query, start, end, limit=4000)

    raw_path = incident_dir / "raw_logs.json.gz"
    export_logs_to_gzip(logs, raw_path, incident_id, query, start, end)

    return {
        "raw_log_file": str(raw_path),
        "loki_query": query,
        "time_window": {"start": start.isoformat(), "end": end.isoformat()},
        "count": len(logs),
    }

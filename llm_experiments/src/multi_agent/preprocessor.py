"""Deterministic log pre-processor — extracts structured observability signals.

No LLM calls. Pure Python + math. Emulates what LogQL/PromQL would compute:
  - Per-service ERROR rate before and after injection
  - Delta (percentage change)
  - First anomaly timestamp relative to injection
  - Cascade chain (which services failed, when)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..prompt_builder import KNOWN_COMPONENTS

logger = logging.getLogger(__name__)


@dataclass
class ServiceSignal:
    """Structured signal for one service."""
    service: str
    baseline_error_count: int = 0
    post_error_count: int = 0
    baseline_rate: float = 0.0        # errors per 10s bucket
    post_rate: float = 0.0
    delta_pct: float = 0.0            # percentage change
    first_anomaly_offset_s: float | None = None  # seconds after injection
    total_entries: int = 0
    is_noise: bool = False            # high baseline, low delta → chronic noise


@dataclass
class ObservabilitySignals:
    """Complete structured observability output for one incident."""
    incident_id: str
    injection_time: str
    injection_service: str               # ground truth, HIDDEN from LLM agents
    total_log_entries: int
    pre_window_s: int
    post_window_s: int
    signals: list[ServiceSignal] = field(default_factory=list)
    failed_services: list[str] = field(default_factory=list)  # services with post_rate > baseline
    summary: str = ""


def _parse_iso8601(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise


def _is_error_line(line: str) -> bool:
    line_upper = line.upper()
    return any(level in line_upper for level in ("ERROR", "CRITICAL", "FATAL"))


def _extract_service_from_line(line: str) -> str | None:
    """Extract service name from a log line using keyword matching.

    Strategy: match against the known OpenStack components list.
    Multiple matches → return the most specific (longest) match.
    """
    line_lower = line.lower()
    matches: list[tuple[int, str]] = []
    for svc in KNOWN_COMPONENTS:
        if svc.lower() in line_lower:
            matches.append((len(svc), svc))
    if not matches:
        return None
    matches.sort(key=lambda x: -x[0])  # longest match first
    return matches[0][1]


def extract_observability_signals(
    incident: Any,
    pre_window_s: int = 300,
    post_window_s: int = 300,
    bucket_s: int = 30,
) -> ObservabilitySignals:
    """Extract structured observability signals from raw incident logs.

    Args:
        incident: An Incident dataclass with .logs and .metadata attributes.
        pre_window_s: Seconds before injection_time to use as baseline.
        post_window_s: Seconds after injection_time to analyze.
        bucket_s: Time bucket size for rate calculation.

    Returns:
        ObservabilitySignals with per-service error rates, deltas, and timing.
    """
    metadata = incident.metadata
    injection = metadata.get("injection", {})
    injection_time_str = injection.get("injection_time", "")
    injection_service = injection.get("service", "")
    incident_id = getattr(incident, "incident_id", "unknown")

    if not injection_time_str:
        logger.warning("No injection_time in incident %s", incident_id)
        return ObservabilitySignals(
            incident_id=incident_id,
            injection_time="",
            injection_service=injection_service,
            total_log_entries=0,
            pre_window_s=pre_window_s,
            post_window_s=post_window_s,
        )

    try:
        inj_dt = _parse_iso8601(injection_time_str)
    except ValueError:
        logger.warning("Unparseable injection_time: %s", injection_time_str)
        return ObservabilitySignals(
            incident_id=incident_id,
            injection_time=injection_time_str,
            injection_service=injection_service,
            total_log_entries=0,
            pre_window_s=pre_window_s,
            post_window_s=post_window_s,
        )

    pre_start = inj_dt - timedelta(seconds=pre_window_s)
    post_end = inj_dt + timedelta(seconds=post_window_s)

    # Per-service accumulators
    svc_baseline_errors: dict[str, int] = defaultdict(int)
    svc_post_errors: dict[str, int] = defaultdict(int)
    svc_baseline_total: dict[str, int] = defaultdict(int)
    svc_post_total: dict[str, int] = defaultdict(int)
    svc_first_error_offset: dict[str, float] = {}
    svc_max_offset: dict[str, float] = {}

    logs = getattr(incident, "logs", [])
    total_entries = len(logs)

    for entry in logs:
        ts_str = entry.get("timestamp", "")
        if not ts_str:
            continue
        try:
            ts = _parse_iso8601(str(ts_str))
        except ValueError:
            continue

        line = entry.get("line", "")
        svc = _extract_service_from_line(line)

        if ts < pre_start or ts > post_end:
            continue

        offset_s = (ts - inj_dt).total_seconds()
        is_error = _is_error_line(line)

        if ts < inj_dt:
            # Baseline window
            if svc:
                svc_baseline_total[svc] += 1
                if is_error:
                    svc_baseline_errors[svc] += 1
        else:
            # Post-injection window
            if svc:
                svc_post_total[svc] += 1
                if is_error:
                    svc_post_errors[svc] += 1
                    if svc not in svc_first_error_offset:
                        svc_first_error_offset[svc] = offset_s
                    if offset_s > svc_max_offset.get(svc, -1e9):
                        svc_max_offset[svc] = offset_s

    # Compute num_buckets for rate calculation
    num_buckets = max(post_window_s // bucket_s, 1)

    signals: list[ServiceSignal] = []
    all_affected_services: set[str] = set()
    for svc in sorted(set(svc_baseline_total) | set(svc_post_total)):
        baseline_err = svc_baseline_errors.get(svc, 0)
        post_err = svc_post_errors.get(svc, 0)
        baseline_rate = baseline_err / num_buckets
        post_rate = post_err / num_buckets

        # Delta: percentage change
        if baseline_rate > 0:
            delta_pct = ((post_rate - baseline_rate) / baseline_rate) * 100
        elif post_rate > 0:
            delta_pct = float("inf")
        else:
            delta_pct = 0.0

        # Noise detection: high baseline (>0.1 errors/bucket) + low delta (<200%)
        is_noise = baseline_rate > 0.1 and abs(delta_pct) < 200

        # Determine if service "failed" (significant error increase)
        if post_err > baseline_err and delta_pct > 200:
            all_affected_services.add(svc)

        signal = ServiceSignal(
            service=svc,
            baseline_error_count=baseline_err,
            post_error_count=post_err,
            baseline_rate=round(baseline_rate, 3),
            post_rate=round(post_rate, 3),
            delta_pct=round(delta_pct, 1) if delta_pct != float("inf") else 9999.0,
            first_anomaly_offset_s=round(svc_first_error_offset.get(svc, 0), 1) if svc in svc_first_error_offset else None,
            total_entries=svc_baseline_total.get(svc, 0) + svc_post_total.get(svc, 0),
            is_noise=is_noise,
        )
        signals.append(signal)

    # Sort: highest delta first
    signals.sort(key=lambda s: s.delta_pct, reverse=True)

    # Build summary
    failed = [s for s in signals if s.post_error_count > s.baseline_error_count and s.delta_pct > 100]
    failed_services = [s.service for s in failed]
    summary_parts: list[str] = []
    summary_parts.append(f"Injection at {injection_time_str} on service {injection_service}")
    summary_parts.append(f"Analyzed {total_entries} log entries ({pre_window_s}s baseline, {post_window_s}s post-injection)")
    summary_parts.append(f"Services with significant error increase: {len(failed)}")
    for s in failed[:8]:
        noise_tag = " [NOISE]" if s.is_noise else ""
        summary_parts.append(
            f"  {s.service}: {s.baseline_error_count}→{s.post_error_count} errors "
            f"(+{s.delta_pct}%), first error at +{s.first_anomaly_offset_s}s{noise_tag}"
        )

    return ObservabilitySignals(
        incident_id=incident_id,
        injection_time=injection_time_str,
        injection_service=injection_service,
        total_log_entries=total_entries,
        pre_window_s=pre_window_s,
        post_window_s=post_window_s,
        signals=signals,
        failed_services=failed_services,
        summary="\n".join(summary_parts),
    )

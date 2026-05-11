"""Generate statistics about the OpenStack RCA dataset for the paper."""

from __future__ import annotations

import json
import logging
import re
import statistics
from pathlib import Path
from typing import Any

from llm_experiments.src.dataset_loader import Incident, load_all_incidents
from llm_experiments.src.evaluator import categorize_service

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "results" / "dataset_stats.json"


def _build_host_role_map(incidents: list[Incident]) -> dict[str, str]:
    """Scan pre/post sanity files to map host IPs to their roles."""
    host_roles: dict[str, str] = {}
    pattern = re.compile(r"^Container Status \((\w+) - ([\d.]+)\)$")
    for incident in incidents:
        if incident.metadata.get("target_host") in host_roles:
            continue
        for sanity_file in ("pre_sanity.json", "post_sanity.json"):
            sanity_path = Path(incident.metadata.get("data", {}).get("raw_log_file", "")).parent / sanity_file
            if not sanity_path.exists():
                # Fallback: derive from incidents dir
                sanity_path = (
                    Path(__file__).resolve().parents[2]
                    / "rca-framework"
                    / "incidents"
                    / incident.incident_id
                    / sanity_file
                )
            if not sanity_path.exists():
                continue
            try:
                with sanity_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            for result in data.get("results", []):
                name = result.get("name", "")
                match = pattern.match(name)
                if match:
                    role, ip = match.groups()
                    host_roles[ip] = role
            if host_roles:
                break
        if len(host_roles) >= 6:  # We know there are 6 hosts total
            break
    return host_roles


def _determine_injection_type(incident: Incident) -> str:
    """Map the raw injection scenario to a canonical injection type."""
    raw = incident.metadata.get("injection", {}).get("scenario", "unknown")
    mapping = {
        "service-stop": "service-stop",
        "process-kill": "process-kill",
        "config-corruption": "config-corruption",
        "port-block": "network-partition",
    }
    return mapping.get(raw, raw)


def _classify_host(host: str, host_roles: dict[str, str]) -> str:
    """Return the role of a host (controller, compute, storage, or unknown)."""
    return host_roles.get(host, "unknown")


def compute_dataset_stats(incidents: list[Incident]) -> dict[str, Any]:
    """Compute comprehensive statistics over the loaded incidents."""
    total = len(incidents)
    if total == 0:
        return {"total_incidents": 0}

    by_scenario: dict[str, int] = {}
    by_service: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_injection_type: dict[str, int] = {}

    log_line_counts: list[int] = []
    durations: list[int] = []
    match_rates: list[float] = []

    host_roles = _build_host_role_map(incidents)
    unique_hosts: set[str] = set()
    controller_incidents = 0
    compute_incidents = 0
    storage_incidents = 0

    for incident in incidents:
        meta = incident.metadata
        injection = meta.get("injection", {})
        validation = meta.get("validation", {})

        scenario = meta.get("scenario", "unknown")
        service = injection.get("service", "unknown")
        category = categorize_service(service)
        inj_type = _determine_injection_type(incident)

        by_scenario[scenario] = by_scenario.get(scenario, 0) + 1
        by_service[service] = by_service.get(service, 0) + 1
        by_category[category] = by_category.get(category, 0) + 1
        by_injection_type[inj_type] = by_injection_type.get(inj_type, 0) + 1

        log_count = len(incident.logs)
        log_line_counts.append(log_count)

        duration = meta.get("duration_seconds")
        if duration is not None:
            durations.append(duration)

        match_rate = validation.get("match_rate")
        if match_rate is not None:
            match_rates.append(match_rate)

        host = meta.get("target_host")
        if host:
            unique_hosts.add(host)
            role = _classify_host(host, host_roles)
            if role == "controller":
                controller_incidents += 1
            elif role == "compute":
                compute_incidents += 1
            elif role == "storage":
                storage_incidents += 1

    log_stats = {
        "mean_lines": round(statistics.mean(log_line_counts), 2),
        "median_lines": round(statistics.median(log_line_counts), 2),
        "min_lines": min(log_line_counts),
        "max_lines": max(log_line_counts),
        "total_lines": sum(log_line_counts),
    }

    duration_stats: dict[str, Any] = {}
    if durations:
        duration_stats = {
            "mean_seconds": round(statistics.mean(durations), 2),
            "min_seconds": min(durations),
            "max_seconds": max(durations),
        }

    validation_stats = {
        "mean_match_rate": round(statistics.mean(match_rates), 4) if match_rates else 0.0,
        "incidents_with_100_percent_match": sum(1 for r in match_rates if r >= 1.0),
    }

    return {
        "total_incidents": total,
        "by_scenario": dict(sorted(by_scenario.items())),
        "by_service": dict(sorted(by_service.items())),
        "by_category": dict(sorted(by_category.items())),
        "by_injection_type": dict(sorted(by_injection_type.items())),
        "log_stats": log_stats,
        "duration_stats": duration_stats,
        "host_stats": {
            "unique_hosts": len(unique_hosts),
            "controller_incidents": controller_incidents,
            "compute_incidents": compute_incidents,
            "storage_incidents": storage_incidents,
        },
        "validation_stats": validation_stats,
    }


def print_summary(stats: dict[str, Any]) -> None:
    """Print a human-readable summary to stdout."""
    print("=" * 50)
    print("OpenStack RCA Dataset Statistics")
    print("=" * 50)
    print(f"Total Incidents: {stats['total_incidents']}")
    print()

    print("By Scenario:")
    for scenario, count in stats.get("by_scenario", {}).items():
        print(f"  {scenario}: {count}")
    print()

    print("By Service:")
    for service, count in stats.get("by_service", {}).items():
        print(f"  {service}: {count}")
    print()

    print("By Category:")
    for category, count in stats.get("by_category", {}).items():
        print(f"  {category}: {count}")
    print()

    print("By Injection Type:")
    for inj_type, count in stats.get("by_injection_type", {}).items():
        print(f"  {inj_type}: {count}")
    print()

    log_stats = stats.get("log_stats", {})
    print("Log Statistics:")
    print(f"  Mean lines:    {log_stats.get('mean_lines')}")
    print(f"  Median lines:  {log_stats.get('median_lines')}")
    print(f"  Min lines:     {log_stats.get('min_lines')}")
    print(f"  Max lines:     {log_stats.get('max_lines')}")
    print(f"  Total lines:   {log_stats.get('total_lines')}")
    print()

    duration_stats = stats.get("duration_stats", {})
    print("Duration Statistics:")
    print(f"  Mean seconds:  {duration_stats.get('mean_seconds')}")
    print(f"  Min seconds:   {duration_stats.get('min_seconds')}")
    print(f"  Max seconds:   {duration_stats.get('max_seconds')}")
    print()

    host_stats = stats.get("host_stats", {})
    print("Host Statistics:")
    print(f"  Unique hosts:         {host_stats.get('unique_hosts')}")
    print(f"  Controller incidents: {host_stats.get('controller_incidents')}")
    print(f"  Compute incidents:    {host_stats.get('compute_incidents')}")
    print(f"  Storage incidents:    {host_stats.get('storage_incidents')}")
    print()

    validation_stats = stats.get("validation_stats", {})
    print("Validation Statistics:")
    print(f"  Mean match rate:            {validation_stats.get('mean_match_rate')}")
    print(f"  Incidents with 100% match:  {validation_stats.get('incidents_with_100_percent_match')}")
    print("=" * 50)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    incidents = load_all_incidents()
    if not incidents:
        logger.error("No incidents loaded. Exiting.")
        return

    stats = compute_dataset_stats(incidents)

    output_path = DEFAULT_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    logger.info("Wrote dataset statistics to %s", output_path)

    print_summary(stats)


if __name__ == "__main__":
    main()

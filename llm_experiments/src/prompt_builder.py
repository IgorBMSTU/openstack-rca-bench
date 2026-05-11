"""Build prompts for LLM RCA analysis with different strategies."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .dataset_loader import Incident

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system_rca.txt"

KNOWN_COMPONENTS = [
    # Compute
    "nova-api", "nova-compute", "nova-conductor", "nova-scheduler",
    "nova-vnc-proxy", "nova-novncproxy", "nova-metadata",
    # Network
    "neutron-server", "neutron-api", "neutron-openvswitch-agent",
    "neutron-l3-agent", "neutron-dhcp-agent", "neutron-dhcp",
    "neutron-metadata-agent",
    # OVN
    "ovn-controller", "ovn-northd", "ovn-north-db", "ovn-south-db",
    "ovn-metadata-agent",
    # Storage
    "cinder-api", "cinder-volume", "cinder-scheduler", "cinder-backup",
    "ceph-mon", "ceph-mgr", "ceph-osd", "ceph-rgw",
    # Image
    "glance-api", "glance-api-internal", "glance-registry",
    # Identity
    "keystone",
    # Orchestration
    "heat-api", "heat-api-cfn", "heat-engine",
    "placement-api",
    # Bare metal
    "ironic-api", "ironic-conductor", "ironic-neutron-agent",
    # Messaging / Database / Cache
    "rabbitmq", "redis",
    "mysql", "mariadb", "galera",
    # Infrastructure
    "haproxy", "memcached", "keepalived",
    "iscsid",
    "grafana", "prometheus",
    # Dashboard
    "skyline-apiserver", "skyline-console",
    # Object storage
    "swift-proxy", "swift-account", "swift-container", "swift-object",
]

VALID_STRATEGIES = {"zero_shot", "with_context", "chain_of_thought"}


def _read_system_prompt() -> str:
    """Read the system prompt from the prompts directory."""
    if not _SYSTEM_PROMPT_PATH.exists():
        logger.warning("System prompt file not found: %s", _SYSTEM_PROMPT_PATH)
        return (
            "You are an expert OpenStack SRE. Analyze the provided logs "
            "and output ONLY a valid JSON object with root cause fields."
        )
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def _get_node_type(entry: dict[str, Any]) -> str:
    """Extract node_type from log entry (direct or nested in labels)."""
    if "node_type" in entry:
        return entry["node_type"]
    labels = entry.get("labels", {})
    if isinstance(labels, dict):
        return labels.get("node_type", "")
    return ""


def _format_logs(logs: list[dict[str, Any]]) -> str:
    """Format log entries as '[timestamp] [node_type] line'."""
    lines: list[str] = []
    for entry in logs:
        ts = entry.get("timestamp", "")
        node = _get_node_type(entry)
        line = entry.get("line", "")
        lines.append(f"[{ts}] [{node}] {line}")
    return "\n".join(lines)


def _truncate_logs(log_text: str, max_chars: int) -> str:
    """Truncate log text from the end, keeping the most recent logs."""
    if len(log_text) <= max_chars:
        return log_text
    truncated = log_text[-max_chars:]
    # Drop the first partial line to avoid a broken entry
    newline_pos = truncated.find("\n")
    if newline_pos != -1:
        truncated = truncated[newline_pos + 1 :]
    return truncated


def build_prompt(
    incident: Incident,
    strategy: str,
    max_log_chars: int = 60000,
) -> tuple[str, str]:
    """Build system and user prompts for an incident.

    Args:
        incident: The incident to build the prompt for.
        strategy: Prompt strategy — one of 'zero_shot', 'with_context', 'chain_of_thought'.
        max_log_chars: Maximum characters for the formatted log section.

    Returns:
        A tuple of (system_prompt, user_prompt).
    """
    if strategy not in VALID_STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{strategy}'. Choose from {VALID_STRATEGIES}."
        )

    system_prompt = _read_system_prompt()

    incident_id = incident.incident_id
    scenario = incident.metadata.get("injection", {}).get("scenario", "N/A")
    injection_time = incident.metadata.get("injection", {}).get("injection_time", "N/A")

    log_text = _format_logs(incident.logs)
    log_text = _truncate_logs(log_text, max_log_chars)

    if not log_text.strip():
        log_text = "[No logs available for this incident.]"

    parts: list[str] = []

    parts.append(
        f"Incident ID: {incident_id}\n"
        f"Scenario: {scenario}\n"
    )

    parts.append(
        f"Below are systemd-journal logs from an OpenStack cluster incident. "
        f"The incident occurred around {injection_time}."
    )

    if strategy == "with_context":
        components_block = "\n".join(f"  - {svc}" for svc in KNOWN_COMPONENTS)
        parts.append(
            f"\nKnown Components in the cluster:\n{components_block}\n"
            f"Injection time window: {injection_time} (±2 minutes)"
        )

    parts.append(f"\n--- Logs ---\n{log_text}\n--- End of Logs ---")

    if strategy == "chain_of_thought":
        parts.append(
            "\nThink step by step. First identify anomalous patterns, then trace to root cause."
        )

    parts.append(
        "\nRemember: output ONLY a valid JSON object with the required fields and no extra text."
    )

    user_prompt = "\n".join(parts)
    return system_prompt, user_prompt

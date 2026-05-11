"""Evaluate LLM predictions against ground truth for OpenStack RCA."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .dataset_loader import Incident

logger = logging.getLogger(__name__)

SERVICE_ALIASES: dict[str, str] = {
    "neutron_api": "neutron_server",
    "neutron-server": "neutron_server",
    "neutron_dhcp": "neutron_dhcp_agent",
    "nova-compute": "nova_compute",
    "nova_novncproxy": "nova_vnc_proxy",
    "nova-novncproxy": "nova_vnc_proxy",
    "nova-vnc-proxy": "nova_vnc_proxy",
    "nova_vncproxy": "nova_vnc_proxy",
    "ceph-osd": "ceph_osd",
    "glance-api-internal": "glance_api",
    "rabbitmq_bundle": "rabbitmq",
    "rabbitmq-bundle": "rabbitmq",
    "ovn-north-db": "ovn_northd",
    "ovn-south-db": "ovn_southdb",
}


@dataclass
class EvaluationResult:
    """Result of evaluating a single LLM prediction."""

    incident_id: str
    predicted_service: str | None
    true_service: str | None
    predicted_host: str | None
    true_host: str | None
    predicted_fault_type: str | None
    correct_service: bool
    correct_host: bool
    match_score: float


def normalize_service(name: str) -> str:
    """Normalize a service name for comparison.

    - Lowercase
    - Replace hyphens with underscores
    - Strip ``.service`` suffix
    - Map common aliases to a canonical form
    """
    normalized = name.lower().replace("-", "_")
    if normalized.endswith(".service"):
        normalized = normalized[:-8]
    return SERVICE_ALIASES.get(normalized, normalized)


def match_service(predicted: str, true: str) -> bool:
    """Fuzzy-match two service names.

    Returns True if:
    - They match exactly after normalization.
    - One normalized name contains the other and their length difference is <= 5.
    - Both contain ``ceph_osd``.
    - Both contain ``neutron`` and one contains ``server`` or ``api``.
    - Both contain ``nova`` and one contains ``compute``.
    """
    p = normalize_service(predicted)
    t = normalize_service(true)

    if p == t:
        return True

    if (p in t or t in p) and abs(len(p) - len(t)) <= 8:
        return True

    if "ceph_osd" in p and "ceph_osd" in t:
        return True

    if "neutron" in p and "neutron" in t:
        if "server" in p or "api" in p or "server" in t or "api" in t:
            return True

    if "nova" in p and "nova" in t:
        # Both must contain the same sub-service (compute/api/scheduler/conductor)
        p_sub = p.split("nova_", 1)[1] if "nova_" in p else ""
        t_sub = t.split("nova_", 1)[1] if "nova_" in t else ""
        if p_sub and t_sub and p_sub == t_sub:
            return True

    return False


def _match_host(predicted: str | None, true: str | None) -> bool:
    """Check whether predicted host matches true host.

    True when:
    - Exact string match.
    - One string is a substring of the other.

    Returns False if either value is missing.
    """
    if predicted is None or true is None:
        return False
    if predicted == true:
        return True
    return predicted in true or true in predicted


def evaluate_prediction(incident: Incident, prediction: dict[str, Any]) -> EvaluationResult:
    """Evaluate a single LLM prediction against an incident's ground truth.

    Args:
        incident: The loaded incident with ground-truth metadata.
        prediction: Dictionary from the LLM containing keys such as
            ``root_cause_service``, ``affected_host``, and ``fault_type``.

    Returns:
        An ``EvaluationResult`` with correctness flags and a match score.
    """
    gt = incident.ground_truth

    predicted_service = prediction.get("root_cause_service")
    true_service = gt.get("true_service")

    predicted_host = prediction.get("affected_host")
    true_host = gt.get("true_host")

    predicted_fault_type = prediction.get("fault_type")
    true_fault_type = gt.get("true_scenario")

    service_correct = False
    if predicted_service is not None and true_service is not None:
        service_correct = match_service(str(predicted_service), str(true_service))

    host_correct = _match_host(predicted_host, true_host)

    if service_correct:
        match_score = 1.0
    elif host_correct:
        match_score = 0.5
    else:
        match_score = 0.0

    return EvaluationResult(
        incident_id=incident.incident_id,
        predicted_service=predicted_service,
        true_service=true_service,
        predicted_host=predicted_host,
        true_host=true_host,
        predicted_fault_type=predicted_fault_type,
        correct_service=service_correct,
        correct_host=host_correct,
        match_score=match_score,
    )


def categorize_service(service: str) -> str:
    """Map a service name to a high-level category.

    Categories:
        - ``compute``
        - ``storage``
        - ``network``
        - ``api``
        - ``backend``
        - ``database``
        - ``message_queue``
        - ``other`` (fallback)
    """
    s = service.lower()

    if "neutron" in s or "ovn" in s:
        return "network"

    if "ceph" in s:
        return "storage"

    if any(db in s for db in ("mysql", "mariadb", "galera")):
        return "database"

    if any(mq in s for mq in ("rabbitmq", "redis")):
        return "message_queue"

    for svc in ("nova", "cinder", "glance", "keystone", "heat", "placement", "ironic", "swift"):
        if svc in s:
            if "-api" in s or "_api" in s:
                return "api"
            return "backend"

    return "other"


def compute_metrics(results: list[EvaluationResult]) -> dict[str, Any]:
    """Aggregate metrics over a list of evaluation results.

    Returns:
        Dictionary with top-level accuracy metrics, category-level accuracy,
        mean match score, and per-incident detail records.
    """
    if not results:
        return {
            "top1_accuracy": 0.0,
            "top1_fuzzy_accuracy": 0.0,
            "host_accuracy": 0.0,
            "category_accuracy": {},
            "mean_match_score": 0.0,
            "per_incident": [],
        }

    total = len(results)
    correct_service_count = sum(1 for r in results if r.correct_service)
    fuzzy_count = sum(
        1
        for r in results
        if r.predicted_service is not None
        and r.true_service is not None
        and match_service(r.predicted_service, r.true_service)
    )
    correct_host_count = sum(1 for r in results if r.correct_host)
    total_match_score = sum(r.match_score for r in results)

    # Category-level accuracy
    category_stats: dict[str, dict[str, int]] = {}
    for r in results:
        cat = categorize_service(r.true_service or "")
        if cat not in category_stats:
            category_stats[cat] = {"correct": 0, "total": 0}
        category_stats[cat]["total"] += 1
        if r.correct_service:
            category_stats[cat]["correct"] += 1

    category_accuracy = {
        cat: stats["correct"] / stats["total"]
        for cat, stats in category_stats.items()
        if stats["total"] > 0
    }

    per_incident = [
        {
            "incident_id": r.incident_id,
            "predicted_service": r.predicted_service,
            "true_service": r.true_service,
            "predicted_host": r.predicted_host,
            "true_host": r.true_host,
            "predicted_fault_type": r.predicted_fault_type,
            "correct_service": r.correct_service,
            "correct_host": r.correct_host,
            "match_score": r.match_score,
        }
        for r in results
    ]

    return {
        "top1_accuracy": correct_service_count / total,
        "top1_fuzzy_accuracy": fuzzy_count / total,
        "host_accuracy": correct_host_count / total,
        "category_accuracy": category_accuracy,
        "mean_match_score": total_match_score / total,
        "per_incident": per_incident,
    }

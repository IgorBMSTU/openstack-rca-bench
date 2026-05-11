"""
OpenStack RCA Dataset - Baseline Experiments
Module: baselines
Purpose: LLM-based and rule-based RCA evaluation on collected incident data

<MODULE_CONTRACT>
Name: baselines
Purpose: Evaluate RCA methods on collected incident data
Inputs: Incident data with logs and ground truth
Outputs: Accuracy metrics and comparison results
Dependencies: openai, re, json
</MODULE_CONTRACT>
"""
import gzip


import os
import re
import json
import logging
from typing import Dict, List, Optional
from pathlib import Path
from dataclasses import dataclass
from collections import Counter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Result of RCA analysis."""

    root_cause: str
    confidence: float
    evidence: List[str]
    method: str
    correct: Optional[bool] = None


class RuleBasedRCA:
    """
    Rule-based RCA using regex patterns.

    <FUNCTION_CONTRACT>
    Name: RuleBasedRCA
    Purpose: Detect root causes using predefined patterns
    Inputs: Log text
    Outputs: Analysis result with matched patterns
    </FUNCTION_CONTRACT>
    """

    def __init__(self):
        self.patterns = {
            "neutron-server": [
                r"neutron-server.*stopped",
                r"neutron-server.*failed",
                r"Neutron server.*error",
                r"neutron.*service.*down",
                r"neutron_api.*stopped",
                r"neutron_api.*exited",
                r"Stopped neutron_api container",
                r"tripleo_neutron_api\.service: Deactivated successfully",
            ],
            "neutron-ovs-agent": [
                r"neutron-ovs-agent.*stopped",
                r"neutron_ovs_agent.*stopped",
                r"setup_ovs_manager.*stopped",
                r"neutron.*ovs.*error",
            ],
            "ironic-api": [
                r"ironic-api.*stopped",
                r"ironic-api.*failed",
                r"ironic.*api.*error",
                r"ironic.*service.*down",
                r"ironic_neutron_agent.*stopped",
            ],
            "mysql": [
                r"mysql.*stopped",
                r"mariadb.*stopped",
                r"mysql.*failed",
                r"database.*connection.*refused",
                r"galera-bundle.*stopped",
            ],
            "rabbitmq": [
                r"rabbitmq.*stopped",
                r"rabbitmq.*failed",
                r"rabbitmq.*error",
                r"amqp.*connection.*refused",
                r"rabbitmq-bundle.*stopped",
            ],
            "redis": [
                r"redis.*stopped",
                r"redis.*failed",
                r"redis.*error",
                r"redis-bundle.*stopped",
            ],
            "keystone": [
                r"keystone.*stopped",
                r"keystone.*failed",
                r"keystone.*error",
                r"Stopped keystone container",
                r"tripleo_keystone\.service: Deactivated successfully",
            ],
            "nova-api": [
                r"nova-api.*stopped",
                r"nova_api.*stopped",
                r"nova.*api.*error",
                r"Stopped nova_api container",
                r"tripleo_nova_api\.service: Deactivated successfully",
            ],
            "nova-conductor": [
                r"nova-conductor.*stopped",
                r"nova_conductor.*stopped",
                r"nova.*conductor.*error",
                r"Stopped nova_conductor container",
                r"tripleo_nova_conductor\.service: Deactivated successfully",
            ],
            "nova-scheduler": [
                r"nova-scheduler.*stopped",
                r"nova_scheduler.*stopped",
                r"Stopped nova_scheduler container",
                r"tripleo_nova_scheduler\.service: Deactivated successfully",
            ],
            "cinder-api": [
                r"cinder-api.*stopped",
                r"cinder_api.*stopped",
                r"cinder.*api.*error",
                r"Stopped cinder_api container",
                r"tripleo_cinder_api\.service: Deactivated successfully",
            ],
            "glance-api": [
                r"glance-api.*stopped",
                r"glance_api.*stopped",
                r"glance.*api.*error",
                r"Stopped glance_api container",
                r"tripleo_glance_api\.service: Deactivated successfully",
            ],
            "heat-api": [
                r"heat-api.*stopped",
                r"heat_api.*stopped",
                r"heat.*api.*error",
                r"Stopped heat_api container",
                r"tripleo_heat_api\.service: Deactivated successfully",
            ],
            "heat-api-cfn": [
                r"heat-api-cfn.*stopped",
                r"heat_api_cfn.*stopped",
                r"Stopped heat_api_cfn container",
                r"tripleo_heat_api_cfn\.service: Deactivated successfully",
            ],
            "placement-api": [
                r"placement-api.*stopped",
                r"placement_api.*stopped",
                r"Stopped placement_api container",
                r"tripleo_placement_api\.service: Deactivated successfully",
            ],
            "ovn-controller": [
                r"ovn-controller.*stopped",
                r"ovn_controller.*stopped",
                r"Stopped ovn_controller container",
                r"tripleo_ovn_controller\.service: Deactivated successfully",
            ],
            "ovn-metadata-agent": [
                r"ovn-metadata-agent.*stopped",
                r"ovn_metadata_agent.*stopped",
                r"Stopped ovn_metadata_agent container",
                r"tripleo_ovn_metadata_agent\.service: Deactivated successfully",
            ],
            "neutron-dhcp": [
                r"neutron-dhcp.*stopped",
                r"neutron_dhcp.*stopped",
                r"Stopped neutron_dhcp container",
                r"tripleo_neutron_dhcp\.service: Deactivated successfully",
            ],
            "ceph-osd": [
                r"ceph.*osd.*out",
                r"ceph.*osd.*down",
                r"osd\.\d+.*out",
                r"PG.*degraded",
                r"ceph-osd@\d+.*stopped",
                r"Stopped ceph-osd@\d+",
            ],
            "network-partition": [
                r"connection.*timeout",
                r"no.*route.*to.*host",
                r"network.*unreachable",
            ],
        }

    def analyze(self, logs: str) -> AnalysisResult:
        matches = []
        for component, patterns in self.patterns.items():
            for pattern in patterns:
                if re.search(pattern, logs, re.IGNORECASE):
                    matches.append({"component": component, "pattern": pattern})

        if not matches:
            return AnalysisResult(
                root_cause="unknown", confidence=0.1, evidence=[], method="rule-based"
            )

        component_counts = Counter([m["component"] for m in matches])
        most_common = component_counts.most_common(1)[0]
        confidence = min(0.9, 0.5 + 0.1 * most_common[1])
        evidence = [m["pattern"] for m in matches if m["component"] == most_common[0]]

        return AnalysisResult(
            root_cause=most_common[0],
            confidence=confidence,
            evidence=evidence[:3],
            method="rule-based",
        )


class LLMRCA:
    """
    LLM-based RCA using OpenAI-compatible API.
    Falls back to heuristic if no API key is available.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.model = model or os.getenv("OPENAI_MODEL", "qwen3-coder-30b-a3b")
        self.client = None
        if self.api_key:
            try:
                from openai import OpenAI
                kwargs = {"api_key": self.api_key}
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                self.client = OpenAI(**kwargs)
            except ImportError:
                logger.error("OpenAI package not installed")
        else:
            logger.warning("OpenAI API key not provided - LLM baseline will use fallback")

    def analyze(self, logs: str, max_tokens: int = 500) -> AnalysisResult:
        if not self.client:
            # Fallback: use simple keyword heuristic
            return self._fallback_analyze(logs)

        logs_truncated = logs[:6000] if len(logs) > 6000 else logs
        prompt = f"""Analyze these OpenStack logs and identify the root cause of the failure.

Logs:
{logs_truncated}

Provide your analysis in this exact JSON format:
{{
    "root_cause": "name of the failed component (e.g., neutron-server, mysql, ceph-osd)",
    "confidence": 0.0 to 1.0,
    "evidence": ["list", "of", "key", "log", "lines"],
    "reasoning": "brief explanation"
}}

Focus on the primary failure, not symptoms."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert OpenStack administrator. Respond only with valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.1,
            )
            content = response.choices[0].message.content
            # Strip markdown code fences if present
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            try:
                result = json.loads(content)
                return AnalysisResult(
                    root_cause=result.get("root_cause", "unknown"),
                    confidence=result.get("confidence", 0.5),
                    evidence=result.get("evidence", []),
                    method="llm",
                )
            except json.JSONDecodeError:
                logger.warning("Failed to parse LLM JSON response")
                return AnalysisResult(
                    root_cause="unknown",
                    confidence=0.3,
                    evidence=[content[:200]],
                    method="llm",
                )
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return self._fallback_analyze(logs)

    def _fallback_analyze(self, logs: str) -> AnalysisResult:
        """Simple heuristic fallback when LLM is unavailable."""
        keywords = {
            "neutron-server": ["neutron", "wsgi"],
            "neutron-ovs-agent": ["ovs", "agent"],
            "mysql": ["mysql", "mariadb", "galera"],
            "rabbitmq": ["rabbitmq", "amqp"],
            "redis": ["redis"],
            "keystone": ["keystone"],
            "nova-api": ["nova-api", "nova_api"],
            "nova-conductor": ["nova-conductor", "nova_conductor"],
            "cinder-api": ["cinder-api", "cinder_api"],
            "glance-api": ["glance-api", "glance_api"],
            "heat-api": ["heat-api", "heat_api"],
            "ironic-api": ["ironic-api", "ironic_api", "ironic_neutron"],
            "ceph-osd": ["ceph", "osd", "pg"],
        }
        best_component = "unknown"
        best_score = 0
        logs_lower = logs.lower()
        for component, words in keywords.items():
            score = sum(1 for w in words if w in logs_lower)
            if score > best_score:
                best_score = score
                best_component = component

        confidence = min(0.7, 0.3 + 0.1 * best_score)
        return AnalysisResult(
            root_cause=best_component,
            confidence=confidence,
            evidence=[f"Keyword match: {best_score}"],
            method="llm-fallback",
        )


def extract_logs_text(data) -> str:
    """Recursively extract all string values from log JSON structure."""
    texts = []
    if isinstance(data, dict):
        for v in data.values():
            texts.append(extract_logs_text(v))
    elif isinstance(data, list):
        for item in data:
            texts.append(extract_logs_text(item))
    elif isinstance(data, str):
        texts.append(data)
    return "\n".join(t for t in texts if t)


class Evaluator:
    """
    Evaluates RCA methods against ground truth.
    """

    def __init__(self, incidents_dir: str = "./rca-framework/incidents"):
        self.incidents_dir = Path(incidents_dir)
        self.rule_based = RuleBasedRCA()
        self.llm = LLMRCA()

    def load_incident_logs(self, incident_id: str) -> str:
        """Load logs from incident directory (supports raw_logs.json.gz and logs_during.json)."""
        incident_dir = self.incidents_dir / incident_id

        # Prefer gzipped raw_logs (repo format), fall back to logs_during.json
        gz_file = incident_dir / "raw_logs.json.gz"
        if gz_file.exists():
            try:
                with gzip.open(gz_file, "rt", encoding="utf-8") as f:
                    data = json.load(f)
                # raw_logs.json.gz has structure {"logs": [...]} or is a bare list
                if isinstance(data, dict):
                    logs_data = data.get("logs", data)
                else:
                    logs_data = data
                return extract_logs_text(logs_data)
            except Exception as e:
                logger.warning("%s: failed to read raw_logs.json.gz: %s", incident_id, e)

        logs_file = incident_dir / "logs_during.json"
        if logs_file.exists():
            with open(logs_file, "r") as f:
                data = json.load(f)
            return extract_logs_text(data)

        return ""

    def load_ground_truth(self, incident_id: str) -> Dict:
        """Load ground truth from metadata.json (repo format) or ground_truth.json."""
        for fn in ("metadata.json", "ground_truth.json"):
            gt_file = self.incidents_dir / incident_id / fn
            if gt_file.exists():
                with open(gt_file, "r") as f:
                    return json.load(f)
        return {}

    def get_true_cause(self, ground_truth: Dict) -> str:
        injection = ground_truth.get("injection", {})
        scenario = injection.get("scenario", "")
        if scenario == "ceph-osd-stop":
            osd_id = injection.get("osd_id", "unknown")
            return f"ceph-osd-{osd_id}"
        service = injection.get("service", "")
        if service:
            return service
        # Fallback for local_incident_creator style
        gt_scenario = ground_truth.get("scenario", "")
        if gt_scenario:
            return gt_scenario.replace("-stop", "").replace("-down", "")
        return "unknown"

    def _check_correct(self, predicted: str, true: str) -> bool:
        predicted_lower = predicted.lower().replace("-", "_").replace(".service", "")
        true_lower = true.lower().replace("-", "_").replace(".service", "")
        if predicted_lower == true_lower:
            return True
        # Allow partial match only if lengths are close (avoids prefix confusion like heat-api vs heat-api-cfn)
        if (true_lower in predicted_lower or predicted_lower in true_lower) and abs(len(predicted_lower) - len(true_lower)) <= 5:
            return True
        # Special case: ceph-osd matches ceph-osd-*
        if "ceph-osd" in predicted_lower and "ceph-osd" in true_lower:
            return True
        return False

    def evaluate_incident(self, incident_id: str) -> Optional[Dict]:
        logs = self.load_incident_logs(incident_id)
        ground_truth = self.load_ground_truth(incident_id)

        if not ground_truth:
            return None

        true_cause = self.get_true_cause(ground_truth)

        # Skip if logs are essentially empty
        if len(logs.strip()) < 20:
            logger.warning(f"{incident_id}: logs too short or empty, skipping evaluation")
            return None

        rule_result = self.rule_based.analyze(logs)
        rule_correct = self._check_correct(rule_result.root_cause, true_cause)
        rule_result.correct = rule_correct

        llm_result = self.llm.analyze(logs)
        llm_correct = self._check_correct(llm_result.root_cause, true_cause)
        llm_result.correct = llm_correct

        return {
            "incident_id": incident_id,
            "true_cause": true_cause,
            "rule_based": {
                "root_cause": rule_result.root_cause,
                "confidence": rule_result.confidence,
                "correct": rule_correct,
            },
            "llm": {
                "root_cause": llm_result.root_cause,
                "confidence": llm_result.confidence,
                "correct": llm_correct,
                "method": llm_result.method,
            },
            "logs_length": len(logs),
        }

    def evaluate_all(self) -> Dict:
        results = []
        for incident_dir in sorted(self.incidents_dir.iterdir()):
            if incident_dir.is_dir() and incident_dir.name.startswith("INC-"):
                incident_id = incident_dir.name
                logger.info(f"Evaluating {incident_id}...")
                result = self.evaluate_incident(incident_id)
                if result:
                    results.append(result)

        if not results:
            return {"error": "No incidents with valid logs and ground truth to evaluate"}

        rule_correct = sum(1 for r in results if r["rule_based"]["correct"])
        llm_correct = sum(1 for r in results if r["llm"]["correct"])

        metrics = {
            "total_incidents": len(results),
            "rule_based_accuracy": rule_correct / len(results),
            "llm_accuracy": llm_correct / len(results),
            "rule_based_correct": rule_correct,
            "llm_correct": llm_correct,
            "detailed_results": results,
        }

        metrics_file = self.incidents_dir / "evaluation_metrics.json"
        with open(metrics_file, "w") as f:
            json.dump(metrics, f, indent=2)

        logger.info(f"Evaluation complete: {metrics_file}")
        logger.info(f"Rule-based accuracy: {metrics['rule_based_accuracy']:.2%}")
        logger.info(f"LLM accuracy: {metrics['llm_accuracy']:.2%}")
        return metrics


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate RCA methods")
    parser.add_argument("--incidents-dir", default="./rca-framework/incidents")
    parser.add_argument("--incident", help="Evaluate single incident")
    parser.add_argument("--openai-key", help="OpenAI API key")

    args = parser.parse_args()

    evaluator = Evaluator(args.incidents_dir)
    if args.openai_key:
        evaluator.llm = LLMRCA(args.openai_key)

    if args.incident:
        result = evaluator.evaluate_incident(args.incident)
        if result:
            print(json.dumps(result, indent=2))
        else:
            print("No valid data for incident")
    else:
        metrics = evaluator.evaluate_all()
        print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()

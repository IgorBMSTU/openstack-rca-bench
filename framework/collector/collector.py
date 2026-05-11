"""
OpenStack RCA Dataset - Data Collection Module
Module: collector
Purpose: Collect logs and system state during incidents

<MODULE_CONTRACT>
Name: collector
Purpose: Collect logs from Loki and system state from OpenStack during incidents
Inputs:
  - incident_id: str - Unique incident identifier
  - start_time: datetime - Incident start time
  - end_time: datetime - Incident end time
Outputs:
  - logs: dict - Collected logs from Loki
  - snapshots: dict - OpenStack system state snapshots
  - metadata: dict - Incident metadata
Dependencies:
  - requests
  - subprocess
  - json
  - datetime
  - pathlib
</MODULE_CONTRACT>

<MODULE_MAP>
- collect_logs(incident_id, start, end): Query Loki for logs during incident
- collect_openstack_snapshot(): Collect OpenStack service state
- save_incident_data(incident_id, data): Save all incident data to disk
- load_incident_data(incident_id): Load previously saved incident data
</MODULE_MAP>
"""

import requests
import json
import subprocess
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LokiCollector:
    """
    Collector for logs from Loki.

    <FUNCTION_CONTRACT>
    Name: LokiCollector
    Purpose: Query and collect logs from Loki API
    Inputs: Loki host and port configuration
    Outputs: Structured log data
    </FUNCTION_CONTRACT>
    """

    def __init__(self, host: str = "localhost", port: int = 3100):
        self.base_url = f"http://{host}:{port}"
        self.session = requests.Session()

    def query_range(
        self, query: str, start: datetime, end: datetime, limit: int = 5000
    ) -> Optional[Dict]:
        """
        <FUNCTION_CONTRACT>
        Name: query_range
        Purpose: Query logs from Loki for a time range
        Inputs:
          - query: str - Loki query (e.g., '{job="neutron"}')
          - start: datetime - Start time
          - end: datetime - End time
          - limit: int - Maximum number of log lines
        Outputs:
          - logs: dict - Loki response with log data
        </FUNCTION_CONTRACT>
        """
        url = f"{self.base_url}/loki/api/v1/query_range"

        # Convert datetime to nanoseconds timestamp
        start_ns = int(start.timestamp() * 1e9)
        end_ns = int(end.timestamp() * 1e9)

        params = {"query": query, "start": start_ns, "end": end_ns, "limit": limit}

        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Failed to query Loki: {e}")
            return None

    def collect_job_logs(self, job: str, start: datetime, end: datetime) -> List[Dict]:
        """
        Collect logs for a specific job.

        <SEMANTIC_BLOCK type="collection" source="loki">
        Queries Loki for logs from a specific job (service).
        Jobs: neutron, ironic, mysql, rabbitmq, redis, system-messages
        </SEMANTIC_BLOCK>
        """
        query = f'{{job="{job}"}}'
        result = self.query_range(query, start, end)

        if result and "data" in result:
            return result["data"].get("result", [])
        return []

    def collect_all_logs(
        self, start: datetime, end: datetime, jobs: List[str] = None
    ) -> Dict[str, List]:
        """
        Collect logs from all configured jobs.

        <FUNCTION_CONTRACT>
        Name: collect_all_logs
        Purpose: Collect logs from all OpenStack services
        Inputs:
          - start: datetime - Start time
          - end: datetime - End time
          - jobs: List[str] - List of job names (optional)
        Outputs:
          - logs: dict - Logs grouped by job
        </FUNCTION_CONTRACT>
        """
        if jobs is None:
            jobs = [
                "neutron",
                "ironic",
                "mysql",
                "rabbitmq",
                "redis",
                "system-messages",
            ]

        all_logs = {}
        for job in jobs:
            logger.info(f"Collecting logs for job: {job}")
            logs = self.collect_job_logs(job, start, end)
            all_logs[job] = logs
            logger.info(f"  Collected {len(logs)} streams")

        return all_logs


class OpenStackCollector:
    """
    Collector for OpenStack system state.

    <FUNCTION_CONTRACT>
    Name: OpenStackCollector
    Purpose: Collect OpenStack service and resource state
    Inputs: OpenStack CLI configuration
    Outputs: System state snapshots
    </FUNCTION_CONTRACT>
    """

    def __init__(self, rc_file: str = "/home/stack/demorc"):
        self.rc_file = rc_file

    def run_openstack_cmd(self, cmd: str) -> tuple:
        """Run OpenStack CLI command."""
        full_cmd = f"source {self.rc_file} && {cmd}"
        try:
            result = subprocess.run(
                full_cmd,
                shell=True,
                capture_output=True,
                text=True,
                executable="/bin/bash",
                timeout=30,
            )
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", "Timeout"
        except Exception as e:
            return False, "", str(e)

    def collect_service_list(self) -> List[Dict]:
        """
        <FUNCTION_CONTRACT>
        Name: collect_service_list
        Purpose: Get list of OpenStack services and their status
        Outputs:
          - services: List[Dict] - Service information
        </FUNCTION_CONTRACT>
        """
        success, stdout, stderr = self.run_openstack_cmd(
            "openstack service list -f json"
        )
        if success:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                logger.error("Failed to parse service list JSON")
        return []

    def collect_hypervisor_list(self) -> List[Dict]:
        """Collect hypervisor information."""
        success, stdout, stderr = self.run_openstack_cmd(
            "openstack hypervisor list -f json"
        )
        if success:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return []
        return []

    def collect_volume_list(self) -> List[Dict]:
        """Collect volume information."""
        success, stdout, stderr = self.run_openstack_cmd(
            "openstack volume list -f json"
        )
        if success:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return []
        return []

    def collect_server_list(self) -> List[Dict]:
        """Collect server (VM) information."""
        success, stdout, stderr = self.run_openstack_cmd(
            "openstack server list -f json"
        )
        if success:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return []
        return []

    def collect_network_list(self) -> List[Dict]:
        """Collect network information."""
        success, stdout, stderr = self.run_openstack_cmd(
            "openstack network list -f json"
        )
        if success:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return []
        return []

    def collect_full_snapshot(self) -> Dict:
        """
        <FUNCTION_CONTRACT>
        Name: collect_full_snapshot
        Purpose: Collect complete OpenStack system state
        Outputs:
          - snapshot: dict - Complete system state
        </FUNCTION_CONTRACT>

        <SEMANTIC_BLOCK type="collection" source="openstack">
        Collects comprehensive snapshot of OpenStack state including:
        - Services
        - Hypervisors
        - Volumes
        - Servers
        - Networks
        Timestamp is included for correlation with logs.
        </SEMANTIC_BLOCK>
        """
        snapshot = {
            "timestamp": datetime.utcnow().isoformat(),
            "services": self.collect_service_list(),
            "hypervisors": self.collect_hypervisor_list(),
            "volumes": self.collect_volume_list(),
            "servers": self.collect_server_list(),
            "networks": self.collect_network_list(),
        }
        return snapshot


class IncidentCollector:
    """
    Main collector for incident data.

    <FUNCTION_CONTRACT>
    Name: IncidentCollector
    Purpose: Orchestrate collection of all incident data
    Inputs: Incident configuration and timing
    Outputs: Complete incident data package
    </FUNCTION_CONTRACT>
    """

    def __init__(self, base_dir: str = "/home/accentos/rca-framework/incidents"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.loki = LokiCollector()
        self.openstack = OpenStackCollector()

    def create_incident(self, incident_id: str, scenario: str, target: str) -> Path:
        """
        Create incident directory structure.

        <SEMANTIC_BLOCK type="organization">
        Creates directory for incident with structure:
        incidents/
          INC-2026-001/
            ground_truth.json
            logs_before.json
            logs_during.json
            logs_after.json
            openstack_snapshot.json
        </SEMANTIC_BLOCK>
        """
        incident_dir = self.base_dir / incident_id
        incident_dir.mkdir(exist_ok=True)

        metadata = {
            "incident_id": incident_id,
            "scenario": scenario,
            "target": target,
            "created_at": datetime.utcnow().isoformat(),
            "status": "created",
        }

        with open(incident_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Created incident: {incident_id}")
        return incident_dir

    def collect_before(self, incident_id: str, window_minutes: int = 5) -> Dict:
        """
        Collect data before incident (baseline).

        <FUNCTION_CONTRACT>
        Name: collect_before
        Purpose: Collect baseline data before fault injection
        Inputs:
          - incident_id: str - Incident identifier
          - window_minutes: int - Time window to collect
        Outputs:
          - data: dict - Baseline logs and snapshot
        </FUNCTION_CONTRACT>
        """
        end = datetime.utcnow()
        start = end - timedelta(minutes=window_minutes)

        logger.info(f"[{incident_id}] Collecting baseline data...")

        logs = self.loki.collect_all_logs(start, end)
        snapshot = self.openstack.collect_full_snapshot()

        data = {
            "timestamp": end.isoformat(),
            "window_minutes": window_minutes,
            "logs": logs,
            "snapshot": snapshot,
        }

        # Save to file
        incident_dir = self.base_dir / incident_id
        with open(incident_dir / "logs_before.json", "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info(f"[{incident_id}] Baseline collected")
        return data

    def collect_during(self, incident_id: str, start: datetime, end: datetime) -> Dict:
        """
        Collect data during incident.

        <FUNCTION_CONTRACT>
        Name: collect_during
        Purpose: Collect data during fault injection
        Inputs:
          - incident_id: str - Incident identifier
          - start: datetime - Incident start time
          - end: datetime - Incident end time
        Outputs:
          - data: dict - Incident logs and snapshot
        </FUNCTION_CONTRACT>
        """
        logger.info(f"[{incident_id}] Collecting incident data...")

        logs = self.loki.collect_all_logs(start, end)
        snapshot = self.openstack.collect_full_snapshot()

        data = {
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "logs": logs,
            "snapshot": snapshot,
        }

        incident_dir = self.base_dir / incident_id
        with open(incident_dir / "logs_during.json", "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info(f"[{incident_id}] Incident data collected")
        return data

    def collect_after(self, incident_id: str, window_minutes: int = 5) -> Dict:
        """
        Collect data after incident (recovery verification).

        <FUNCTION_CONTRACT>
        Name: collect_after
        Purpose: Collect post-recovery data
        Inputs:
          - incident_id: str - Incident identifier
          - window_minutes: int - Time window to collect
        Outputs:
          - data: dict - Post-recovery logs and snapshot
        </FUNCTION_CONTRACT>
        """
        start = datetime.utcnow()
        end = start + timedelta(minutes=window_minutes)

        logger.info(f"[{incident_id}] Collecting recovery data...")

        # Wait for the window
        import time

        time.sleep(window_minutes * 60)

        logs = self.loki.collect_all_logs(start, end)
        snapshot = self.openstack.collect_full_snapshot()

        data = {
            "timestamp": start.isoformat(),
            "window_minutes": window_minutes,
            "logs": logs,
            "snapshot": snapshot,
        }

        incident_dir = self.base_dir / incident_id
        with open(incident_dir / "logs_after.json", "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info(f"[{incident_id}] Recovery data collected")
        return data

    def save_ground_truth(
        self,
        incident_id: str,
        injection_result: Dict,
        recovery_result: Dict,
        log_signatures: List[str] = None,
    ):
        """
        Save ground truth data for incident.

        <FUNCTION_CONTRACT>
        Name: save_ground_truth
        Purpose: Save validated ground truth for incident
        Inputs:
          - incident_id: str - Incident identifier
          - injection_result: dict - Injection metadata
          - recovery_result: dict - Recovery metadata
          - log_signatures: List[str] - Validated log signatures
        Outputs:
          - ground_truth.json file
        </FUNCTION_CONTRACT>

        <SEMANTIC_BLOCK type="ground-truth" validation="three-level">
        Ground truth includes:
        1. Injection truth: Known fault parameters
        2. Log validation: Signature matching in logs
        3. Expert audit: Manual review flag
        </SEMANTIC_BLOCK>
        """
        ground_truth = {
            "incident_id": incident_id,
            "validation_method": "injection_truth",
            "injection": injection_result,
            "recovery": recovery_result,
            "log_signatures_matched": log_signatures or [],
            "expert_audited": False,
            "created_at": datetime.utcnow().isoformat(),
        }

        incident_dir = self.base_dir / incident_id
        with open(incident_dir / "ground_truth.json", "w") as f:
            json.dump(ground_truth, f, indent=2)

        logger.info(f"[{incident_id}] Ground truth saved")


def main():
    """CLI entry point for collector."""
    import argparse

    parser = argparse.ArgumentParser(description="OpenStack RCA Data Collector")
    parser.add_argument("--incident-id", required=True, help="Incident ID")
    parser.add_argument(
        "--action", choices=["before", "during", "after", "ground-truth"], required=True
    )
    parser.add_argument("--scenario", help="Scenario name")
    parser.add_argument("--target", help="Target host")

    args = parser.parse_args()

    collector = IncidentCollector()

    if args.action == "before":
        collector.collect_before(args.incident_id)
    elif args.action == "during":
        # For during, we'd need start/end times from injection
        logger.info("Use --start and --end parameters for 'during' collection")
    elif args.action == "after":
        collector.collect_after(args.incident_id)
    elif args.action == "ground-truth":
        # Would need injection/recovery results
        logger.info("Use injection results to save ground truth")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
OpenStack-RCA-Bench: Realistic Incident Runner
Module: realistic_runner
Purpose: Execute incidents using podman injector and simple SSH log collection

<MODULE_CONTRACT>
Name: realistic_runner
Purpose: Orchestrate incident execution with podman injector and SSH log collection
Inputs: Incident configuration
Outputs: Complete incident dataset with ground truth
Dependencies: injector_v3_podman, simple_collector
</MODULE_CONTRACT>
"""

import sys
import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
import time
from typing import Dict, List, Optional

# Add framework to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import our components
from framework.injector.injector_v3_podman import FaultInjector
from framework.collector.simple_collector import SimpleLogCollector


class Stand16FaultInjector(FaultInjector):
    """
    FaultInjector adapted for Stand16 with correct SSH credentials.
    """

    def __init__(
        self,
        ssh_user: str,
        ssh_key: str,
        jump_host: Optional[str] = None,
        base_dir: str = str(Path(__file__).parent.parent / "rca-framework"),
    ):
        self.ssh_user = ssh_user
        self.ssh_key = ssh_key
        self.jump_host = jump_host
        super().__init__(base_dir)

    def run_ssh_command(self, host: str, command: str, timeout: int = 180):
        """
        Execute command on remote host via SSH with Stand16 credentials.
        """
        import subprocess
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"Executing on {host}: {command}")

        # For localhost, don't use SSH
        if host in ["localhost", "control", "undercloud"]:
            logger.info(f"  Executing locally (host={host})")
            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True, text=True, timeout=timeout
                )
                success = result.returncode == 0
                if not success:
                    logger.error(f"  Command failed: {result.stderr}")
                return success, result.stdout, result.stderr
            except subprocess.TimeoutExpired:
                logger.error(f"  Command timed out after {timeout}s")
                return False, "", "Timeout"
            except Exception as e:
                logger.error(f"  Command exception: {e}")
                return False, "", str(e)

        # Remote execution with configured user and key
        ssh_cmd = ["ssh"]

        if self.ssh_key:
            ssh_cmd.extend(["-i", self.ssh_key])

        ssh_cmd.extend(
            [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=30",
                "-o",
                "ServerAliveInterval=60",
                "-o",
                "ServerAliveCountMax=3",
            ]
        )

        # Add jump host if configured
        if self.jump_host:
            ssh_cmd.extend(["-J", self.jump_host])

        ssh_cmd.extend([f"{self.ssh_user}@{host}", command])

        try:
            result = subprocess.run(
                ssh_cmd, capture_output=True, text=True, timeout=timeout
            )
            success = result.returncode == 0
            if not success:
                logger.error(f"  Command failed on {host}: {result.stderr}")
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error(f"  Command timed out after {timeout}s on {host}")
            return False, "", "Timeout"
        except Exception as e:
            logger.error(f"  Command exception on {host}: {e}")
            return False, "", str(e)


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class RealisticIncidentRunner:
    """
    Orchestrates realistic incident execution with SSH-based log collection.
    """

    def __init__(
        self,
        ssh_user: str = "accentos",
        ssh_key: str = os.path.expanduser("~/.ssh/standkey"),
        jump_host: str = "stack@10.197.75.10",
        base_dir: str = str(Path(__file__).parent.parent / "rca-framework"),
    ):
        self.base_dir = Path(base_dir)
        self.incidents_dir = self.base_dir / "incidents"
        self.incidents_dir.mkdir(parents=True, exist_ok=True)

        # Initialize injector and collector
        self.injector = Stand16FaultInjector(
            ssh_user, ssh_key, jump_host, str(self.base_dir)
        )
        self.collector = SimpleLogCollector(ssh_user, ssh_key, jump_host)

        self.incident_counter = self._get_next_incident_counter()

        logger.info(f"RealisticIncidentRunner initialized. Base dir: {self.base_dir}")
        logger.info(f"Incidents dir: {self.incidents_dir}")

    def _get_next_incident_counter(self) -> int:
        """Find the next incident ID based on existing incident directories."""
        if not self.incidents_dir.exists():
            return 1

        max_id = 0
        for item in self.incidents_dir.iterdir():
            if item.is_dir() and item.name.startswith("INC-2026-"):
                try:
                    id_num = int(item.name.split("-")[-1])
                    max_id = max(max_id, id_num)
                except (ValueError, IndexError):
                    pass

        return max_id + 1

    def generate_incident_id(self) -> str:
        """Generate unique incident ID."""
        incident_id = f"INC-2026-{self.incident_counter:03d}"
        self.incident_counter += 1
        return incident_id

    def create_incident_directory(self, incident_id: str) -> Path:
        """Create directory structure for incident."""
        incident_dir = self.incidents_dir / incident_id
        incident_dir.mkdir(exist_ok=True)

        logger.info(f"Created incident directory: {incident_dir}")
        return incident_dir

    def save_metadata(self, incident_dir: Path, metadata: Dict):
        """Save incident metadata."""
        metadata_path = incident_dir / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
        logger.info(f"Metadata saved to {metadata_path}")

    def save_ground_truth(self, incident_dir: Path, ground_truth: Dict):
        """Save ground truth data."""
        ground_truth_path = incident_dir / "ground_truth.json"
        with open(ground_truth_path, "w") as f:
            json.dump(ground_truth, f, indent=2)
        logger.info(f"Ground truth saved to {ground_truth_path}")

    def collect_logs(
        self,
        incident_dir: Path,
        phase: str,
        start_time: datetime,
        end_time: datetime,
        host: str,
        service: Optional[str] = None,
    ):
        """
        Collect logs for a specific phase (before, during, after).
        """
        logger.info(f"Collecting {phase} logs from {host}")

        if service:
            logs = self.collector.collect_service_logs(
                host, service, start_time, end_time
            )
            log_data = {
                "phase": phase,
                "host": host,
                "service": service,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "logs": logs,
            }
        else:
            logs = self.collector.collect_all_logs(
                start_time, end_time, [host], ["containers", "ceph", "system"]
            )
            log_data = {
                "phase": phase,
                "host": host,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "logs": logs,
            }

        # Save logs to file
        log_filename = f"logs_{phase}.json"
        log_path = incident_dir / log_filename
        with open(log_path, "w") as f:
            json.dump(log_data, f, indent=2, default=str)

        logger.info(f"{phase.capitalize()} logs saved to {log_path}")
        return log_data

    def _get_ceph_cluster_id(self, storage_host: str) -> str:
        """
        Get Ceph cluster ID from systemd units on storage host.
        """
        cmd = "sudo systemctl list-units 'ceph*@osd.*' --no-legend | head -1 | sed 's/@.*//' | xargs"
        success, stdout, stderr = self.injector.run_ssh_command(
            storage_host, cmd, timeout=30
        )
        if success and stdout.strip():
            cluster_id = stdout.strip().replace("ceph-", "")
            return cluster_id
        else:
            # Fallback to default cluster ID for Stand16
            return "db4210f2-3a13-4568-b524-7578e71429d4"

    def inject_ceph_osd_stop(self, storage_host: str, osd_id: str) -> Dict:
        """
        Stop a Ceph OSD on storage host.
        Returns injection result dict.
        """
        timestamp = datetime.utcnow().isoformat()
        logger.info(f"[INJECTION] Stopping Ceph OSD.{osd_id} on {storage_host}")

        # Get cluster ID and construct correct service name
        cluster_id = self._get_ceph_cluster_id(storage_host)
        command = f"sudo systemctl stop ceph-{cluster_id}@osd.{osd_id}"

        # Use injector's SSH command method
        success, stdout, stderr = self.injector.run_ssh_command(
            storage_host, command, timeout=30
        )

        if success:
            time.sleep(10)
            # Verify OSD is down
            verify_cmd = f"sudo ceph osd tree --format=json 2>/dev/null | jq -r '.nodes[] | select(.name==\"osd.{osd_id}\") | .status'"
            verify_success, verify_output, _ = self.injector.run_ssh_command(
                storage_host, verify_cmd
            )
            is_down = verify_success and "down" in verify_output.lower()

            result = {
                "scenario": "ceph-osd-stop",
                "osd_id": osd_id,
                "target": storage_host,
                "injection_time": timestamp,
                "status": "success" if is_down else "partial",
                "command": command,
                "verification": "down" if is_down else verify_output.strip(),
                "stdout": stdout,
                "stderr": stderr,
            }

            if is_down:
                logger.info(
                    f"[INJECTION] Success: OSD.{osd_id} stopped on {storage_host}"
                )
            else:
                logger.warning(f"[INJECTION] Partial: OSD.{osd_id} may still be up")
        else:
            result = {
                "scenario": "ceph-osd-stop",
                "osd_id": osd_id,
                "target": storage_host,
                "injection_time": timestamp,
                "status": "failed",
                "error": stderr,
                "stdout": stdout,
                "command": command,
            }
            logger.error(f"[INJECTION] Failed to stop OSD.{osd_id}: {stderr}")

        return result

    def recover_ceph_osd(self, storage_host: str, osd_id: str) -> Dict:
        """
        Start a stopped Ceph OSD.
        """
        timestamp = datetime.utcnow().isoformat()
        logger.info(f"[RECOVERY] Starting Ceph OSD.{osd_id} on {storage_host}")

        # Get cluster ID and construct correct service name
        cluster_id = self._get_ceph_cluster_id(storage_host)
        command = f"sudo systemctl start ceph-{cluster_id}@osd.{osd_id}"

        success, stdout, stderr = self.injector.run_ssh_command(
            storage_host, command, timeout=30
        )

        if success:
            time.sleep(30)
            # Verify OSD is up
            verify_cmd = f"sudo ceph osd tree --format=json 2>/dev/null | jq -r '.nodes[] | select(.name==\"osd.{osd_id}\") | .status'"
            verify_success, verify_output, _ = self.injector.run_ssh_command(
                storage_host, verify_cmd
            )
            is_up = verify_success and "up" in verify_output.lower()

            result = {
                "scenario": "ceph-osd-stop",
                "osd_id": osd_id,
                "target": storage_host,
                "recovery_time": timestamp,
                "status": "success" if is_up else "failed",
                "verification": "up" if is_up else verify_output.strip(),
                "stdout": stdout,
                "stderr": stderr,
            }

            if is_up:
                logger.info(
                    f"[RECOVERY] Success: OSD.{osd_id} started on {storage_host}"
                )
            else:
                logger.error(f"[RECOVERY] Failed: OSD.{osd_id} not up")
        else:
            result = {
                "scenario": "ceph-osd-stop",
                "osd_id": osd_id,
                "target": storage_host,
                "recovery_time": timestamp,
                "status": "failed",
                "error": stderr,
                "stdout": stdout,
                "command": command,
            }
            logger.error(f"[RECOVERY] Failed to start OSD.{osd_id}: {stderr}")

        return result

    def execute_scenario(
        self,
        scenario: str,
        target: str,
        duration: int = 180,
        osd_id: Optional[str] = None,
    ) -> Dict:
        """
        Execute a scenario (service stop or Ceph OSD stop).

        Returns injection and recovery results.
        """
        logger.info(f"[SCENARIO] Executing: {scenario} on {target} for {duration}s")

        if scenario.startswith("ceph-osd"):
            # Ceph OSD stop scenario
            if not osd_id:
                # Extract OSD ID from scenario name, e.g., "ceph-osd-0-stop"
                osd_id = scenario.split("-")[2]  # "0"

            injection_result = self.inject_ceph_osd_stop(target, osd_id)

            if injection_result.get("status") == "success":
                logger.info(f"Waiting {duration}s for fault to manifest...")
                time.sleep(duration)
                recovery_result = self.recover_ceph_osd(target, osd_id)
            else:
                recovery_result = {"status": "skipped", "reason": "injection_failed"}
        else:
            # Standard service stop scenario
            full_injector_result = self.injector.execute_scenario(
                scenario, target, duration
            )
            injection_result = full_injector_result.get("injection", {})
            recovery_result = full_injector_result.get("recovery", {})

        full_result = {
            "scenario": scenario,
            "target": target,
            "duration": duration,
            "osd_id": osd_id,
            "injection": injection_result,
            "recovery": recovery_result,
            "completed_at": datetime.utcnow().isoformat(),
        }

        logger.info(
            f"[SCENARIO] Complete. Injection: {injection_result.get('status')}, Recovery: {recovery_result.get('status')}"
        )
        return full_result

    def run_incident(
        self,
        scenario: str,
        target: str,
        duration: int = 180,
        collect_before_minutes: int = 1,
        collect_after_minutes: int = 1,
        osd_id: Optional[str] = None,
    ) -> Dict:
        """
        Execute complete incident workflow with log collection.

        Returns incident result dict.
        """
        incident_id = self.generate_incident_id()
        logger.info(f"{'=' * 60}")
        logger.info(f"STARTING INCIDENT: {incident_id}")
        logger.info(f"Scenario: {scenario}, Target: {target}, Duration: {duration}s")
        logger.info(f"{'=' * 60}")

        # Step 1: Create incident directory
        incident_dir = self.create_incident_directory(incident_id)

        # Determine service/container to log
        from framework.injector.injector_v3_podman import SERVICE_MAPPINGS

        service = SERVICE_MAPPINGS.get(scenario, None)

        # Step 2: Collect baseline logs (before injection)
        logger.info(f"[{incident_id}] Step 1/7: Collecting baseline logs...")
        before_start = datetime.utcnow() - timedelta(minutes=collect_before_minutes)
        before_end = datetime.utcnow()
        self.collect_logs(
            incident_dir, "before", before_start, before_end, target, service=service
        )

        # Step 3: Inject fault
        logger.info(f"[{incident_id}] Step 2/7: Injecting fault...")
        injection_start = datetime.utcnow()
        scenario_result = self.execute_scenario(scenario, target, duration, osd_id)

        if scenario_result.get("injection", {}).get("status") != "success":
            logger.error(f"[{incident_id}] Injection failed! Aborting.")
            metadata = {
                "incident_id": incident_id,
                "scenario": scenario,
                "target": target,
                "duration": duration,
                "status": "failed",
                "failure_reason": "injection_failed",
                "created_at": datetime.utcnow().isoformat(),
            }
            self.save_metadata(incident_dir, metadata)
            return {
                "incident_id": incident_id,
                "status": "failed",
                "reason": "injection_failed",
                "result": scenario_result,
            }

        # Step 4: Collect during-incident logs
        logger.info(f"[{incident_id}] Step 3/7: Collecting during-incident logs...")
        during_start = injection_start
        during_end = datetime.utcnow()
        self.collect_logs(
            incident_dir, "during", during_start, during_end, target, service=service
        )

        # Step 5: Wait for remaining fault duration (if any)
        elapsed = (during_end - injection_start).total_seconds()
        remaining = duration - elapsed
        if remaining > 0:
            logger.info(
                f"[{incident_id}] Step 4/7: Waiting {remaining}s for fault duration..."
            )
            time.sleep(remaining)

        # Step 6: Collect after-incident logs
        logger.info(f"[{incident_id}] Step 5/7: Collecting after-incident logs...")
        after_start = datetime.utcnow()
        after_end = after_start + timedelta(minutes=collect_after_minutes)
        time.sleep(collect_after_minutes * 60)  # Wait for recovery period
        self.collect_logs(
            incident_dir, "after", after_start, after_end, target, service=service
        )

        # Step 7: Save ground truth
        logger.info(f"[{incident_id}] Step 6/7: Saving ground truth...")
        ground_truth = {
            "incident_id": incident_id,
            "validation_method": "injection_truth",
            "injection": scenario_result.get("injection", {}),
            "recovery": scenario_result.get("recovery", {}),
            "log_signatures_matched": [],
            "expert_audited": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        self.save_ground_truth(incident_dir, ground_truth)

        # Step 8: Save metadata
        logger.info(f"[{incident_id}] Step 7/7: Saving metadata...")
        metadata = {
            "incident_id": incident_id,
            "scenario": scenario,
            "target": target,
            "duration": duration,
            "osd_id": osd_id,
            "status": "completed",
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "injection_status": scenario_result.get("injection", {}).get("status"),
            "recovery_status": scenario_result.get("recovery", {}).get("status"),
            "log_files": [
                "logs_before.json",
                "logs_during.json",
                "logs_after.json",
                "ground_truth.json",
                "metadata.json",
            ],
        }
        self.save_metadata(incident_dir, metadata)

        logger.info(f"{'=' * 60}")
        logger.info(f"INCIDENT COMPLETED: {incident_id}")
        logger.info(f"Data location: {incident_dir}")
        logger.info(f"{'=' * 60}")

        return {
            "incident_id": incident_id,
            "status": "completed",
            "scenario": scenario,
            "target": target,
            "data_location": str(incident_dir),
            "result": scenario_result,
        }


# Define incident scenarios from REALISTIC_PLAN.md
REALISTIC_SCENARIOS = [
    # Category A: Single-Service (10 incidents)
    {"scenario": "neutron-api-stop", "target": "10.197.76.21", "duration": 120},
    {"scenario": "nova-api-stop", "target": "10.197.76.21", "duration": 120},
    {"scenario": "keystone-stop", "target": "10.197.76.21", "duration": 120},
    {"scenario": "glance-api-stop", "target": "10.197.76.21", "duration": 120},
    {"scenario": "heat-api-stop", "target": "10.197.76.21", "duration": 120},
    {"scenario": "heat-api-cfn-stop", "target": "10.197.76.21", "duration": 120},
    {"scenario": "neutron-dhcp-stop", "target": "10.197.76.21", "duration": 120},
    {"scenario": "ovn-controller-stop", "target": "10.197.76.21", "duration": 120},
    {"scenario": "ovn-metadata-agent-stop", "target": "10.197.76.21", "duration": 120},
    {"scenario": "placement-api-stop", "target": "10.197.76.21", "duration": 120},
    # Category B: Storage (3 incidents)
    {
        "scenario": "ceph-osd-0-stop",
        "target": "10.197.76.41",
        "duration": 300,
        "osd_id": "0",
    },
    {
        "scenario": "ceph-osd-1-stop",
        "target": "10.197.76.42",
        "duration": 300,
        "osd_id": "1",
    },
    {
        "scenario": "ceph-osd-2-stop",
        "target": "10.197.76.43",
        "duration": 300,
        "osd_id": "2",
    },
    # Category C: Compute Internal (2 incidents)
    {"scenario": "nova-conductor-stop", "target": "10.197.76.21", "duration": 120},
    {"scenario": "nova-scheduler-stop", "target": "10.197.76.21", "duration": 120},
    # Category D: Port Block (4 incidents)
    {"scenario": "keystone-port-block", "target": "10.197.76.21", "duration": 120},
    {"scenario": "nova-api-port-block", "target": "10.197.76.21", "duration": 120},
    {"scenario": "neutron-api-port-block", "target": "10.197.76.21", "duration": 120},
    {"scenario": "glance-api-port-block", "target": "10.197.76.21", "duration": 120},
    # Category E: Process Crash (2 incidents)
    {"scenario": "mysql-crash", "target": "10.197.76.21", "duration": 120},
    {"scenario": "rabbitmq-crash", "target": "10.197.76.21", "duration": 120},
    # Category F: Config Corruption (2 incidents)
    {"scenario": "keystone-config", "target": "10.197.76.21", "duration": 120},
    {"scenario": "nova-api-config", "target": "10.197.76.21", "duration": 120},
]


def run_all_realistic_incidents(
    start_from: int = 0,
    max_incidents: Optional[int] = None,
    delay_between: int = 300,
):
    """Run all realistic incident scenarios."""
    runner = RealisticIncidentRunner()
    results = []

    scenarios = REALISTIC_SCENARIOS[start_from:]
    if max_incidents:
        scenarios = scenarios[:max_incidents]

    logger.info("Starting OpenStack RCA Realistic Dataset Generation")
    logger.info(f"Total incidents to execute: {len(scenarios)}")
    logger.info(f"Delay between incidents: {delay_between}s")

    for i, config in enumerate(scenarios, start_from + 1):
        logger.info(f"\n\n{'=' * 60}")
        logger.info(
            f"Progress: {i}/{len(REALISTIC_SCENARIOS)} (starting from {start_from})"
        )
        logger.info(f"Scenario: {config['scenario']} on {config['target']}")
        logger.info(f"{'=' * 60}\n")

        try:
            result = runner.run_incident(
                scenario=config["scenario"],
                target=config["target"],
                duration=config.get("duration", 180),
                osd_id=config.get("osd_id"),
            )
            results.append(result)

            # Wait between incidents for system stabilization
            if i < len(scenarios) + start_from:
                logger.info(f"Waiting {delay_between}s for system stabilization...")
                time.sleep(delay_between)

        except Exception as e:
            logger.error(f"Failed to run incident: {e}")
            import traceback

            traceback.print_exc()
            results.append(
                {
                    "status": "failed",
                    "error": str(e),
                    "config": config,
                }
            )

    # Save summary
    summary = {
        "generated_at": datetime.utcnow().isoformat(),
        "total_incidents": len(scenarios),
        "completed": len([r for r in results if r.get("status") == "completed"]),
        "failed": len([r for r in results if r.get("status") == "failed"]),
        "results": results,
    }

    summary_path = runner.incidents_dir / "summary_realistic.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info(f"\n\n{'=' * 60}")
    logger.info("DATASET GENERATION COMPLETE")
    logger.info(f"Completed: {summary['completed']}/{summary['total_incidents']}")
    logger.info(f"Failed: {summary['failed']}/{summary['total_incidents']}")
    logger.info(f"Summary saved to: {summary_path}")
    logger.info(f"{'=' * 60}")

    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Realistic Incident Runner")
    parser.add_argument("--scenario", help="Run single scenario")
    parser.add_argument("--target", help="Target host")
    parser.add_argument("--duration", type=int, default=180, help="Duration in seconds")
    parser.add_argument("--osd-id", help="OSD ID for Ceph scenarios")
    parser.add_argument(
        "--all", action="store_true", help="Run all realistic scenarios"
    )
    parser.add_argument(
        "--start-from", type=int, default=0, help="Start from scenario index"
    )
    parser.add_argument("--max", type=int, help="Maximum number of incidents to run")
    parser.add_argument(
        "--delay", type=int, default=300, help="Delay between incidents"
    )

    args = parser.parse_args()

    if args.all:
        run_all_realistic_incidents(
            start_from=args.start_from,
            max_incidents=args.max,
            delay_between=args.delay,
        )
    elif args.scenario and args.target:
        runner = RealisticIncidentRunner()
        result = runner.run_incident(
            scenario=args.scenario,
            target=args.target,
            duration=args.duration,
            osd_id=args.osd_id,
        )
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()
        print("\nExamples:")
        print(
            "  Run single incident: python realistic_runner.py --scenario neutron-api-stop --target control"
        )
        print("  Run all incidents: python realistic_runner.py --all")

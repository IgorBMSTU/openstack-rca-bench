#!/usr/bin/env python3
"""
Single Incident Orchestrator
Executes ONE complete, validated incident for the OpenStack RCA dataset.
Pipeline: pre-sanity → watchdog → inject → hold → rollback → post-sanity → loki-export → validate → save
"""

import sys
import os
import json
import time
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sanity_checks.sanity_checks import ComprehensiveSanityCheck
from framework.injector.injector_v3_podman import (
    FaultInjector,
    SERVICE_MAPPINGS,
    PACEKER_RESOURCES,
    CEPH_SERVICES,
    SYSTEMCTL_SERVICES,
    PORT_MAPPINGS,
    CONFIG_PATHS,
)
from orchestrator.loki_exporter import extract_logs_for_incident

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stand16 injector with correct SSH credentials (copied from realistic_runner)
# ---------------------------------------------------------------------------
class Stand16FaultInjector(FaultInjector):
    def __init__(
        self,
        ssh_user: str = "tripleo-admin",
        ssh_key: str = os.path.expanduser("~/.ssh/standkey"),
        jump_host: Optional[str] = "stack@10.197.75.10",
        base_dir: str = str(Path(__file__).parent.parent / "rca-framework"),
    ):
        self.ssh_user = ssh_user
        self.ssh_key = ssh_key
        self.jump_host = jump_host
        super().__init__(base_dir)

    def run_ssh_command(self, host: str, command: str, timeout: int = 180):
        logger.info(f"Executing on {host}: {command}")
        if host in ["localhost", "control", "undercloud"]:
            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True, text=True, timeout=timeout
                )
                return result.returncode == 0, result.stdout, result.stderr
            except subprocess.TimeoutExpired:
                return False, "", "Timeout"
            except Exception as e:
                return False, "", str(e)

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
        if self.jump_host:
            ssh_cmd.extend(["-J", self.jump_host])
        ssh_cmd.extend([f"{self.ssh_user}@{host}", command])

        try:
            result = subprocess.run(
                ssh_cmd, capture_output=True, text=True, timeout=timeout
            )
            success = result.returncode == 0
            if not success:
                logger.error(f"  Command failed on {host}: {result.stderr[:500]}")
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error(f"  Command timed out after {timeout}s on {host}")
            return False, "", "Timeout"
        except Exception as e:
            logger.error(f"  Command exception on {host}: {e}")
            return False, "", str(e)


# ---------------------------------------------------------------------------
# Watchdog: host-local self-recovery even if we lose connectivity
# ---------------------------------------------------------------------------
class WatchdogManager:
    """Installs a nohup watchdog on the target host before injection."""

    @staticmethod
    def _recovery_command(service: str, scenario_type: str = "stop") -> str:
        container = SERVICE_MAPPINGS.get(service, service)
        resource = PACEKER_RESOURCES.get(service)
        ceph_daemon = CEPH_SERVICES.get(service)

        if scenario_type == "port-block":
            port = PORT_MAPPINGS.get(service)
            if port:
                return f"sudo iptables -D INPUT -p tcp --dport {port} -j DROP -m comment --comment 'rca_fault_injection_{service}' || true"
            return "true"

        if scenario_type == "config":
            conf_path = CONFIG_PATHS.get(service, "")
            if ceph_daemon:
                return f"sudo cp {conf_path}.bak {conf_path} 2>/dev/null; sudo ceph orch daemon start {ceph_daemon}"
            if resource:
                return f"sudo cp {conf_path}.bak {conf_path} 2>/dev/null; sudo pcs resource restart {resource}"
            if container in SYSTEMCTL_SERVICES:
                return f"sudo cp {conf_path}.bak {conf_path} 2>/dev/null; sudo systemctl restart tripleo_{container}.service"
            return f"sudo cp {conf_path}.bak {conf_path} 2>/dev/null; sudo podman restart {container}"

        if ceph_daemon:
            return f"sudo ceph orch daemon start {ceph_daemon}"
        if resource:
            return f"sudo pcs resource enable {resource}"
        if container == "neutron_api":
            return "sudo systemctl start tripleo_neutron_api.service"
        if container == "nova_compute":
            return "sudo systemctl start tripleo_nova_compute.service"
        if container == "ovn_controller":
            return "sudo systemctl start tripleo_ovn_controller.service"
        return f"sudo podman start {container}"

    @staticmethod
    def install(
        host: str,
        injector: Stand16FaultInjector,
        service: str,
        duration: int,
        scenario_type: str = "stop",
    ) -> Optional[int]:
        """
        Install a nohup watchdog that fires after duration+120s.
        Returns PID on host or None on failure.
        """
        recovery_cmd = WatchdogManager._recovery_command(service, scenario_type)
        sleep_time = duration + 120
        # Use disown + nohup to survive SSH disconnect
        watchdog_script = (
            f"nohup sh -c 'sleep {sleep_time}; {recovery_cmd}' "
            f"> /tmp/watchdog_{service}.log 2>&1 </dev/null & echo $!"
        )
        success, stdout, stderr = injector.run_ssh_command(
            host, watchdog_script, timeout=30
        )
        if success and stdout.strip().isdigit():
            pid = int(stdout.strip())
            logger.info(
                f"[WATCHDOG] Installed on {host} PID={pid} (fires in {sleep_time}s)"
            )
            return pid
        logger.error(f"[WATCHDOG] Failed to install on {host}: {stderr}")
        return None

    @staticmethod
    def remove(host: str, injector: Stand16FaultInjector, pid: int) -> bool:
        """Best-effort removal of watchdog after normal recovery."""
        success, _, _ = injector.run_ssh_command(
            host, f"kill {pid} 2>/dev/null; rm -f /tmp/watchdog_*.log", timeout=10
        )
        return success


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class LogValidator:
    """Basic rule-based validation of extracted logs against expected signatures."""

    SIGNATURES = {
        "neutron-api-stop": ["neutron", "stop", "503"],
        "nova-api-stop": ["nova", "api", "stop", "503"],
        "cinder-api-stop": ["cinder", "stop", "503"],
        "glance-api-stop": ["glance", "stop", "503"],
        "keystone-stop": ["keystone", "stop", "503"],
        "heat-api-stop": ["heat", "stop", "503"],
        "placement-api-stop": ["placement", "stop", "503"],
        "heat-api-cfn-stop": ["heat", "cfn", "stop", "503"],
        "heat-engine-stop": ["heat", "engine", "stop", "down"],
        "neutron-dhcp-stop": ["neutron", "dhcp", "stop", "down"],
        "cinder-scheduler-stop": ["cinder", "scheduler", "stop", "down"],
        "nova-vnc-proxy-stop": ["nova", "vnc", "proxy", "stop", "down"],
        "nova-conductor-stop": ["nova", "conductor", "stop", "down"],
        "nova-scheduler-stop": ["nova", "scheduler", "stop", "down"],
        "nova-metadata-stop": ["nova", "metadata", "stop", "down"],
        "redis-stop": ["redis", "ERROR", "connection refused"],
        "mysql-stop": ["mysql", "ERROR", "Can't connect"],
        "haproxy-stop": ["haproxy", "bundle", "stop", "down"],
        "cinder-volume-stop": ["cinder", "volume", "stop", "down"],
        "glance-api-internal-stop": ["glance", "internal", "stop", "down"],
        "skyline-apiserver-stop": ["skyline", "apiserver", "stop", "down"],
        "frr-manager-stop": ["frr", "manager", "stop", "down"],
        "iscsid-stop": ["iscsid", "stop", "down"],
        "ovn-northd-stop": ["ovn", "northd", "stop", "down"],
        "ovn-north-db-stop": ["ovn", "north", "db", "stop", "down"],
        "ovn-south-db-stop": ["ovn", "south", "db", "stop", "down"],
        "skyline-console-stop": ["skyline", "console", "stop", "down"],
        "grafana-stop": ["grafana", "stop", "down"],
        "prometheus-stop": ["prometheus", "stop", "down"],
        "rabbitmq-stop": ["rabbitmq", "ERROR", "connection"],
        "nova-compute-stop": ["nova-compute", "stop", "down"],
        "ovn-controller-stop": ["ovn", "controller", "stop", "down"],
        "ovn-metadata-agent-stop": ["ovn", "metadata", "stop", "down"],
        "ceph-osd-0-stop": ["ceph", "osd", "ERROR", "down"],
        "ceph-osd-1-stop": ["ceph", "osd", "ERROR", "down"],
        "ceph-osd-2-stop": ["ceph", "osd", "ERROR", "down"],
        "ceph-mon-stop": ["ceph", "mon", "ERROR", "down"],
        "ceph-mgr-stop": ["ceph", "mgr", "ERROR", "down"],
        "keystone-port-block": ["keystone", "timeout", "connection", "5000"],
        "nova-api-port-block": ["nova", "timeout", "connection", "8774"],
        "neutron-api-port-block": ["neutron", "timeout", "connection", "9696"],
        "glance-api-port-block": ["glance", "timeout", "connection", "9292"],
        "mysql-crash": ["mysql", "killed", "shutdown", "mariadb"],
        "rabbitmq-crash": ["rabbitmq", "killed", "shutdown", "erlang"],
        "keystone-config": ["keystone", "config", "error", "invalid"],
        "nova-api-config": ["nova", "config", "error", "invalid"],
    }

    @staticmethod
    def validate(logs: list, scenario: str) -> dict:
        signatures = LogValidator.SIGNATURES.get(scenario, [])
        lines = [entry["line"].lower() for entry in logs]
        matched = []
        for sig in signatures:
            if any(sig.lower() in line for line in lines):
                matched.append(sig)
        return {
            "signatures_expected": signatures,
            "signatures_found": matched,
            "match_rate": len(matched) / len(signatures) if signatures else 1.0,
            "log_count": len(logs),
            "has_errors": any("error" in line for line in lines),
        }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
class SingleIncidentOrchestrator:
    def __init__(
        self,
        base_dir: str = str(Path(__file__).parent.parent / "rca-framework"),
        ssh_key: str = os.path.expanduser("~/.ssh/standkey"),
        jump_host: str = "stack@10.197.75.10",
    ):
        self.base_dir = Path(base_dir).resolve()
        self.incidents_dir = self.base_dir / "incidents"
        self.incidents_dir.mkdir(parents=True, exist_ok=True)

        self.injector = Stand16FaultInjector(
            ssh_user="tripleo-admin",
            ssh_key=ssh_key,
            jump_host=jump_host,
            base_dir=str(self.base_dir),
        )
        self.sanity = ComprehensiveSanityCheck(
            ssh_key=ssh_key,
            jump_host=jump_host,
            base_dir=str(self.base_dir),
        )

        self.incident_counter = self._next_counter()
        logger.info(f"Orchestrator ready. Base dir: {self.base_dir}")

    def _next_counter(self) -> int:
        max_id = 0
        for item in self.incidents_dir.iterdir():
            if item.is_dir() and item.name.startswith("INC-2026-"):
                try:
                    num = int(item.name.split("-")[-1])
                    max_id = max(max_id, num)
                except ValueError:
                    pass
        return max_id + 1

    def generate_id(self) -> str:
        incident_id = f"INC-2026-{self.incident_counter:03d}"
        self.incident_counter += 1
        return incident_id

    def run_pre_sanity(self, report_file: Optional[Path] = None) -> Tuple[bool, list]:
        logger.info("[PHASE 1/9] Pre-sanity checks...")
        results, has_failures = self.sanity.run_all_checks()
        if report_file:
            report_file.parent.mkdir(parents=True, exist_ok=True)
            self.sanity.generate_report(str(report_file))
        passed = not has_failures
        if passed:
            logger.info("[PHASE 1/9] Pre-sanity PASSED")
        else:
            logger.error("[PHASE 1/9] Pre-sanity FAILED — aborting incident")
        return passed, results

    def install_watchdog(
        self, host: str, service: str, duration: int, scenario_type: str = "stop"
    ) -> Optional[int]:
        logger.info("[PHASE 2/9] Installing watchdog...")
        pid = WatchdogManager.install(
            host, self.injector, service, duration, scenario_type
        )
        if pid:
            logger.info(f"[PHASE 2/9] Watchdog installed PID={pid}")
        else:
            logger.warning("[PHASE 2/9] Watchdog install failed — proceeding anyway")
        return pid

    def inject(self, scenario: str, host: str) -> dict:
        logger.info("[PHASE 3/9] Injecting fault...")
        if scenario.endswith("-stop"):
            service = scenario.replace("-stop", "")
            result = self.injector.inject_service_stop(service, host)
        elif scenario.endswith("-port-block"):
            service = scenario.replace("-port-block", "")
            result = self.injector.inject_port_block(service, host)
        elif scenario.endswith("-crash"):
            service = scenario.replace("-crash", "")
            result = self.injector.inject_process_kill(service, host)
        elif scenario.endswith("-config"):
            service = scenario.replace("-config", "")
            result = self.injector.inject_config_corruption(service, host)
        else:
            result = {"status": "failed", "error": f"Unknown scenario type: {scenario}"}
        logger.info(f"[PHASE 3/9] Injection status: {result['status']}")
        return result

    def hold(self, duration: int):
        logger.info(f"[PHASE 4/9] Holding fault for {duration}s...")
        time.sleep(duration)
        logger.info("[PHASE 4/9] Hold complete")

    def rollback(self, scenario: str, host: str) -> dict:
        logger.info("[PHASE 5/9] Rolling back (normal path)...")
        if scenario.endswith("-stop"):
            service = scenario.replace("-stop", "")
            result = self.injector.recover_service_stop(service, host)
        elif scenario.endswith("-port-block"):
            service = scenario.replace("-port-block", "")
            result = self.injector.recover_port_block(service, host)
        elif scenario.endswith("-crash"):
            service = scenario.replace("-crash", "")
            result = self.injector.recover_process_kill(service, host)
        elif scenario.endswith("-config"):
            service = scenario.replace("-config", "")
            result = self.injector.recover_config_corruption(service, host)
        else:
            result = {"status": "failed", "error": f"Unknown scenario type: {scenario}"}
        logger.info(f"[PHASE 5/9] Rollback status: {result['status']}")
        return result

    def run_post_sanity(self, report_file: Optional[Path] = None) -> Tuple[bool, list]:
        logger.info("[PHASE 6/9] Post-sanity checks (waiting 30s for stabilization)...")
        time.sleep(30)
        # Re-instantiate sanity checker to avoid accumulated state
        self.sanity = ComprehensiveSanityCheck(
            ssh_key=self.injector.ssh_key,
            jump_host=self.injector.jump_host,
            base_dir=str(self.base_dir),
        )
        results, has_failures = self.sanity.run_all_checks()
        if report_file:
            report_file.parent.mkdir(parents=True, exist_ok=True)
            self.sanity.generate_report(str(report_file))
        passed = not has_failures
        if passed:
            logger.info("[PHASE 6/9] Post-sanity PASSED")
        else:
            logger.error("[PHASE 6/9] Post-sanity FAILED — incident marked FAILED")
        return passed, results

    def export_logs(
        self,
        incident_id: str,
        incident_dir: Path,
        injection_start: datetime,
        injection_end: datetime,
        query: str = '{job="systemd-journal"}',
    ) -> dict:
        logger.info("[PHASE 7/9] Exporting logs from Loki...")
        data = extract_logs_for_incident(
            incident_id=incident_id,
            incident_dir=incident_dir,
            injection_start=injection_start,
            injection_end=injection_end,
            query=query,
            padding_seconds=120,
        )
        logger.info(f"[PHASE 7/9] Exported {data['count']} log lines")
        return data

    def validate_logs(self, logs: list, scenario: str) -> dict:
        logger.info("[PHASE 8/9] Validating logs...")
        val = LogValidator.validate(logs, scenario)
        logger.info(
            f"[PHASE 8/9] Validation: {val['match_rate']:.0%} signatures matched"
        )
        return val

    def save_metadata(
        self,
        incident_dir: Path,
        incident_id: str,
        scenario: str,
        target: str,
        duration: int,
        injection_result: dict,
        recovery_result: dict,
        log_meta: dict,
        validation: dict,
        pre_sanity_passed: bool,
        post_sanity_passed: bool,
        watchdog_pid: Optional[int],
    ):
        logger.info("[PHASE 9/9] Saving metadata...")
        metadata = {
            "incident_id": incident_id,
            "scenario": scenario,
            "target_host": target,
            "duration_seconds": duration,
            "injection": injection_result,
            "recovery": recovery_result,
            "data": log_meta,
            "validation": validation,
            "sanity": {
                "pre_check_passed": pre_sanity_passed,
                "post_check_passed": post_sanity_passed,
                "recovery_time_seconds": None,
            },
            "safety": {
                "watchdog_installed": watchdog_pid is not None,
                "watchdog_pid": watchdog_pid,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed" if post_sanity_passed else "failed",
        }
        path = incident_dir / "metadata.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(metadata, f, indent=2)
        logger.info(f"[PHASE 9/9] Metadata saved to {path}")
        return metadata

    def run_single_incident(
        self,
        scenario: str,
        target: str,
        duration: int = 60,
        loki_query: str = '{job="systemd-journal"}',
        workload_type: str = "readonly",
    ) -> dict:
        """
        Execute full pipeline for one incident.
        Returns result summary dict.
        """
        incident_id = self.generate_id()
        incident_dir = self.incidents_dir / incident_id
        incident_dir.mkdir(parents=True, exist_ok=True)
        if not incident_dir.exists():
            raise RuntimeError(f"Failed to create incident directory: {incident_dir}")
        logger.info(
            f"\n{'=' * 60}\n[INCIDENT] {incident_id} | {scenario} on {target} for {duration}s\n{'=' * 60}"
        )

        # 1. Pre-sanity
        pre_passed, _ = self.run_pre_sanity(incident_dir / "pre_sanity.json")
        if not pre_passed:
            return {
                "incident_id": incident_id,
                "status": "aborted",
                "reason": "pre_sanity_failed",
            }

        # 2. Start workload (optional)
        if workload_type:
            from orchestrator.workload_generator import start_workload

            start_workload(workload_type)

        # 3. Watchdog
        scenario_type = "stop"
        if scenario.endswith("-port-block"):
            scenario_type = "port-block"
            service = scenario.replace("-port-block", "")
        elif scenario.endswith("-crash"):
            scenario_type = "crash"
            service = scenario.replace("-crash", "")
        elif scenario.endswith("-config"):
            scenario_type = "config"
            service = scenario.replace("-config", "")
        else:
            service = scenario.replace("-stop", "")
        watchdog_pid = self.install_watchdog(target, service, duration, scenario_type)

        # 4-6. Inject → Hold → Rollback
        injection_start = datetime.now(timezone.utc)
        injection_result = self.inject(scenario, target)

        if injection_result["status"] != "success":
            logger.error("Injection failed — waiting for watchdog then aborting")
            time.sleep(duration + 130)
            return {
                "incident_id": incident_id,
                "status": "aborted",
                "reason": "injection_failed",
                "injection": injection_result,
            }

        self.hold(duration)
        injection_end = datetime.now(timezone.utc)
        recovery_result = self.rollback(scenario, target)

        # Remove watchdog (best effort)
        if watchdog_pid:
            WatchdogManager.remove(target, self.injector, watchdog_pid)

        # 6. Post-sanity
        post_passed, _ = self.run_post_sanity(incident_dir / "post_sanity.json")
        if not post_passed:
            return {
                "incident_id": incident_id,
                "status": "failed",
                "reason": "post_sanity_failed",
                "injection": injection_result,
                "recovery": recovery_result,
            }

        # 7. Export logs
        log_meta = self.export_logs(
            incident_id, incident_dir, injection_start, injection_end, loki_query
        )

        # 8. Validate
        # Read back logs from gzip for validation
        import gzip

        with gzip.open(log_meta["raw_log_file"], "rt") as f:
            log_payload = json.load(f)
        validation = self.validate_logs(log_payload.get("logs", []), scenario)

        # 9. Save metadata
        metadata = self.save_metadata(
            incident_dir=incident_dir,
            incident_id=incident_id,
            scenario=scenario,
            target=target,
            duration=duration,
            injection_result=injection_result,
            recovery_result=recovery_result,
            log_meta=log_meta,
            validation=validation,
            pre_sanity_passed=pre_passed,
            post_sanity_passed=post_passed,
            watchdog_pid=watchdog_pid,
        )

        logger.info("Cooldown 300s for stabilization...")
        time.sleep(300)
        logger.info("Cooldown complete")

        logger.info(f"\n{'=' * 60}\n[INCIDENT] {incident_id} COMPLETED ✅\n{'=' * 60}")
        return {
            "incident_id": incident_id,
            "status": "completed",
            "directory": str(incident_dir),
            "metadata": metadata,
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run a single validated incident")
    parser.add_argument(
        "--scenario", required=True, help="Scenario name, e.g. nova_compute-stop"
    )
    parser.add_argument("--target", required=True, help="Target host IP")
    parser.add_argument(
        "--duration", type=int, default=60, help="Fault duration in seconds"
    )
    parser.add_argument(
        "--query", default='{job="systemd-journal"}', help="Loki LogQL query"
    )
    parser.add_argument(
        "--base-dir",
        default=str(Path(__file__).parent.parent / "rca-framework"),
        help="Base directory",
    )
    parser.add_argument(
        "--workload",
        default="readonly",
        choices=["readonly", "write", "none"],
        help="Background workload type",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    orch = SingleIncidentOrchestrator(base_dir=args.base_dir)
    result = orch.run_single_incident(
        scenario=args.scenario,
        target=args.target,
        duration=args.duration,
        loki_query=args.query,
        workload_type=None if args.workload == "none" else args.workload,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

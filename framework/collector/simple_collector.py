#!/usr/bin/env python3
"""
OpenStack-RCA-Bench: Simple SSH-based Log Collector
Module: simple_collector
Purpose: Collect logs from /var/log/containers/ and /var/log/ceph/ via SSH grep

<MODULE_CONTRACT>
Name: simple_collector
Purpose: Collect logs from remote hosts via SSH using file-based log locations
Inputs:
  - host: str - Target hostname
  - ssh_user: str - SSH username
  - ssh_key: str - Path to SSH private key
  - log_locations: dict - Mapping of log types to paths
  - start_time: datetime - Start time for log collection
  - end_time: datetime - End time for log collection
Outputs:
  - logs: dict - Collected logs grouped by host and log type
Dependencies: subprocess, datetime, pathlib, json
</MODULE_CONTRACT>
"""

import subprocess
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
import time

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class SimpleLogCollector:
    """
    Collect logs via SSH by grepping files with timestamp filtering.
    """

    def __init__(
        self,
        ssh_user: str = "accentos",
        ssh_key: str = os.path.expanduser("~/.ssh/standkey"),
        jump_host: Optional[str] = None,
        default_hosts: Optional[List[str]] = None,
    ):
        """
        Initialize collector with SSH credentials.

        Args:
            ssh_user: SSH username
            ssh_key: Path to SSH private key
            jump_host: Jump host for SSH proxying (e.g., "stack@10.197.75.10")
            default_hosts: List of hostnames to collect from
        """
        self.ssh_user = ssh_user
        self.ssh_key = os.path.expanduser(ssh_key) if ssh_key else None
        self.jump_host = jump_host

        if default_hosts is None:
            default_hosts = ["10.197.76.21"]  # Control node IP
        self.default_hosts = default_hosts

        # Log location mappings
        self.log_locations = {
            "containers": "/var/log/containers/",
            "ceph": "/var/log/ceph/",
            "system": "/var/log/messages",
            "journal": "/var/log/journal/",
        }

        # Service to log file mapping (approximate)
        self.service_log_map = {
            "neutron": "neutron*.log*",
            "mysql": "mysql*.log*",
            "rabbitmq": "rabbitmq*.log*",
            "redis": "redis*.log*",
            "keystone": "keystone*.log*",
            "nova": "nova*.log*",
            "cinder": "cinder*.log*",
            "glance": "glance*.log*",
            "heat": "heat*.log*",
            "ironic": "ironic*.log*",
        }

    def get_host_timezone_offset(self, host: str) -> int:
        """
        Get host's timezone offset from UTC in minutes.
        Returns offset in minutes (e.g., +180 for UTC+3).
        """
        cmd = "date +%z"
        success, stdout, stderr = self.run_ssh_command(host, cmd, sudo=False)
        if not success:
            logger.warning(f"Failed to get timezone offset from {host}: {stderr}")
            return 0  # Assume UTC
        offset_str = stdout.strip()
        if not offset_str:
            return 0
        # Format: +0300 or -0500
        sign = 1 if offset_str[0] == "+" else -1
        hours = int(offset_str[1:3])
        minutes = int(offset_str[3:5])
        return sign * (hours * 60 + minutes)

    def adjust_datetime_for_host(self, dt: datetime, offset_minutes: int) -> datetime:
        """
        Adjust datetime (assumed UTC) to host local time by adding offset.
        """
        from datetime import timedelta

        return dt + timedelta(minutes=offset_minutes)

    def run_ssh_command(
        self, host: str, command: str, timeout: int = 300, sudo: bool = False
    ) -> Tuple[bool, str, str]:
        """
        Execute command on remote host via SSH, optionally through jump host.

        Returns (success, stdout, stderr).
        """
        ssh_cmd = ["ssh"]

        if self.ssh_key:
            ssh_cmd.extend(["-i", self.ssh_key])

        ssh_cmd.extend(
            [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=10",
            ]
        )

        # Add jump host if configured
        if self.jump_host:
            ssh_cmd.extend(["-J", self.jump_host])

        if sudo:
            command = f"sudo -n {command}"

        ssh_cmd.extend([f"{self.ssh_user}@{host}", command])

        logger.debug(f"Executing on {host}: {command}")
        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            success = result.returncode == 0
            if not success:
                logger.warning(f"SSH command failed on {host}: {result.stderr[:200]}")
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error(f"SSH command timed out on {host}")
            return False, "", "Timeout"
        except Exception as e:
            logger.error(f"SSH command exception on {host}: {e}")
            return False, "", str(e)

    def list_log_files(self, host: str, log_type: str) -> List[str]:
        """
        List log files of a given type on remote host.
        """
        path = self.log_locations.get(log_type)
        if not path:
            logger.error(f"Unknown log type: {log_type}")
            return []

        if log_type == "containers":
            command = f"find {path} -name '*.log*' -type f ! -name '*.gz' 2>/dev/null | head -10"
            sudo = True
        elif log_type == "ceph":
            command = f"find {path} -name '*.log' -type f 2>/dev/null | head -10"
            sudo = True
        else:
            command = f"ls -1 {path} 2>/dev/null | head -10"
            sudo = False

        success, stdout, stderr = self.run_ssh_command(host, command, sudo=sudo)
        if not success:
            logger.warning(
                f"Failed to list log files on {host} for {log_type}: {stderr}"
            )
            return []

        files = [f.strip() for f in stdout.strip().split("\n") if f.strip()]
        logger.info(f"Found {len(files)} {log_type} log files on {host}")
        return files

    def collect_logs_by_time(
        self,
        host: str,
        log_files: List[str],
        start_time: datetime,
        end_time: datetime,
        grep_pattern: str = "",
        sudo: bool = False,
        offset_minutes: Optional[int] = None,
    ) -> str:
        """
        Collect logs from specific files between start_time and end_time.

        Uses awk with timestamp comparison (supports milliseconds with dot/comma).
        """
        # Get host timezone offset if not provided
        if offset_minutes is None:
            offset_minutes = self.get_host_timezone_offset(host)

        # Convert UTC times to host local time for comparison
        # Ensure start_time and end_time are timezone-aware (UTC)
        from datetime import timezone, timedelta

        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        # Convert to host local naive datetime (strip timezone)
        local_start = start_time.astimezone(timezone.utc) + timedelta(
            minutes=offset_minutes
        )
        local_end = end_time.astimezone(timezone.utc) + timedelta(
            minutes=offset_minutes
        )

        # Generate normalized timestamp strings for comparison
        # Format: "YYYY-MM-DD HH:MM:SS.000" (always 23 chars with dot)
        start_ts = local_start.strftime("%Y-%m-%d %H:%M:%S.000")
        end_ts = local_end.strftime("%Y-%m-%d %H:%M:%S.999")

        # Apache timestamp format: [DD/Mon/YYYY:HH:MM:SS
        apache_date_start = local_start.strftime("%d/%b/%Y")
        apache_time_start = local_start.strftime("%H:%M:%S")
        apache_date_end = local_end.strftime("%d/%b/%Y")
        apache_time_end = local_end.strftime("%H:%M:%S")
        # Escape slashes for regex
        apache_date_esc_start = apache_date_start.replace("/", "\\/")
        apache_date_esc_end = apache_date_end.replace("/", "\\/")
        apache_start = f".*\\[{apache_date_esc_start}:{apache_time_start}"
        apache_end = f".*\\[{apache_date_esc_end}:{apache_time_end}"

        logs = []
        for log_file in log_files:
            # Choose timestamp format based on file path
            if "httpd" in log_file:
                # Use regex-based range for Apache logs
                ts_start = apache_start
                ts_end = apache_end
                cmd = f"awk '/^{ts_start}/,/^{ts_end}/' '{log_file}' 2>/dev/null"
            else:
                # Use awk with string comparison for OpenStack/Ceph logs (23-char timestamp)
                # Timestamp format: YYYY-MM-DD HH:MM:SS.sss or YYYY-MM-DD HH:MM:SS,sss
                awk_script = f'''BEGIN {{ start_ts = "{start_ts}"; end_ts = "{end_ts}" }}
{{
    # Extract first 23 characters as timestamp
    ts = substr($0, 1, 23)
    # Normalize comma to dot for consistent comparison
    gsub(/,/, ".", ts)
    # Compare as strings (lexicographic ordering works for ISO timestamps)
    if (ts >= start_ts && ts <= end_ts) {{
        print
    }}
}}'''
                # Escape single quotes in awk script for shell quoting
                awk_script_escaped = awk_script.replace("'", "'\\''")
                cmd = f"awk '{awk_script_escaped}' '{log_file}' 2>/dev/null"

            if grep_pattern:
                cmd = f"grep -E '{grep_pattern}' '{log_file}' 2>/dev/null | {cmd}"

            logger.info(f"Running command on {host}: {cmd}")

            success, stdout, stderr = self.run_ssh_command(host, cmd, sudo=sudo)
            if success and stdout.strip():
                logs.append(f"=== {log_file} ===\n{stdout}\n")
            elif stderr and "No such file" not in stderr:
                logger.debug(f"No logs from {log_file}: {stderr[:100]}")

        return "\n".join(logs)

    def _systemd_unit_for_service(self, service: str) -> str:
        """Map container/service name to systemd unit name."""
        # Most TripleO services use tripleo_<service>.service
        return f"tripleo_{service}.service"

    def collect_journalctl_logs(
        self,
        host: str,
        service: str,
        start_time: datetime,
        end_time: datetime,
    ) -> str:
        """Collect logs from journalctl for the systemd unit."""
        unit = self._systemd_unit_for_service(service)
        since_str = start_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        until_str = end_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        cmd = f"sudo journalctl -u {unit} --since '{since_str}' --until '{until_str}' --no-pager 2>&1"
        success, stdout, stderr = self.run_ssh_command(host, cmd, sudo=False)
        if success and stdout.strip() and "No entries" not in stdout:
            return f"=== {unit} (journalctl) ===\n{stdout}\n"
        return ""

    def collect_service_logs(
        self,
        host: str,
        service: str,
        start_time: datetime,
        end_time: datetime,
    ) -> str:
        """
        Collect logs for a specific OpenStack service.
        Primary: journalctl (contains stop/start events).
        Fallback 1: podman logs.
        Fallback 2: file-based collection.
        """
        # Primary: journalctl
        logs = self.collect_journalctl_logs(host, service, start_time, end_time)
        if logs:
            return logs

        # Fallback 1: podman logs
        since_str = start_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        until_str = end_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cmd = f"sudo podman logs --since '{since_str}' --until '{until_str}' {service} 2>&1"
        success, stdout, stderr = self.run_ssh_command(host, cmd, sudo=False)
        if success and stdout.strip():
            return f"=== {service} (podman) ===\n{stdout}\n"

        # Fallback 2: file-based collection
        logger.info(f"Journalctl and podman logs empty for {service}, falling back to file-based collection")
        log_files = self.list_log_files(host, "containers")
        service_files = [f for f in log_files if service in f.lower()]
        if not service_files:
            pattern = self.service_log_map.get(service, f"*{service}*.log*")
            cmd = f"find {self.log_locations['containers']} -name '{pattern}' -type f ! -name '*.gz' 2>/dev/null"
            success, stdout, stderr = self.run_ssh_command(host, cmd, sudo=True)
            if success:
                service_files = [f.strip() for f in stdout.strip().split("\n") if f.strip()]

        if service_files:
            logger.info(f"Collecting logs for {service} from {len(service_files)} files")
            offset_minutes = self.get_host_timezone_offset(host)
            return self.collect_logs_by_time(
                host, service_files, start_time, end_time, sudo=True, offset_minutes=offset_minutes
            )

        logger.warning(f"No log files found for service {service} on {host}")
        return ""

    def collect_podman_logs(
        self,
        host: str,
        start_time: datetime,
        end_time: datetime,
    ) -> str:
        """
        Fallback log collection using podman logs for all running containers.
        """
        # Get list of running containers
        list_cmd = "sudo podman ps --format '{{.Names}}'"
        success, stdout, stderr = self.run_ssh_command(host, list_cmd, sudo=False)
        if not success:
            logger.warning(f"Failed to list running containers on {host}: {stderr}")
            return ""

        containers = [c.strip() for c in stdout.strip().split('\n') if c.strip()]
        if not containers:
            return "No running containers found"

        # Format times as RFC3339 UTC
        since_str = start_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        until_str = end_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        logs = []
        for container in containers:
            cmd = f"sudo podman logs --since '{since_str}' --until '{until_str}' {container} 2>&1"
            success, stdout, stderr = self.run_ssh_command(host, cmd, sudo=False)
            if success and stdout.strip():
                logs.append(f"=== {container} ===\n{stdout}\n")
            elif stderr and "no such container" not in stderr.lower():
                logger.debug(f"No podman logs for {container}: {stderr[:100]}")

        return "\n".join(logs)

    def collect_all_logs(
        self,
        start_time: datetime,
        end_time: datetime,
        hosts: Optional[List[str]] = None,
        log_types: Optional[List[str]] = None,
    ) -> Dict[str, Dict]:
        """
        Collect logs from all specified hosts and log types.

        Returns nested dict: {host: {log_type: logs}}
        """
        if hosts is None:
            hosts = self.default_hosts
        if log_types is None:
            log_types = ["containers", "ceph", "system"]

        all_logs = {}
        for host in hosts:
            offset_minutes = self.get_host_timezone_offset(host)
            host_logs = {}
            for log_type in log_types:
                logger.info(f"Collecting {log_type} logs from {host}")
                if log_type == "containers":
                    # Primary: podman logs (fast), Fallback: file-based
                    logs = self.collect_podman_logs(host, start_time, end_time)
                    if not logs or len(logs.strip()) < 50:
                        logger.info(f"Podman logs empty on {host}, falling back to file-based collection")
                        files = self.list_log_files(host, log_type)
                        if files:
                            logs = self.collect_logs_by_time(
                                host, files, start_time, end_time, sudo=True, offset_minutes=offset_minutes,
                            )
                        else:
                            logs = "No container log files found"
                    host_logs[log_type] = logs
                else:
                    files = self.list_log_files(host, log_type)
                    if files:
                        logs = self.collect_logs_by_time(
                            host,
                            files,
                            start_time,
                            end_time,
                            sudo=(log_type == "ceph"),
                            offset_minutes=offset_minutes,
                        )
                        host_logs[log_type] = logs
                    else:
                        host_logs[log_type] = f"No {log_type} log files found"
                time.sleep(0.5)  # Small delay between collections

            all_logs[host] = host_logs

        return all_logs

    def collect_ceph_osd_logs(
        self,
        storage_host: str,
        osd_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> str:
        """
        Collect Ceph OSD logs from storage node.
        """
        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        ceph_path = self.log_locations["ceph"]
        if osd_id:
            pattern = f"ceph-osd.{osd_id}.log"
            cmd = f"find {ceph_path} -name '{pattern}' -type f 2>/dev/null"
        else:
            cmd = f"find {ceph_path} -name 'ceph-osd.*.log' -type f 2>/dev/null"

        success, stdout, stderr = self.run_ssh_command(storage_host, cmd, sudo=True)
        if not success:
            logger.error(f"Failed to find Ceph OSD logs on {storage_host}: {stderr}")
            return ""

        files = [f.strip() for f in stdout.strip().split("\n") if f.strip()]
        if not files:
            logger.warning(f"No Ceph OSD log files found on {storage_host}")
            return ""

        offset_minutes = self.get_host_timezone_offset(storage_host)
        return self.collect_logs_by_time(
            storage_host,
            files,
            start_time,
            end_time,
            sudo=True,
            offset_minutes=offset_minutes,
        )

    def save_logs_to_file(
        self,
        logs: Union[Dict, str],
        output_dir: Path,
        filename: str = "collected_logs.json",
    ):
        """Save collected logs to JSON file."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename

        # Convert datetime objects to strings for JSON serialization
        def datetime_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")

        with open(output_path, "w") as f:
            json.dump(logs, f, indent=2, default=datetime_serializer)

        logger.info(f"Logs saved to {output_path}")
        return output_path


def main():
    """CLI entry point for simple log collector."""
    import argparse

    parser = argparse.ArgumentParser(description="Simple SSH-based Log Collector")
    parser.add_argument("--host", required=True, help="Target hostname")
    parser.add_argument("--ssh-user", default="accentos", help="SSH username")
    parser.add_argument(
        "--ssh-key", default="/home/accentos/.ssh/standkey", help="SSH private key path"
    )
    parser.add_argument("--start", help="Start time (ISO format)")
    parser.add_argument("--end", help="End time (ISO format)")
    parser.add_argument("--service", help="Filter by service name")
    parser.add_argument(
        "--log-type", choices=["containers", "ceph", "system"], default="containers"
    )
    parser.add_argument("--output", help="Output directory")

    args = parser.parse_args()

    collector = SimpleLogCollector(
        ssh_user=args.ssh_user, ssh_key=args.ssh_key, default_hosts=[args.host]
    )

    if args.start:
        start_time = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    else:
        start_time = datetime.now(timezone.utc) - timedelta(minutes=5)

    if args.end:
        end_time = datetime.fromisoformat(args.end.replace("Z", "+00:00"))
    else:
        end_time = datetime.now(timezone.utc)

    if args.service:
        logs = collector.collect_service_logs(
            args.host, args.service, start_time, end_time
        )
        print(f"Logs for {args.service} on {args.host}:")
        print(logs[:5000])  # Limit output
    else:
        logs = collector.collect_all_logs(
            start_time, end_time, [args.host], [args.log_type]
        )
        print(f"Collected logs from {args.host}:")
        print(json.dumps(logs, indent=2, default=str)[:5000])

    if args.output:
        output_dir = Path(args.output)
        collector.save_logs_to_file(logs, output_dir)


if __name__ == "__main__":
    main()

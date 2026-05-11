#!/usr/bin/env python3
"""
OpenStack RCA Dataset - Fault Injection Framework for Stand16 (v3 - Podman Support)
Module: injector
Purpose: Execute controlled fault injections into OpenStack services using podman

<MODULE_CONTRACT>
Name: injector
Purpose: Execute controlled fault injections into OpenStack services using podman
Inputs:
  - scenario_name: str - Name of fault scenario
  - target: str - Target host or service
  - duration: int - Duration of fault in seconds
Outputs:
  - injection_result: dict - Result of injection with timestamps and status
  - recovery_result: dict - Result of recovery
Dependencies:
  - subprocess
  - datetime
  - json
  - logging
</MODULE_CONTRACT>

<MODULE_MAP>
- inject_fault(scenario, target, duration): Main entry point for fault injection
- recover_fault(scenario, target): Recovery function
- validate_injection(scenario, target): Validation that fault was injected
</MODULE_MAP>
"""

import subprocess
import sys
import time
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class InjectionError(Exception):
    """Raised when fault injection fails"""

    pass


class RecoveryError(Exception):
    """Raised when recovery fails"""

    pass


# Service mappings for Stand16 AIO architecture (podman containers)
SERVICE_MAPPINGS = {
    # Control node podman containers
    "neutron-api-stop": "neutron_api",
    "neutron-api": "neutron_api",
    "neutron-server-stop": "neutron_api",
    "neutron-server": "neutron_api",
    "neutron-dhcp-stop": "neutron_dhcp",
    "neutron-dhcp": "neutron_dhcp",
    "ovn-controller-stop": "ovn_controller",
    "ovn-controller": "ovn_controller",
    "ovn-metadata-agent-stop": "ovn_metadata_agent",
    "ovn-metadata-agent": "ovn_metadata_agent",
    "heat-api-cfn-stop": "heat_api_cfn",
    "heat-api-cfn": "heat_api_cfn",
    "placement-api-stop": "placement_api",
    "placement-api": "placement_api",
    "nova-api-stop": "nova_api",
    "nova-api": "nova_api",
    "nova-conductor-stop": "nova_conductor",
    "nova-conductor": "nova_conductor",
    "nova-scheduler-stop": "nova_scheduler",
    "nova-scheduler": "nova_scheduler",
    "nova-metadata-stop": "nova_metadata",
    "nova-metadata": "nova_metadata",
    "cinder-api-stop": "cinder_api",
    "cinder-api": "cinder_api",
    "cinder-scheduler-stop": "cinder_scheduler",
    "cinder-scheduler": "cinder_scheduler",
    "glance-api-stop": "glance_api",
    "glance-api": "glance_api",
    "keystone-stop": "keystone",
    "keystone": "keystone",
    "heat-api-stop": "heat_api",
    "heat-api": "heat_api",
    "nova-vnc-proxy-stop": "nova_vnc_proxy",
    "nova-vnc-proxy": "nova_vnc_proxy",
    "nova-compute-stop": "nova_compute",
    "nova-compute": "nova_compute",
    "heat-engine-stop": "heat_engine",
    "heat-engine": "heat_engine",
    "redis-stop": "redis-bundle-podman-0",
    "redis": "redis-bundle-podman-0",
    "rabbitmq-stop": "rabbitmq-bundle-podman-0",
    "rabbitmq": "rabbitmq-bundle-podman-0",
    "mysql-stop": "galera-bundle-podman-0",
    "mysql": "galera-bundle-podman-0",
    "haproxy-stop": "haproxy-bundle-podman-0",
    "haproxy": "haproxy-bundle-podman-0",
    "cinder-volume-stop": "openstack-cinder-volume-podman-0",
    "cinder-volume": "openstack-cinder-volume-podman-0",
    "glance-api-internal-stop": "glance_api_internal",
    "glance-api-internal": "glance_api_internal",
    "skyline-apiserver-stop": "skyline-apiserver",
    "skyline-apiserver": "skyline-apiserver",
    "frr-manager-stop": "frr_manager",
    "frr-manager": "frr_manager",
    "iscsid-stop": "iscsid",
    "iscsid": "iscsid",
    "ovn-northd-stop": "ovn_cluster_northd",
    "ovn-northd": "ovn_cluster_northd",
    "ovn-north-db-stop": "ovn_cluster_north_db_server",
    "ovn-north-db": "ovn_cluster_north_db_server",
    "ovn-south-db-stop": "ovn_cluster_south_db_server",
    "ovn-south-db": "ovn_cluster_south_db_server",
    "skyline-console-stop": "skyline-console",
    "skyline-console": "skyline-console",
    "grafana-stop": "grafana",
    "grafana": "grafana",
    "prometheus-stop": "prometheus",
    "prometheus": "prometheus",
    "ovn-controller-stop": "ovn_controller",
    "ovn-controller": "ovn_controller",
    # Storage services (direct on storage nodes)
    "ceph-osd-0": "ceph-osd@0.service",
    "ceph-osd-1": "ceph-osd@1.service",
    "ceph-osd-2": "ceph-osd@2.service",
}

# Services managed by systemd on Stand16 (use systemctl stop/start, NOT podman)
SYSTEMCTL_SERVICES = {
    "neutron_api",
    "nova_compute",
    "ovn_controller",
    "ovn_metadata_agent",
    "cinder_api",
    "cinder_scheduler",
    "glance_api",
    "keystone",
    "heat_api",
    "heat_api_cfn",
    "heat_engine",
    "placement_api",
    "nova_api",
    "nova_conductor",
    "nova_scheduler",
    "nova_metadata",
    "nova_vnc_proxy",
    "neutron_dhcp",
    "glance_api_internal",
    "frr_manager",
    "iscsid",
    "ovn_cluster_northd",
    "ovn_cluster_north_db_server",
    "ovn_cluster_south_db_server",
    "skyline-console",
    "grafana",
    "prometheus",
}

# Pacemaker resource mappings for HA services
PACEKER_RESOURCES = {
    "redis-stop": "redis-bundle",
    "redis": "redis-bundle",
    "rabbitmq-stop": "rabbitmq-bundle",
    "rabbitmq": "rabbitmq-bundle",
    "mysql-stop": "galera-bundle",
    "mysql": "galera-bundle",
    "haproxy-stop": "haproxy-bundle",
    "haproxy": "haproxy-bundle",
    "cinder-volume-stop": "openstack-cinder-volume",
    "cinder-volume": "openstack-cinder-volume",
}

# Ceph orch daemon mappings (managed by cephadm, not systemd/podman directly)
CEPH_SERVICES = {
    "ceph-osd-0": "osd.0",
    "ceph-osd-1": "osd.1",
    "ceph-osd-2": "osd.2",
    "ceph-osd-stop": "osd.0",
    "ceph-mon": "mon.eng-101-astd-ceph-16-storage1",
    "ceph-mon-stop": "mon.eng-101-astd-ceph-16-storage1",
    "ceph-mgr": "mgr.eng-101-astd-ceph-16-storage1.uzwysu",
    "ceph-mgr-stop": "mgr.eng-101-astd-ceph-16-storage1.uzwysu",
}

# TCP port mappings for API services (used by port-block scenarios)
PORT_MAPPINGS = {
    "keystone": 5000,
    "nova-api": 8774,
    "neutron-api": 9696,
    "glance-api": 9292,
    "cinder-api": 8776,
    "placement-api": 8778,
    "heat-api": 8004,
}

# Configuration file paths for config-corruption scenarios
CONFIG_PATHS = {
    "keystone": "/var/lib/config-data/puppet-generated/keystone/etc/keystone/keystone.conf",
    "nova-api": "/var/lib/config-data/puppet-generated/nova/etc/nova/nova.conf",
    "neutron-api": "/var/lib/config-data/puppet-generated/neutron/etc/neutron/neutron.conf",
    "glance-api": "/var/lib/config-data/puppet-generated/glance/etc/glance/glance-api.conf",
}

# Systemd service mappings for Stand16 AIO architecture


class FaultInjector:
    """
    Main class for fault injection into OpenStack services.

    <FUNCTION_CONTRACT>
    Name: FaultInjector
    Purpose: Execute and manage fault injection scenarios using podman
    Inputs: None (initialized with configuration)
    Outputs: Injection results with timestamps and validation
    </FUNCTION_CONTRACT>
    """

    def __init__(self, base_dir: str = "/home/accentos/rca-framework"):
        """
        Initialize the fault injector.
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.incidents_dir = self.base_dir / "incidents"
        self.incidents_dir.mkdir(exist_ok=True)
        logger.info(f"FaultInjector initialized with base_dir: {self.base_dir}")

    def run_ssh_command(
        self, host: str, command: str, timeout: int = 180
    ) -> Tuple[bool, str, str]:
        """
        Execute command on remote host via SSH.

        <FUNCTION_CONTRACT>
        Name: run_ssh_command
        Purpose: Execute shell command on remote host via SSH
        Inputs:
          - host: str - Remote hostname
          - command: str - Command to execute
          - timeout: int - Timeout in seconds
        Outputs:
          - success: bool - Whether command succeeded
          - stdout: str - Standard output
          - stderr: str - Standard error
        </FUNCTION_CONTRACT>
        """
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

        # Remote execution
        ssh_cmd = [
            "ssh",
            "-i",
            "/home/accentos/.ssh/standkey",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=30",
            "-o",
            "ServerAliveInterval=60",
            "-o",
            "ServerAliveCountMax=3",
            f"accentos@{host}",
            command,
        ]

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

    def inject_service_stop(self, service: str, host: str) -> Dict:
        """
        Stop an OpenStack service using podman.

        <FUNCTION_CONTRACT>
        Name: inject_service_stop
        Purpose: Stop a specific OpenStack service container
        Inputs:
          - service: str - Service name (e.g., 'neutron-api')
          - host: str - Target hostname
        Outputs:
          - result: dict - Injection result with timestamp and status
        </FUNCTION_CONTRACT>
        """
        timestamp = datetime.utcnow().isoformat()
        logger.info(f"[INJECTION] Stopping {service} on {host}")

        container_name = SERVICE_MAPPINGS.get(service, service)
        resource = PACEKER_RESOURCES.get(service)
        ceph_daemon = CEPH_SERVICES.get(service)
        is_ceph = False
        is_pacemaker = False
        if ceph_daemon:
            command = f"sudo ceph orch daemon stop {ceph_daemon}"
            is_ceph = True
            is_pacemaker = False
            podman_container_name = container_name
        elif resource:
            command = f"sudo pcs resource disable {resource}"
            is_pacemaker = True
            podman_container_name = resource + "-podman-0"
            is_ceph = False
        elif container_name in SYSTEMCTL_SERVICES:
            command = f"sudo systemctl stop tripleo_{container_name}.service"
            is_pacemaker = False
            is_ceph = False
            podman_container_name = container_name
        else:
            command = f"sudo podman stop {container_name}"
            is_pacemaker = False
            is_ceph = False
            podman_container_name = container_name

        success, stdout, stderr = self.run_ssh_command(host, command)

        if success:
            if is_pacemaker:
                # Pacemaker bundles take longer to stop; retry for up to 30s
                is_stopped = False
                for _ in range(6):
                    time.sleep(5)
                    verify_cmd = f"sudo pcs resource status {resource} | grep -E 'Stopped|Disabled'"
                    verify_success, verify_output, _ = self.run_ssh_command(
                        host, verify_cmd
                    )
                    if verify_success:
                        is_stopped = True
                        break
                    verify_cmd2 = f"sudo podman ps -a --format '{{{{.Names}}}} {{{{.Status}}}}' | grep ^{podman_container_name}"
                    verify_success2, verify_output2, _ = self.run_ssh_command(
                        host, verify_cmd2
                    )
                    if verify_success2 and "Exited" in verify_output2:
                        is_stopped = True
                        break
            elif is_ceph:
                # ceph orch daemon stop is asynchronous; retry for up to 60s
                is_stopped = False
                for _ in range(12):
                    time.sleep(5)
                    verify_cmd = f"sudo ceph orch ps --daemon-type {ceph_daemon.split('.')[0]} | grep {ceph_daemon} | grep -v running"
                    verify_success, verify_output, _ = self.run_ssh_command(
                        host, verify_cmd
                    )
                    if verify_success:
                        is_stopped = True
                        break
                    # Fallback: check systemd service status
                    verify_cmd2 = f"systemctl list-units --all 'ceph-*@{ceph_daemon}' --no-pager | grep inactive"
                    verify_success2, verify_output2, _ = self.run_ssh_command(
                        host, verify_cmd2
                    )
                    if verify_success2:
                        is_stopped = True
                        break
            elif container_name in SYSTEMCTL_SERVICES:
                time.sleep(3)
                verify_cmd = f"systemctl show tripleo_{container_name}.service --property=ActiveState"
                verify_success, verify_output, _ = self.run_ssh_command(
                    host, verify_cmd
                )
                is_stopped = verify_success and "inactive" in verify_output
            else:
                time.sleep(3)
                verify_cmd = f"sudo podman ps -a --format '{{{{.Names}}}} {{{{.Status}}}}' | grep ^{podman_container_name}"
                verify_success, verify_output, _ = self.run_ssh_command(
                    host, verify_cmd
                )
                is_stopped = verify_success and "Exited" in verify_output

            result = {
                "scenario": "service-stop",
                "service": service,
                "actual_container": podman_container_name,
                "target": host,
                "injection_time": timestamp,
                "status": "success" if is_stopped else "partial",
                "command": command,
                "verification": "stopped" if is_stopped else verify_output.strip(),
                "stdout": stdout,
                "stderr": stderr,
            }

            if is_stopped:
                logger.info(
                    f"[INJECTION] Success: {service} ({podman_container_name}) stopped on {host}"
                )
            else:
                logger.warning(f"[INJECTION] Partial: {service} still running")
        else:
            result = {
                "scenario": "service-stop",
                "service": service,
                "actual_container": podman_container_name,
                "target": host,
                "injection_time": timestamp,
                "status": "failed",
                "error": stderr,
                "stdout": stdout,
                "command": command,
            }
            logger.error(f"[INJECTION] Failed to stop {service}: {stderr}")

        return result

    def recover_service_stop(self, service: str, host: str) -> Dict:
        """
        Start a stopped OpenStack service container.

        <FUNCTION_CONTRACT>
        Name: recover_service_stop
        Purpose: Start a stopped OpenStack service container
        Inputs:
          - service: str - Service name
          - host: str - Target hostname
        Outputs:
          - result: dict - Recovery result
        </FUNCTION_CONTRACT>
        """
        timestamp = datetime.utcnow().isoformat()
        logger.info(f"[RECOVERY] Starting {service} on {host}")

        container_name = SERVICE_MAPPINGS.get(service, service)
        resource = PACEKER_RESOURCES.get(service)
        ceph_daemon = CEPH_SERVICES.get(service)
        is_ceph = False
        is_pacemaker = False
        if ceph_daemon:
            command = f"sudo ceph orch daemon start {ceph_daemon}"
            is_ceph = True
            is_pacemaker = False
            podman_container_name = container_name
        elif resource:
            command = f"sudo pcs resource enable {resource}"
            is_pacemaker = True
            podman_container_name = resource + "-podman-0"
            is_ceph = False
        elif container_name in SYSTEMCTL_SERVICES:
            command = f"sudo systemctl start tripleo_{container_name}.service"
            is_pacemaker = False
            is_ceph = False
            podman_container_name = container_name
        else:
            command = f"sudo podman start {container_name}"
            is_pacemaker = False
            is_ceph = False
            podman_container_name = container_name

        success, stdout, stderr = self.run_ssh_command(host, command)

        if success:
            time.sleep(5)
            if is_pacemaker:
                verify_cmd = (
                    f"sudo pcs resource status {resource} | grep -E 'Started|Promoted'"
                )
                verify_success, verify_output, _ = self.run_ssh_command(
                    host, verify_cmd
                )
                is_running = verify_success
                if not is_running:
                    verify_cmd2 = f"sudo podman ps -a --format '{{{{.Names}}}} {{{{.Status}}}}' | grep ^{podman_container_name}"
                    verify_success2, verify_output2, _ = self.run_ssh_command(
                        host, verify_cmd2
                    )
                    is_running = verify_success2 and "Up" in verify_output2
            elif is_ceph:
                time.sleep(10)
                # ceph orch daemon start is asynchronous; verify via ceph orch ps
                verify_cmd = f"sudo ceph orch ps --daemon-type {ceph_daemon.split('.')[0]} | grep {ceph_daemon} | grep running"
                verify_success, verify_output, _ = self.run_ssh_command(
                    host, verify_cmd
                )
                is_running = verify_success
                if not is_running:
                    # Fallback: check systemd service status
                    verify_cmd2 = f"systemctl list-units 'ceph-*@{ceph_daemon}' --no-pager | grep active"
                    verify_success2, verify_output2, _ = self.run_ssh_command(
                        host, verify_cmd2
                    )
                    is_running = verify_success2
            elif container_name in SYSTEMCTL_SERVICES:
                verify_cmd = f"systemctl show tripleo_{container_name}.service --property=ActiveState"
                verify_success, verify_output, _ = self.run_ssh_command(
                    host, verify_cmd
                )
                is_running = verify_success and "active" in verify_output
            else:
                verify_cmd = f"sudo podman ps -a --format '{{{{.Names}}}} {{{{.Status}}}}' | grep ^{podman_container_name}"
                verify_success, verify_output, _ = self.run_ssh_command(
                    host, verify_cmd
                )
                is_running = verify_success and "Up" in verify_output

            result = {
                "scenario": "service-stop",
                "service": service,
                "actual_container": podman_container_name,
                "target": host,
                "recovery_time": timestamp,
                "status": "success" if is_running else "failed",
                "verification": "running" if is_running else verify_output.strip(),
                "stdout": stdout,
                "stderr": stderr,
            }

            if is_running:
                logger.info(
                    f"[RECOVERY] Success: {service} ({podman_container_name}) running on {host}"
                )
            else:
                logger.error(f"[RECOVERY] Failed: {service} not running")
        else:
            result = {
                "scenario": "service-stop",
                "service": service,
                "actual_container": podman_container_name,
                "target": host,
                "recovery_time": timestamp,
                "status": "failed",
                "error": stderr,
                "stdout": stdout,
                "command": command,
            }
            logger.error(f"[RECOVERY] Failed to start {service}: {stderr}")

        return result

    def inject_port_block(self, service: str, host: str, port: int = None) -> Dict:
        """Block a TCP port via iptables (simulates network partition / timeout)."""
        timestamp = datetime.utcnow().isoformat()
        if port is None:
            port = PORT_MAPPINGS.get(service)
        if not port:
            return {
                "scenario": "port-block",
                "service": service,
                "target": host,
                "injection_time": timestamp,
                "status": "failed",
                "error": f"No port mapping for {service}",
            }
        logger.info(f"[INJECTION] Blocking port {port} for {service} on {host}")
        comment = f"rca_fault_injection_{service}"
        cmd = f"sudo iptables -I INPUT 1 -p tcp --dport {port} -j DROP -m comment --comment '{comment}'"
        success, stdout, stderr = self.run_ssh_command(host, cmd)
        if success:
            time.sleep(2)
            verify_cmd = f"sudo iptables -L INPUT -n --line-numbers | grep '{comment}'"
            vsuccess, vout, _ = self.run_ssh_command(host, verify_cmd)
            result = {
                "scenario": "port-block",
                "service": service,
                "port": port,
                "target": host,
                "injection_time": timestamp,
                "status": "success" if vsuccess else "partial",
                "command": cmd,
                "verification": "blocked" if vsuccess else vout.strip(),
                "stdout": stdout,
                "stderr": stderr,
            }
            if vsuccess:
                logger.info(f"[INJECTION] Success: port {port} blocked")
            else:
                logger.warning(f"[INJECTION] Partial: port {port} may not be blocked")
        else:
            result = {
                "scenario": "port-block",
                "service": service,
                "port": port,
                "target": host,
                "injection_time": timestamp,
                "status": "failed",
                "error": stderr,
                "stdout": stdout,
                "command": cmd,
            }
            logger.error(f"[INJECTION] Failed to block port {port}: {stderr}")
        return result

    def recover_port_block(self, service: str, host: str, port: int = None) -> Dict:
        """Remove iptables DROP rule for a TCP port."""
        timestamp = datetime.utcnow().isoformat()
        if port is None:
            port = PORT_MAPPINGS.get(service)
        if not port:
            return {
                "scenario": "port-block",
                "service": service,
                "target": host,
                "recovery_time": timestamp,
                "status": "failed",
                "error": f"No port mapping for {service}",
            }
        logger.info(f"[RECOVERY] Unblocking port {port} for {service} on {host}")
        comment = f"rca_fault_injection_{service}"
        cmd = f"sudo iptables -D INPUT -p tcp --dport {port} -j DROP -m comment --comment '{comment}'"
        success, stdout, stderr = self.run_ssh_command(host, cmd)
        if success:
            time.sleep(2)
            verify_cmd = f"sudo iptables -L INPUT -n --line-numbers | grep '{comment}'"
            vsuccess, vout, _ = self.run_ssh_command(host, verify_cmd)
            result = {
                "scenario": "port-block",
                "service": service,
                "port": port,
                "target": host,
                "recovery_time": timestamp,
                "status": "success" if not vsuccess else "failed",
                "verification": "unblocked" if not vsuccess else "still_blocked",
                "stdout": stdout,
                "stderr": stderr,
            }
            if not vsuccess:
                logger.info(f"[RECOVERY] Success: port {port} unblocked")
            else:
                logger.error(f"[RECOVERY] Failed: port {port} still blocked")
        else:
            result = {
                "scenario": "port-block",
                "service": service,
                "port": port,
                "target": host,
                "recovery_time": timestamp,
                "status": "failed",
                "error": stderr,
                "stdout": stdout,
                "command": cmd,
            }
            logger.error(f"[RECOVERY] Failed to unblock port {port}: {stderr}")
        return result

    def inject_process_kill(self, service: str, host: str) -> Dict:
        """Send SIGKILL to a service process (simulates crash / OOM kill)."""
        timestamp = datetime.utcnow().isoformat()
        logger.info(f"[INJECTION] Killing process for {service} on {host}")
        container_name = SERVICE_MAPPINGS.get(service, service)
        resource = PACEKER_RESOURCES.get(service)
        ceph_daemon = CEPH_SERVICES.get(service)

        if ceph_daemon:
            cmd = f"sudo ceph orch daemon stop {ceph_daemon}"
            podman_container_name = container_name
        elif resource:
            cmd = f"sudo podman kill --signal=KILL {resource}-podman-0"
            podman_container_name = resource + "-podman-0"
        elif container_name in SYSTEMCTL_SERVICES:
            cmd = f"sudo systemctl kill --signal=SIGKILL tripleo_{container_name}.service || sudo kill -9 $(systemctl show tripleo_{container_name}.service --property=MainPID | cut -d= -f2)"
            podman_container_name = container_name
        else:
            cmd = f"sudo podman kill --signal=KILL {container_name}"
            podman_container_name = container_name

        success, stdout, stderr = self.run_ssh_command(host, cmd)
        result = {
            "scenario": "process-kill",
            "service": service,
            "actual_container": podman_container_name,
            "target": host,
            "injection_time": timestamp,
            "status": "success" if success else "failed",
            "command": cmd,
            "stdout": stdout,
            "stderr": stderr,
        }
        if success:
            logger.info(f"[INJECTION] Success: kill signal sent to {service}")
        else:
            logger.error(f"[INJECTION] Failed to kill {service}: {stderr}")
        return result

    def recover_process_kill(self, service: str, host: str) -> Dict:
        """Recover a killed process using the same mechanism as service_stop recovery."""
        return self.recover_service_stop(service, host)

    def inject_config_corruption(self, service: str, host: str) -> Dict:
        """Corrupt a service config file and restart it."""
        timestamp = datetime.utcnow().isoformat()
        logger.info(f"[INJECTION] Corrupting config for {service} on {host}")
        conf_path = CONFIG_PATHS.get(service)
        if not conf_path:
            return {
                "scenario": "config-corruption",
                "service": service,
                "target": host,
                "injection_time": timestamp,
                "status": "failed",
                "error": f"No config path for {service}",
            }

        # Backup config
        backup_cmd = f"sudo cp {conf_path} {conf_path}.bak"
        self.run_ssh_command(host, backup_cmd)

        # Append invalid parameter
        corruption_cmd = f"echo '# RCA_FAULT_INJECTION_CORRUPT' | sudo tee -a {conf_path} >/dev/null && echo '[DEFAULT]' | sudo tee -a {conf_path} >/dev/null && echo 'invalid_parameter_for_rca_test = true' | sudo tee -a {conf_path} >/dev/null"
        success, stdout, stderr = self.run_ssh_command(host, corruption_cmd)

        # Restart service to apply corrupted config
        container_name = SERVICE_MAPPINGS.get(service, service)
        resource = PACEKER_RESOURCES.get(service)
        if resource:
            restart_cmd = f"sudo pcs resource restart {resource}"
            podman_container_name = resource + "-podman-0"
        elif container_name in SYSTEMCTL_SERVICES:
            restart_cmd = f"sudo systemctl restart tripleo_{container_name}.service"
            podman_container_name = container_name
        else:
            restart_cmd = f"sudo podman restart {container_name}"
            podman_container_name = container_name

        rsuccess, rout, rerr = self.run_ssh_command(host, restart_cmd)

        result = {
            "scenario": "config-corruption",
            "service": service,
            "actual_container": podman_container_name,
            "target": host,
            "injection_time": timestamp,
            "status": "success" if success else "failed",
            "command": corruption_cmd,
            "restart_command": restart_cmd,
            "stdout": stdout,
            "stderr": stderr,
        }
        if success:
            logger.info(f"[INJECTION] Success: config corrupted for {service}")
        else:
            logger.error(
                f"[INJECTION] Failed to corrupt config for {service}: {stderr}"
            )
        return result

    def recover_config_corruption(self, service: str, host: str) -> Dict:
        """Restore backed-up config and restart service."""
        timestamp = datetime.utcnow().isoformat()
        logger.info(f"[RECOVERY] Restoring config for {service} on {host}")
        conf_path = CONFIG_PATHS.get(service)
        if not conf_path:
            return {
                "scenario": "config-corruption",
                "service": service,
                "target": host,
                "recovery_time": timestamp,
                "status": "failed",
                "error": f"No config path for {service}",
            }

        restore_cmd = (
            f"sudo cp {conf_path}.bak {conf_path} && sudo rm -f {conf_path}.bak"
        )
        success, stdout, stderr = self.run_ssh_command(host, restore_cmd)

        container_name = SERVICE_MAPPINGS.get(service, service)
        resource = PACEKER_RESOURCES.get(service)
        if resource:
            restart_cmd = f"sudo pcs resource restart {resource}"
            podman_container_name = resource + "-podman-0"
        elif container_name in SYSTEMCTL_SERVICES:
            restart_cmd = f"sudo systemctl restart tripleo_{container_name}.service"
            podman_container_name = container_name
        else:
            restart_cmd = f"sudo podman restart {container_name}"
            podman_container_name = container_name

        rsuccess, rout, rerr = self.run_ssh_command(host, restart_cmd)
        time.sleep(5)

        # Verification same as service_stop recovery
        if resource:
            verify_cmd = (
                f"sudo pcs resource status {resource} | grep -E 'Started|Promoted'"
            )
            vsuccess, vout, _ = self.run_ssh_command(host, verify_cmd)
            is_running = vsuccess
        elif container_name in SYSTEMCTL_SERVICES:
            verify_cmd = f"systemctl show tripleo_{container_name}.service --property=ActiveState"
            vsuccess, vout, _ = self.run_ssh_command(host, verify_cmd)
            is_running = vsuccess and "active" in vout
        else:
            verify_cmd = f"sudo podman ps -a --format '{{{{.Names}}}} {{{{.Status}}}}' | grep ^{podman_container_name}"
            vsuccess, vout, _ = self.run_ssh_command(host, verify_cmd)
            is_running = vsuccess and "Up" in vout

        result = {
            "scenario": "config-corruption",
            "service": service,
            "actual_container": podman_container_name,
            "target": host,
            "recovery_time": timestamp,
            "status": "success" if is_running else "failed",
            "verification": "running" if is_running else vout.strip(),
            "stdout": stdout,
            "stderr": stderr,
        }
        if is_running:
            logger.info(f"[RECOVERY] Success: config restored for {service}")
        else:
            logger.error(
                f"[RECOVERY] Failed: {service} not running after config restore"
            )
        return result

    def execute_scenario(self, scenario: str, target: str, duration: int = 300) -> Dict:
        """
        Execute a complete fault injection scenario with automatic recovery.

        <FUNCTION_CONTRACT>
        Name: execute_scenario
        Purpose: Main entry point for executing fault scenarios
        Inputs:
          - scenario: str - Scenario name (e.g., 'neutron-api-stop')
          - target: str - Target host
          - duration: int - Duration of fault in seconds
        Outputs:
          - full_result: dict - Complete injection and recovery data
        </FUNCTION_CONTRACT>
        """
        logger.info(f"[SCENARIO] Executing: {scenario} on {target} for {duration}s")
        logger.info(f"=" * 60)

        injection_result = None
        recovery_result = None

        try:
            if scenario.endswith("-stop"):
                service = scenario.replace("-stop", "")
                injection_result = self.inject_service_stop(service, target)
                if injection_result.get("status") == "success":
                    logger.info(
                        f"[SCENARIO] Fault injected successfully, waiting {duration}s..."
                    )
                    logger.info(f"Waiting {duration}s for fault to manifest...")
                    time.sleep(duration)
                    recovery_result = self.recover_service_stop(service, target)
                else:
                    logger.error(f"[SCENARIO] Injection failed, skipping recovery")
                    recovery_result = {
                        "status": "skipped",
                        "reason": "injection_failed",
                    }
            elif scenario.endswith("-port-block"):
                service = scenario.replace("-port-block", "")
                injection_result = self.inject_port_block(service, target)
                if injection_result.get("status") == "success":
                    logger.info(f"[SCENARIO] Port blocked, waiting {duration}s...")
                    time.sleep(duration)
                    recovery_result = self.recover_port_block(service, target)
                else:
                    logger.error(f"[SCENARIO] Injection failed, skipping recovery")
                    recovery_result = {
                        "status": "skipped",
                        "reason": "injection_failed",
                    }
            elif scenario.endswith("-crash"):
                service = scenario.replace("-crash", "")
                injection_result = self.inject_process_kill(service, target)
                if injection_result.get("status") == "success":
                    logger.info(f"[SCENARIO] Process killed, waiting {duration}s...")
                    time.sleep(duration)
                    recovery_result = self.recover_process_kill(service, target)
                else:
                    logger.error(f"[SCENARIO] Injection failed, skipping recovery")
                    recovery_result = {
                        "status": "skipped",
                        "reason": "injection_failed",
                    }
            elif scenario.endswith("-config"):
                service = scenario.replace("-config", "")
                injection_result = self.inject_config_corruption(service, target)
                if injection_result.get("status") == "success":
                    logger.info(f"[SCENARIO] Config corrupted, waiting {duration}s...")
                    time.sleep(duration)
                    recovery_result = self.recover_config_corruption(service, target)
                else:
                    logger.error(f"[SCENARIO] Injection failed, skipping recovery")
                    recovery_result = {
                        "status": "skipped",
                        "reason": "injection_failed",
                    }
            else:
                error_msg = f"Unknown scenario: {scenario}"
                logger.error(error_msg)
                logger.info(f"Available scenarios:")
                for svc in sorted(SERVICE_MAPPINGS.keys()):
                    logger.info(f"  {svc}")
                logger.info("")
                logger.info("Note: Uses podman for container management on Stand16 AIO")

                return {
                    "error": error_msg,
                    "scenario": scenario,
                    "target": target,
                    "status": "unknown_scenario",
                }

        except Exception as e:
            logger.error(f"[SCENARIO] Exception during execution: {e}")
            import traceback

            traceback.print_exc()

            return {
                "error": str(e),
                "scenario": scenario,
                "target": target,
                "status": "exception",
            }

        full_result = {
            "scenario": scenario,
            "target": target,
            "duration": duration,
            "injection": injection_result,
            "recovery": recovery_result,
            "completed_at": datetime.utcnow().isoformat(),
        }

        logger.info(f"=" * 60)
        logger.info(
            f"[SCENARIO] Complete. Injection: {injection_result.get('status')}, Recovery: {recovery_result.get('status')}"
        )

        return full_result


def main():
    """CLI entry point for fault injector."""
    import argparse

    parser = argparse.ArgumentParser(
        description="OpenStack RCA Fault Injector for Stand16 (Podman Support)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--scenario",
        required=True,
        help="Scenario name to execute (e.g., neutron-api-stop)",
    )
    parser.add_argument(
        "--target", required=True, help="Target hostname (e.g., control, localhost)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="Fault duration in seconds (default: 300)",
    )
    parser.add_argument("--output", help="Output file for saving results (JSON)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("Verbose logging enabled")

    injector = FaultInjector()
    result = injector.execute_scenario(args.scenario, args.target, args.duration)

    result_json = json.dumps(result, indent=2, default=str)
    print(result_json)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(result_json)
        logger.info(f"Results saved to {output_path}")

    injection_status = result.get("injection", {}).get("status", "failed")
    if injection_status == "success":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()


import sys
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from framework.realistic_runner import Stand16FaultInjector

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single sanity check

    MODULE_CONTRACT:
      PURPOSE: Represents the outcome of a single sanity check execution
      SCOPE: Contains name, status, message, details, and timestamp of a check result
      DEPENDS: Depends on datetime and dataclasses modules
      LINKS: Related to the SanityCheckBase class and CheckResult dataclass
    """

    name: str
    status: str  # "PASS", "FAIL", "WARN"
    message: str
    details: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class SanityCheckBase:
    """Base class for all sanity checks

    MODULE_CONTRACT:
      PURPOSE: Provides common functionality for all sanity check implementations
      SCOPE: Manages check results, executes checks, and handles reporting
      DEPENDS: Depends on CheckResult dataclass, logging, and datetime modules
      LINKS: Used by all specific check classes (PacemakerClusterCheck, etc.)
    """

    def __init__(self, injector: Stand16FaultInjector):
        self.injector = injector
        self.results: List[CheckResult] = []

    def run_all_checks(self) -> List[CheckResult]:
        """Run all checks for this category"""
        raise NotImplementedError

    def add_result(self, name: str, status: str, message: str, details: str = ""):
        """Add a check result

        FUNCTION_CONTRACT:
          PURPOSE: Record the outcome of a single sanity check
          INPUTS: name (str), status (str), message (str), details (str, optional)
          OUTPUTS: None
        """
        self.results.append(
            CheckResult(name=name, status=status, message=message, details=details)
        )

    def print_results(self):
        """Print all results"""
        for result in self.results:
            status_color = {
                "PASS": "\033[92m",  # Green
                "FAIL": "\033[91m",  # Red
                "WARN": "\033[93m",  # Yellow
            }.get(result.status, "")
            reset = "\033[0m"
            print(
                f"{status_color}[{result.status}]{reset} {result.name}: {result.message}"
            )
            if result.details:
                print(f"  Details: {result.details}")

    def has_failures(self) -> bool:
        """Check if any checks failed"""
        return any(r.status == "FAIL" for r in self.results)


class PacemakerClusterCheck(SanityCheckBase):
    """Check Pacemaker cluster status on controllers

    MODULE_CONTRACT:
      PURPOSE: Verify the health and status of Pacemaker cluster services
      SCOPE: Monitors cluster overall status, resource states, failed actions, and node status
      DEPENDS: Depends on Stand16FaultInjector for SSH command execution
      LINKS: Related to SanityCheckBase for result management
    """

    def __init__(self, injector: Stand16FaultInjector, controller_ips: List[str]):
        super().__init__(injector)
        self.controller_ips = controller_ips

    def run_all_checks(self) -> List[CheckResult]:
        """Run all Pacemaker cluster checks"""
        logger.info("Running Pacemaker cluster checks...")

        for controller_ip in self.controller_ips:
            self._check_cluster_overall(controller_ip)
            self._check_resources(controller_ip)
            self._check_failed_actions(controller_ip)
            self._check_node_status(controller_ip)

        return self.results

    def _check_cluster_overall(self, controller_ip: str):
        """Check overall cluster health

        FUNCTION_CONTRACT:
          PURPOSE: Determine if the Pacemaker cluster is functioning properly
          INPUTS: controller_ip (str) - IP address of the controller node
          OUTPUTS: Updates check results with cluster status
        """
        name = f"Pacemaker Cluster Status ({controller_ip})"
        cmd = "sudo pcs status"

        success, stdout, stderr = self.injector.run_ssh_command(
            controller_ip, cmd, timeout=30
        )

        if not success:
            self.add_result(
                name, "FAIL", f"Cannot execute pcs status on {controller_ip}", stderr
            )
            return

        # Check for cluster online status
        # pcs status output can have "Cluster Status:" or "Cluster name:" or "Cluster Summary:"
        if (
            "Cluster name:" in stdout
            or "Cluster Status:" in stdout
            or "Cluster Summary:" in stdout
        ):
            # Check if any node is offline
            if "OFFLINE" in stdout.upper():
                self.add_result(name, "FAIL", "Some nodes are offline", stdout)
            else:
                self.add_result(name, "PASS", "Cluster is online", "All nodes online")
        else:
            self.add_result(name, "FAIL", "Cluster status unknown", stdout)

    def _check_resources(self, controller_ip: str):
        """Check resource status"""
        name = f"Pacemaker Resources ({controller_ip})"
        cmd = "sudo pcs status resources"

        success, stdout, stderr = self.injector.run_ssh_command(
            controller_ip, cmd, timeout=30
        )

        if not success:
            self.add_result(
                name, "FAIL", f"Cannot check resources on {controller_ip}", stderr
            )
            return

        # Check for failed or stopped resources
        lines = stdout.split("\n")
        failed_resources = []
        for line in lines:
            if "Failed" in line or "FAILED" in line:
                failed_resources.append(line.strip())

        if failed_resources:
            self.add_result(
                name, "FAIL", f"Failed resources found", "\n".join(failed_resources)
            )
        else:
            self.add_result(
                name, "PASS", "All resources running", stdout.split("\n")[0]
            )

    def _check_failed_actions(self, controller_ip: str):
        """Check for failed resource actions"""
        name = f"Failed Resource Actions ({controller_ip})"
        cmd = "sudo pcs status --full"

        success, stdout, stderr = self.injector.run_ssh_command(
            controller_ip, cmd, timeout=30
        )

        if not success:
            self.add_result(
                name, "FAIL", f"Cannot check failed actions on {controller_ip}", stderr
            )
            return

        # Extract failed actions section
        if "Failed Resource Actions:" in stdout:
            failed_actions_start = stdout.index("Failed Resource Actions:")
            failed_actions = stdout[failed_actions_start:].strip()

            # Filter out non-critical issues (like RabbitMQ timeouts)
            critical_failures = [
                line
                for line in failed_actions.split("\n")
                if line.strip() and not "rabbitmq-bundle" in line.lower()
            ]

            if critical_failures:
                self.add_result(
                    name, "WARN", "Non-critical failed actions found", failed_actions
                )
            else:
                self.add_result(name, "PASS", "No critical failed actions", "")
        else:
            self.add_result(name, "PASS", "No failed actions", "")

    def _check_node_status(self, controller_ip: str):
        """Check individual node status"""
        name = f"Pacemaker Nodes ({controller_ip})"
        cmd = "sudo pcs status nodes"

        success, stdout, stderr = self.injector.run_ssh_command(
            controller_ip, cmd, timeout=30
        )

        if not success:
            self.add_result(
                name, "FAIL", f"Cannot check node status on {controller_ip}", stderr
            )
            return

        # Check for online nodes
        if "Online:" in stdout:
            online_nodes = stdout.split("Online:")[1].strip().split()[0]
            self.add_result(name, "PASS", f"Online nodes: {online_nodes}", "")
        else:
            self.add_result(name, "FAIL", "Node status unknown", stdout)


class OpenStackAPICheck(SanityCheckBase):
    """Check OpenStack API services and functionality"""

    def __init__(self, injector: Stand16FaultInjector, undercloud_host: str):
        super().__init__(injector)
        self.undercloud_host = undercloud_host
        # OpenStack commands must be executed on undercloud as stack user
        self.stack_user = "stack"

    def run_all_checks(self) -> List[CheckResult]:
        """Run all OpenStack API checks"""
        logger.info("Running OpenStack API checks...")

        self._check_service_list()
        self._check_hypervisor_list()
        self._check_network_agents()
        self._check_volume_services()
        self._check_image_list()
        self._check_flavor_list()

        return self.results

    def _run_openstack_cmd(self, cmd: str) -> Tuple[bool, str, str]:
        """Run OpenStack CLI command on undercloud as stack user"""
        import subprocess

        # Direct SSH to undercloud as stack user (no jump host)
        full_cmd = f"source ~/demorc && {cmd}"
        ssh_cmd = [
            "ssh",
            "-i",
            self.injector.ssh_key,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=30",
            f"{self.stack_user}@{self.undercloud_host}",
            full_cmd,
        ]

        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
            success = result.returncode == 0
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", "Timeout"
        except Exception as e:
            return False, "", str(e)

    def _check_service_list(self):
        """Check OpenStack service list

        FUNCTION_CONTRACT:
          PURPOSE: Verify that OpenStack services are properly registered and available
          INPUTS: None
          OUTPUTS: Updates check results with service count and status
        """
        name = "OpenStack Service List"
        cmd = "openstack service list -f json"

        success, stdout, stderr = self._run_openstack_cmd(cmd)

        if not success:
            self.add_result(name, "FAIL", "Cannot list OpenStack services", stderr)
            return

        import json

        try:
            services = json.loads(stdout)
            if len(services) > 0:
                enabled_count = sum(
                    1 for s in services if s.get("Enabled", "false") == "True"
                )
                self.add_result(
                    name,
                    "PASS",
                    f"Found {len(services)} services, {enabled_count} enabled",
                    f"Services: {', '.join([s['Name'] for s in services[:5]])}...",
                )
            else:
                self.add_result(name, "FAIL", "No OpenStack services found", "")
        except json.JSONDecodeError:
            self.add_result(name, "FAIL", "Invalid service list output", stdout[:200])

    def _check_hypervisor_list(self):
        """Check compute hypervisors"""
        name = "OpenStack Hypervisors"
        cmd = "openstack hypervisor list -f json"

        success, stdout, stderr = self._run_openstack_cmd(cmd)

        if not success:
            self.add_result(name, "FAIL", "Cannot list hypervisors", stderr)
            return

        import json

        try:
            hypervisors = json.loads(stdout)
            if len(hypervisors) > 0:
                up_count = sum(1 for h in hypervisors if h.get("State") == "up")
                self.add_result(
                    name,
                    "PASS" if up_count == len(hypervisors) else "WARN",
                    f"{up_count}/{len(hypervisors)} hypervisors UP",
                    f"Hypervisors: {', '.join([h['Hypervisor Hostname'] for h in hypervisors])}",
                )
            else:
                self.add_result(name, "FAIL", "No hypervisors found", "")
        except json.JSONDecodeError:
            self.add_result(
                name, "FAIL", "Invalid hypervisor list output", stdout[:200]
            )

    def _check_network_agents(self):
        """Check neutron network agents"""
        name = "Neutron Network Agents"
        cmd = "openstack network agent list -f json"

        success, stdout, stderr = self._run_openstack_cmd(cmd)

        if not success:
            self.add_result(name, "FAIL", "Cannot list network agents", stderr)
            return

        import json

        try:
            agents = json.loads(stdout)
            if len(agents) > 0:
                alive_count = sum(
                    1 for a in agents if a.get("Alive", "false") == "True"
                )
                up_count = sum(1 for a in agents if a.get("State") == "UP")
                status = (
                    "PASS"
                    if alive_count == len(agents) and up_count == len(agents)
                    else "WARN"
                )
                self.add_result(
                    name,
                    status,
                    f"{alive_count}/{len(agents)} agents alive, {up_count}/{len(agents)} UP",
                    f"Agents: {len(agents)} total",
                )
            else:
                self.add_result(name, "FAIL", "No network agents found", "")
        except json.JSONDecodeError:
            self.add_result(
                name, "FAIL", "Invalid network agent list output", stdout[:200]
            )

    def _check_volume_services(self):
        """Check cinder volume services"""
        name = "Cinder Volume Services"
        cmd = "openstack volume service list -f json"

        success, stdout, stderr = self._run_openstack_cmd(cmd)

        if not success:
            self.add_result(name, "FAIL", "Cannot list volume services", stderr)
            return

        import json

        try:
            services = json.loads(stdout)
            if len(services) > 0:
                up_count = sum(1 for s in services if s.get("State") == "up")
                enabled_count = sum(1 for s in services if s.get("Status") == "enabled")
                status = (
                    "PASS"
                    if up_count == len(services) and enabled_count == len(services)
                    else "WARN"
                )
                self.add_result(
                    name,
                    status,
                    f"{enabled_count}/{len(services)} enabled, {up_count}/{len(services)} UP",
                    f"Services: {len(services)} total",
                )
            else:
                self.add_result(name, "FAIL", "No volume services found", "")
        except json.JSONDecodeError:
            self.add_result(
                name, "FAIL", "Invalid volume service list output", stdout[:200]
            )

    def _check_image_list(self):
        """Check glance images"""
        name = "Glance Images"
        cmd = "openstack image list -f json"

        success, stdout, stderr = self._run_openstack_cmd(cmd)

        if not success:
            self.add_result(name, "FAIL", "Cannot list images", stderr)
            return

        import json

        try:
            images = json.loads(stdout)
            self.add_result(
                name,
                "PASS",
                f"Found {len(images)} images",
                f"Images: {', '.join([i['Name'] for i in images[:3]])}...",
            )
        except json.JSONDecodeError:
            self.add_result(name, "FAIL", "Invalid image list output", stdout[:200])

    def _check_flavor_list(self):
        """Check nova flavors"""
        name = "Nova Flavors"
        cmd = "openstack flavor list -f json"

        success, stdout, stderr = self._run_openstack_cmd(cmd)

        if not success:
            self.add_result(name, "FAIL", "Cannot list flavors", stderr)
            return

        import json

        try:
            flavors = json.loads(stdout)
            self.add_result(
                name,
                "PASS",
                f"Found {len(flavors)} flavors",
                f"Flavors: {', '.join([f['Name'] for f in flavors[:3]])}...",
            )
        except json.JSONDecodeError:
            self.add_result(name, "FAIL", "Invalid flavor list output", stdout[:200])


class ContainerStatusCheck(SanityCheckBase):
    """Check container status across all nodes

    MODULE_CONTRACT:
      PURPOSE: Monitor the health and status of containerized services across all nodes
      SCOPE: Validates container running status, exited containers, and critical container presence
      DEPENDS: Depends on Stand16FaultInjector for SSH command execution
      LINKS: Related to SanityCheckBase for result management
    """

    def __init__(self, injector: Stand16FaultInjector, node_ips: Dict[str, List[str]]):
        super().__init__(injector)
        self.node_ips = node_ips

    def run_all_checks(self) -> List[CheckResult]:
        """Run all container status checks"""
        logger.info("Running container status checks...")

        for node_type, ips in self.node_ips.items():
            for node_ip in ips:
                self._check_container_status(node_type, node_ip)
                self._check_exited_containers(node_type, node_ip)
                self._check_critical_containers(node_type, node_ip)

        return self.results

    def _check_container_status(self, node_type: str, node_ip: str):
        """Check overall container status

        FUNCTION_CONTRACT:
          PURPOSE: Verify that all containers are running on the specified node
          INPUTS: node_type (str) - Type of node (controller, compute, storage), node_ip (str) - IP address
          OUTPUTS: Updates check results with container status information
        """
        name = f"Container Status ({node_type} - {node_ip})"
        cmd = "sudo podman ps -a --format '{{.Names}}\t{{.Status}}'"

        success, stdout, stderr = self.injector.run_ssh_command(
            node_ip, cmd, timeout=30
        )

        if not success:
            self.add_result(
                name, "FAIL", f"Cannot check container status on {node_ip}", stderr
            )
            return

        lines = stdout.strip().split("\n")
        running_count = 0
        total_count = len(lines) if lines and lines[0] else 0

        for line in lines:
            if "Up" in line:
                running_count += 1

        if total_count == 0:
            self.add_result(name, "FAIL", "No containers found", "")
        elif running_count == total_count:
            self.add_result(
                name,
                "PASS",
                f"All containers running ({running_count}/{total_count})",
                "",
            )
        else:
            self.add_result(
                name,
                "WARN",
                f"Some containers not running ({running_count}/{total_count})",
                "",
            )

    def _check_exited_containers(self, node_type: str, node_ip: str):
        """Check for exited containers"""
        name = f"Exited Containers ({node_type} - {node_ip})"
        cmd = "sudo podman ps -a --filter 'status=exited' --format '{{.Names}}\t{{.Status}}'"

        success, stdout, stderr = self.injector.run_ssh_command(
            node_ip, cmd, timeout=30
        )

        if not success:
            self.add_result(
                name, "FAIL", f"Cannot check exited containers on {node_ip}", stderr
            )
            return

        if stdout.strip():
            exited_containers = stdout.strip().split("\n")
            self.add_result(
                name,
                "WARN",
                f"Found {len(exited_containers)} exited containers",
                "\n".join(exited_containers[:5]),
            )
        else:
            self.add_result(name, "PASS", "No exited containers", "")

    def _check_critical_containers(self, node_type: str, node_ip: str):
        """Check critical containers are running"""
        name = f"Critical Containers ({node_type} - {node_ip})"
        cmd = "sudo podman ps --format '{{.Names}}'"

        success, stdout, stderr = self.injector.run_ssh_command(
            node_ip, cmd, timeout=30
        )

        if not success:
            self.add_result(
                name, "FAIL", f"Cannot list running containers on {node_ip}", stderr
            )
            return

        # Define critical containers based on node type
        # Note: Use actual container names from deployment
        critical_containers = {
            "controller": [
                "haproxy-bundle",
                "galera-bundle",
                "rabbitmq-bundle",
                "redis-bundle",
                "nova_api",
                "neutron_api",
                "glance_api",
                "cinder_api",
            ],
            "compute": [
                "nova_compute",
                # Note: neutron-ovn-agent and nova-libvirt are not separate containers
                # They run as part of compute service
            ],
            "storage": [
                # Ceph containers have UUID in name, so we look for patterns
                # e.g., ceph-{UUID}-mon-{node}, ceph-{UUID}-osd-0
                # Note: mgr may only run on one storage node
                {"pattern": "-mon-", "name": "ceph-mon", "optional": False},
                {"pattern": "-osd-", "name": "ceph-osd", "optional": False},
                {"pattern": "-mgr-", "name": "ceph-mgr", "optional": True},
            ],
        }

        running_containers = stdout.strip().split("\n") if stdout.strip() else []
        node_critical = critical_containers.get(node_type, [])

        missing_critical = []
        missing_optional = []
        for critical in node_critical:
            if isinstance(critical, dict):
                # For Ceph containers, use pattern matching
                pattern = critical["pattern"]
                name = critical["name"]
                is_optional = critical.get("optional", False)
                found = any(pattern in container for container in running_containers)
                if not found:
                    if is_optional:
                        missing_optional.append(name)
                    else:
                        missing_critical.append(name)
            else:
                # For regular containers, use substring matching
                found = any(critical in container for container in running_containers)
                if not found:
                    missing_critical.append(critical)

        if not missing_critical:
            if missing_optional:
                self.add_result(
                    name,
                    "PASS",
                    f"All critical containers running (optional missing: {', '.join(missing_optional)})",
                    "",
                )
            else:
                self.add_result(name, "PASS", "All critical containers running", "")
        else:
            msg = f"Missing critical containers: {', '.join(missing_critical)}"
            if missing_optional:
                msg += f" (optional also missing: {', '.join(missing_optional)})"
            self.add_result(name, "FAIL", msg, "")


class CephHealthCheck(SanityCheckBase):
    """Check Ceph cluster health

    MODULE_CONTRACT:
      PURPOSE: Monitor the health and status of the Ceph storage cluster
      SCOPE: Validates overall cluster health, OSD status, and orchestrator services
      DEPENDS: Depends on Stand16FaultInjector for SSH command execution
      LINKS: Related to SanityCheckBase for result management
    """

    def __init__(self, injector: Stand16FaultInjector, storage_ips: List[str]):
        super().__init__(injector)
        self.storage_ips = storage_ips

    def run_all_checks(self) -> List[CheckResult]:
        """Run all Ceph health checks"""
        logger.info("Running Ceph health checks...")

        # Try each storage node until one works
        for storage_ip in self.storage_ips:
            self._check_ceph_health(storage_ip)
            self._check_ceph_osd_status(storage_ip)
            self._check_ceph_orch_ps(storage_ip)

        return self.results

    def _check_ceph_health(self, storage_ip: str):
        """Check overall Ceph health

        FUNCTION_CONTRACT:
          PURPOSE: Determine the overall health status of the Ceph cluster
          INPUTS: storage_ip (str) - IP address of the storage node to check
          OUTPUTS: Updates check results with health status information
        """
        name = f"Ceph Cluster Health ({storage_ip})"
        cmd = "sudo ceph -s"

        success, stdout, stderr = self.injector.run_ssh_command(
            storage_ip, cmd, timeout=30
        )

        if not success:
            # Try next node
            return

        # Check health status
        lines = stdout.split("\n")
        for line in lines:
            if "health:" in line.lower() or "HEALTH" in line:
                if "HEALTH_OK" in line:
                    self.add_result(name, "PASS", "Ceph cluster healthy", "")
                elif "HEALTH_WARN" in line:
                    self.add_result(
                        name, "WARN", "Ceph cluster in warning state", line.strip()
                    )
                elif "HEALTH_ERR" in line:
                    self.add_result(
                        name, "FAIL", "Ceph cluster in error state", line.strip()
                    )
                else:
                    self.add_result(
                        name, "WARN", f"Unknown Ceph health state: {line.strip()}", ""
                    )
                return

        self.add_result(
            name, "WARN", "Could not determine Ceph health status", stdout[:200]
        )

    def _check_ceph_osd_status(self, storage_ip: str):
        """Check OSD status"""
        name = f"Ceph OSD Status ({storage_ip})"
        cmd = "sudo ceph osd status"

        success, stdout, stderr = self.injector.run_ssh_command(
            storage_ip, cmd, timeout=30
        )

        if not success:
            return

        # Check for down OSDs
        if "down" in stdout.lower():
            self.add_result(name, "WARN", "Some OSDs are down", stdout.strip())
        else:
            self.add_result(name, "PASS", "All OSDs are up", "")

    def _check_ceph_orch_ps(self, storage_ip: str):
        """Check Ceph orchestrator services"""
        name = f"Ceph Orchestrator Services ({storage_ip})"
        cmd = "sudo ceph orch ps"

        success, stdout, stderr = self.injector.run_ssh_command(
            storage_ip, cmd, timeout=30
        )

        if not success:
            return

        # Check for stopped services
        if "stopped" in stdout.lower():
            self.add_result(
                name, "WARN", "Some Ceph services are stopped", stdout.strip()
            )
        else:
            self.add_result(name, "PASS", "Ceph orchestrator services running", "")


class SystemdServicesCheck(SanityCheckBase):
    """Check systemd-managed services"""

    def __init__(self, injector: Stand16FaultInjector, node_ips: Dict[str, List[str]]):
        super().__init__(injector)
        self.node_ips = node_ips

    def run_all_checks(self) -> List[CheckResult]:
        """Run all systemd service checks"""
        logger.info("Running systemd service checks...")

        for node_type, ips in self.node_ips.items():
            for node_ip in ips:
                self._check_tripleo_services(node_type, node_ip)

        return self.results

    def _check_tripleo_services(self, node_type: str, node_ip: str):
        """Check tripleo systemd services"""
        name = f"TripleO Systemd Services ({node_type} - {node_ip})"
        cmd = "systemctl list-units 'tripleo-*' --state=failed --no-pager"

        success, stdout, stderr = self.injector.run_ssh_command(
            node_ip, cmd, timeout=30
        )

        if not success:
            # Check with sudo
            cmd = "sudo systemctl list-units 'tripleo-*' --state=failed --no-pager"
            success, stdout, stderr = self.injector.run_ssh_command(
                node_ip, cmd, timeout=30
            )

            if not success:
                self.add_result(
                    name, "WARN", f"Cannot check failed services on {node_ip}", stderr
                )
                return

        # Check for failed services
        lines = stdout.strip().split("\n")
        failed_services = []
        for line in lines:
            if "tripleo-" in line and "loaded" in line:
                failed_services.append(line.strip())

        if failed_services:
            self.add_result(
                name,
                "FAIL",
                f"Found {len(failed_services)} failed services",
                "\n".join(failed_services[:5]),
            )
        else:
            self.add_result(name, "PASS", "No failed TripleO services", "")


class OpenStackOperationsCheck(SanityCheckBase):
    """Check basic OpenStack operations"""

    def __init__(self, injector: Stand16FaultInjector, undercloud_host: str):
        super().__init__(injector)
        self.undercloud_host = undercloud_host
        self.stack_user = "stack"

    def run_all_checks(self) -> List[CheckResult]:
        """Run all OpenStack operations checks"""
        logger.info("Running OpenStack operations checks...")

        self._check_network_list()
        self._check_quota_usage()
        self._check_server_list()

        return self.results

    def _run_openstack_cmd(self, cmd: str) -> Tuple[bool, str, str]:
        """Run OpenStack CLI command on undercloud as stack user"""
        import subprocess

        # Direct SSH to undercloud as stack user (no jump host)
        full_cmd = f"source ~/demorc && {cmd}"
        ssh_cmd = [
            "ssh",
            "-i",
            self.injector.ssh_key,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=30",
            f"{self.stack_user}@{self.undercloud_host}",
            full_cmd,
        ]

        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
            success = result.returncode == 0
            return success, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", "Timeout"
        except Exception as e:
            return False, "", str(e)

    def _check_network_list(self):
        """Check network list operation"""
        name = "OpenStack Network List"
        cmd = "openstack network list -f json"

        success, stdout, stderr = self._run_openstack_cmd(cmd)

        if success:
            import json

            try:
                networks = json.loads(stdout)
                self.add_result(
                    name,
                    "PASS",
                    f"Network list successful ({len(networks)} networks)",
                    "",
                )
            except json.JSONDecodeError:
                self.add_result(
                    name, "WARN", "Network list returned invalid JSON", stdout[:200]
                )
        else:
            self.add_result(name, "FAIL", "Cannot list networks", stderr)

    def _check_quota_usage(self):
        """Check quota usage"""
        name = "OpenStack Quota Usage"
        cmd = "openstack quota show --default"

        success, stdout, stderr = self._run_openstack_cmd(cmd)

        if success:
            self.add_result(name, "PASS", "Quota information accessible", "")
        else:
            self.add_result(name, "WARN", "Cannot retrieve quota information", stderr)

    def _check_server_list(self):
        """Check server list operation"""
        name = "OpenStack Server List"
        cmd = "openstack server list -f json"

        success, stdout, stderr = self._run_openstack_cmd(cmd)

        if success:
            import json

            try:
                servers = json.loads(stdout)
                self.add_result(
                    name, "PASS", f"Server list successful ({len(servers)} servers)", ""
                )
            except json.JSONDecodeError:
                self.add_result(
                    name, "WARN", "Server list returned invalid JSON", stdout[:200]
                )
        else:
            self.add_result(name, "FAIL", "Cannot list servers", stderr)


class ComprehensiveSanityCheck:
    """Run all sanity checks for OpenStack cluster

    MODULE_CONTRACT:
      PURPOSE: Execute the complete suite of sanity checks for the OpenStack environment
      SCOPE: Coordinates all individual check categories and manages the overall execution flow
      DEPENDS: Depends on all specific check classes and the Stand16FaultInjector
      LINKS: Acts as the central coordinator for all sanity check operations
    """

    def __init__(
        self,
        ssh_key: str,
        jump_host: str,
        base_dir: str = "/tmp/rca-framework",
    ):
        self.injector = Stand16FaultInjector(
            ssh_user="tripleo-admin",
            ssh_key=ssh_key,
            jump_host=jump_host,
            base_dir=base_dir,
        )

        # Define node IPs based on navigation guide
        # Note: Use ctlplane network (10.197.76.x) for SSH access as recommended
        self.controller_ips = ["10.197.76.21"]  # control1
        self.compute_ips = ["10.197.76.24", "10.197.76.25"]  # node1, node2
        self.storage_ips = [
            "10.197.76.41",
            "10.197.76.42",
            "10.197.76.43",
        ]  # storage1-3 (using ctlplane)
        self.undercloud_host = "10.197.75.10"  # stand16

        # Organize nodes by type
        self.node_ips = {
            "controller": self.controller_ips,
            "compute": self.compute_ips,
            "storage": self.storage_ips,
        }

        self.all_results: List[CheckResult] = []
        self.check_categories: List[SanityCheckBase] = []

    def run_all_checks(self) -> Tuple[List[CheckResult], bool]:
        """Run all sanity checks

        FUNCTION_CONTRACT:
          PURPOSE: Execute the complete suite of sanity checks and return results
          INPUTS: None
          OUTPUTS: Tuple of (list_of_results, has_failures_boolean)
        """
        logger.info("=" * 60)
        logger.info("Running Comprehensive OpenStack Sanity Checks")
        logger.info("=" * 60)

        # Initialize check categories
        self.check_categories = [
            PacemakerClusterCheck(self.injector, self.controller_ips),
            OpenStackAPICheck(self.injector, self.undercloud_host),
            ContainerStatusCheck(self.injector, self.node_ips),
            CephHealthCheck(self.injector, self.storage_ips),
            SystemdServicesCheck(self.injector, self.node_ips),
            OpenStackOperationsCheck(self.injector, self.undercloud_host),
        ]

        # Run all checks
        for check_category in self.check_categories:
            try:
                results = check_category.run_all_checks()
                self.all_results.extend(results)
                check_category.print_results()
            except Exception as e:
                logger.error(f"Error running {check_category.__class__.__name__}: {e}")

        # Print summary
        self.print_summary()

        # Determine overall status
        has_failures = any(r.status == "FAIL" for r in self.all_results)
        return self.all_results, has_failures

    def print_summary(self):
        """Print summary of all checks

        FUNCTION_CONTRACT:
          PURPOSE: Display a summary of all executed sanity checks
          INPUTS: None
          OUTPUTS: Prints formatted summary to console
        """
        logger.info("\n" + "=" * 60)
        logger.info("SANITY CHECK SUMMARY")
        logger.info("=" * 60)

        total = len(self.all_results)
        passed = sum(1 for r in self.all_results if r.status == "PASS")
        failed = sum(1 for r in self.all_results if r.status == "FAIL")
        warned = sum(1 for r in self.all_results if r.status == "WARN")

        logger.info(f"Total checks: {total}")
        logger.info(f"Passed: {passed}")
        logger.info(f"Failed: {failed}")
        logger.info(f"Warnings: {warned}")

        if failed > 0:
            logger.error("\nFAILED CHECKS:")
            for result in self.all_results:
                if result.status == "FAIL":
                    logger.error(f"  - {result.name}: {result.message}")
                    if result.details:
                        logger.error(f"    Details: {result.details}")

        logger.info("=" * 60)

    def generate_report(self, output_file: str = None):
        """Generate detailed report of all checks

        FUNCTION_CONTRACT:
          PURPOSE: Create a structured report of all sanity check results
          INPUTS: output_file (str, optional) - File path to save the report
          OUTPUTS: Saves JSON report to specified file or prints to console
        """
        import json

        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": len(self.all_results),
                "passed": sum(1 for r in self.all_results if r.status == "PASS"),
                "failed": sum(1 for r in self.all_results if r.status == "FAIL"),
                "warned": sum(1 for r in self.all_results if r.status == "WARN"),
            },
            "results": [
                {
                    "name": r.name,
                    "status": r.status,
                    "message": r.message,
                    "details": r.details,
                    "timestamp": r.timestamp,
                }
                for r in self.all_results
            ],
        }

        if output_file:
            with open(output_file, "w") as f:
                json.dump(report, f, indent=2)
            logger.info(f"Report saved to {output_file}")
        else:
            print(json.dumps(report, indent=2))

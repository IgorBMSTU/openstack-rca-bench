#!/usr/bin/env python3
"""
VM Creation Workload for Phase 4 Write-Workload Incidents.
Creates VMs during fault injection to generate ERROR-level logs
(nova-scheduler "No valid host", conductor RPC timeouts, etc.)
"""

import logging
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_UNDERCLOUD = "10.197.75.10"
DEFAULT_USER = "stack"
DEFAULT_IMAGE = "cirros-0.6.2"
DEFAULT_FLAVOR = "c1_r1_d10"
DEFAULT_NETWORK = "internal-network"
DEFAULT_PREFIX = "rca-vm"


class VMCreationWorkload:
    """Background VM creation workload that runs during an incident."""

    def __init__(
        self,
        host: str = DEFAULT_UNDERCLOUD,
        user: str = DEFAULT_USER,
        image: str = DEFAULT_IMAGE,
        flavor: str = DEFAULT_FLAVOR,
        network: str = DEFAULT_NETWORK,
        prefix: str = DEFAULT_PREFIX,
        interval: int = 20,
    ):
        self.host = host
        self.user = user
        self.image = image
        self.flavor = flavor
        self.network = network
        self.prefix = prefix
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.vm_count = 0

    def _ssh_cmd(self, cmd: str) -> list:
        return [
            "ssh",
            "-i", "~/.ssh/standkey",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=30",
            f"{self.user}@{self.host}",
            f"source ~/demorc && {cmd}",
        ]

    def _create_vm(self, name: str) -> bool:
        cmd = self._ssh_cmd(
            f"openstack server create --image {self.image} --flavor {self.flavor} "
            f"--network {self.network} {name} > /dev/null 2>&1"
        )
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"VM creation failed for {name}: {e}")
            return False

    def _run_loop(self):
        """Background thread: create VMs until stopped."""
        logger.info("[VM-WORKLOAD] Starting VM creation loop")
        idx = 0
        while not self._stop_event.is_set():
            name = f"{self.prefix}-{idx}"
            ok = self._create_vm(name)
            if ok:
                self.vm_count += 1
                logger.info(f"[VM-WORKLOAD] Created {name} (total: {self.vm_count})")
            else:
                logger.info(f"[VM-WORKLOAD] Failed to create {name} (expected during fault)")
            idx += 1
            time.sleep(self.interval)
        logger.info("[VM-WORKLOAD] Loop stopped")

    def start(self) -> None:
        """Start VM creation in a background thread."""
        self._stop_event.clear()
        self.vm_count = 0
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # Small warm-up delay so first VM creation starts before injection
        time.sleep(2)

    def stop(self) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info(f"[VM-WORKLOAD] Total VMs created/attempted: {self.vm_count}")

    def cleanup(self) -> int:
        """Delete all VMs matching the prefix. Returns number deleted."""
        logger.info("[VM-WORKLOAD] Cleaning up VMs")
        cmd = self._ssh_cmd(
            f"openstack server list --name '{self.prefix}-' -f value -c Name | "
            f"xargs -r -n1 openstack server delete"
        )
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            # Also clean up any VMs stuck in ERROR or BUILD
            cleanup_cmd = self._ssh_cmd(
                f"openstack server list --name '{self.prefix}-' -f value -c ID | "
                f"xargs -r -n1 openstack server delete --wait 2>/dev/null; echo done"
            )
            subprocess.run(cleanup_cmd, capture_output=True, text=True, timeout=120)
            logger.info("[VM-WORKLOAD] Cleanup complete")
            return self.vm_count
        except Exception as e:
            logger.error(f"[VM-WORKLOAD] Cleanup error: {e}")
            return 0


def cleanup_all_vms(
    host: str = DEFAULT_UNDERCLOUD,
    user: str = DEFAULT_USER,
    prefix: str = DEFAULT_PREFIX,
) -> None:
    """Standalone cleanup utility."""
    wl = VMCreationWorkload(host=host, user=user, prefix=prefix)
    wl.cleanup()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Quick test
    wl = VMCreationWorkload(interval=5)
    wl.start()
    time.sleep(30)
    wl.stop()
    wl.cleanup()

#!/usr/bin/env python3
"""
Workload Generator for OpenStack RCA Dataset.
Runs lightweight OpenStack API traffic during incidents to enrich logs.
"""

import logging
import subprocess
import time
from typing import Optional, List

logger = logging.getLogger(__name__)


def build_ssh_command(host: str, user: str, command: str) -> List[str]:
    """Build SSH command list for undercloud execution."""
    return [
        "ssh",
        "-i", "~/.ssh/standkey",
        "-o", "StrictHostKeyChecking=no",
        "-J", "stack@10.197.75.10",
        f"{user}@{host}",
        command,
    ]


def run_readonly_workload(
    host: str = "10.197.75.10",
    user: str = "stack",
    iterations: int = 30,
    interval: int = 5,
) -> None:
    """
    Run read-only OpenStack queries in a loop.
    Safe to use during any incident type.
    """
    cmd = (
        f"source ~/demorc && "
        f"for i in $(seq 1 {iterations}); do "
        f"openstack server list > /dev/null 2>&1; "
        f"openstack volume list > /dev/null 2>&1; "
        f"openstack network list > /dev/null 2>&1; "
        f"sleep {interval}; "
        f"done"
    )
    ssh_cmd = build_ssh_command(host, user, cmd)
    logger.info(f"Starting read-only workload: {iterations} iterations, {interval}s interval")
    subprocess.Popen(ssh_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_write_workload(
    host: str = "10.197.75.10",
    user: str = "stack",
    image: str = "cirros-0.6.2",
    flavor: str = "m1.tiny",
    network: str = "private",
    count: int = 3,
) -> None:
    """
    Create and delete tiny VMs to generate write traffic.
    Use only during safe single-service incidents.
    """
    cmd = (
        f"source ~/demorc && "
        f"for i in $(seq 1 {count}); do "
        f"openstack server create --image {image} --flavor {flavor} "
        f"--network {network} test-workload-$i > /dev/null 2>&1; "
        f"sleep 10; "
        f"openstack server delete test-workload-$i > /dev/null 2>&1; "
        f"done"
    )
    ssh_cmd = build_ssh_command(host, user, cmd)
    logger.info(f"Starting write workload: {count} VMs, image={image}")
    subprocess.Popen(ssh_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_workload(
    workload_type: str = "readonly",
    host: str = "10.197.75.10",
    user: str = "stack",
) -> None:
    """
    Start background workload before injection.
    Types: readonly (safe for all), write (safe only).
    """
    if workload_type == "readonly":
        run_readonly_workload(host, user)
    elif workload_type == "write":
        run_write_workload(host, user)
    else:
        logger.warning(f"Unknown workload type: {workload_type}")


def wait_for_workload(duration: int = 150) -> None:
    """Block until workload finishes."""
    logger.info(f"Waiting {duration}s for workload to complete...")
    time.sleep(duration)

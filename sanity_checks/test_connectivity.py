#!/usr/bin/env python3
"""
Quick sanity check test
Tests basic connectivity without full checks
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from framework.realistic_runner import Stand16FaultInjector

def test_connectivity():
    """Test basic SSH connectivity to key nodes"""

    ssh_key = os.path.expanduser("~/.ssh/wtfkey")
    if not os.path.exists(ssh_key):
        print(f"ERROR: SSH key not found: {ssh_key}")
        return False

    injector = Stand16FaultInjector(
        ssh_user="tripleo-admin",
        ssh_key=ssh_key,
        jump_host="stack@10.197.75.10",
        base_dir="/tmp/rca-framework",
    )

    # Test nodes to check
    test_nodes = {
        "Controller (10.197.76.21)": "10.197.76.21",
        "Compute 1 (10.197.76.24)": "10.197.76.24",
        "Compute 2 (10.197.76.25)": "10.197.76.25",
        "Storage 1 (172.17.76.41)": "172.17.76.41",
        "Undercloud (10.197.75.10)": "10.197.75.10",
    }

    print("Testing SSH connectivity...")
    print("=" * 60)

    all_ok = True
    for name, ip in test_nodes.items():
        success, stdout, stderr = injector.run_ssh_command(
            ip, "hostname", timeout=10
        )

        if success and stdout.strip():
            print(f"[OK] {name} - Hostname: {stdout.strip()}")
        else:
            print(f"[FAIL] {name} - {stderr}")
            all_ok = False

    print("=" * 60)

    if all_ok:
        print("All nodes accessible!")
        return True
    else:
        print("Some nodes are not accessible")
        return False


if __name__ == "__main__":
    success = test_connectivity()
    sys.exit(0 if success else 1)

#!/usr/bin/env python3
"""
Phase 4 Write-Workload Incident Runner.

Wraps SingleIncidentOrchestrator but replaces the read-only workload
with VM creation workload to generate strong ERROR signals for
compute-node and controller-backend incidents.

Usage:
    python3 orchestrator/run_write_incident.py \
        --scenario nova-compute-stop --target 10.197.76.24 \
        --duration 60 --query '{job="systemd-journal"}'
"""

import argparse
import json
import gzip
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.single_incident_orchestrator import (
    SingleIncidentOrchestrator,
    logger,
)
from orchestrator.vm_workload import VMCreationWorkload


def main():
    parser = argparse.ArgumentParser(description="Run Phase 4 write-workload incident")
    parser.add_argument("--scenario", required=True, help="Fault scenario (e.g., nova-compute-stop)")
    parser.add_argument("--target", required=True, help="Target host IP")
    parser.add_argument("--duration", type=int, default=60, help="Fault hold duration in seconds")
    parser.add_argument("--query", default='{job="systemd-journal"}', help="Loki query")
    parser.add_argument("--loki-url", default="http://10.197.76.10:3100", help="Loki endpoint")
    parser.add_argument(
        "--base-dir",
        default=str(Path(__file__).parent.parent / "rca-framework"),
        help="Base directory for incidents",
    )
    parser.add_argument("--vm-interval", type=int, default=20, help="Seconds between VM creation attempts")
    parser.add_argument("--vm-prefix", default="rca-phase4-vm", help="VM name prefix")
    args = parser.parse_args()

    # Initialize orchestrator (uses same base dir for consistency)
    orch = SingleIncidentOrchestrator(base_dir=args.base_dir)

    # Generate incident ID
    incident_id = orch.generate_id()
    incident_dir = orch.incidents_dir / incident_id
    incident_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        f"\n{'='*60}\n"
        f"[PHASE-4 WRITE WORKLOAD] {incident_id} | {args.scenario} on {args.target} for {args.duration}s\n"
        f"{'='*60}"
    )

    # 1. Pre-sanity
    pre_passed, _ = orch.run_pre_sanity(incident_dir / "pre_sanity.json")
    if not pre_passed:
        logger.error("Pre-sanity FAILED — aborting")
        return

    # 2. Start VM creation workload
    vm_workload = VMCreationWorkload(
        interval=args.vm_interval,
        prefix=args.vm_prefix,
    )
    vm_workload.start()

    # 3. Install watchdog
    service = args.scenario.replace("-stop", "")
    watchdog_pid = orch.install_watchdog(args.target, service, args.duration)

    # 4-6. Inject → Hold → Rollback
    injection_start = datetime.now(timezone.utc)
    injection_result = orch.inject(args.scenario, args.target)

    if injection_result["status"] != "success":
        logger.error("Injection failed — waiting for watchdog")
        vm_workload.stop()
        vm_workload.cleanup()
        time.sleep(args.duration + 130)
        return

    orch.hold(args.duration)
    injection_end = datetime.now(timezone.utc)
    recovery_result = orch.rollback(args.scenario, args.target)

    # Remove watchdog
    if watchdog_pid:
        from orchestrator.single_incident_orchestrator import WatchdogManager
        WatchdogManager.remove(args.target, orch.injector, watchdog_pid)

    # 6. Stop workload and cleanup VMs
    vm_workload.stop()
    time.sleep(5)  # let any pending creates finish
    vm_workload.cleanup()

    # 7. Post-sanity
    post_passed, _ = orch.run_post_sanity(incident_dir / "post_sanity.json")
    if not post_passed:
        logger.error("Post-sanity FAILED")

    # 8. Export logs
    log_meta = orch.export_logs(
        incident_id, incident_dir, injection_start, injection_end, args.query
    )

    # 9. Validate
    with gzip.open(log_meta["raw_log_file"], "rt") as f:
        log_payload = json.load(f)
    validation = orch.validate_logs(log_payload.get("logs", []), args.scenario)

    # 10. Save metadata with workload annotation
    metadata = orch.save_metadata(
        incident_dir=incident_dir,
        incident_id=incident_id,
        scenario=args.scenario,
        target=args.target,
        duration=args.duration,
        injection_result=injection_result,
        recovery_result=recovery_result,
        log_meta=log_meta,
        validation=validation,
        pre_sanity_passed=pre_passed,
        post_sanity_passed=post_passed,
        watchdog_pid=watchdog_pid,
    )
    # Annotate workload type
    metadata["workload"] = {
        "type": "vm_creation",
        "vm_prefix": args.vm_prefix,
        "vm_interval_seconds": args.vm_interval,
        "vms_created": vm_workload.vm_count,
    }
    with open(incident_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(
        f"\n{'='*60}\n"
        f"[PHASE-4] {incident_id} COMPLETED | VMs created: {vm_workload.vm_count} | "
        f"Validation: {validation['match_rate']:.0%}\n"
        f"{'='*60}"
    )

    # 11. Cooldown
    logger.info("Cooldown 300s for stabilization...")
    time.sleep(300)
    logger.info("Cooldown complete")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    main()

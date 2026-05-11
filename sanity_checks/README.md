# OpenStack Sanity Checks

Comprehensive health checks for TripleO-deployed OpenStack clusters.

## Purpose

This module performs comprehensive health checks on an OpenStack cloud deployed via TripleO to verify that all services are functioning correctly. It is designed to be run before and after incident injection experiments to ensure cloud health.

## Features

The sanity checks verify the following components:

1. **Pacemaker Cluster Status** - Check cluster health, resources, and failed actions on controllers
2. **OpenStack API Services** - Verify OpenStack service list, hypervisors, network agents, volume services, images, and flavors
3. **Container Status** - Check container health, exited containers, and critical containers on all nodes
4. **Ceph Health** - Monitor Ceph cluster health, OSD status, and orchestrator services
5. **Systemd Services** - Verify TripleO systemd services are running without failures
6. **OpenStack Operations** - Test basic OpenStack operations (network list, quota usage, server list)

## Usage

### Basic Usage

```bash
python sanity_checks/run_sanity_checks.py
```

### With Custom Parameters

```bash
python sanity_checks/run_sanity_checks.py \
  --ssh-key ~/.ssh/standkey \
  --jump-host stack@10.197.75.10 \
  --report sanity_report.json \
  --verbose
```

### Command-Line Options

- `--ssh-key PATH` - SSH key path (default: ~/.ssh/standkey)
- `--jump-host HOST` - Jump host (default: stack@10.197.75.10)
- `--base-dir PATH` - Base directory for operations (default: /tmp/rca-framework)
- `--report FILE` - Output file for JSON report
- `--verbose` - Enable verbose logging

## Exit Codes

- **0** - All sanity checks passed (no failures)
- **1** - Some sanity checks failed (services not working)
- **130** - Interrupted by user

## Check Categories

### Pacemaker Cluster Check
- Overall cluster health
- Resource status
- Failed resource actions (with filtering for non-critical issues)
- Node status

### OpenStack API Check
- Service list and enabled status
- Hypervisor availability and state
- Network agent liveness and state
- Volume service status and health
- Image availability
- Flavor availability

### Container Status Check
- Overall container running status
- Exited containers identification
- Critical containers verification by node type:
  - Controllers: haproxy, galera, rabbitmq, redis, nova-api, neutron-api, glance-api, cinder-api
  - Compute: nova-compute, neutron-ovn-agent, nova-libvirt
  - Storage: ceph-mon, ceph-osd

### Ceph Health Check
- Overall cluster health (HEALTH_OK/WARN/ERR)
- OSD status and availability
- Orchestrator service status

### Systemd Services Check
- Failed TripleO systemd services identification

### OpenStack Operations Check
- Network list operation
- Quota information retrieval
- Server list operation

## Known Limitations

1. **Container Health Checks** - Some containers may show unhealthy status despite functioning correctly (e.g., ovn_cluster_northd, nova_metadata, nova_scheduler). These are filtered out and reported as warnings rather than failures.

2. **RabbitMQ Monitor Timeouts** - In development environments, RabbitMQ may show monitor timeouts that are non-critical. These are filtered out as warnings.

3. **Network Connectivity** - The script assumes network connectivity to all nodes. If a node is unreachable, the check will fail.

## Integration with Incident Runner

Before running incident experiments:

```bash
# Run sanity checks to verify cloud health
python sanity_checks/run_sanity_checks.py --report pre_incident_sanity.json

# If exit code is 0, proceed with incident injection
python run_dataset_generation.py
```

After incident recovery:

```bash
# Run sanity checks to verify cloud recovery
python sanity_checks/run_sanity_checks.py --report post_incident_sanity.json

# Verify exit code is 0 before running next incident
```

## Example Output

```
2026-04-21 09:34:58 - INFO - ============================================================
2026-04-21 09:34:58 - INFO - Running Comprehensive OpenStack Sanity Checks
2026-04-21 09:34:58 - INFO - ============================================================
2026-04-21 09:34:58 - INFO - Running Pacemaker cluster checks...
[PASS] Pacemaker Cluster Status (10.197.76.21): Cluster is online
[PASS] Pacemaker Resources (10.197.76.21): All resources running
[PASS] Failed Resource Actions (10.197.76.21): No critical failed actions
[PASS] Pacemaker Nodes (10.197.76.21): Online nodes: [10.197.76.21]
...

============================================================
SANITY CHECK SUMMARY
============================================================
Total checks: 42
Passed: 40
Failed: 0
Warnings: 2
============================================================

============================================================
ALL CHECKS COMPLETED
============================================================
List of all checks performed:
   1. [✓] Pacemaker Cluster Status (10.197.76.21): Cluster is online
   2. [✓] Pacemaker Resources (10.197.76.21): All resources running
...

============================================================
SANITY CHECKS PASSED - All critical services are working
Exit code: 0
```

## Troubleshooting

### SSH Connection Issues
- Verify SSH key permissions: `chmod 600 ~/.ssh/standkey`
- Check jump host connectivity: `ssh stack@10.197.75.10`
- Verify node IPs are accessible from jump host

### OpenStack CLI Failures
- Verify demorc file exists on undercloud: `~/demorc`
- Check OpenStack credentials are valid
- Verify OpenStack API endpoints are reachable

### Container Check Failures
- Check if containers are actually running: `sudo podman ps`
- Review container logs: `sudo podman logs <container_id>`
- Restart failed containers if needed

### Pacemaker Issues
- Check cluster status manually: `sudo pcs status`
- Review failed resource actions: `sudo pcs status --full`
- Check Corosync logs: `sudo journalctl -u corosync`

## Architecture

The sanity checks are built using a modular architecture:

- `SanityCheckBase` - Base class for all check categories
- `PacemakerClusterCheck` - Pacemaker cluster health verification
- `OpenStackAPICheck` - OpenStack API services verification
- `ContainerStatusCheck` - Container health and status
- `CephHealthCheck` - Ceph cluster monitoring
- `SystemdServicesCheck` - Systemd service verification
- `OpenStackOperationsCheck` - Basic OpenStack operations testing
- `ComprehensiveSanityCheck` - Orchestrates all checks

## Contributing

When adding new sanity checks:

1. Create a new class inheriting from `SanityCheckBase`
2. Implement `run_all_checks()` method
3. Add checks using `add_result()` method
4. Register the new check in `ComprehensiveSanityCheck.__init__()`

## License

Part of OpenStack-RCA-Bench project.

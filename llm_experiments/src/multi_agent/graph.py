"""Static OpenStack service dependency graph.

Used by the Topology Reasoner agent for backward-tracing.
Derived from TripleO Wallaby architecture with Pacemaker bundles.
"""

# Directed graph: service → [downstream dependencies]
# If service X depends on Y, then X → Y (Y must be healthy for X to work)
DEPENDENCY_GRAPH: dict[str, list[str]] = {
    # Compute
    "nova-compute": ["nova-conductor", "neutron-openvswitch-agent", "libvirt"],
    "nova-api": ["nova-conductor", "nova-scheduler", "keystone", "placement-api", "glance-api"],
    "nova-conductor": ["rabbitmq", "mysql"],
    "nova-scheduler": ["rabbitmq", "mysql"],
    "nova-metadata": ["nova-api", "neutron-metadata-agent"],
    "nova-vnc-proxy": ["nova-api"],
    "nova-novncproxy": ["nova-api"],

    # Network / Neutron
    "neutron-api": ["neutron-server", "keystone", "mysql", "rabbitmq"],
    "neutron-server": ["mysql", "rabbitmq", "ovn-northd"],
    "neutron-dhcp": ["neutron-server", "ovn-controller"],
    "neutron-l3-agent": ["neutron-server", "ovn-controller"],
    "neutron-openvswitch-agent": ["ovn-controller"],
    "neutron-metadata-agent": ["neutron-server", "nova-api"],

    # OVN (SDN)
    "ovn-northd": ["ovn-north-db", "ovn-south-db"],
    "ovn-controller": ["ovn-south-db"],
    "ovn-north-db": [],
    "ovn-south-db": [],
    "ovn-metadata-agent": ["neutron-server", "nova-api"],

    # Storage / Cinder
    "cinder-api": ["cinder-scheduler", "cinder-volume", "keystone", "mysql", "rabbitmq"],
    "cinder-scheduler": ["mysql", "rabbitmq"],
    "cinder-volume": ["mysql", "rabbitmq", "ceph-mon"],
    "cinder-backup": ["mysql", "ceph-mon"],

    # Ceph
    "ceph-mon": [],
    "ceph-mgr": ["ceph-mon"],
    "ceph-osd": ["ceph-mon", "ceph-mgr"],
    "ceph-rgw": ["ceph-mon"],

    # Image
    "glance-api": ["glance-api-internal", "keystone", "mysql"],
    "glance-api-internal": ["mysql"],
    "glance-registry": ["mysql"],

    # Identity
    "keystone": ["mysql"],

    # Orchestration
    "heat-api": ["heat-engine", "keystone", "mysql"],
    "heat-api-cfn": ["heat-engine", "keystone"],
    "heat-engine": ["mysql", "rabbitmq"],
    "placement-api": ["keystone", "mysql"],

    # DB / MQ / Cache
    "mysql": [],
    "mariadb": [],
    "galera": [],
    "rabbitmq": [],
    "redis": [],

    # Infrastructure
    "haproxy": ["keystone"],
    "memcached": [],
    "keepalived": [],
    "iscsid": [],

    # Monitoring / Dashboard
    "grafana": ["prometheus"],
    "prometheus": [],
    "skyline-apiserver": ["keystone"],
    "skyline-console": ["skyline-apiserver"],

    # Baremetal
    "ironic-api": ["ironic-conductor", "keystone", "mysql"],
    "ironic-conductor": ["mysql", "rabbitmq"],
    "ironic-neutron-agent": ["neutron-server"],

    # Object storage
    "swift-proxy": ["keystone"],
    "swift-account": ["swift-proxy"],
    "swift-container": ["swift-proxy"],
    "swift-object": ["swift-proxy"],
}


def get_upstream_dependencies(service: str) -> list[str]:
    """Return services that depend on this service.

    If X → [Y], then X depends on Y. This returns all X for which Y is a dependency.
    """
    upstream: list[str] = []
    for svc, deps in DEPENDENCY_GRAPH.items():
        if service in deps:
            upstream.append(svc)
    return upstream


def get_downstream_dependencies(service: str) -> list[str]:
    """Return services that this service depends on."""
    return DEPENDENCY_GRAPH.get(service, [])


def find_root_causes(failing_services: list[str]) -> list[str]:
    """Given a set of failing services, find candidate root causes.

    A service is a candidate root cause if:
    1. It is in the failing set
    2. None of its upstream dependencies are in the failing set
    (i.e., the failure propagated FROM this service, not TO it)

    Args:
        failing_services: List of services that show errors.

    Returns:
        List of candidate root cause services (may be empty or have multiple).
    """
    failing_set = set(failing_services)
    candidates: list[str] = []

    for svc in failing_set:
        upstream = get_upstream_dependencies(svc)
        upstream_failing = [u for u in upstream if u in failing_set]
        if not upstream_failing:
            # No upstream dependency is also failing → this may be the root
            candidates.append(svc)

    return candidates if candidates else list(failing_set)


def format_graph_for_prompt(failing_services: list[str]) -> str:
    """Format a relevant subset of the dependency graph for the LLM prompt."""
    failing_set = set(failing_services)
    relevant_services: set[str] = set()

    for svc in failing_set:
        relevant_services.add(svc)
        for dep in get_downstream_dependencies(svc):
            relevant_services.add(dep)
        for up in get_upstream_dependencies(svc):
            relevant_services.add(up)

    lines: list[str] = []
    lines.append("Key dependency chains (X → Y means X depends on Y):")
    for svc in sorted(relevant_services):
        deps = get_downstream_dependencies(svc)
        if deps:
            lines.append(f"  {svc} → {', '.join(deps)}")
        else:
            lines.append(f"  {svc} → [leaf service]")

    candidates = find_root_causes(failing_services)
    lines.append(f"\nFailing services: {', '.join(sorted(failing_set))}")
    lines.append(f"Candidate root causes (no failing upstream): {', '.join(candidates) if candidates else 'none found'}")

    return "\n".join(lines)

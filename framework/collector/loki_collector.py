#!/usr/bin/env python3
"""
OpenStack-RCA-Bench: Log Collector via Loki PromQL
Module: loki_collector
Purpose: Query Loki for logs during incidents with time-based filtering

<MODULE_CONTRACT>
Name: loki_collector
Purpose: Collect logs from Loki using PromQL queries for incident time ranges
Inputs:
  - incident_id: str - Incident identifier
  - start_time: str - ISO8601 start timestamp
  - end_time: str - ISO8601 end timestamp
  - loki_url: str - Loki API endpoint
Outputs:
  - logs_before: list - Logs before incident
  - logs_during: list - Logs during incident
  - logs_after: list - Logs after incident
  - metadata: dict - Log statistics and counts
Dependencies: requests, datetime
</MODULE_CONTRACT>
"""

import requests
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class LokiLogCollector:
    """Query Loki for incident logs using PromQL."""

    def __init__(
        self, loki_url: str = "http://eng-101-aio-ceph-16-undercloud.ctlplane:3100"
    ):
        self.loki_url = loki_url
        self.session = requests.Session()
        self.session.timeout = 30

    def query_logs(
        self, query: str, start_time: datetime, end_time: datetime, limit: int = 10000
    ) -> Dict:
        """
        Query Loki with PromQL-like syntax.

        Args:
            query: LogQL query (e.g., '{job="neutron"} |= "error"')
            start_time: Start timestamp
            end_time: End timestamp
            limit: Maximum number of log entries

        Returns:
            Dict with 'data' and 'stats'
        """
        # Convert to nanoseconds (Loki uses ns)
        start_ns = int(start_time.timestamp() * 1e9)
        end_ns = int(end_time.timestamp() * 1e9)

        # Build query URL
        url = f"{self.loki_url}/loki/api/v1/query_range"

        params = {
            "query": query,
            "start": start_ns,
            "end": end_ns,
            "limit": limit,
            "direction": "BACKWARD",  # Most recent first
            "time": start_ns,  # Use specific time
        }

        logger.info(f"[LOKI] Querying: {query[:100]}...")
        logger.debug(f"[LOKI] Time range: {start_time} to {end_time}")
        logger.debug(f"[LOKI] URL: {url}")
        logger.debug(f"[LOKI] Params: {params}")

        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()

            result = response.json()

            # Extract stream data
            if "data" in result and "result" in result["data"]:
                streams = result["data"]["result"]
                logger.info(
                    f"[LOKI] Retrieved {len(streams)} streams, "
                    f"{sum(len(s.get('values', [])) for s in streams)} entries"
                )

                return {
                    "streams": streams,
                    "stats": result.get("data", {}).get("stats", {}),
                    "status": "success",
                }
            else:
                logger.warning(f"[LOKI] No data returned")
                return {"streams": [], "stats": {}, "status": "no_data"}

        except requests.exceptions.RequestException as e:
            logger.error(f"[LOKI] Query failed: {e}")
            return {"streams": [], "stats": {}, "status": "error", "error": str(e)}

    def collect_incident_logs(
        self,
        incident_id: str,
        injection_time: datetime,
        duration_seconds: int,
        services: List[str] = None,
    ) -> Dict:
        """
        Collect logs for an incident in three phases.

        Phases:
        1. Before: 5 minutes before injection
        2. During: During fault duration
        3. After: 5 minutes after recovery

        Args:
            incident_id: Incident identifier
            injection_time: When fault was injected
            duration_seconds: How long fault lasted
            services: List of services to query (all if None)

        Returns:
            Dict with logs_before, logs_during, logs_after, metadata
        """
        logger.info(f"[COLLECT] Collecting logs for {incident_id}")

        # Define time ranges
        before_start = injection_time - timedelta(minutes=5)
        before_end = injection_time

        during_start = injection_time
        during_end = injection_time + timedelta(seconds=duration_seconds)

        after_start = during_end
        after_end = after_start + timedelta(minutes=5)

        # Define queries for different log types
        queries = {
            "neutron": '{job="neutron"}',
            "mysql": '{job="mysql"}',
            "rabbitmq": '{job="rabbitmq"}',
            "redis": '{job="redis"}',
            "ironic": '{job="ironic"}',
            "system": '{job="system-messages"}',
        }

        # If specific services requested, filter queries
        if services:
            queries = {k: v for k, v in queries.items() if k in services}

        # Collect logs for each phase
        logs_before = []
        logs_during = []
        logs_after = []

        for service_name, query in queries.items():
            logger.info(f"[COLLECT] Querying {service_name} logs...")

            # Before incident
            result = self.query_logs(query, before_start, before_end)
            if result["status"] == "success":
                logs_before.extend(result["streams"])

            # During incident
            result = self.query_logs(query, during_start, during_end)
            if result["status"] == "success":
                logs_during.extend(result["streams"])

            # After incident
            result = self.query_logs(query, after_start, after_end)
            if result["status"] == "success":
                logs_after.extend(result["streams"])

        # Generate metadata
        metadata = {
            "incident_id": incident_id,
            "log_collection_time": datetime.now().isoformat(),
            "time_ranges": {
                "before": {
                    "start": before_start.isoformat(),
                    "end": before_end.isoformat(),
                    "duration_seconds": 300,
                },
                "during": {
                    "start": during_start.isoformat(),
                    "end": during_end.isoformat(),
                    "duration_seconds": duration_seconds,
                },
                "after": {
                    "start": after_start.isoformat(),
                    "end": after_end.isoformat(),
                    "duration_seconds": 300,
                },
            },
            "services_queried": list(queries.keys()),
            "stats": {
                "before_count": sum(len(s.get("values", [])) for s in logs_before),
                "during_count": sum(len(s.get("values", [])) for s in logs_during),
                "after_count": sum(len(s.get("values", [])) for s in logs_after),
                "total_entries": sum(
                    len(s.get("values", []))
                    for s in logs_before + logs_during + logs_after
                ),
            },
        }

        logger.info(
            f"[COLLECT] Collected {metadata['stats']['during_count']} entries during incident"
        )
        logger.info(f"[COLLECT] Total: {metadata['stats']['total_entries']} entries")

        return {
            "logs_before": logs_before,
            "logs_during": logs_during,
            "logs_after": logs_after,
            "metadata": metadata,
        }

    def save_logs_to_file(self, incident_data: Dict, output_dir: str):
        """
        Save collected logs to JSON files.

        Files:
        - logs_before.json
        - logs_during.json
        - logs_after.json
        - collection_metadata.json
        """
        import json
        from pathlib import Path

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        incident_id = incident_data["metadata"]["incident_id"]

        # Save logs
        for phase in ["logs_before", "logs_during", "logs_after"]:
            filename = output_path / f"{phase}.json"

            with open(filename, "w") as f:
                json.dump(incident_data[phase], f, indent=2)

            logger.info(f"[SAVE] {filename}: {len(incident_data[phase])} streams")

        # Save metadata
        metadata_file = output_path / "collection_metadata.json"
        with open(metadata_file, "w") as f:
            json.dump(incident_data["metadata"], f, indent=2)

        logger.info(f"[SAVE] {metadata_file}")

        # Calculate file sizes
        total_size = sum(
            (output_path / f"{p}.json").stat().st_size
            for p in ["logs_before", "logs_during", "logs_after", "collection_metadata"]
        )

        logger.info(f"[SAVE] Total log size: {total_size / 1024:.2f} KB")


class LokiQueryExamples:
    """Example PromQL queries for incident analysis."""

    @staticmethod
    def error_logs(service: str):
        """Get error-level logs for service."""
        return f'{{job="{service}"}} |= "error" or |= "ERROR" or |= "failed"'

    @staticmethod
    def container_stopped(service: str):
        """Get logs indicating container stopped."""
        return (
            f'{{job="{service}"}} |= "stopped" or |= "stopped\\"" or |= "shutting down"'
        )

    @staticmethod
    def container_started(service: str):
        """Get logs indicating container started."""
        return f'{{job="{service}"}} |= "started" or |= "started\\"" or |= "ready to accept"'

    @staticmethod
    def time_range_query(service: str, minutes: int = 5):
        """Get logs for specific time range."""
        return f'{{job="{service}"}} | line_format "{{{{.timestamp}}}}" | unwrap | timestamp() > {minutes * 60}s'


def main():
    """Test Loki log collector."""
    import argparse
    from datetime import datetime, timedelta

    parser = argparse.ArgumentParser(description="Collect logs from Loki for incidents")
    parser.add_argument("--incident-id", required=True, help="Incident ID")
    parser.add_argument(
        "--injection-time", required=True, help="Injection time (ISO8601 format)"
    )
    parser.add_argument(
        "--duration", type=int, default=60, help="Incident duration in seconds"
    )
    parser.add_argument(
        "--loki-url",
        default="http://eng-101-aio-ceph-16-undercloud.ctlplane:3100",
        help="Loki API endpoint",
    )
    parser.add_argument(
        "--services", nargs="+", help="Services to query (default: all)"
    )

    args = parser.parse_args()

    # Parse injection time
    injection_time = datetime.fromisoformat(args.injection_time)

    # Create collector
    collector = LokiLogCollector(loki_url=args.loki_url)

    # Collect logs
    incident_data = collector.collect_incident_logs(
        incident_id=args.incident_id,
        injection_time=injection_time,
        duration_seconds=args.duration,
        services=args.services,
    )

    # Save to file
    incident_dir = f"incidents/{args.incident_id}"
    collector.save_logs_to_file(incident_data, incident_dir)

    print(f"\n=== Log Collection Complete ===")
    print(f"Incident: {args.incident_id}")
    print(
        f"During incident logs: {incident_data['metadata']['stats']['during_count']} entries"
    )
    print(f"Saved to: {incident_dir}/")


if __name__ == "__main__":
    main()

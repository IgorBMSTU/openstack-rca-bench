#!/usr/bin/env python3
"""
OpenStack Sanity Checks Runner
Runs comprehensive health checks for TripleO-deployed OpenStack clusters
"""

import sys
import os
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sanity_checks.sanity_checks import ComprehensiveSanityCheck

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Run OpenStack sanity checks for TripleO-deployed clouds"
    )
    parser.add_argument(
        "--ssh-key",
        type=str,
        default=os.path.expanduser("~/.ssh/standkey"),
        help="SSH key path (default: ~/.ssh/standkey)",
    )
    parser.add_argument(
        "--jump-host",
        type=str,
        default="stack@10.197.75.10",
        help="Jump host (default: stack@10.197.75.10)",
    )
    parser.add_argument(
        "--base-dir",
        type=str,
        default="/tmp/rca-framework",
        help="Base directory for operations (default: /tmp/rca-framework)",
    )
    parser.add_argument(
        "--report",
        type=str,
        help="Output file for JSON report",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Check SSH key exists
    if not os.path.exists(args.ssh_key):
        logger.error(f"SSH key not found: {args.ssh_key}")
        sys.exit(1)

    try:
        # Initialize sanity check runner
        runner = ComprehensiveSanityCheck(
            ssh_key=args.ssh_key,
            jump_host=args.jump_host,
            base_dir=args.base_dir,
        )

        # Run all checks
        results, has_failures = runner.run_all_checks()

        # Generate report if requested
        if args.report:
            runner.generate_report(args.report)

        # Print final list of all checks
        logger.info("\n" + "=" * 60)
        logger.info("ALL CHECKS COMPLETED")
        logger.info("=" * 60)
        logger.info("List of all checks performed:")
        for i, result in enumerate(results, 1):
            status_symbol = {
                "PASS": "✓",
                "FAIL": "✗",
                "WARN": "⚠",
            }.get(result.status, "?")
            logger.info(f"  {i:2d}. [{status_symbol}] {result.name}: {result.message}")

        logger.info("\n" + "=" * 60)
        if has_failures:
            logger.error("SANITY CHECKS FAILED - Some services are not working")
            logger.error("Please review failed checks above")
            sys.exit(1)
        else:
            logger.info("SANITY CHECKS PASSED - All critical services are working")
            logger.info("Exit code: 0")
            sys.exit(0)

    except KeyboardInterrupt:
        logger.warning("\nSanity checks interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

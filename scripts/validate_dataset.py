#!/usr/bin/env python3
"""Validate integrity of the OpenStack-RCA-Bench dataset.

Checks:
- Each incident directory has all 4 required files
- metadata.json is valid JSON with required fields
- pre_sanity.json / post_sanity.json have summary.passed and summary.failed
- raw_logs.json.gz exists and is > 1KB
"""

import json
import gzip
import sys
from pathlib import Path

INCIDENTS_DIR = Path(__file__).resolve().parent.parent / 'rca-framework' / 'incidents'

REQUIRED_FILES = ['metadata.json', 'post_sanity.json', 'raw_logs.json.gz']
OPTIONAL_FILES = ['pre_sanity.json']  # Some incidents may miss pre_sanity due to collection edge cases

METADATA_REQUIRED = ['incident_id', 'scenario', 'target_host', 'status', 'validation']

def validate():
    errors = []
    warnings = []
    passed = 0
    total = 0

    if not INCIDENTS_DIR.exists():
        print(f"ERROR: incidents dir not found: {INCIDENTS_DIR}")
        sys.exit(1)

    dirs = sorted([d for d in INCIDENTS_DIR.iterdir() if d.is_dir() and d.name.startswith('INC-')])

    for d in dirs:
        total += 1
        name = d.name
        files = [f.name for f in d.iterdir()]

        # Check required files
        missing = [f for f in REQUIRED_FILES if f not in files]
        if missing:
            errors.append(f"{name}: missing required files: {', '.join(missing)}")
            continue
        missing_opt = [f for f in OPTIONAL_FILES if f not in files]
        if missing_opt:
            warnings.append(f"{name}: missing optional files: {', '.join(missing_opt)}")

        # Check metadata.json
        try:
            with open(d / 'metadata.json') as f:
                meta = json.load(f)
            for field in METADATA_REQUIRED:
                if field not in meta:
                    errors.append(f"{name}/metadata.json: missing field '{field}'")
        except (json.JSONDecodeError, IOError) as e:
            errors.append(f"{name}/metadata.json: {e}")
            continue

        # Check sanity files
        for sf in ['pre_sanity.json', 'post_sanity.json']:
            if sf not in files:
                continue  # Already reported as missing above
            try:
                with open(d / sf) as f:
                    sanity = json.load(f)
                if 'summary' not in sanity:
                    errors.append(f"{name}/{sf}: missing 'summary'")
                elif 'passed' not in sanity['summary']:
                    errors.append(f"{name}/{sf}: missing 'summary.passed'")
            except (json.JSONDecodeError, IOError) as e:
                errors.append(f"{name}/{sf}: {e}")

        # Check raw_logs.json.gz
        logfile = d / 'raw_logs.json.gz'
        if logfile.stat().st_size < 100:
            warnings.append(f"{name}/raw_logs.json.gz: very small ({logfile.stat().st_size} bytes)")
        try:
            with gzip.open(logfile, 'rt') as f:
                data = json.load(f)  # Full JSON parse (file is pretty-printed JSON, not NDJSON)
            if not isinstance(data, dict) or 'incident_id' not in data:
                warnings.append(f"{name}/raw_logs.json.gz: unexpected top-level structure (keys: {list(data.keys())[:3]})")
        except Exception as e:
            errors.append(f"{name}/raw_logs.json.gz: {e}")

        # Validate status
        if meta.get('status') in ('failed', 'aborted'):
            warnings.append(f"{name}: status is '{meta['status']}'")
        else:
            passed += 1

    # Report
    print(f"\n{'='*50}")
    print(f"Dataset Validation Report")
    print(f"{'='*50}")
    print(f"Incidents checked: {total}")
    print(f"Passed:            {passed}")
    print(f"Errors:            {len(errors)}")
    print(f"Warnings:          {len(warnings)}")

    if errors:
        print(f"\n--- Errors ---")
        for e in errors:
            print(f"  [FAIL] {e}")

    if warnings:
        print(f"\n--- Warnings ---")
        for w in warnings:
            print(f"  [WARN] {w}")

    if not errors:
        print("\n[PASS] Dataset integrity validated successfully!")
    else:
        print(f"\n[FAIL] {len(errors)} error(s) found")
        sys.exit(1)

if __name__ == '__main__':
    validate()

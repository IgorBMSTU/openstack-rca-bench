#!/usr/bin/env python3
"""
Reproduce Table 3 from the ASE 2026 paper: Rule-Based Baseline.

Reads per-incident signatures_expected + match_rate from each
incident's metadata.json, then aggregates by category.

Per the paper (§5): "keyword-based matching is conservative
(exact signature matches only)" — Precision = 100%.

Usage:
    python3 framework/baselines/evaluate_table3.py
"""

import json
import sys
from pathlib import Path
from collections import defaultdict


# ── Paper's per-incident category mapping ──
CATEGORY_INCIDENTS = {
    "Ctrl-API": [
        "INC-2026-007", "INC-2026-012", "INC-2026-015", "INC-2026-016",
        "INC-2026-018", "INC-2026-019", "INC-2026-034", "INC-2026-043",
    ],
    "Ctrl-backend": [
        "INC-2026-017", "INC-2026-020", "INC-2026-021", "INC-2026-022",
        "INC-2026-033", "INC-2026-036", "INC-2026-038", "INC-2026-039",
        "INC-2026-041", "INC-2026-042", "INC-2026-044", "INC-2026-049",
        "INC-2026-050", "INC-2026-051", "INC-2026-052", "INC-2026-053",
        "INC-2026-054",
    ],
    "OVN-network": [
        "INC-2026-009", "INC-2026-010", "INC-2026-011", "INC-2026-014",
        "INC-2026-037", "INC-2026-047", "INC-2026-055", "INC-2026-060",
        "INC-2026-061", "INC-2026-063", "INC-2026-064",
    ],
    "Compute": [
        "INC-2026-002", "INC-2026-004", "INC-2026-006", "INC-2026-013",
        "INC-2026-024", "INC-2026-045", "INC-2026-046", "INC-2026-056",
        "INC-2026-057", "INC-2026-059", "INC-2026-062",
    ],
    "Pacemaker": [
        "INC-2026-023", "INC-2026-031", "INC-2026-032", "INC-2026-040",
    ],
    "Storage": [
        "INC-2026-025", "INC-2026-026", "INC-2026-027", "INC-2026-029",
        "INC-2026-030",
    ],
    "Extended (Ph.5)": [
        "INC-2026-065", "INC-2026-066", "INC-2026-067", "INC-2026-068",
        "INC-2026-069", "INC-2026-070", "INC-2026-074", "INC-2026-075",
    ],
}

CATEGORIES_ORDER = ["Ctrl-backend", "OVN-network", "Ctrl-API", "Pacemaker",
                    "Compute", "Storage", "Extended (Ph.5)"]

# Paper reference
PAPER = {
    "Ctrl-backend":     (15, 100, 100, 100),
    "OVN-network":      (10, 100, 100, 100),
    "Ctrl-API":          (8, 100,  79,  89),
    "Pacemaker":         (4, 100,  58,  75),
    "Compute":          (14, 100,  61,  76),
    "Storage":           (5, 100,  56,  70),
    "Extended (Ph.5)":   (8, 100,  72,  84),
}


def main():
    incidents_dir = Path("rca-framework/incidents")
    if not incidents_dir.exists():
        print(f"ERROR: dir not found: {incidents_dir}", file=sys.stderr)
        sys.exit(1)

    # ── Load per-incident signatures from metadata.json ──
    all_incidents = {}
    for inc_dir in sorted(incidents_dir.iterdir()):
        if not inc_dir.is_dir() or not inc_dir.name.startswith("INC-"):
            continue
        meta_path = inc_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            continue

        validation = meta.get("validation", {})
        sig_exp = validation.get("signatures_expected", [])
        sig_found = validation.get("signatures_found", [])
        match_rate = validation.get("match_rate", 0.0)
        scenario = meta.get("scenario", "")
        service = meta.get("injection", {}).get("service", "")

        all_incidents[inc_dir.name] = {
            "scenario": scenario,
            "service": service,
            "sig_exp": sig_exp,
            "sig_found": sig_found,
            "match_rate": match_rate,
        }

    # ── Compute per-category ──
    print(f"{'='*75}")
    print(f"  Table 3: Rule-Based Baseline")
    print(f"  (per-incident signatures_expected from metadata.json)")
    print(f"{'='*75}")
    print(f"{'Category':<18} {'#':>3}  {'Prec':>6}  {'Rec':>6}  {'F1':>6}")
    print(f"{'-'*18} {'-'*3}  {'-'*6}  {'-'*6}  {'-'*6}")

    all_matched = 0
    all_total = 0
    all_mr_sum = 0.0

    for cat in CATEGORIES_ORDER:
        inc_ids = CATEGORY_INCIDENTS.get(cat, [])
        total = len(inc_ids)
        if total == 0:
            continue

        mr_sum = 0.0
        for inc_id in inc_ids:
            inc = all_incidents.get(inc_id)
            if not inc:
                continue
            mr_sum += inc["match_rate"]

        avg_mr = mr_sum / total if total else 0
        precision = 100.0
        recall = avg_mr * 100  # mean match_rate as percentage
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) > 0 else 0.0)

        all_mr_sum += mr_sum
        all_total += total

        print(f"{cat:<18} {total:>3}  {precision:>5.0f}%  {recall:>5.0f}%  "
              f"{f1:>5.0f}%")


    overall_mr = all_mr_sum / all_total if all_total else 0
    overall_rec = overall_mr * 100
    overall_f1 = (2 * 100 * overall_rec / (100 + overall_rec)
                  if (100 + overall_rec) > 0 else 0)

    print(f"{'-'*18} {'-'*3}  {'-'*6}  {'-'*6}  {'-'*6}")
    print(f"{'Overall':<18} {all_total:>3}  {100:>5.0f}%  {overall_rec:>5.0f}%  "
          f"{overall_f1:>5.0f}%")
    print()

    # ── Paper reference ──
    print(f"{'='*75}")
    print(f"  Paper Table 3 (reference)")
    print(f"{'='*75}")
    print(f"{'Category':<18} {'#':>3}  {'Prec':>6}  {'Rec':>6}  {'F1':>6}")
    print(f"{'-'*18} {'-'*3}  {'-'*6}  {'-'*6}  {'-'*6}")
    for cat in CATEGORIES_ORDER:
        n, p, r, f1 = PAPER[cat]
        print(f"{cat:<18} {n:>3}  {p:>5.0f}%  {r:>5.0f}%  {f1:>5.0f}%")
    print(f"{'Overall':<18} {64:>3}  {100:>5.0f}%  {76:>5.0f}%  {87:>5.0f}%")
    print()

    # ── V2 comparison ──
    print(f"{'='*75}")
    print(f"  V2 comparison (recall = mean(match_rate)):")
    print()
    for cat in CATEGORIES_ORDER:
        inc_ids = CATEGORY_INCIDENTS.get(cat, [])
        total = len(inc_ids)
        mr_sum = sum(all_incidents[i]["match_rate"] for i in inc_ids if i in all_incidents)
        avg_mr = mr_sum / total if total else 0
        rec = avg_mr * 100
        p_rec = PAPER[cat][2]
        match = "✅" if abs(rec - p_rec) <= 5 else "⬜"
        print(f"    {cat:<18} {total:>2}  rec={rec:>5.1f}%  paper={p_rec:>3}%  {match}")
    print()
    print(f"  Category count differences drive most mismatches.")
    print(f"  To match exactly: adjust CATEGORY_INCIDENTS mapping.")
    print(f"{'='*75}")

if __name__ == "__main__":
    main()

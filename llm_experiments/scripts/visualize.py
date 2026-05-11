"""Generate ASE paper figures from experiment results.

Reads results/qwen_v1_clean_batch_*/metrics.json and
results/deepseek_v4_clean_batch_*/metrics.json, builds 3 figures.

Usage:
    python scripts/visualize.py

Output: results/figures/*.png
"""

import json
import sys
from pathlib import Path
from collections import Counter

RESULTS = Path(__file__).resolve().parent.parent / "results"


def _load_batch_metrics(prefix: str, n_batches: int = 4) -> list[dict]:
    """Load per_incident records from batch_{0..n_batches-1}/metrics.json."""
    records = []
    for i in range(n_batches):
        path = RESULTS / f"{prefix}_batch_{i}" / "metrics.json"
        if not path.exists():
            continue
        with open(path) as f:
            data = json.load(f)
        records.extend(data.get("per_incident", []))
    return records


def _load_degraded() -> dict[str, dict]:
    """Load degraded_incidents.json keyed by incident_id."""
    path = RESULTS / "degraded_incidents.json"
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    return {d["incident_id"]: d for d in data.get("degraded_incidents", [])}


def _plot_top1_accuracy(qwen, ds):
    """Bar chart: overall Top-1 accuracy per model."""
    import matplotlib.pyplot as plt

    q_correct = sum(1 for r in qwen if r.get("correct_service"))
    d_correct = sum(1 for r in ds if r.get("correct_service"))
    total = len(qwen)

    fig, ax = plt.subplots(figsize=(5, 4))
    models = ["Qwen 3 Coder 30B", "DeepSeek V4 Flash", "DeepSeek V4 Pro (subset)"]
    accs = [q_correct / total, d_correct / total, 0.10]

    bars = ax.bar(models, accs, color=["steelblue", "coral", "lightgray"], width=0.6)
    ax.set_ylabel("Top-1 Accuracy")
    ax.set_title("LLM Baseline Performance (64 incidents)")
    ax.set_ylim(0, 0.35)

    for bar, acc, cor in zip(bars, accs, [q_correct, d_correct, 1]):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.005,
                f"{acc:.1%} ({cor}/{total})", ha="center", va="bottom", fontsize=9)

    fig.tight_layout()
    fig.savefig(RESULTS / "figures" / "model_comparison.png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print("  saved model_comparison.png")


def _plot_by_category(qwen, ds):
    """Bar chart: accuracy breakdown by incident category."""
    import matplotlib.pyplot as plt
    import numpy as np

    categories = sorted({r.get("true_service", "") for r in qwen + ds})
    # Group into higher-level categories
    def categorize(svc):
        s = svc.lower()
        if any(x in s for x in ["nova"]): return "Compute"
        if any(x in s for x in ["neutron", "ovn"]): return "Network"
        if any(x in s for x in ["cinder", "ceph", "glance"]): return "Storage"
        if any(x in s for x in ["keystone", "placement", "heat"]): return "API/Orch"
        if any(x in s for x in ["mysql", "redis", "rabbitmq"]): return "DB/MQ"
        if any(x in s for x in ["haproxy", "memcached", "prometheus", "grafana", "skyline"]): return "Infra"
        return "Other"

    cats = sorted({categorize(s) for s in categories})
    q_by_cat = {c: [] for c in cats}
    d_by_cat = {c: [] for c in cats}
    for r in qwen:
        q_by_cat[categorize(r.get("true_service", ""))].append(r.get("correct_service", False))
    for r in ds:
        d_by_cat[categorize(r.get("true_service", ""))].append(r.get("correct_service", False))

    x = np.arange(len(cats))
    width = 0.35

    fig, ax = plt.subplots(figsize=(8, 4))
    q_vals = [sum(q_by_cat[c]) / len(q_by_cat[c]) if q_by_cat[c] else 0 for c in cats]
    d_vals = [sum(d_by_cat[c]) / len(d_by_cat[c]) if d_by_cat[c] else 0 for c in cats]

    ax.bar(x - width / 2, q_vals, width, label="Qwen 3 Coder 30B", color="steelblue")
    ax.bar(x + width / 2, d_vals, width, label="DeepSeek V4 Flash", color="coral")

    # Add count labels
    for i, c in enumerate(cats):
        n_q = len(q_by_cat[c])
        n_d = len(d_by_cat[c])
        ax.text(i - width / 2, q_vals[i] + 0.01, f"{n_q}", ha="center", fontsize=7, color="steelblue")
        ax.text(i + width / 2, d_vals[i] + 0.01, f"{n_d}", ha="center", fontsize=7, color="coral")

    ax.set_xticks(x)
    ax.set_xticklabels(cats, rotation=30, ha="right")
    ax.set_ylabel("Accuracy")
    ax.set_title("Accuracy by Incident Category")
    ax.legend(fontsize=8)
    ax.set_ylim(0, 0.35)

    fig.tight_layout()
    fig.savefig(RESULTS / "figures" / "accuracy_by_category.png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print("  saved accuracy_by_category.png")


def _plot_clean_vs_degraded(qwen, ds):
    """Scatter: each incident as correct/incorrect × failing_streak."""
    import matplotlib.pyplot as plt
    import numpy as np

    degraded = _load_degraded()
    points = []

    for model_label, records in [("Qwen", qwen), ("DeepSeek", ds)]:
        for r in records:
            iid = r.get("incident_id", "")
            di = degraded.get(iid, {})
            streak = di.get("max_failing_streak_before", 0)
            delta = di.get("delta_seconds", 0)
            correct = r.get("correct_service", False)
            points.append((model_label, streak, delta, correct, iid))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

    # Left: streak vs correct/incorrect
    colors = {"Qwen": "steelblue", "DeepSeek": "coral"}
    for model in ["Qwen", "DeepSeek"]:
        pts = [(s, c) for m, s, d, c, i in points if m == model]
        streaks = [p[0] for p in pts]
        correctness = [int(p[1]) for p in pts]
        jitter = np.random.normal(0, 200, len(streaks))
        ax1.scatter([s + j for s, j in zip(streaks, jitter)],
                    correctness, alpha=0.5, s=30,
                    c=colors[model], label=model, edgecolors="none")

    ax1.set_xlabel("Max failing_streak before injection")
    ax1.set_ylabel("Correct (1) / Incorrect (0)")
    ax1.set_title("Accuracy vs Pre-existing Failure Severity")
    ax1.set_yticks([0, 1])
    ax1.legend(fontsize=8)
    ax1.axvline(x=5000, color="gray", linestyle="--", alpha=0.5, label="streak=5000")

    # Right: histogram of deltas, colored by correct/incorrect
    for model in ["Qwen", "DeepSeek"]:
        pts = [(d, c) for m, s, d, c, i in points if m == model]
        correct_deltas = [p[0] for p in pts if p[1]]
        incorrect_deltas = [p[0] for p in pts if not p[1]]
        ax2.hist(correct_deltas, bins=10, alpha=0.6, color="green",
                 label=f"{model} correct", range=(0, 300))
        ax2.hist(incorrect_deltas, bins=10, alpha=0.3, color="red",
                 label=f"{model} incorrect", range=(0, 300))

    ax2.set_xlabel("Delta (s) — first symptom before injection")
    ax2.set_ylabel("Count")
    ax2.set_title("Distribution: Time Before Injection")
    ax2.legend(fontsize=7)

    fig.tight_layout()
    fig.savefig(RESULTS / "figures" / "clean_vs_degraded_scatter.png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print("  saved clean_vs_degraded_scatter.png")


def main():
    (RESULTS / "figures").mkdir(parents=True, exist_ok=True)

    print("Loading Qwen results...")
    qwen = _load_batch_metrics("qwen_v1_clean")
    print(f"  {len(qwen)} records")

    print("Loading DeepSeek results...")
    ds = _load_batch_metrics("deepseek_v4_clean")
    print(f"  {len(ds)} records")

    print("Building figures...")
    _plot_top1_accuracy(qwen, ds)
    _plot_by_category(qwen, ds)
    _plot_clean_vs_degraded(qwen, ds)

    print(f"\nAll figures saved to {RESULTS / 'figures'}")


if __name__ == "__main__":
    main()

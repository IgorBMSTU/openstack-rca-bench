"""Save and load experiment results in a structured, reproducible way."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RESULTS_BASE_DIR = Path(__file__).resolve().parent.parent / "results"


def create_experiment_dir(
    name: str | None = None,
    *,
    provider: str = "unknown",
    model: str = "unknown",
    strategy: str = "unknown",
) -> Path:
    """Create and return a directory for an experiment.

    If name is None, auto-generates as {provider}_{model}_{strategy}_{timestamp}.

    Args:
        name: Explicit experiment directory name. If None, auto-generated.
        provider: Provider nickname for auto-generated name.
        model: Model name for auto-generated name.
        strategy: Strategy name for auto-generated name.

    Returns:
        Path to the created experiment directory.
    """
    if name is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_model = model.replace("/", "_")
        name = f"{provider}_{safe_model}_{strategy}_{timestamp}"

    exp_dir = RESULTS_BASE_DIR / name
    exp_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Created experiment directory: %s", exp_dir)
    return exp_dir


def save_config(path: Path, config: dict[str, Any]) -> None:
    """Save experiment configuration to config.json.

    Args:
        path: Experiment directory path.
        config: Experiment configuration dict.
    """
    config_path = path / "config.json"
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    logger.debug("Saved config to %s", config_path)


def save_prediction(path: Path, record: dict[str, Any]) -> None:
    """Append a single prediction record to predictions.jsonl.

    Args:
        path: Experiment directory path.
        record: Prediction record dict.
    """
    predictions_path = path / "predictions.jsonl"
    with predictions_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.debug("Appended prediction to %s", predictions_path)


def save_metrics(path: Path, metrics: dict[str, Any]) -> None:
    """Save aggregated metrics to metrics.json.

    Args:
        path: Experiment directory path.
        metrics: Aggregated metrics dict.
    """
    metrics_path = path / "metrics.json"
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    logger.debug("Saved metrics to %s", metrics_path)


def save_summary(path: Path, summary: str) -> None:
    """Save human-readable summary to summary.txt.

    Args:
        path: Experiment directory path.
        summary: Summary text.
    """
    summary_path = path / "summary.txt"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write(summary)
    logger.debug("Saved summary to %s", summary_path)


def load_experiment(path: Path) -> dict[str, Any]:
    """Load all experiment data from the given directory.

    Args:
        path: Experiment directory path.

    Returns:
        Dict with keys 'config', 'predictions', and 'metrics'.
    """
    config_path = path / "config.json"
    predictions_path = path / "predictions.jsonl"
    metrics_path = path / "metrics.json"

    result: dict[str, Any] = {}

    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            result["config"] = json.load(f)
    else:
        logger.warning("config.json not found in %s", path)
        result["config"] = {}

    result["predictions"] = []
    if predictions_path.exists():
        with predictions_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    result["predictions"].append(json.loads(line))
    else:
        logger.warning("predictions.jsonl not found in %s", path)

    if metrics_path.exists():
        with metrics_path.open("r", encoding="utf-8") as f:
            result["metrics"] = json.load(f)
    else:
        logger.warning("metrics.json not found in %s", path)
        result["metrics"] = {}

    return result


def list_experiments() -> list[Path]:
    """List all experiment directories under results/.

    Returns:
        List of Paths to experiment directories.
    """
    if not RESULTS_BASE_DIR.exists():
        return []

    experiments: list[Path] = []
    for entry in sorted(RESULTS_BASE_DIR.iterdir()):
        if entry.is_dir() and entry.name != "cache":
            experiments.append(entry)

    return experiments

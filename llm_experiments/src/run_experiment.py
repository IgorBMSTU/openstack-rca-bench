"""Main orchestrator for running LLM RCA experiments on the OpenStack dataset."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dataset_loader import DEFAULT_INCIDENTS_DIR, Incident, load_all_incidents
from .evaluator import EvaluationResult, compute_metrics, evaluate_prediction
from .llm_client import DEFAULT_MODELS, LLMClient
from .multi_agent import MultiAgentOrchestrator
from .prompt_builder import build_prompt
from .results_store import (
    create_experiment_dir,
    save_config,
    save_metrics,
    save_prediction,
    save_summary,
)

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _load_existing_predictions(exp_dir: Path) -> set[str]:
    """Load incident IDs already present in predictions.jsonl."""
    predictions_path = exp_dir / "predictions.jsonl"
    existing_ids: set[str] = set()
    if not predictions_path.exists():
        return existing_ids
    try:
        with predictions_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    incident_id = record.get("incident_id")
                    if incident_id:
                        existing_ids.add(incident_id)
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.warning("Failed to read existing predictions: %s", exc)
    return existing_ids


def _format_summary(
    metrics: dict[str, Any],
    total_processed: int,
    latencies: list[float],
    dry_run: bool,
    interrupted: bool,
) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("Experiment Summary")
    lines.append("=" * 60)
    if dry_run:
        lines.append("MODE: DRY RUN (no LLM calls made)")
    if interrupted:
        lines.append("NOTE: Interrupted — results are partial")
    lines.append(f"Total incidents processed: {total_processed}")
    lines.append(
        f"Top-1 accuracy (exact):   {metrics.get('top1_accuracy', 0.0):.4f}"
    )
    lines.append(
        f"Top-1 accuracy (fuzzy):   {metrics.get('top1_fuzzy_accuracy', 0.0):.4f}"
    )
    lines.append(f"Host accuracy:            {metrics.get('host_accuracy', 0.0):.4f}")
    lines.append(
        f"Mean match score:         {metrics.get('mean_match_score', 0.0):.4f}"
    )
    lines.append("")
    lines.append("Per-category accuracy:")
    category_accuracy = metrics.get("category_accuracy", {})
    if category_accuracy:
        for cat, acc in sorted(category_accuracy.items()):
            lines.append(f"  {cat}: {acc:.4f}")
    else:
        lines.append("  (none)")
    lines.append("")
    if latencies:
        mean_latency = sum(latencies) / len(latencies)
        total_latency = sum(latencies)
        lines.append(
            f"Latency — mean: {mean_latency:.2f}s, total: {total_latency:.2f}s"
        )
    else:
        lines.append("Latency — no data")
    lines.append("=" * 60)
    return "\n".join(lines) + "\n"


def run_experiment() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="Run LLM RCA experiments on the OpenStack dataset"
    )
    parser.add_argument(
        "--provider",
        default="qwen",
        choices=["qwen", "deepseek", "glm", "kimi", "openai"],
        help="LLM provider",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name (defaults to provider default)",
    )
    parser.add_argument(
        "--prompt-strategy",
        default="zero_shot",
        choices=["zero_shot", "with_context", "chain_of_thought", "multi_agent"],
        help="Prompt strategy",
    )
    parser.add_argument(
        "--log-strategy",
        default="hybrid",
        choices=["full", "error_only", "around_injection", "truncated", "hybrid"],
        help="Log reduction strategy",
    )
    parser.add_argument(
        "--max-log-chars",
        type=int,
        default=60000,
        help="Maximum characters of logs in the prompt",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first OFFSET incidents (for parallel batches)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N incidents after offset",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip incidents already present in predictions.jsonl",
    )
    parser.add_argument(
        "--experiment-name",
        default=None,
        help="Explicit experiment directory name",
    )
    parser.add_argument(
        "--incidents-dir",
        default=None,
        type=str,
        help="Path to incidents directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompts without calling the LLM",
    )

    args = parser.parse_args()

    provider: str = args.provider
    model: str | None = args.model or DEFAULT_MODELS.get(provider)
    prompt_strategy: str = args.prompt_strategy
    log_strategy: str = args.log_strategy
    max_log_chars: int = args.max_log_chars
    offset: int = args.offset
    limit: int | None = args.limit
    resume: bool = args.resume
    experiment_name: str | None = args.experiment_name
    incidents_dir: str | None = args.incidents_dir
    dry_run: bool = args.dry_run

    # ------------------------------------------------------------------
    # 1. Load incidents
    # ------------------------------------------------------------------
    incidents = load_all_incidents(incidents_dir)
    if not incidents:
        logger.error("No incidents loaded. Exiting.")
        sys.exit(1)

    # 2. Filter by offset + limit
    if offset:
        incidents = incidents[offset:]
        logger.info("Offset %d applied, %d incidents remaining", offset, len(incidents))
    if limit is not None:
        incidents = incidents[:limit]
        logger.info("Limit %d applied, %d incidents remaining", limit, len(incidents))

    # 3. Create experiment directory
    exp_dir = create_experiment_dir(
        name=experiment_name,
        provider=provider,
        model=model or "unknown",
        strategy=prompt_strategy,
    )

    # 4. Save config
    config: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "prompt_strategy": prompt_strategy,
        "log_strategy": log_strategy,
        "max_log_chars": max_log_chars,
        "limit": limit,
        "resume": resume,
        "experiment_name": experiment_name,
        "offset": offset,
        "incidents_dir": str(incidents_dir if incidents_dir else DEFAULT_INCIDENTS_DIR),
        "dry_run": dry_run,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    save_config(exp_dir, config)

    # 5. Initialize LLM client
    llm_client: LLMClient | None = None
    orchestrator: MultiAgentOrchestrator | None = None
    if not dry_run:
        llm_client = LLMClient(provider=provider, model=model)
        if prompt_strategy == "multi_agent":
            orchestrator = MultiAgentOrchestrator(llm_client)

    # 6. Process each incident
    existing_ids = _load_existing_predictions(exp_dir) if resume else set()

    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None  # type: ignore[assignment]

    results: list[EvaluationResult] = []
    latencies: list[float] = []
    interrupted = False

    iterator = tqdm(incidents, desc="Processing incidents") if tqdm else incidents

    try:
        for incident in iterator:
            # a. Resume check
            if resume and incident.incident_id in existing_ids:
                logger.info("Skipping %s (already predicted)", incident.incident_id)
                continue

            # ── Multi-agent path ──
            if prompt_strategy == "multi_agent" and orchestrator is not None:
                start_time = time.perf_counter()
                final = orchestrator.analyze(incident)
                latency = time.perf_counter() - start_time
                latencies.append(latency)

                # Build prediction dict compatible with evaluate_prediction
                prediction = {
                    "root_cause_service": final.root_cause_service,
                    "affected_host": "",
                    "fault_type": "",
                }
                raw_text = json.dumps({
                    "method": final.method,
                    "confidence": final.confidence,
                    "tie_broken": final.tie_broken,
                    "judge_reasoning": final.judge_reasoning[:500] if final.tie_broken else "",
                    "signals_summary": final.signals_summary[:500],
                    "agents": [
                        {"name": p.agent, "service": p.service, "confidence": p.confidence, "reasoning": p.reasoning[:200]}
                        for p in final.agent_predictions
                    ],
                }, indent=2)

                eval_result = evaluate_prediction(incident, prediction)
                results.append(eval_result)

                record: dict[str, Any] = {
                    "incident_id": incident.incident_id,
                    "prediction": prediction,
                    "raw_response": raw_text,
                    "latency_seconds": round(latency, 3),
                    "tokens": None,
                    "cached": False,
                    "correct_service": eval_result.correct_service,
                    "correct_host": eval_result.correct_host,
                    "match_score": eval_result.match_score,
                    "multi_agent": {
                        "method": final.method,
                        "confidence": final.confidence,
                        "tie_broken": final.tie_broken,
                        "judge_reasoning": final.judge_reasoning,
                        "signals_summary": final.signals_summary,
                        "agent_predictions": [
                            {"agent": p.agent, "service": p.service, "confidence": p.confidence, "reasoning": p.reasoning}
                            for p in final.agent_predictions
                        ],
                    },
                }
                save_prediction(exp_dir, record)

                if tqdm is None:
                    logger.info(
                        "Processed %s [multi_agent] | predicted=%s true=%s correct=%s method=%s latency=%.2fs",
                        incident.incident_id,
                        final.root_cause_service,
                        incident.ground_truth.get("true_service"),
                        eval_result.correct_service,
                        final.method,
                        latency,
                    )
                continue

            # ── Standard (zero_shot / with_context / chain_of_thought) path ──

            # b. Build prompt with reduced logs
            incident_for_prompt = replace(
                incident, logs=incident.get_logs_reduced(log_strategy)
            )
            system_prompt, user_prompt = build_prompt(
                incident_for_prompt, prompt_strategy, max_log_chars
            )

            # c. Dry run
            if dry_run:
                print(f"\n--- Incident: {incident.incident_id} ---")
                print(f"System prompt ({len(system_prompt)} chars):")
                print(system_prompt[:500] + "...")
                print(f"User prompt ({len(user_prompt)} chars):")
                print(user_prompt[:1000] + "...")
                continue

            # d. Call LLM
            start_time = time.perf_counter()
            response = llm_client.complete(  # type: ignore[union-attr]
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.1,
                max_tokens=4096,
                json_mode=True,
            )
            latency = time.perf_counter() - start_time
            latencies.append(latency)

            prediction = response.get("parsed") or {}
            raw_text = response.get("text", "")

            # e. Evaluate
            eval_result = evaluate_prediction(incident, prediction)
            results.append(eval_result)

            # f. Save prediction
            record: dict[str, Any] = {
                "incident_id": incident.incident_id,
                "prediction": prediction,
                "raw_response": raw_text,
                "latency_seconds": round(latency, 3),
                "tokens": response.get("tokens"),
                "cached": response.get("cached", False),
                "correct_service": eval_result.correct_service,
                "correct_host": eval_result.correct_host,
                "match_score": eval_result.match_score,
            }
            save_prediction(exp_dir, record)

            # g. Log progress
            if tqdm is None:
                logger.info(
                    "Processed %s | service=%s host=%s score=%.1f latency=%.2fs",
                    incident.incident_id,
                    eval_result.correct_service,
                    eval_result.correct_host,
                    eval_result.match_score,
                    latency,
                )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user. Saving partial results...")
        interrupted = True

    # 7. Compute metrics
    metrics = compute_metrics(results)
    metrics["latency"] = {
        "mean": sum(latencies) / len(latencies) if latencies else 0.0,
        "total": sum(latencies),
        "count": len(latencies),
    }

    # 8. Save metrics and summary
    save_metrics(exp_dir, metrics)
    summary = _format_summary(
        metrics, len(results), latencies, dry_run, interrupted
    )
    save_summary(exp_dir, summary)

    # 9. Print summary
    print(summary)
    logger.info("Experiment complete. Results saved to %s", exp_dir)


if __name__ == "__main__":
    run_experiment()

"""Multi-agent orchestrator.

Runs 3 specialist agents in parallel, synthesizes their predictions.
Pure Python — no frameworks. Deterministic signal extraction + LLM reasoning.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .preprocessor import extract_observability_signals, ObservabilitySignals
from .prompts import (
    SYSTEM_ANOMALY, build_anomaly_prompt,
    SYSTEM_TOPOLOGY, build_topology_prompt,
    SYSTEM_TEMPORAL, build_temporal_prompt,
    SYSTEM_JUDGE, build_judge_prompt,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentPrediction:
    agent: str
    service: str
    confidence: float
    reasoning: str
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class FinalPrediction:
    root_cause_service: str
    confidence: float
    method: str  # "consensus", "majority", "judge"
    agent_predictions: list[AgentPrediction] = field(default_factory=list)
    tie_broken: bool = False
    judge_reasoning: str = ""
    signals_summary: str = ""


class MultiAgentOrchestrator:
    """Orchestrates the hybrid neuro-symbolic RCA pipeline."""

    def __init__(self, llm_client: Any):
        """Initialize with an LLMClient instance.

        Args:
            llm_client: An LLMClient (from llm_client.py) for LLM calls.
        """
        self.client = llm_client
        self._agent_registry = [
            ("anomaly", SYSTEM_ANOMALY, build_anomaly_prompt),
            ("topology", SYSTEM_TOPOLOGY, build_topology_prompt),
            ("temporal", SYSTEM_TEMPORAL, build_temporal_prompt),
        ]

    def analyze(self, incident: Any) -> FinalPrediction:
        """Run the full hybrid pipeline on one incident.

        Args:
            incident: An Incident dataclass from dataset_loader.py.

        Returns:
            FinalPrediction with root cause service and confidence.
        """
        # ── Level 1: Deterministic Pre-processing ──
        signals = extract_observability_signals(incident)

        if not signals.signals:
            return FinalPrediction(
                root_cause_service="unknown",
                confidence=0.0,
                method="no_signals",
                signals_summary=signals.summary,
            )

        # ── Level 2: Run 3 specialist agents ──
        agent_predictions: list[AgentPrediction] = []
        for agent_name, system_prompt, prompt_builder in self._agent_registry:
            pred = self._run_agent(agent_name, system_prompt, prompt_builder(signals))
            agent_predictions.append(pred)

        # ── Level 3: Synthesize ──
        final = self._synthesize(signals.incident_id, agent_predictions)
        final.signals_summary = signals.summary
        return final

    def _run_agent(
        self,
        agent_name: str,
        system_prompt: str,
        user_prompt: str,
    ) -> AgentPrediction:
        """Run a single specialist agent and parse its response."""
        logger.info("Running %s agent...", agent_name)
        response = self.client.complete(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.1,
            max_tokens=1024,
            json_mode=True,
        )

        parsed = response.get("parsed") or {}
        service = parsed.get("root_cause_service", "unknown")
        confidence = float(parsed.get("confidence", 0))
        reasoning = parsed.get("reasoning", response.get("text", "")[:200])

        logger.info(
            "%s agent → %s (confidence: %.2f)",
            agent_name, service, confidence,
        )

        return AgentPrediction(
            agent=agent_name,
            service=service,
            confidence=confidence,
            reasoning=reasoning,
            raw_response=response,
        )

    NULL_VOTES = frozenset({"none", "unknown", "null", "", "not_determined", "no_root_cause"})

    @staticmethod
    def _is_null_vote(service: str | None) -> bool:
        if service is None:
            return True
        return str(service).lower().strip() in MultiAgentOrchestrator.NULL_VOTES

    def _synthesize(
        self,
        incident_id: str,
        predictions: list[AgentPrediction],
    ) -> FinalPrediction:
        """Synthesize agent predictions, filtering null votes.

        Agents returning "none"/"unknown" have no signal — their vote is discarded.
        Algorithm: filter nulls → consensus → majority → judge.
        """
        valid = [p for p in predictions if not self._is_null_vote(p.service)]

        if not valid:
            return FinalPrediction(
                root_cause_service="unknown", confidence=0.0,
                method="no_consensus", agent_predictions=predictions,
            )

        if len(valid) == 1:
            p = valid[0]
            return FinalPrediction(
                root_cause_service=p.service,
                confidence=round(p.confidence * 0.8, 2),
                method="single_agent", agent_predictions=predictions,
            )

        from collections import Counter
        services = [p.service for p in valid]
        unique = set(services)

        # Unanimous among valid
        if len(unique) == 1:
            svc = services[0]
            conf = max(p.confidence for p in valid)
            return FinalPrediction(
                root_cause_service=svc, confidence=round(conf, 2),
                method="consensus", agent_predictions=predictions,
            )

        # Majority among valid
        counts = Counter(services)
        most_common = counts.most_common(1)[0]
        if most_common[1] >= 2:
            svc = most_common[0]
            matching = [p for p in valid if p.service == svc]
            avg_conf = sum(p.confidence for p in matching) / len(matching)
            return FinalPrediction(
                root_cause_service=svc, confidence=round(avg_conf, 2),
                method="majority", agent_predictions=predictions,
            )

        # All disagree → LLM Judge
        logger.info("Agents disagree — invoking LLM Judge for %s", incident_id)
        judge_input = [
            {"agent": p.agent, "service": p.service, "confidence": p.confidence, "reasoning": p.reasoning}
            for p in predictions
        ]
        judge_prompt = build_judge_prompt(incident_id, judge_input)
        response = self.client.complete(
            prompt=judge_prompt,
            system_prompt=SYSTEM_JUDGE,
            temperature=0.1,
            max_tokens=512,
            json_mode=True,
        )

        parsed = response.get("parsed") or {}
        svc = parsed.get("root_cause_service", services[0])
        conf = float(parsed.get("confidence", 0.3))
        reasoning = parsed.get("reasoning", "")

        return FinalPrediction(
            root_cause_service=svc,
            confidence=round(conf, 2),
            method="judge",
            agent_predictions=predictions,
            tie_broken=True,
            judge_reasoning=reasoning,
        )

"""Multi-agent hybrid neuro-symbolic RCA pipeline.

Architecture:
  Level 1: Deterministic Pre-processor (Python — no LLM)
  Level 2: 3 Specialist LLM Agents (reasoning over structured signals)
  Level 3: Synthesizer (algorithmic consensus + LLM judge)
"""

from .orchestrator import MultiAgentOrchestrator, AgentPrediction, FinalPrediction

__all__ = ["MultiAgentOrchestrator", "AgentPrediction", "FinalPrediction"]

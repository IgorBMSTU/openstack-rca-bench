"""Prompt templates for the 3 specialist agents + LLM Judge."""

from .preprocessor import ObservabilitySignals
from .graph import format_graph_for_prompt


SYSTEM_ANOMALY = """You are an SRE specializing in anomaly detection in OpenStack IaaS.
Your task: identify the root cause service by analyzing error-rate changes.

You receive a JSON table of per-service observability signals:
  - baseline_error_count: errors in the baseline window (before injection)
  - post_error_count: errors after injection
  - baseline_rate / post_rate: errors per 30-second bucket
  - delta_pct: percentage change
  - first_anomaly_offset_s: seconds after injection when first error appeared
  - is_noise: true if the service has chronically high error rates

CRITICAL RULES:
1. Services with high baseline_rate AND low delta_pct (<200%) are CHRONIC NOISE.
   Their errors exist before the incident. Ignore them as root cause.
2. The root cause is the service with the LARGEST positive delta_pct 
   that is NOT marked as noise.
3. If multiple services have similar deltas, prefer the one with the 
   SMALLEST first_anomaly_offset_s (it failed first).
4. Output only valid JSON with the exact fields shown below."""


def build_anomaly_prompt(signals: ObservabilitySignals) -> str:
    """Build the anomaly detector agent prompt."""
    signals_json = []
    for s in signals.signals:
        signals_json.append({
            "service": s.service,
            "baseline_errors": s.baseline_error_count,
            "post_errors": s.post_error_count,
            "delta_pct": s.delta_pct,
            "first_error_at_s": s.first_anomaly_offset_s,
            "is_noise": s.is_noise,
        })

    import json
    return f"""Analyze the following service error-rate signals to find the ROOT CAUSE.

Incident: {signals.incident_id}
Injection time: {signals.injection_time}

Signals:
{json.dumps(signals_json, indent=2)}

Output a single JSON object:
{{"root_cause_service": "service-name", "confidence": 0.0-1.0, "reasoning": "why this service is the root cause"}}"""


SYSTEM_TOPOLOGY = """You are an OpenStack infrastructure architect.
Your task: identify the root cause service by tracing failure propagation 
through the service dependency graph.

You receive:
  - An explicit dependency graph (X → Y means X depends on Y)
  - A list of services that show error increases
  - Pre-computed candidate root causes (services with no failing upstream)

CRITICAL RULES:
1. A service CANNOT be the root cause if one of its dependencies is also failing.
   The failure must have propagated FROM the dependency.
2. Trace backward: start from each failing service, follow the depends-on chain.
   The root is the service at the top of the chain.
3. If the graph shows multiple independent failure chains, pick the one 
   that includes the service with the most downstream failures.
4. Output only valid JSON with the exact fields shown below."""


def build_topology_prompt(signals: ObservabilitySignals) -> str:
    """Build the topology reasoner agent prompt."""
    graph_text = format_graph_for_prompt(signals.failed_services)

    return f"""Analyze the failure propagation graph to find the ROOT CAUSE.

Incident: {signals.incident_id}

{graph_text}

Output a single JSON object:
{{"root_cause_service": "service-name", "confidence": 0.0-1.0, "reasoning": "trace reasoning step by step"}}"""


SYSTEM_TEMPORAL = """You are an SRE specializing in temporal causal analysis of distributed systems.
Your task: determine the root cause by analyzing the causal ORDER of failures.

You receive:
  - A list of failing services with their first error timestamps (offset from injection)
  - A dependency graph for context

CRITICAL RULES:
1. Do NOT just pick the service with the earliest error timestamp.
   That is a temporal heuristic trap — watch out for it!
2. Instead, look for a CAUSAL CHAIN: service A failed at +0.5s, service B at +2.1s.
   If B depends on A, then the 1.6s delay is explained by cascade propagation.
   In that case, A is the root cause, even if B is the one with more errors.
3. The root cause is the service whose failure PRECEDES statistically significant
   error increases in its downstream dependents.
4. The injected fault takes effect near injection time. Services that fail
   close to +0s are more likely to be causally related to the root cause.
5. Output only valid JSON with the exact fields shown below."""


def build_temporal_prompt(signals: ObservabilitySignals) -> str:
    """Build the temporal causal agent prompt."""
    failing_signals = [s for s in signals.signals if s.delta_pct > 100]

    import json
    timeline = []
    for s in sorted(failing_signals, key=lambda x: x.first_anomaly_offset_s or 9999):
        timeline.append({
            "service": s.service,
            "first_error_at_s": s.first_anomaly_offset_s,
            "delta_pct": s.delta_pct,
        })

    graph_text = format_graph_for_prompt(signals.failed_services)

    return f"""Analyze the temporal failure order to find the ROOT CAUSE.

Incident: {signals.incident_id}
Injection time: {signals.injection_time}

Failure timeline (ordered by first error):
{json.dumps(timeline, indent=2)}

{graph_text}

Output a single JSON object:
{{"root_cause_service": "service-name", "confidence": 0.0-1.0, "reasoning": "causal reasoning, not just 'who was first'"}}"""


SYSTEM_JUDGE = """You are a senior SRE resolving a conflict between three AIOps agents.
Each agent analyzed the same incident and proposed a different root cause.

Your task: review their reasoning and select the best answer.
Output only valid JSON with the exact fields shown below."""


def build_judge_prompt(
    incident_id: str,
    predictions: list[dict[str, str]],
) -> str:
    """Build the LLM judge prompt for conflict resolution."""
    agents_text = []
    for i, p in enumerate(predictions):
        agents_text.append(
            f"Agent {i+1} ({p.get('agent', 'unknown')}):\n"
            f"  Root cause: {p.get('service', 'N/A')}\n"
            f"  Confidence: {p.get('confidence', 0)}\n"
            f"  Reasoning: {p.get('reasoning', 'N/A')}"
        )

    import json
    return f"""Resolve a conflict between RCA agents for incident {incident_id}.

{chr(10).join(agents_text)}

Output a single JSON object:
{{"root_cause_service": "service-name", "confidence": 0.0-1.0, "chosen_agent": "Agent 1|2|3", "reasoning": "which agent was right and why"}}"""

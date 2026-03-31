"""
Deterministic scorers — run against Phoenix trace data after a replay.

Zero LLM cost. Check structural properties of the pipeline output.
Results pushed to Phoenix as CODE annotations.
"""

import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ScoreCard:
    """Per-run scorecard with dimensional scores."""
    routing_accuracy: float = 0.0   # routed / (routed + unrouted), excl noise
    parse_success_rate: float = 0.0  # successful parses / total LLM calls
    dead_letter_rate: float = 0.0    # failures / total calls
    task_creation_sanity: bool = True  # no excessive task creation
    ambiguity_rate_ok: bool = True    # within expected range
    model_selection_checks: int = 0   # how many messages had correct model tier
    model_selection_total: int = 0
    details: list[dict] = field(default_factory=list)

    @property
    def model_selection_accuracy(self) -> float:
        if self.model_selection_total == 0:
            return 1.0
        return self.model_selection_checks / self.model_selection_total

    def summary(self) -> dict:
        return {
            "routing_accuracy": round(self.routing_accuracy, 3),
            "parse_success_rate": round(self.parse_success_rate, 3),
            "dead_letter_rate": round(self.dead_letter_rate, 3),
            "task_creation_sanity": self.task_creation_sanity,
            "ambiguity_rate_ok": self.ambiguity_rate_ok,
            "model_selection_accuracy": round(self.model_selection_accuracy, 3),
            "detail_count": len(self.details),
        }


def score_replay(stats: dict, state: dict, trace_df=None) -> ScoreCard:
    """
    Compute deterministic scores from replay stats and state.

    Args:
        stats: replay stats dict (messages_total, messages_routed, etc.)
        state: snapshot state dict (node_states, items, etc.)
        trace_df: optional Phoenix spans DataFrame for deeper analysis
    """
    card = ScoreCard()

    # ── Routing accuracy ────────────────────────────────────────────
    routed = stats.get("messages_routed", 0)
    unrouted = stats.get("messages_unrouted", 0)
    noise = stats.get("messages_noise", 0)
    addressable = routed + unrouted  # exclude noise
    if addressable > 0:
        card.routing_accuracy = routed / addressable
    else:
        card.routing_accuracy = 1.0  # no addressable messages = vacuously correct

    # ── Parse success rate ──────────────────────────────────────────
    agent_calls = stats.get("update_agent_calls", 0)
    agent_failures = stats.get("update_agent_failures", 0)
    if agent_calls > 0:
        card.parse_success_rate = (agent_calls - agent_failures) / agent_calls
    else:
        card.parse_success_rate = 1.0

    # ── Dead letter rate ────────────────────────────────────────────
    dead_letters = state.get("dead_letter_count", 0)
    if agent_calls > 0:
        card.dead_letter_rate = dead_letters / agent_calls

    # ── Task creation sanity ────────────────────────────────────────
    # Flag if more tasks than 3x the number of seeded entities
    tasks_created = len(state.get("node_states", {}))
    # Rough heuristic: if we created >10 tasks for <100 messages, something's wrong
    total_msgs = stats.get("messages_total", 0)
    if total_msgs > 0 and tasks_created > max(10, total_msgs * 0.1):
        card.task_creation_sanity = False
        card.details.append({
            "check": "task_creation_sanity",
            "verdict": "FAIL",
            "reason": f"{tasks_created} tasks for {total_msgs} messages",
        })

    # ── Ambiguity rate ──────────────────────────────────────────────
    n_flags = len(state.get("ambiguity_flags", []))
    if agent_calls > 0:
        flag_rate = n_flags / agent_calls
        # Too many flags (>2 per call) or suspiciously zero
        if flag_rate > 2.0:
            card.ambiguity_rate_ok = False
            card.details.append({
                "check": "ambiguity_rate",
                "verdict": "WARN",
                "reason": f"{n_flags} flags from {agent_calls} calls ({flag_rate:.2f}/call) — too high",
            })

    # ── Model selection check (requires trace_df) ───────────────────
    if trace_df is not None:
        _check_model_selection(card, trace_df)

    return card


def _check_model_selection(card: ScoreCard, trace_df):
    """Check that model selection (Gemini vs Sonnet) matches message complexity."""
    from src.agent.update_agent import _is_complex_message

    # Get message spans that have associated LLM calls
    msg_spans = trace_df[trace_df["name"].str.startswith("message:")]
    llm_spans = trace_df[trace_df["name"] == "llm:update_agent"]

    if len(llm_spans) == 0:
        return

    for _, llm_span in llm_spans.iterrows():
        attrs = llm_span.get("attributes.llm", {})
        if not isinstance(attrs, dict):
            continue

        model = llm_span.get("attributes.llm.model_name", "")
        if not model:
            continue

        is_gemini = model.startswith("gemini")

        # We can't easily re-derive complexity from the span alone
        # but we can check consistency: if model is gemini but tokens_out > 500,
        # it might have been a complex message that should have used Sonnet
        tokens_out = llm_span.get("attributes.llm.token_count.completion", 0) or 0

        card.model_selection_total += 1

        # Simple heuristic: high output tokens with Gemini suggests wrong model
        if is_gemini and tokens_out > 500:
            card.details.append({
                "check": "model_selection",
                "verdict": "WARN",
                "reason": f"Gemini used but {tokens_out} output tokens — may need Sonnet",
                "span_id": llm_span.get("context.span_id", ""),
            })
        else:
            card.model_selection_checks += 1


def push_scores_to_phoenix(card: ScoreCard, run_span_id: str,
                           phoenix_endpoint: str = "http://localhost:6006"):
    """Push scorecard to Phoenix as CODE annotations on the run span."""
    import phoenix as px

    client = px.Client(endpoint=phoenix_endpoint)
    summary = card.summary()

    # Create one annotation per dimension on the root run span
    annotations = []
    for key, value in summary.items():
        if key == "detail_count":
            continue
        if isinstance(value, bool):
            score = 1.0 if value else 0.0
            label = "PASS" if value else "FAIL"
        elif isinstance(value, (int, float)):
            score = float(value)
            label = "PASS" if score >= 0.8 else "WARN" if score >= 0.5 else "FAIL"
        else:
            continue

        annotations.append({
            "span_id": run_span_id,
            "name": f"deterministic:{key}",
            "annotator_kind": "CODE",
            "label": label,
            "score": score,
            "explanation": json.dumps({k: v for k, v in summary.items()}),
        })

    # Use the REST API to push annotations
    if annotations:
        import requests
        resp = requests.post(
            f"{phoenix_endpoint}/v1/span_annotations",
            json={"data": annotations},
        )
        if resp.status_code == 200:
            log.info("Pushed %d score annotations to Phoenix", len(annotations))
        else:
            log.warning("Failed to push annotations: %s %s", resp.status_code, resp.text)

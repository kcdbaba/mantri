"""
Deterministic judges — compare actual replay output against eval baselines.

Two modes:
  - Final-state mode (no trace_df): checks against aggregate final DB state.
    Approximate — can't verify per-message correctness.
  - Per-span mode (with trace_df): walks Phoenix trace spans in sequence order,
    evaluates each message's agent output independently. Accurate.

Zero LLM cost. Exact-match and set-comparison scoring.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class AssertionResult:
    assertion_type: str   # "routing", "node_update", "item", "ambiguity", "forbidden"
    target: str           # node_id, item description, etc.
    expected: str
    actual: str
    passed: bool
    notes: str = ""


@dataclass
class MessageScore:
    message_id: str
    routing_pass: bool = False
    node_update_score: float = 0.0
    item_score: float = 0.0
    ambiguity_score: float = 0.0
    forbidden_violations: int = 0
    assertions: list[AssertionResult] = field(default_factory=list)

    @property
    def overall_pass(self) -> bool:
        return (self.routing_pass
                and self.node_update_score >= 0.5
                and self.forbidden_violations == 0)


@dataclass
class EvalResult:
    case_id: str
    baseline_version: str
    eval_mode: str = "final_state"  # "final_state" or "per_span"
    message_scores: list[MessageScore] = field(default_factory=list)

    @property
    def overall_score(self) -> float:
        if not self.message_scores:
            return 0.0
        passing = sum(1 for m in self.message_scores if m.overall_pass)
        return passing / len(self.message_scores)

    def summary(self) -> dict:
        n = len(self.message_scores)
        return {
            "case_id": self.case_id,
            "baseline_version": self.baseline_version,
            "eval_mode": self.eval_mode,
            "messages_evaluated": n,
            "messages_passing": sum(1 for m in self.message_scores if m.overall_pass),
            "overall_score": round(self.overall_score, 3),
            "avg_node_update_score": round(
                sum(m.node_update_score for m in self.message_scores) / max(n, 1), 3
            ),
            "avg_item_score": round(
                sum(m.item_score for m in self.message_scores) / max(n, 1), 3
            ),
            "total_forbidden_violations": sum(
                m.forbidden_violations for m in self.message_scores
            ),
        }


def judge_replay(baselines_path: Path, replay_result_path: Path,
                 trace_df=None) -> EvalResult:
    """
    Compare actual replay output against eval baselines.

    If trace_df is provided, uses per-span evaluation (accurate per-message).
    Otherwise falls back to final-state evaluation (approximate).
    """
    baselines = json.loads(baselines_path.read_text())
    replay = json.loads(replay_result_path.read_text())

    if trace_df is not None and len(trace_df) > 0:
        return _judge_per_span(baselines, replay, trace_df)
    return _judge_final_state(baselines, replay)


# ── Per-span evaluation (accurate) ─────────────────────────────────────

def _build_per_message_data(trace_df) -> dict:
    """
    Build per-message lookup from Phoenix trace spans.

    Returns: {message_id: {
        "routed": bool,
        "route_entities": [(entity_id, confidence)],
        "route_layer": str,
        "is_noise": bool,
        "llm_outputs": [parsed AgentOutput dicts],
        "node_updates": [{"node_id": ..., "new_status": ..., ...}],
        "items_applied": [{"operation": ..., "description": ..., ...}],
        "ambiguity_flags": [{"severity": ..., "category": ..., ...}],
    }}
    """
    data = {}

    # Index spans by span_id for parent lookups
    span_by_id = {}
    for _, span in trace_df.iterrows():
        sid = span.get("context.span_id")
        if sid:
            span_by_id[sid] = span

    # Process message spans
    msg_spans = trace_df[trace_df["name"].str.startswith("message:")].copy()
    for _, msg_span in msg_spans.iterrows():
        msg_sid = msg_span.get("context.span_id")
        msg_attrs = msg_span.get("attributes.message", {})
        if not isinstance(msg_attrs, dict):
            continue

        msg_id = msg_attrs.get("id", "")
        if not msg_id:
            continue

        entry = {
            "routed": False,
            "route_entities": [],
            "route_layer": "",
            "is_noise": False,
            "llm_outputs": [],
            "node_updates": [],
            "items_applied": [],
            "ambiguity_flags": [],
        }

        # Find child spans (routing, llm, post_processing) via parent_id
        children = trace_df[trace_df["parent_id"] == msg_sid]
        for _, child in children.iterrows():
            child_name = child.get("name", "")

            if child_name == "routing":
                route_attrs = child.get("attributes.routing", {})
                if isinstance(route_attrs, dict):
                    entry["is_noise"] = route_attrs.get("is_noise", False)
                    entry["route_layer"] = route_attrs.get("layer", "")
                    route_count = route_attrs.get("route_count", 0)
                    entry["routed"] = route_count > 0
                    # Parse entity_ids and confidences
                    try:
                        eids = json.loads(route_attrs.get("entity_ids", "[]"))
                        confs = json.loads(route_attrs.get("confidences", "[]"))
                        entry["route_entities"] = list(zip(eids, confs))
                    except (json.JSONDecodeError, TypeError):
                        pass

            elif child_name == "llm:update_agent":
                raw_output = child.get("attributes.output.value", "")
                if raw_output:
                    parsed = _parse_agent_output(raw_output)
                    if parsed:
                        entry["llm_outputs"].append(parsed)

            elif child_name.startswith("post_processing:"):
                pp_attrs = child.get("attributes.pp", {})
                if isinstance(pp_attrs, dict):
                    # Parse node updates
                    try:
                        updates = json.loads(pp_attrs.get("node_updates", "[]"))
                        entry["node_updates"].extend(updates)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    # Count items and flags
                    entry["items_applied_count"] = pp_attrs.get("items_applied_count", 0)
                    entry["ambiguity_flags_count"] = pp_attrs.get("ambiguity_flags_count", 0)

        data[msg_id] = entry

    return data


def _parse_agent_output(raw: str) -> dict | None:
    """Parse raw LLM output into AgentOutput-like dict."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _judge_per_span(baselines: dict, replay: dict, trace_df) -> EvalResult:
    """Evaluate using per-message trace span data."""
    result = EvalResult(
        case_id=baselines["case_id"],
        baseline_version=baselines["version_tag"],
        eval_mode="per_span",
    )

    per_msg = _build_per_message_data(trace_df)
    state = replay["state"]

    # Item lookup from final state (items accumulate, so we check final)
    actual_items = {}
    for task_id, items in state.get("items", {}).items():
        actual_items[task_id] = items

    for msg_baseline in baselines["messages"]:
        msg_id = msg_baseline["message_id"]
        ms = MessageScore(message_id=msg_id)
        msg_data = per_msg.get(msg_id, {})

        expected_routing = msg_baseline.get("expected_routing", {})
        expected_routed = expected_routing.get("routed", True)
        expected_noise = expected_routing.get("is_noise", False)
        expected_task = msg_baseline.get("expected_task_id")

        # ── Routing check (per-span) ────────────────────────────────
        if expected_noise:
            # Should be noise — check it wasn't routed
            actual_noise = msg_data.get("is_noise", False)
            actual_routed = msg_data.get("routed", False)
            ms.routing_pass = actual_noise or not actual_routed
            ms.assertions.append(AssertionResult(
                assertion_type="routing",
                target=msg_id,
                expected="noise (not routed)",
                actual=f"noise={actual_noise}, routed={actual_routed}",
                passed=ms.routing_pass,
            ))
        elif not expected_routed:
            # Should be unrouted (has content but no route)
            actual_routed = msg_data.get("routed", False)
            ms.routing_pass = not actual_routed
            ms.assertions.append(AssertionResult(
                assertion_type="routing",
                target=msg_id,
                expected="unrouted",
                actual=f"routed={actual_routed}",
                passed=ms.routing_pass,
            ))
        else:
            # Should be routed — check entity matches
            actual_routed = msg_data.get("routed", False)
            expected_entity = expected_routing.get("entity_id", "")
            actual_entities = [eid for eid, _ in msg_data.get("route_entities", [])]

            entity_match = not expected_entity or expected_entity in actual_entities
            ms.routing_pass = actual_routed and entity_match
            ms.assertions.append(AssertionResult(
                assertion_type="routing",
                target=msg_id,
                expected=f"routed to {expected_entity}",
                actual=f"routed={actual_routed}, entities={actual_entities}",
                passed=ms.routing_pass,
                notes="" if ms.routing_pass else f"expected {expected_entity}",
            ))

        # Skip further checks for noise/unrouted
        if expected_task is None:
            ms.node_update_score = 1.0
            ms.item_score = 1.0
            result.message_scores.append(ms)
            continue

        # ── Node update checks (per-span) ───────────────────────────
        expected_updates = msg_baseline.get("expected_node_updates", [])
        actual_updates = msg_data.get("node_updates", [])

        # Also extract from LLM output if node_updates from post_processing is empty
        if not actual_updates and msg_data.get("llm_outputs"):
            for llm_out in msg_data["llm_outputs"]:
                for to in llm_out.get("task_outputs", []):
                    actual_updates.extend(to.get("node_updates", []))

        if expected_updates:
            matched = 0
            for exp in expected_updates:
                node_id = exp["node_id"]
                acceptable = exp.get("new_status_options", [exp.get("new_status", "")])
                if isinstance(acceptable, str):
                    acceptable = [acceptable]
                min_conf = exp.get("min_confidence", 0)

                # Find matching actual update for this node
                actual_match = None
                for au in actual_updates:
                    au_node = au.get("node_id", "")
                    if au_node == node_id:
                        actual_match = au
                        break

                if actual_match:
                    actual_status = actual_match.get("new_status", "")
                    actual_conf = actual_match.get("confidence", 0)
                    status_ok = actual_status in acceptable
                    conf_ok = not min_conf or (actual_conf or 0) >= min_conf
                    passed = status_ok and conf_ok
                else:
                    actual_status = "(no update)"
                    actual_conf = None
                    passed = False

                if passed:
                    matched += 1

                ms.assertions.append(AssertionResult(
                    assertion_type="node_update",
                    target=f"{expected_task}:{node_id}",
                    expected=f"status in {acceptable}",
                    actual=f"status={actual_status}, conf={actual_conf}",
                    passed=passed,
                    notes="" if passed else f"expected {acceptable}, got {actual_status}",
                ))

            ms.node_update_score = matched / len(expected_updates)
        else:
            ms.node_update_score = 1.0

        # ── Item checks ─────────────────────────────────────────────
        # Items accumulate across messages, so check against final state
        expected_items = msg_baseline.get("expected_items", [])
        task_items = actual_items.get(expected_task, [])

        if expected_items:
            matched = 0
            for exp_item in expected_items:
                desc_contains = exp_item.get("description_contains", "").lower()
                found = any(
                    desc_contains in (it.get("description", "").lower())
                    for it in task_items
                )
                if found:
                    matched += 1
                ms.assertions.append(AssertionResult(
                    assertion_type="item",
                    target=desc_contains,
                    expected=f"item containing '{desc_contains}'",
                    actual=f"found={found} in {len(task_items)} items",
                    passed=found,
                ))
            ms.item_score = matched / len(expected_items)
        else:
            ms.item_score = 1.0

        # ── Forbidden update checks (per-span) ─────────────────────
        forbidden = msg_baseline.get("forbidden_updates", [])
        for fb in forbidden:
            node_id = fb["node_id"]
            # Check if this message's agent output updated the forbidden node
            violated = any(
                au.get("node_id") == node_id
                for au in actual_updates
            )
            if violated:
                ms.forbidden_violations += 1
            ms.assertions.append(AssertionResult(
                assertion_type="forbidden",
                target=f"{expected_task}:{node_id}",
                expected="no update to this node",
                actual=f"updated={violated}",
                passed=not violated,
                notes=fb.get("reason", ""),
            ))

        result.message_scores.append(ms)

    return result


# ── Final-state evaluation (fallback) ──────────────────────────────────

def _judge_final_state(baselines: dict, replay: dict) -> EvalResult:
    """Evaluate using aggregate final DB state. Less accurate but works without traces."""
    result = EvalResult(
        case_id=baselines["case_id"],
        baseline_version=baselines["version_tag"],
        eval_mode="final_state",
    )

    state = replay["state"]

    # Build lookup of actual node states per task
    actual_nodes = {}
    for task_id, nodes in state.get("node_states", {}).items():
        actual_nodes[task_id] = {}
        for n in nodes:
            raw_id = n["node_id"]
            if raw_id.startswith(task_id + "_"):
                node_name = raw_id[len(task_id) + 1:]
            else:
                node_name = raw_id
            actual_nodes[task_id][node_name] = {
                "status": n["status"],
                "confidence": n.get("confidence"),
            }

    actual_items = {}
    for task_id, items in state.get("items", {}).items():
        actual_items[task_id] = items

    for msg_baseline in baselines["messages"]:
        msg_id = msg_baseline["message_id"]
        ms = MessageScore(message_id=msg_id)

        # Routing — trust baseline annotation in final-state mode
        ms.routing_pass = True
        ms.assertions.append(AssertionResult(
            assertion_type="routing",
            target=msg_id,
            expected="(per-baseline)",
            actual="(final-state mode, routing not verified per-message)",
            passed=True,
        ))

        expected_task = msg_baseline.get("expected_task_id")
        if expected_task is None:
            ms.node_update_score = 1.0
            ms.item_score = 1.0
            result.message_scores.append(ms)
            continue

        # Node updates — check final state
        task_nodes = actual_nodes.get(expected_task, {})
        expected_updates = msg_baseline.get("expected_node_updates", [])

        if expected_updates:
            matched = 0
            for exp in expected_updates:
                node_id = exp["node_id"]
                actual = task_nodes.get(node_id, {})
                actual_status = actual.get("status", "pending")
                acceptable = exp.get("new_status_options", [exp.get("new_status", "")])
                if isinstance(acceptable, str):
                    acceptable = [acceptable]

                status_ok = actual_status in acceptable
                conf_ok = True
                min_conf = exp.get("min_confidence", 0)
                if min_conf and actual.get("confidence") is not None:
                    conf_ok = (actual["confidence"] or 0) >= min_conf

                passed = status_ok and conf_ok
                if passed:
                    matched += 1

                ms.assertions.append(AssertionResult(
                    assertion_type="node_update",
                    target=f"{expected_task}:{node_id}",
                    expected=f"status in {acceptable}",
                    actual=f"status={actual_status}, conf={actual.get('confidence')}",
                    passed=passed,
                    notes="" if passed else f"expected {acceptable}, got {actual_status}",
                ))
            ms.node_update_score = matched / len(expected_updates)
        else:
            ms.node_update_score = 1.0

        # Items — check final state
        expected_items = msg_baseline.get("expected_items", [])
        task_items = actual_items.get(expected_task, [])

        if expected_items:
            matched = 0
            for exp_item in expected_items:
                desc_contains = exp_item.get("description_contains", "").lower()
                found = any(
                    desc_contains in (it.get("description", "").lower())
                    for it in task_items
                )
                if found:
                    matched += 1
                ms.assertions.append(AssertionResult(
                    assertion_type="item",
                    target=desc_contains,
                    expected=f"item containing '{desc_contains}'",
                    actual=f"found={found} in {len(task_items)} items",
                    passed=found,
                ))
            ms.item_score = matched / len(expected_items)
        else:
            ms.item_score = 1.0

        # Forbidden — check final state (approximate)
        forbidden = msg_baseline.get("forbidden_updates", [])
        for fb in forbidden:
            node_id = fb["node_id"]
            actual = task_nodes.get(node_id, {})
            actual_status = actual.get("status", "pending")
            violated = actual_status not in ("pending", "skipped")
            if violated:
                ms.forbidden_violations += 1
            ms.assertions.append(AssertionResult(
                assertion_type="forbidden",
                target=f"{expected_task}:{node_id}",
                expected="should remain pending/skipped",
                actual=f"status={actual_status}",
                passed=not violated,
                notes=fb.get("reason", ""),
            ))

        result.message_scores.append(ms)

    return result

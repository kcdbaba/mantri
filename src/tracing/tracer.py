"""
Phoenix OTEL tracer for replay and eval runs.

Usage:
    from src.tracing.tracer import ReplayTracer

    tracer = ReplayTracer(project_name="mantri")
    tracer.start()
    # ... run replay, calling tracer.record_* methods ...
    tracer.stop()

Non-invasive: production code doesn't import this module.
"""

import hashlib
import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import StatusCode

log = logging.getLogger(__name__)

DEFAULT_PHOENIX_ENDPOINT = "http://localhost:6006/v1/traces"

# Cost per token (approximate, for cost tracking)
MODEL_COSTS = {
    "claude-sonnet-4-6": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "claude-haiku-4-5-20251001": {"input": 0.80 / 1_000_000, "output": 4.0 / 1_000_000},
    "gemini-2.5-flash": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    costs = MODEL_COSTS.get(model, {"input": 0, "output": 0})
    return tokens_in * costs["input"] + tokens_out * costs["output"]


def _now_ns() -> int:
    """Current time as nanoseconds since epoch (OTEL format)."""
    return int(time.time() * 1_000_000_000)


@dataclass
class RunContext:
    """Metadata for a single replay run."""
    run_id: str
    case_id: str
    run_type: str = "live_replay"
    git_commit: str = ""
    config_flags: dict = field(default_factory=dict)
    run_notes: str = ""


class ReplayTracer:
    """Non-invasive tracer for replay runs. Emits OpenTelemetry spans to Phoenix."""

    def __init__(self, project_name: str = "mantri",
                 phoenix_endpoint: str | None = None):
        self.project_name = project_name
        self.phoenix_endpoint = phoenix_endpoint or DEFAULT_PHOENIX_ENDPOINT
        self._provider: TracerProvider | None = None
        self._tracer: trace.Tracer | None = None
        self._run_span = None
        self._run_ctx = None
        self._run_start_ns = 0
        # Cumulative stats for the run
        self._total_tokens_in = 0
        self._total_tokens_out = 0
        self._total_cost = 0.0

    def start(self, run_ctx: RunContext):
        """Initialize OTEL provider and start the root run span."""
        from phoenix.otel import register

        self._provider = register(
            project_name=self.project_name,
            endpoint=self.phoenix_endpoint,
        )
        self._tracer = self._provider.get_tracer("mantri.replay")
        self._run_ctx = run_ctx
        self._run_start_ns = _now_ns()

        self._run_span = self._tracer.start_span(
            name=f"replay:{run_ctx.case_id}",
            start_time=self._run_start_ns,
            attributes={
                "run.id": run_ctx.run_id,
                "run.case_id": run_ctx.case_id,
                "run.type": run_ctx.run_type,
                "run.git_commit": run_ctx.git_commit,
                "run.config_flags": json.dumps(run_ctx.config_flags),
                "run.notes": run_ctx.run_notes,
                "openinference.span.kind": "AGENT",
            },
        )
        self._run_token = trace.context_api.attach(
            trace.set_span_in_context(self._run_span)
        )
        log.info("Tracing started: project=%s run=%s",
                 self.project_name, run_ctx.run_id)

    def stop(self, stats: dict | None = None):
        """Flush and shutdown the tracer."""
        if self._run_span:
            end_ns = _now_ns()
            if stats:
                self._run_span.set_attribute("run.stats", json.dumps(stats))
            self._run_span.set_attribute("cumulative.tokens_in", self._total_tokens_in)
            self._run_span.set_attribute("cumulative.tokens_out", self._total_tokens_out)
            self._run_span.set_attribute("cumulative.tokens_total",
                                         self._total_tokens_in + self._total_tokens_out)
            self._run_span.set_attribute("cumulative.cost_usd", round(self._total_cost, 6))
            self._run_span.set_status(StatusCode.OK)
            self._run_span.end(end_time=end_ns)
            trace.context_api.detach(self._run_token)

        if self._provider:
            self._provider.force_flush()
            self._provider.shutdown()
            log.info("Tracing stopped. Total: %d tokens in, %d tokens out, $%.4f",
                     self._total_tokens_in, self._total_tokens_out, self._total_cost)

    @contextmanager
    def trace_message(self, message: dict, seq: int):
        """Context manager for tracing a single message through the pipeline."""
        if not self._tracer:
            yield MessageTrace()
            return

        msg_id = message.get("message_id", f"msg_{seq}")
        start_ns = _now_ns()
        span = self._tracer.start_span(
            name=f"message:{msg_id}",
            start_time=start_ns,
            attributes={
                "message.id": msg_id,
                "message.seq": seq,
                "message.body": (message.get("body") or "")[:500],
                "message.sender_jid": message.get("sender_jid", ""),
                "message.group_id": message.get("group_id", ""),
                "message.has_image": bool(message.get("image_path") or message.get("image_bytes")),
                "openinference.span.kind": "CHAIN",
            },
        )
        token = trace.context_api.attach(trace.set_span_in_context(span))
        msg_trace = MessageTrace(tracer=self._tracer, parent_span=span, parent_tracer=self)
        try:
            yield msg_trace
            span.set_status(StatusCode.OK)
        except Exception as e:
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            raise
        finally:
            # Set cumulative stats on message span
            if msg_trace.msg_tokens_in > 0:
                span.set_attribute("cumulative.tokens_in", msg_trace.msg_tokens_in)
                span.set_attribute("cumulative.tokens_out", msg_trace.msg_tokens_out)
                span.set_attribute("cumulative.cost_usd", round(msg_trace.msg_cost, 6))
            if msg_trace.route_data:
                span.set_attribute("route.data", json.dumps(msg_trace.route_data))
            span.end(end_time=_now_ns())
            trace.context_api.detach(token)


@dataclass
class MessageTrace:
    """Accumulates trace data for a single message."""
    tracer: trace.Tracer | None = None
    parent_span: Any = None
    parent_tracer: ReplayTracer | None = None
    route_data: dict = field(default_factory=dict)
    msg_tokens_in: int = 0
    msg_tokens_out: int = 0
    msg_cost: float = 0.0

    def record_routing(self, routes: list[tuple[str, float]],
                       layer: str = "", is_noise: bool = False):
        """Record the routing decision."""
        self.route_data = {
            "routes": [(eid, conf) for eid, conf in routes],
            "layer": layer,
            "is_noise": is_noise,
        }
        if not self.tracer:
            return

        span = self.tracer.start_span(
            name="routing",
            attributes={
                "routing.entity_ids": json.dumps([eid for eid, _ in routes]),
                "routing.confidences": json.dumps([conf for _, conf in routes]),
                "routing.layer": layer,
                "routing.is_noise": is_noise,
                "routing.route_count": len(routes),
                "openinference.span.kind": "CHAIN",
                "input.value": json.dumps(self.route_data),
                "output.value": f"routed={len(routes) > 0}, layer={layer}",
            },
        )
        span.set_status(StatusCode.OK)
        span.end()

    def record_task_resolution(self, entity_id: str, entity_tasks: list[dict],
                                resolved_task_id: str | None,
                                resolution_method: str):
        """Record how entity resolved to task(s)."""
        if not self.tracer:
            return

        tasks_summary = [
            {"task_id": t["task_id"], "order_type": t["order_type"],
             "is_mature": t.get("is_mature", False),
             "item_count": len(t.get("items", []))}
            for t in entity_tasks
        ]

        input_val = json.dumps({"entity_id": entity_id, "tasks": tasks_summary})
        output_val = json.dumps({
            "resolved_task_id": resolved_task_id,
            "method": resolution_method,
        })

        span = self.tracer.start_span(
            name="task_resolution",
            attributes={
                "resolution.entity_id": entity_id,
                "resolution.method": resolution_method,
                "resolution.resolved_task_id": resolved_task_id or "",
                "resolution.entity_tasks": json.dumps(tasks_summary),
                "resolution.task_count": len(entity_tasks),
                "openinference.span.kind": "CHAIN",
                "input.value": input_val,
                "output.value": output_val,
            },
        )
        span.set_status(StatusCode.OK)
        span.end()

    def record_llm_call(self, call_type: str, task_id: str,
                        system_prompt: str, user_section: str,
                        raw_output: str, parsed_output: dict | None,
                        model: str, model_selection_reason: str,
                        tokens_in: int, tokens_out: int,
                        cache_creation: int, cache_read: int,
                        latency_ms: int, parse_success: bool,
                        is_retry: bool = False):
        """Record a full LLM call with proper timing and token tracking."""
        if not self.tracer:
            return

        # Compute cost
        cost = _estimate_cost(model, tokens_in, tokens_out)

        # Track cumulative stats
        self.msg_tokens_in += tokens_in
        self.msg_tokens_out += tokens_out
        self.msg_cost += cost
        if self.parent_tracer:
            self.parent_tracer._total_tokens_in += tokens_in
            self.parent_tracer._total_tokens_out += tokens_out
            self.parent_tracer._total_cost += cost

        # Create span with proper timing
        end_ns = _now_ns()
        start_ns = end_ns - (latency_ms * 1_000_000)  # backdate start by latency

        span = self.tracer.start_span(
            name=f"llm:{call_type}",
            start_time=start_ns,
            attributes={
                "llm.call_type": call_type,
                "llm.task_id": task_id,
                "llm.model_name": model,
                "llm.model_selection_reason": model_selection_reason,
                "llm.token_count.prompt": tokens_in,
                "llm.token_count.completion": tokens_out,
                "llm.token_count.total": tokens_in + tokens_out,
                "llm.cache_creation_tokens": cache_creation,
                "llm.cache_read_tokens": cache_read,
                "llm.latency_ms": latency_ms,
                "llm.parse_success": parse_success,
                "llm.is_retry": is_retry,
                "llm.cost_usd": round(cost, 6),
                "input.value": f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_section}",
                "output.value": raw_output,
                "openinference.span.kind": "LLM",
            },
        )
        if parsed_output:
            span.set_attribute("llm.parsed_output",
                               json.dumps(parsed_output, default=str)[:5000])

        span.set_status(StatusCode.OK if parse_success else StatusCode.ERROR)
        span.end(end_time=end_ns)

    def record_post_processing(self, task_id: str, task_output_index: int,
                                cascades_fired: list[str],
                                tasks_created: list[dict],
                                ambiguity_flags: list[dict],
                                items_applied: list[dict],
                                node_updates: list[dict]):
        """Record deterministic post-processing actions."""
        if not self.tracer:
            return

        input_val = json.dumps({
            "task_id": task_id,
            "node_updates": node_updates,
            "items": items_applied,
            "ambiguity_flags": ambiguity_flags,
        }, default=str)

        output_val = json.dumps({
            "cascades_fired": cascades_fired,
            "tasks_created": tasks_created,
            "updates_applied": len(node_updates),
            "items_applied": len(items_applied),
            "flags_raised": len(ambiguity_flags),
        })

        span = self.tracer.start_span(
            name=f"post_processing:{task_id}",
            attributes={
                "pp.task_id": task_id,
                "pp.task_output_index": task_output_index,
                "pp.cascades_fired": json.dumps(cascades_fired),
                "pp.tasks_created": json.dumps(tasks_created),
                "pp.ambiguity_flags_count": len(ambiguity_flags),
                "pp.items_applied_count": len(items_applied),
                "pp.node_updates_count": len(node_updates),
                "pp.node_updates": json.dumps(node_updates)[:3000],
                "openinference.span.kind": "TOOL",
                "input.value": input_val[:5000],
                "output.value": output_val,
            },
        )
        span.set_status(StatusCode.OK)
        span.end()

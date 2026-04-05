"""
Phoenix OTEL tracer for replay and eval runs.

Data model:
  Session (session.id = run_id)     → one replay run
    Trace (trace_id per message)    → one message processing
      Span: routing                 → routing decision
      Span: task_resolution         → entity → task resolution
      Span: llm:update_agent        → LLM call with full prompt/output
      Span: post_processing         → node updates, items, flags applied

Supports 1-2 Phoenix endpoints (e.g., local + remote) with dual-write.
Each endpoint receives identical trace data via separate BatchSpanProcessors.

Usage:
    tracer = ReplayTracer(
        project_name="mantri",
        phoenix_endpoints=["http://localhost:6006/v1/traces",
                          "http://droplet:80/developer/phoenix/v1/traces"],
    )
    tracer.start(run_ctx)
    with tracer.trace_message(msg, seq=0) as mt:
        mt.record_routing(...)
        mt.record_llm_call(...)
    tracer.stop()
"""

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import requests as _requests

from opentelemetry import trace, context as otel_context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import StatusCode

log = logging.getLogger(__name__)

# Default: remote droplet Phoenix
DEFAULT_PHOENIX_ENDPOINT = "http://152.42.156.128/developer/phoenix/v1/traces"

# Cost per token (approximate)
MODEL_COSTS = {
    "claude-sonnet-4-6": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "claude-haiku-4-5-20251001": {"input": 0.80 / 1_000_000, "output": 4.0 / 1_000_000},
    "gemini-2.5-flash": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    costs = MODEL_COSTS.get(model, {"input": 0, "output": 0})
    return tokens_in * costs["input"] + tokens_out * costs["output"]


def _now_ns() -> int:
    return int(time.time() * 1_000_000_000)


def check_phoenix_connectivity(endpoint: str, auth: dict | None = None,
                                timeout: int = 5) -> bool:
    """Check if a Phoenix endpoint is reachable. Returns True if healthy."""
    # Convert /v1/traces to /healthz for the check
    base = endpoint.replace("/v1/traces", "").rstrip("/")
    try:
        resp = _requests.get(f"{base}/healthz", headers=auth or {}, timeout=timeout)
        return resp.text.strip() == "OK"
    except Exception as e:
        log.warning("Phoenix endpoint unreachable: %s — %s", base, e)
        return False


@dataclass
class RunContext:
    run_id: str
    case_id: str
    run_type: str = "live_replay"
    git_commit: str = ""
    config_flags: dict = field(default_factory=dict)
    run_notes: str = ""


class ReplayTracer:
    """
    Tracer for replay runs. Supports 1-2 Phoenix endpoints with dual-write.
    Each instance has its own TracerProvider (no global state).
    """

    def __init__(self, project_name: str = "mantri",
                 phoenix_endpoints: list[str] | None = None,
                 auth_headers: dict | None = None):
        """
        Args:
            project_name: Phoenix project name (default: mantri)
            phoenix_endpoints: list of 1-2 OTEL trace endpoints
                              (default: [remote droplet])
            auth_headers: HTTP headers for authenticated endpoints (e.g., basic auth)
        """
        self.project_name = project_name
        # None  → use default remote endpoint
        # []    → no tracing (no-op tracer)
        # [eps] → use exactly these endpoints
        self.phoenix_endpoints = phoenix_endpoints if phoenix_endpoints is not None else [DEFAULT_PHOENIX_ENDPOINT]
        self.auth_headers = auth_headers or {}
        self._provider: TracerProvider | None = None
        self._tracer: trace.Tracer | None = None
        self._run_ctx: RunContext | None = None
        self._total_tokens_in = 0
        self._total_tokens_out = 0
        self._total_cost = 0.0
        self._active_endpoints: list[str] = []

    def start(self, run_ctx: RunContext):
        """
        Initialize TracerProvider with one BatchSpanProcessor per endpoint.
        Checks connectivity first — skips unreachable endpoints with a warning.
        Fails if no endpoints are reachable.

        Pass phoenix_endpoints=[] to run as a no-op tracer (no spans sent).
        Pass phoenix_endpoints=None to use the default remote endpoint.

        Uses phoenix.otel.register() for the first endpoint (correct project targeting),
        then adds additional exporters for dual-write.
        """
        self._run_ctx = run_ctx

        # Empty list = no-op tracer (e.g. --run-cache without --traced)
        if not self.phoenix_endpoints:
            log.info("Tracing disabled (no endpoints configured)")
            return

        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from phoenix.otel import register

        # Check connectivity for each endpoint
        self._active_endpoints = []
        for ep in self.phoenix_endpoints:
            if check_phoenix_connectivity(ep, auth=self.auth_headers):
                self._active_endpoints.append(ep)
                log.info("Phoenix endpoint OK: %s", ep)
            else:
                log.warning("Phoenix endpoint UNREACHABLE (skipping): %s", ep)

        if not self._active_endpoints:
            raise ConnectionError(
                f"No Phoenix endpoints reachable: {self.phoenix_endpoints}"
            )

        # Get the Resource from phoenix.otel.register() — this sets project.name
        # correctly. But don't use its provider — build our own with explicit
        # HTTP exporters for all endpoints.
        temp_provider = register(
            project_name=self.project_name,
            endpoint=self._active_endpoints[0],
            headers=dict(self.auth_headers),
            set_global_tracer_provider=False,
        )
        # Steal the resource, then shut down the temp provider
        resource = temp_provider.resource
        temp_provider.shutdown()

        # Build our own provider with explicit HTTP exporters for ALL endpoints
        self._provider = TracerProvider(resource=resource)
        for ep in self._active_endpoints:
            headers = dict(self.auth_headers)
            exporter = OTLPSpanExporter(endpoint=ep, headers=headers)
            self._provider.add_span_processor(BatchSpanProcessor(exporter))
            log.info("Exporter added: %s", ep)

        self._tracer = self._provider.get_tracer("mantri.replay")
        self._run_ctx = run_ctx

        log.info("Tracing started: project=%s run=%s endpoints=%s",
                 self.project_name, run_ctx.run_id,
                 [ep.split("/")[2] for ep in self._active_endpoints])

    def stop(self, stats: dict | None = None):
        """Flush and shutdown."""
        if self._provider:
            self._provider.force_flush(timeout_millis=10000)
            self._provider.shutdown()
            log.info("Tracing stopped. Total: %d tokens in, %d out, $%.4f → %d endpoints",
                     self._total_tokens_in, self._total_tokens_out,
                     self._total_cost, len(self._active_endpoints))

    @contextmanager
    def trace_message(self, message: dict, seq: int):
        """
        Create a new TRACE for this message (its own trace_id).
        The root span represents the message processing.
        session.id groups all message traces into one replay run.
        """
        if not self._tracer:
            yield MessageTrace()
            return

        msg_id = message.get("message_id", f"msg_{seq}")
        body = (message.get("body") or "")[:500]
        start_ns = _now_ns()

        # Fresh context = new trace_id, no parent
        ctx = otel_context.Context()

        root_span = self._tracer.start_span(
            name=f"message:{msg_id}",
            context=ctx,
            start_time=start_ns,
            attributes={
                "session.id": self._run_ctx.run_id,
                "openinference.span.kind": "CHAIN",
                "input.value": body,
                "message.id": msg_id,
                "message.seq": seq,
                "message.sender_jid": message.get("sender_jid", ""),
                "message.group_id": message.get("group_id", ""),
                "message.has_image": bool(message.get("image_path") or message.get("image_bytes")),
                "run.case_id": self._run_ctx.case_id,
                "run.git_commit": self._run_ctx.git_commit,
                "run.notes": self._run_ctx.run_notes,
            },
        )

        token = otel_context.attach(trace.set_span_in_context(root_span))
        mt = MessageTrace(tracer=self._tracer, parent_tracer=self)

        try:
            yield mt
            root_span.set_status(StatusCode.OK)
        except Exception as e:
            root_span.set_status(StatusCode.ERROR, str(e))
            root_span.record_exception(e)
            raise
        finally:
            root_span.set_attribute("output.value", json.dumps({
                "routed": mt.route_data.get("routes", []) != [],
                "tokens_in": mt.msg_tokens_in,
                "tokens_out": mt.msg_tokens_out,
                "cost_usd": round(mt.msg_cost, 6),
            }))
            if mt.msg_tokens_in > 0:
                root_span.set_attribute("llm.token_count.prompt", mt.msg_tokens_in)
                root_span.set_attribute("llm.token_count.completion", mt.msg_tokens_out)
                root_span.set_attribute("llm.token_count.total",
                                        mt.msg_tokens_in + mt.msg_tokens_out)
            root_span.end(end_time=_now_ns())
            otel_context.detach(token)


@dataclass
class MessageTrace:
    """Accumulates trace data for a single message (one trace)."""
    tracer: trace.Tracer | None = None
    parent_tracer: ReplayTracer | None = None
    route_data: dict = field(default_factory=dict)
    msg_tokens_in: int = 0
    msg_tokens_out: int = 0
    msg_cost: float = 0.0

    def record_routing(self, routes: list[tuple[str, float]],
                       layer: str = "", is_noise: bool = False):
        self.route_data = {
            "routes": [(eid, conf) for eid, conf in routes],
            "layer": layer,
            "is_noise": is_noise,
        }
        if not self.tracer:
            return

        input_val = json.dumps({"layer": layer, "is_noise": is_noise})
        output_val = json.dumps({"routed": len(routes) > 0,
                                  "entities": [eid for eid, _ in routes],
                                  "confidences": [conf for _, conf in routes]})

        span = self.tracer.start_span(
            name="routing",
            attributes={
                "openinference.span.kind": "CHAIN",
                "input.value": input_val,
                "output.value": output_val,
                "routing.entity_ids": json.dumps([eid for eid, _ in routes]),
                "routing.confidences": json.dumps([conf for _, conf in routes]),
                "routing.layer": layer,
                "routing.is_noise": is_noise,
                "routing.route_count": len(routes),
            },
        )
        span.set_status(StatusCode.OK)
        span.end()

    def record_task_resolution(self, entity_id: str, entity_tasks: list[dict],
                                resolved_task_id: str | None,
                                resolution_method: str):
        if not self.tracer:
            return

        tasks_summary = [
            {"task_id": t["task_id"], "order_type": t["order_type"],
             "is_mature": t.get("is_mature", False),
             "item_count": len(t.get("items", []))}
            for t in entity_tasks
        ]

        span = self.tracer.start_span(
            name="task_resolution",
            attributes={
                "openinference.span.kind": "CHAIN",
                "input.value": json.dumps({"entity_id": entity_id,
                                            "task_count": len(entity_tasks)}),
                "output.value": json.dumps({"resolved_task_id": resolved_task_id,
                                             "method": resolution_method}),
                "resolution.entity_id": entity_id,
                "resolution.method": resolution_method,
                "resolution.resolved_task_id": resolved_task_id or "",
                "resolution.entity_tasks": json.dumps(tasks_summary),
                "resolution.task_count": len(entity_tasks),
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
                        is_retry: bool = False, cache_hit: bool = False):
        if not self.tracer:
            return

        cost = _estimate_cost(model, tokens_in, tokens_out)
        self.msg_tokens_in += tokens_in
        self.msg_tokens_out += tokens_out
        self.msg_cost += cost
        if self.parent_tracer:
            self.parent_tracer._total_tokens_in += tokens_in
            self.parent_tracer._total_tokens_out += tokens_out
            self.parent_tracer._total_cost += cost

        end_ns = _now_ns()
        start_ns = end_ns - (latency_ms * 1_000_000)

        span = self.tracer.start_span(
            name=f"llm:{call_type}",
            start_time=start_ns,
            attributes={
                "openinference.span.kind": "LLM",
                "input.value": f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_section}",
                "output.value": raw_output,
                "llm.model_name": model,
                "llm.token_count.prompt": tokens_in,
                "llm.token_count.completion": tokens_out,
                "llm.token_count.total": tokens_in + tokens_out,
                "llm.cost_usd": round(cost, 6),
                "llm.cache_creation_tokens": cache_creation,
                "llm.cache_read_tokens": cache_read,
                "llm.latency_ms": latency_ms,
                "llm.parse_success": parse_success,
                "llm.is_retry": is_retry,
                "cache.hit": cache_hit,
                "llm.call_type": call_type,
                "llm.task_id": task_id,
                "llm.model_selection_reason": model_selection_reason,
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
        if not self.tracer:
            return

        input_val = json.dumps({
            "node_updates": node_updates,
            "items": items_applied,
            "ambiguity_flags": ambiguity_flags,
        }, default=str)[:5000]

        output_val = json.dumps({
            "updates_applied": len(node_updates),
            "items_applied": len(items_applied),
            "flags_raised": len(ambiguity_flags),
            "cascades_fired": cascades_fired,
            "tasks_created": tasks_created,
        })

        span = self.tracer.start_span(
            name=f"post_processing:{task_id}",
            attributes={
                "openinference.span.kind": "TOOL",
                "input.value": input_val,
                "output.value": output_val,
                "pp.task_id": task_id,
                "pp.task_output_index": task_output_index,
                "pp.node_updates_count": len(node_updates),
                "pp.items_applied_count": len(items_applied),
                "pp.ambiguity_flags_count": len(ambiguity_flags),
                "pp.node_updates": json.dumps(node_updates)[:3000],
                "pp.cascades_fired": json.dumps(cascades_fired),
                "pp.tasks_created": json.dumps(tasks_created),
            },
        )
        span.set_status(StatusCode.OK)
        span.end()

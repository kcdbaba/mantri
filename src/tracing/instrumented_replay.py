"""
Instrumented replay runner — wraps the production pipeline with Phoenix tracing.

Non-invasive: uses monkey-patching during replay only. Production code unchanged.
Uses process_message() — the actual entity-first production code path.

Patches:
  - route() → capture routing decisions
  - _call_with_retry() → capture full prompts, raw output, tokens
  - _resolve_task_for_entity() → capture entity→task resolution
  - _apply_output() → capture post-processing (cascades, items, flags)
"""

import json
import logging
import time
from pathlib import Path
from unittest.mock import patch

from src.tracing.tracer import ReplayTracer, RunContext, MessageTrace

log = logging.getLogger(__name__)


def run_instrumented_replay(
    case_dir: Path,
    trace_messages: list[dict],
    seed: dict,
    db_path: str,
    mock_redis,
    run_ctx: RunContext,
    run_linkage: bool = True,
    max_messages: int | None = None,
    phoenix_endpoints: list[str] | None = None,
    auth_headers: dict | None = None,
) -> dict:
    """
    Run the full entity-first pipeline with Phoenix trace capture.

    Args:
        phoenix_endpoints: list of 1-2 Phoenix OTEL endpoints (default: remote droplet)
        auth_headers: HTTP headers for authenticated endpoints

    Returns stats dict compatible with test_live_replay.py expectations.
    """
    tracer = ReplayTracer(
        project_name="mantri",
        phoenix_endpoints=phoenix_endpoints,
        auth_headers=auth_headers,
    )
    tracer.start(run_ctx)

    monitored = seed.get("monitored_groups", {})
    messages_to_process = trace_messages[:max_messages] if max_messages else trace_messages
    progress_path = case_dir / "replay_progress.json"
    t_start = time.time()

    stats = {
        "messages_total": len(messages_to_process),
        "messages_routed": 0,
        "messages_unrouted": 0,
        "messages_noise": 0,
        "update_agent_calls": 0,
        "update_agent_failures": 0,
        "linkage_events_processed": 0,
        "linkage_agent_failures": 0,
        "tasks_created_live": 0,
        "errors": [],
    }

    def _write_progress(phase: str, detail: str = ""):
        elapsed = time.time() - t_start
        progress = {
            "phase": phase,
            "detail": detail,
            "elapsed_s": round(elapsed, 1),
            "stats": {k: v for k, v in stats.items() if k != "errors"},
            "error_count": len(stats["errors"]),
        }
        try:
            progress_path.write_text(json.dumps(progress, indent=2, default=str))
        except Exception:
            pass

    with patch("src.store.db.DB_PATH", db_path), \
         patch("src.config.DB_PATH", db_path), \
         patch("src.router.router.MONITORED_GROUPS", monitored):

        from src.router.router import route as _orig_route
        from src.router.worker import (
            process_message,
            _resolve_task_for_entity as _orig_resolve,
            _apply_output as _orig_apply,
        )
        from src.linkage.linkage_worker import process_event
        from src.agent.update_agent import (
            _call_with_retry as _orig_call,
            _select_model, _is_complex_message,
            _parse_raw,
        )

        # Mutable container for the current message's trace context
        _mt: list[MessageTrace] = [MessageTrace()]

        # ── Phase 1a: Wrap route() ──────────────────────────────────────

        def _traced_route(message):
            mt = _mt[0]
            routes = _orig_route(message)

            if not routes:
                body = message.get("body") or ""
                has_content = body.strip() or message.get("image_path") or message.get("image_bytes")
                if not has_content:
                    stats["messages_noise"] += 1
                    mt.record_routing([], is_noise=True)
                else:
                    stats["messages_unrouted"] += 1
                    mt.record_routing([], layer="unrouted")
            else:
                stats["messages_routed"] += 1
                group_id = message.get("group_id", "")
                layer = "2a" if group_id in monitored else "2b"
                mt.record_routing(routes, layer=layer)

            return routes

        # ── Phase 1a: Wrap _call_with_retry() for full prompt capture ───

        def _traced_call_with_retry(system_prompt, user_section,
                                     message_id, task_id, **kwargs):
            mt = _mt[0]
            model = kwargs.get("model", "unknown")

            t0 = time.time()
            resp = _orig_call(system_prompt, user_section,
                              message_id, task_id, **kwargs)
            latency_ms = int((time.time() - t0) * 1000)

            # Determine model selection reason from messages context
            # (we don't have messages here, but model is already selected)
            is_retry = kwargs.get("max_retries", 3) < 3

            mt.record_llm_call(
                call_type="update_agent",
                task_id=task_id,
                system_prompt=system_prompt,
                user_section=user_section,
                raw_output=resp.raw if resp else "(failed)",
                parsed_output=None,  # parsed later by caller
                model=model,
                model_selection_reason="",  # set by run_update_agent wrapper
                tokens_in=resp.tokens_in if resp else 0,
                tokens_out=resp.tokens_out if resp else 0,
                cache_creation=resp.cache_creation_tokens if resp else 0,
                cache_read=resp.cache_read_tokens if resp else 0,
                latency_ms=latency_ms,
                parse_success=True,  # parse happens later
                is_retry=is_retry,
            )

            stats["update_agent_calls"] += 1
            if resp is None:
                stats["update_agent_failures"] += 1

            return resp

        # ── Phase 1b: Wrap _resolve_task_for_entity() ───────────────────

        def _traced_resolve(entity_id, entity_tasks, message, r):
            mt = _mt[0]
            result = _orig_resolve(entity_id, entity_tasks, message, r)

            if not entity_tasks:
                method = "nil_create"
                stats["tasks_created_live"] += 1
            elif len(entity_tasks) == 1:
                method = "single_task"
            else:
                method = "agent_assignment" if result is None else "resolved"

            mt.record_task_resolution(
                entity_id=entity_id,
                entity_tasks=entity_tasks,
                resolved_task_id=result,
                resolution_method=method,
            )
            return result

        # ── Phase 1c: Wrap _apply_output() ──────────────────────────────

        _apply_idx = [0]  # counter for task_output_index

        def _traced_apply(task_id, order_type, output, message, r):
            mt = _mt[0]
            idx = _apply_idx[0]
            _apply_idx[0] += 1

            # Capture what's about to be applied
            node_updates = [
                {"node_id": u.node_id, "new_status": u.new_status,
                 "confidence": u.confidence, "evidence": u.evidence}
                for u in output.node_updates
            ]
            ambiguity_flags = [
                {"description": f.description, "severity": f.severity,
                 "category": f.category, "blocking_node_id": f.blocking_node_id}
                for f in output.ambiguity_flags
            ]
            items = [
                {"operation": i.operation, "description": i.description,
                 "quantity": i.quantity}
                for i in output.item_extractions
            ]

            # Call the real function
            _orig_apply(task_id, order_type, output, message, r)

            # Record post-processing (cascades are logged inside _apply_output
            # but we can't easily intercept them — record what we know)
            mt.record_post_processing(
                task_id=task_id,
                task_output_index=idx,
                cascades_fired=[],  # would need deeper patch to capture
                tasks_created=[],
                ambiguity_flags=ambiguity_flags,
                items_applied=items,
                node_updates=node_updates,
            )

        # ── Main replay loop ────────────────────────────────────────────

        _write_progress("processing", f"0/{len(messages_to_process)} messages")

        with patch("src.router.worker.route", _traced_route), \
             patch("src.agent.update_agent._call_with_retry", _traced_call_with_retry), \
             patch("src.router.worker._resolve_task_for_entity", _traced_resolve), \
             patch("src.router.worker._apply_output", _traced_apply):

            for i, msg in enumerate(messages_to_process):
                if (i + 1) % 25 == 0:
                    log.info("Processing message %d/%d: %s",
                             i + 1, len(messages_to_process), msg.get("message_id"))
                    _write_progress("processing",
                                    f"{i+1}/{len(messages_to_process)} messages")

                _apply_idx[0] = 0  # reset per message

                with tracer.trace_message(msg, seq=i) as mt:
                    _mt[0] = mt

                    try:
                        process_message(msg, mock_redis)
                    except Exception as e:
                        stats["errors"].append({
                            "phase": "process_message",
                            "message_id": msg.get("message_id"),
                            "error": str(e),
                        })
                        log.error("process_message failed for %s: %s",
                                  msg.get("message_id"), e)
                        continue

                    # Feed stream events to linkage worker
                    if run_linkage:
                        new_events = mock_redis.drain_events()
                        for stream_key, fields in new_events:
                            try:
                                process_event(f"replay-{i}", fields, mock_redis)
                                stats["linkage_events_processed"] += 1
                            except Exception as e:
                                stats["linkage_agent_failures"] += 1
                                stats["errors"].append({
                                    "phase": "linkage_agent",
                                    "message_id": msg.get("message_id"),
                                    "error": str(e),
                                })

    _write_progress("complete", f"done in {time.time()-t_start:.0f}s")
    tracer.stop(stats=stats)

    return stats

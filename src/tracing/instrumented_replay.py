"""
Instrumented replay runner — wraps the production pipeline with Phoenix tracing.

Non-invasive: uses monkey-patching during replay only. Production code unchanged.
Uses replay_messages() from worker.py — the shared processing function for both
production and test harness. Only infrastructure (Redis, DB path) is mocked.

Patches (tracing only, no behavior change):
  - route() → capture routing decisions
  - _call_with_retry() → capture LLM prompts, raw output, tokens
  - _resolve_task_for_entity() → capture entity→task resolution
  - _apply_output() → capture post-processing (cascades, items, flags)

Dev-test mode adds one behavior change: LLM response caching via _call_with_retry patch.
"""

import contextlib
import json
import logging
import time
from pathlib import Path
from unittest.mock import patch

import src.api_guard
src.api_guard.activate()

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
    no_conv_llm: bool = False,
    dev_test: bool = False,
    allow_api_calls: bool = True,
) -> dict:
    """
    Run the full pipeline with Phoenix trace capture.

    Uses replay_messages() from worker.py — the shared processing function.
    Only mocks infrastructure (Redis, DB path). All business logic (routing,
    sender-scrap batching, conversation routing, agent calls, output application)
    runs through production code unchanged.
    """
    test_window = seed.get("test_window", {})
    warmup_end_ts = test_window.get("warmup_end_ts", 0)
    config_overrides = seed.get("config_overrides", {})

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
        "messages_total": 0,
        "messages_routed": 0,
        "messages_unrouted": 0,
        "messages_noise": 0,
        "warmup_messages": 0,
        "update_agent_calls": 0,
        "update_agent_failures": 0,
        "linkage_events_processed": 0,
        "linkage_agent_failures": 0,
        "tasks_created_live": 0,
        "conversations_created": 0,
        "entities_discovered": 0,
        "errors": [],
    }

    # LLM response cache — always active, saves money on re-runs
    from src.tracing import agent_cache
    from src.tracing.agent_cache import CacheMissError
    cache_path = str(case_dir / "dev_cache.db")
    agent_cache.init(cache_path)

    # Conversation routing setup — always enabled
    from src.conversation.conversation_router import (
        ConversationRouter, load_ocr_cache, set_ocr_cache,
    )
    conv_router = ConversationRouter(
        enable_llm_matching=not no_conv_llm,
    )
    for group_id, ocr_file in seed.get("ocr_caches", {}).items():
        ocr_path = case_dir / ocr_file
        if not ocr_path.exists():
            ocr_path = case_dir.parent / ocr_file
        if ocr_path.exists():
            ocr = load_ocr_cache(str(ocr_path))
            set_ocr_cache(group_id, ocr)
            log.info("Loaded OCR cache for %s: %s", group_id, ocr_path)

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

    # Split messages into warmup and test phases
    # Warmup always runs (builds conv router state). For dev-test, the
    # 50-message limit applies to test messages only — warmup is free
    # after first run (LLM calls cached).
    if warmup_end_ts:
        warmup_msgs = [m for m in trace_messages if m.get("timestamp", 0) < warmup_end_ts]
        test_pool = [m for m in trace_messages if m.get("timestamp", 0) >= warmup_end_ts]
        test_msgs = test_pool[:max_messages] if max_messages else test_pool
    else:
        warmup_msgs = []
        test_msgs = messages_to_process

    stats["warmup_messages"] = len(warmup_msgs)
    stats["messages_total"] = len(test_msgs)

    # Build dynamic patches from config_overrides
    config_patches = [
        patch("src.store.db.DB_PATH", db_path),
        patch("src.config.DB_PATH", db_path),
        patch("src.router.router.MONITORED_GROUPS", monitored),
        patch("src.config.PERMIT_API", allow_api_calls),
    ]

    with contextlib.ExitStack() as stack:
        stack.callback(lambda: tracer.stop(stats=stats))
        for p in config_patches:
            stack.enter_context(p)

        from src.router.router import route as _orig_route
        from src.router.worker import (
            replay_messages,
            _resolve_task_for_entity as _orig_resolve,
            _apply_output as _orig_apply,
        )
        from src.linkage.linkage_worker import process_event
        from src.agent.update_agent import (
            _call_with_retry as _orig_call,
            _call_anthropic_with_retry as _orig_anthropic,
            _call_gemini_with_retry as _orig_gemini,
            LLMResponse,
        )
        from src.config import CLAUDE_MODEL, GEMINI_MODEL

        # Mutable tracing context
        _mt = [MessageTrace()]
        _apply_idx = [0]

        # ── Tracing wrappers (no behavior change except dev_test cache) ──

        def _traced_route(message):
            mt = _mt[0]
            routes = _orig_route(message)
            if not routes:
                body = message.get("body") or ""
                has_content = body.strip() or message.get("image_path") or message.get("image_bytes")
                if not has_content:
                    mt.record_routing([], is_noise=True)
                else:
                    mt.record_routing([], layer="unrouted")
            elif routes == [("__conv_pending__", 0.0)]:
                mt.record_routing(routes, layer="conv_pending")
            else:
                group_id = message.get("group_id", "")
                layer = "2a" if group_id in monitored else "2b"
                mt.record_routing(routes, layer=layer)
            return routes

        def _traced_call_with_retry(system_prompt, user_section,
                                     message_id, task_id, **kwargs):
            mt = _mt[0]
            model = kwargs.get("model", "unknown")

            # Cache check before LLM call
            cache_key = agent_cache.make_key(system_prompt, user_section)
            cached = agent_cache.get(cache_key)
            if cached:
                resp = LLMResponse(
                    raw=cached["raw"],
                    tokens_in=cached["tokens_in"],
                    tokens_out=cached["tokens_out"],
                    cache_creation_tokens=cached.get("cache_creation_tokens", 0),
                    cache_read_tokens=cached.get("cache_read_tokens", 0),
                )
                mt.record_llm_call(
                    call_type="update_agent", task_id=task_id,
                    system_prompt="(cached)", user_section="(cached)",
                    raw_output=resp.raw, parsed_output=None,
                    model=f"{model} (cached)", model_selection_reason="cache_hit",
                    tokens_in=0, tokens_out=0, cache_creation=0, cache_read=0,
                    latency_ms=0, parse_success=True, is_retry=False,
                )
                return resp

            if not allow_api_calls:
                raise CacheMissError(
                    phase="update_agent",
                    key=cache_key,
                    model=model,
                    task_id=task_id,
                    message_id=message_id,
                    allow_api_calls=allow_api_calls,
                )

            t0 = time.time()
            resp = _orig_call(system_prompt, user_section,
                              message_id, task_id, **kwargs)
            latency_ms = int((time.time() - t0) * 1000)

            if resp:
                agent_cache.put(cache_key, resp.raw, {
                    "tokens_in": resp.tokens_in,
                    "tokens_out": resp.tokens_out,
                    "cache_creation_tokens": resp.cache_creation_tokens,
                    "cache_read_tokens": resp.cache_read_tokens,
                })

            is_retry = kwargs.get("max_retries", 3) < 3
            mt.record_llm_call(
                call_type="update_agent", task_id=task_id,
                system_prompt=system_prompt, user_section=user_section,
                raw_output=resp.raw if resp else "(failed)",
                parsed_output=None, model=model, model_selection_reason="",
                tokens_in=resp.tokens_in if resp else 0,
                tokens_out=resp.tokens_out if resp else 0,
                cache_creation=resp.cache_creation_tokens if resp else 0,
                cache_read=resp.cache_read_tokens if resp else 0,
                latency_ms=latency_ms, parse_success=True, is_retry=is_retry,
            )
            return resp

        # ── Cache wrappers for low-level SDK call functions ────────────
        # These cover ALL callers: update agent (via _call_with_retry) AND
        # linkage agent (calls _call_anthropic/_call_gemini directly).

        def _cached_anthropic(system_prompt, user_section,
                              message_id, task_id, **kwargs):
            model = kwargs.get("model", CLAUDE_MODEL)
            cache_key = agent_cache.make_key(system_prompt, user_section)
            cached = agent_cache.get(cache_key)
            if cached:
                return LLMResponse(
                    raw=cached["raw"], tokens_in=cached["tokens_in"],
                    tokens_out=cached["tokens_out"],
                    cache_creation_tokens=cached.get("cache_creation_tokens", 0),
                    cache_read_tokens=cached.get("cache_read_tokens", 0),
                )
            if not allow_api_calls:
                raise CacheMissError(
                    phase="anthropic",
                    key=cache_key,
                    model=model,
                    task_id=task_id,
                    message_id=message_id,
                    allow_api_calls=allow_api_calls,
                )
            resp = _orig_anthropic(system_prompt, user_section,
                                   message_id, task_id, **kwargs)
            if resp:
                agent_cache.put(cache_key, resp.raw, {
                    "tokens_in": resp.tokens_in,
                    "tokens_out": resp.tokens_out,
                    "cache_creation_tokens": resp.cache_creation_tokens,
                    "cache_read_tokens": resp.cache_read_tokens,
                })
            return resp

        def _cached_gemini(system_prompt, user_section,
                           message_id, task_id, **kwargs):
            model = kwargs.get("model", GEMINI_MODEL)
            cache_key = agent_cache.make_key(system_prompt, user_section)
            cached = agent_cache.get(cache_key)
            if cached:
                return LLMResponse(
                    raw=cached["raw"], tokens_in=cached["tokens_in"],
                    tokens_out=cached["tokens_out"],
                    cache_creation_tokens=cached.get("cache_creation_tokens", 0),
                    cache_read_tokens=cached.get("cache_read_tokens", 0),
                )
            if not allow_api_calls:
                raise CacheMissError(
                    phase="gemini",
                    key=cache_key,
                    model=model,
                    task_id=task_id,
                    message_id=message_id,
                    allow_api_calls=allow_api_calls,
                )
            resp = _orig_gemini(system_prompt, user_section,
                                message_id, task_id, **kwargs)
            if resp:
                agent_cache.put(cache_key, resp.raw, {
                    "tokens_in": resp.tokens_in,
                    "tokens_out": resp.tokens_out,
                    "cache_creation_tokens": resp.cache_creation_tokens,
                    "cache_read_tokens": resp.cache_read_tokens,
                })
            return resp

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
                entity_id=entity_id, entity_tasks=entity_tasks,
                resolved_task_id=result, resolution_method=method,
            )
            return result

        def _traced_apply(task_id, order_type, output, message, r):
            mt = _mt[0]
            idx = _apply_idx[0]
            _apply_idx[0] += 1
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
            _orig_apply(task_id, order_type, output, message, r)
            mt.record_post_processing(
                task_id=task_id, task_output_index=idx,
                cascades_fired=[], tasks_created=[],
                ambiguity_flags=ambiguity_flags,
                items_applied=items, node_updates=node_updates,
            )

        # ── Cache wrapper for conversation router LLM matching ─────────

        from src.conversation.llm_context_matcher import (
            _try_gemini as _orig_conv_gemini,
        )

        def _cached_conv_gemini(prompt, max_retries=2):
            cache_key = agent_cache.make_key(prompt, "")
            cached = agent_cache.get(cache_key)
            if cached:
                import json as _json
                try:
                    return _json.loads(cached["raw"])
                except Exception:
                    return None

            if not allow_api_calls:
                raise CacheMissError(
                    phase="conv_gemini",
                    key=cache_key,
                    model=GEMINI_MODEL,
                    allow_api_calls=allow_api_calls,
                )

            # Bypass api_guard using original unpatched function
            # PERMIT_API stays False — only this specific call goes through
            import src.api_guard
            orig_fn = src.api_guard._original_genai_generate
            if orig_fn is None:
                result = _orig_conv_gemini(prompt, max_retries)
            else:
                from google.genai.models import Models
                guarded_fn = Models.generate_content
                Models.generate_content = orig_fn
                try:
                    result = _orig_conv_gemini(prompt, max_retries)
                finally:
                    Models.generate_content = guarded_fn
            if result is not None:
                import json as _json
                agent_cache.put(cache_key, _json.dumps(result), {
                    "tokens_in": 0,
                    "tokens_out": 0,
                })
            return result

        # ── Linkage drain ────────────────────────────────────────────────

        def _drain_linkage(scrap=None):
            if not run_linkage:
                return
            new_events = mock_redis.drain_events()
            for stream_key, fields in new_events:
                try:
                    process_event("replay", fields, mock_redis)
                    stats["linkage_events_processed"] += 1
                except CacheMissError:
                    raise
                except Exception as e:
                    stats["linkage_agent_failures"] += 1
                    stats["errors"].append({
                        "phase": "linkage_agent",
                        "message_id": scrap.messages[-1].get("message_id") if scrap else "?",
                        "error": str(e),
                    })
                    log.error("Linkage agent failed: stream=%s task=%s event_type=%s: %s",
                              stream_key, fields.get("task_id", "?"),
                              fields.get("event_type", "?"), e)

        # ── Apply tracing patches ────────────────────────────────────────

        with patch("src.router.worker.route", _traced_route), \
             patch("src.agent.update_agent._call_with_retry", _traced_call_with_retry), \
             patch("src.agent.update_agent._call_anthropic_with_retry", _cached_anthropic), \
             patch("src.agent.update_agent._call_gemini_with_retry", _cached_gemini), \
             patch("src.linkage.agent._call_anthropic_with_retry", _cached_anthropic), \
             patch("src.linkage.agent._call_gemini_with_retry", _cached_gemini), \
             patch("src.conversation.llm_context_matcher._try_gemini", _cached_conv_gemini), \
             patch("src.router.worker._resolve_task_for_entity", _traced_resolve), \
             patch("src.router.worker._apply_output", _traced_apply):

            # Phase 1: Warmup — process through pipeline, no tracing
            if warmup_msgs:
                log.info("Warmup: %d messages", len(warmup_msgs))
                _write_progress("warmup", f"{len(warmup_msgs)} messages")
                replay_messages(
                    warmup_msgs, mock_redis, conv_router=conv_router,
                    on_scrap_processed=lambda s: _drain_linkage(s),
                )
                # Drain any remaining linkage events from warmup
                _drain_linkage()

            # Phase 2: Test — process with linkage drain after each scrap
            log.info("Test: %d messages", len(test_msgs))
            _write_progress("processing", f"0/{len(test_msgs)} messages")

            _msg_count = [0]

            def _on_scrap_processed(scrap):
                _drain_linkage(scrap)

            def _on_message_routed(msg, routes):
                _msg_count[0] += 1
                if _msg_count[0] % 25 == 0:
                    _write_progress("processing",
                                    f"{_msg_count[0]}/{len(test_msgs)} messages")

            replay_stats = replay_messages(
                test_msgs, mock_redis, conv_router=conv_router,
                on_scrap_processed=_on_scrap_processed,
                on_message_routed=_on_message_routed,
            )

            # Merge replay_messages stats
            stats["messages_routed"] = replay_stats["messages_routed"]
            stats["messages_unrouted"] = replay_stats["messages_unrouted"]
            stats["messages_noise"] = replay_stats["messages_noise"]
            stats["update_agent_calls"] = replay_stats["update_agent_calls"]
            stats["errors"].extend(replay_stats["errors"])

            # Drain final linkage events
            _drain_linkage()

        # Close cache and record stats
        stats["cache"] = agent_cache.stats()
        agent_cache.close()

        _write_progress("complete", f"done in {time.time()-t_start:.0f}s")
        # tracer.stop() is called by tracer_stack.callback on exit (normal or exception)

    return stats

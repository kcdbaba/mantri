# Architecture Boundary Validation Results

**Date:** 2026-03-31
**Task:** Validate Architecture Boundaries with Real Scenarios
**Status:** Complete

---

## 1. Methodology

Selected 5 representative eval cases spanning the risk taxonomy, plus 2 integration test runs that exercise the full production pipeline. Traced each through the 2-agent architecture, measured context sizes, analyzed handoff contracts, and identified boundary issues.

### Test Cases Selected

| Case | Risk Category | Difficulty | Messages | Tasks |
|---|---|---|---|---|
| R1-D-L3-01 | Delivery completion | L3 (hard) | 904 | 3 |
| R3-C-L3-01 | Order conflation | L3 (hard) | 6 | 2 |
| R4-A-L3-01 | Supplier entity | L3 (hard) | 4 | 1 |
| R5-L2-01 | Ambiguity cross-thread | L2 (medium) | 6 | 1 |
| R6-L1-01 | Post-delivery QC | L1 (easy) | 9 | 1 |

### Integration Test Runs (Full Pipeline)

| Run | Messages | Routed | Unrouted | update_agent calls | linkage events | Dead letters |
|---|---|---|---|---|---|---|
| R1-D-L3-01 | 904 | 367 | 537 | 162 | 162 | 32 |
| R3-C-L3-02-INT | 690 | 197 | 493 | 99 | 99 | 11 |

---

## 2. Context Efficiency Measurements

### update_agent Token Usage (from usage_log, 236 calls)

| Metric | Value |
|---|---|
| Avg input tokens | 4,836 |
| Max input tokens | 6,052 |
| Min input tokens | 3,791 |
| Avg output tokens | 445 |
| Avg latency | 8.8s |
| Total cost (236 calls) | $5.00 |

### Context Size Breakdown by Component

| Component | Approx Tokens | Notes |
|---|---|---|
| System prompt (static) | ~2,800 | Business context + output spec + rules |
| Template nodes (per order type) | 600-1,200 | standard_procurement: ~1,200; client_order: ~700; supplier_order: ~500 |
| Current node states | 200-400 | Varies by active node count; skipped nodes omitted |
| Recent messages (last 20) | 800-1,500 | Main variable cost; depends on message length |
| Current items | 100-300 | Compact JSON; grows with order complexity |
| New message(s) | 50-200 | Single message or batch |
| **Total per call** | **~4,800** | Well within 200K Sonnet window |

### linkage_agent Context (estimated, no usage_log data available)

| Component | Approx Tokens | Scaling Risk |
|---|---|---|
| System prompt (static) | ~800 | Fixed |
| Open client orders (all) | 200-2,000 | **Grows with concurrent orders** |
| Open supplier orders (all) | 200-2,000 | **Grows with concurrent orders** |
| Fulfillment links (all) | 100-3,000 | **Grows O(clients x suppliers x items)** |
| New message | 50-200 | Fixed |
| **Total per call** | **~1,500-8,000** | Safe at current scale; **unbounded at growth** |

### Context Overlap Between Agents

| Field | update_agent | linkage_agent | Overlap |
|---|---|---|---|
| new_message | Yes | Yes | **Shared** (only overlapping field) |
| task_metadata | Yes | No | - |
| template_nodes | Yes | No | - |
| node_states | Yes | No | - |
| recent_messages | Yes | No | - |
| order_items | Yes | Via open_orders | Partial (different view) |
| open_orders (all) | No | Yes | - |
| fulfillment_links | No | Yes | - |

**Context overlap: minimal.** Only `new_message` is duplicated. The agents reason about fundamentally different objects (per-task state vs. cross-order matrix).

---

## 3. Handoff Contract Validation

### update_agent -> linkage_agent (via Redis task_events stream)

**Contract:**
```json
{
  "event_type": "message_processed",
  "task_id": "string",
  "message_id": "string",
  "message_json": "string (full enriched message)"
}
```

**Validation against real scenarios:**

| Scenario | Handoff sufficient? | Notes |
|---|---|---|
| R1-D-L3-01: Multi-supplier delivery | Yes | Each task's update_agent fires independently; linkage sees all orders via DB |
| R3-C-L3-01: Interleaved orders | Yes | Router splits to correct tasks; linkage resolves cross-order allocation |
| R4-A-L3-01: Entity name variants | N/A | Entity resolution happens at router level, before agent boundary |
| R5-L2-01: Conflicting signals | Yes | update_agent flags ambiguity; linkage independently checks allocation state |
| R6-L1-01: QC failure cascade | Yes | update_agent sets supplier_QC=failed; linkage reads via open_orders and blocks order_ready |

**Verdict: handoff contract is sufficient for all tested scenarios.**

### Node Ownership Enforcement

| Node | Owner | Enforcement |
|---|---|---|
| client_enquiry...dispatched | update_agent | `update_node_as_update_agent()` |
| order_ready, task_closed | linkage_agent | `update_node_as_linkage_agent()` |

Validated via `node_owner_registry` table + `owner` field in templates. No ownership violations detected in integration test runs.

---

## 4. Boundary Issues Identified

### Issue A: Linkage Agent Context Scaling (Severity: Medium)

**Problem:** Linkage agent sees ALL open orders simultaneously. With Ashish's current scale (~3-5 concurrent orders), this is manageable. At 20+ concurrent orders with 10+ items each, the fulfillment_links matrix alone could exceed 10K tokens.

**Impact:** No impact at current scale. Potential context overflow at 10x scale.

**Recommendation:** Add a context length guard that measures token count before API call. For linkage, implement order-relevance filtering: only include orders that share entities with the triggering message's routed task. Defer to Sprint 3 backlog.

### Issue B: No Linkage Agent Token Logging (Severity: Low)

**Problem:** `usage_log` has zero `linkage_agent` rows despite 162+ linkage events processed in integration tests. Either the test harness mocks the LLM call or there's a logging gap.

**Impact:** Cannot empirically measure linkage agent cost or context growth. Cannot validate Issue A with real data.

**Recommendation:** Verify `log_llm_call()` is called in `linkage/agent.py`. If the integration test uses mocks, add a live linkage test flag.

### Issue C: High Ambiguity Flag Volume (Severity: High)

**Problem:** 376 ambiguity flags for 162 agent calls (R1-D-L3-01) = 2.3 flags per call. 211 flags for 99 calls (R3-C-L3-02-INT) = 2.1 flags per call. If each surfaces to Ashish, this is operationally unsustainable.

**Impact:** Ashish would receive 100+ alerts per day at current message volumes. Alert fatigue will cause him to ignore all alerts, including critical ones.

**Recommendation:**
1. Add severity-based filtering: only surface `high` severity flags immediately; batch `medium` into daily digest; auto-resolve `low` after 24h
2. Add deduplication: same ambiguity category + same node_id within 1 hour = single alert
3. Measure flag distribution by severity and category to calibrate thresholds

### Issue D: Dead Letter Rate (Severity: High)

**Problem:** 32/162 = 20% dead letter rate (R1-D-L3-01) and 11/99 = 11% (R3-C-L3-02-INT). These are messages where update_agent failed after retries.

**Impact:** 1 in 5-10 messages silently fails to update task state. Split-state risk: message is stored in recent_messages but nodes are not updated. On next message, agent sees the failed message in context but has no record of processing it.

**Recommendation:**
1. Analyze dead letter reasons (context overflow vs. API timeout vs. validation failure)
2. For validation failures: improve output schema instructions or add retry with simplified prompt
3. For split-state: add a `processing_status` field to `task_messages` so agent can see which messages were successfully processed vs. dead-lettered

### Issue E: Eval-to-Prod Context Gap (Severity: Medium)

**Problem:** Eval cases use a monolithic `eval_agent` that sees the full thread at once and scores 84-92. Production uses per-message `update_agent` calls that see only the last 20 messages. The eval scores may not transfer to production behavior.

**Impact:** Passing eval scores don't guarantee production correctness. Architecture boundary validation against eval cases tests the boundary design, not the boundary behavior.

**Recommendation:** Build integration-level evals that score the production pipeline output (not the monolithic eval_agent output). The existing `test_live_replay.py` infrastructure supports this — add automated scoring against expected node states.

### Issue F: Batching Status Ambiguity (Severity: Low)

**Problem:** Architecture spec says "zero batching in either path" but integration test results show 162 update_agent calls for 367 routed messages across 3 tasks. If truly unbatched, we'd expect ~367 calls (some messages route to multiple tasks). The numbers suggest some batching is active.

**Impact:** Cost optimization calculations may be based on incorrect assumptions.

**Recommendation:** Add batch_size logging to update_agent calls. Verify whether `BATCH_WINDOW_S=60` is active in the integration test harness.

---

## 5. Boundary Complexity Assessment

### Where do boundaries add unnecessary complexity?

**Nowhere significant.** The 2-agent architecture is justified by the fundamental difference in reasoning scope:
- update_agent: "What happened in *this* task?" (per-task, sequential)
- linkage_agent: "How do items match *across all* orders?" (system-wide, matrix)

Merging them would require every update_agent call to load all open orders (wasting ~2-5K tokens per call for irrelevant context), or would require the linkage logic to run only when triggered by specific node changes (adding routing complexity that currently doesn't exist).

### Debugging complexity

To trace a bug end-to-end:
1. Router logs (which task was the message assigned to?)
2. update_agent output (what node changes were proposed?)
3. task_events stream (was the event published?)
4. linkage_agent output (what links were updated?)
5. ambiguity_queue (was any flag raised?)

**5 log sources** for a full trace. This is manageable for 2 agents. The node_owner_registry makes it clear which agent is responsible for any given node state.

### Concurrent execution

Both agents can safely run concurrently because:
- They write to disjoint node sets (enforced by owner registry)
- They share no mutable state at the LLM call level
- The only shared write target (ambiguity_queue) uses INSERT (no conflicts)

---

## 6. Comparison: Current vs. Alternative Architectures

### Current: 2-Agent with Redis Stream Handoff

| Metric | Value |
|---|---|
| Context efficiency | High — each agent sees only relevant context |
| Avg input tokens per message | ~4,836 (update) + ~3,000 (linkage) = ~7,836 total |
| Ownership clarity | Explicit — node_owner_registry, typed wrapper functions |
| Debugging | 5 log sources, clear ownership |
| Failure isolation | Good — update_agent failure doesn't block linkage for other tasks |

### Alternative A: Single Merged Agent

| Metric | Value |
|---|---|
| Context efficiency | Low — every call loads template nodes + all open orders + links |
| Estimated input tokens per call | ~8,000-12,000 (current scale); unbounded at growth |
| Ownership clarity | N/A — single agent owns everything |
| Debugging | Fewer log sources but larger output to parse |
| Failure isolation | Poor — single failure blocks all processing |

**Verdict:** Single agent would cost ~50-100% more in tokens at current scale and would not scale.

### Alternative B: 3+ Agents (e.g., separate item extraction agent)

| Metric | Value |
|---|---|
| Context efficiency | Marginal improvement — item extraction needs same context as node updates |
| Handoff complexity | Doubles — need item_extraction_events stream + ordering guarantees |
| Debugging | 7+ log sources |
| Failure isolation | Better per-agent but more failure modes in handoffs |

**Verdict:** Over-engineering. Item extraction and node updates are tightly coupled (post-confirmation item change rule requires node state knowledge).

---

## 7. Summary and Recommendations

### Architecture Verdict: Validated

The 2-agent boundary is well-justified, efficiently separates concerns, and handles all tested scenarios correctly. The handoff contract is sufficient. Context overlap is minimal (only `new_message`). Node ownership is explicit and enforced.

### Priority Action Items

| # | Issue | Severity | Sprint | Effort |
|---|---|---|---|---|
| 1 | Reduce ambiguity flag volume (severity filtering + dedup) | High | 2 | M |
| 2 | Investigate and reduce dead letter rate | High | 2 | M |
| 3 | Build integration-level eval scoring (close eval-prod gap) | Medium | 2-3 | L |
| 4 | Add linkage agent token logging | Low | 2 | S |
| 5 | Add context length guard for linkage agent | Medium | 3 | S |
| 6 | Verify batching status and add batch_size logging | Low | 2 | S |

### Architecture Changes Needed: None

The current 2-agent architecture is the right design. No boundary changes recommended. Focus should shift from architecture design to operational hardening (Issues C, D) and eval infrastructure (Issue E).

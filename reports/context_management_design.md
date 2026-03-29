# Context Management Design

**Workflow:** context_management
**Status:** In progress
**Date:** 2026-03-29

---

## Step 1: Implementation Reality Check

### What Was Planned

The original design called for a background AI agent that:
- Monitors WhatsApp messages and transcribed call recordings (Hindi/English/Assamese)
- Extracts entities (customers, orders, items, vendors)
- Tracks milestones using a node-based task model (three task types: `client_order`, `supplier_order`, `linkage_task`)
- Disambiguates with Ashish when confidence is low
- Surfaces findings via WhatsApp and a dashboard

The implementation approach: **custom Python** (not n8n), chosen for control over LLM call structure and auditability.

---

### What Was Actually Built

The implementation is real and running. Three distinct LLM call paths exist:

#### Call 1: `update_agent` — Per-task context updater
- **File:** `src/agent/update_agent.py`, `src/agent/prompt.py`
- **Trigger:** One call per `(message, task_id)` pair when a new message arrives
- **System prompt:** Static business context + task template node definitions (JSON) + output spec (~2–3K tokens)
- **User section:** Current node states (JSON) + last N messages + new incoming message + optional image bytes
- **Output:** Updated node states (JSON)
- **Token profile:** System ~2–3K (static), user grows with node count and message history

#### Call 2: `linkage_agent` — Cross-order coordinator
- **File:** `src/linkage/agent.py`, `src/linkage/prompt.py`
- **Trigger:** One call per message, across ALL open client and supplier orders simultaneously
- **System prompt:** Static linkage prompt (~1.5K tokens)
- **User section:** All open client orders JSON + all open supplier orders JSON + current fulfilment matrix + new message
- **Output:** `LinkageAgentOutput` (linkage_updates, client_order_updates, new_task_candidates, ambiguity_flags)
- **Token profile:** System static; user grows O(open orders²) — unbounded scaling risk

#### Call 3: `eval agent` — Test harness agent
- **File:** `scripts/run_test.py`
- **Trigger:** Per test case run during evaluation
- **System prompt:** Full `prompts/testing_prompt.txt` (~15K tokens)
- **User section:** Raw `threads.txt` (up to 70K+ chars of message history)
- **Output:** JSON task tree
- **Token profile:** Very large — designed for batch eval, not production

#### Call 4: `eval judge` — Evaluator
- **File:** `scripts/run_test.py`
- **Trigger:** Per test case, after agent produces output
- **System prompt:** Judge prompt (few hundred tokens)
- **User section:** Agent metadata + agent output (JSON)
- **Output:** Pass/fail with per-criterion scores

---

### How This Compares to the Plan

| Dimension | Planned | Actual |
|---|---|---|
| Platform | Custom Python | ✅ Custom Python |
| Task types | 3 types (client_order, supplier_order, linkage_task) | ✅ Implemented |
| LLM call structure | Vaguely "one agent per message" | ✅ Two separate agents (update + linkage) |
| Context scope | Not formally designed | ⚠️ Implicitly unbounded in linkage_agent |
| Eval harness | Not in original plan | ✅ Built with judge + LangSmith tracing |
| Ambiguity routing | Mentioned in design | ✅ `escalation_router.py` fully implemented |

The biggest deviation from plan: **two separate LLM call paths per message** (update + linkage) rather than one unified agent. This emerged from the M:N coordination problem — you can't update individual task nodes and simultaneously reason across all open orders in one prompt without blowing up token count.

---

### Quality Risk Connection

The priority quality risk identified earlier: **hallucinated tasks and missed delivery milestones** due to incomplete data flows.

Three potential failure modes visible in the current context design:

1. **`linkage_agent` sees all open orders** — All open orders appear in every call. Order count is bounded by Ashish's personal bandwidth (he manages delivery with limited staff he can't fully trust), and delivery completions prune the linkage table — so this is not an unbounded scaling risk. At current scale (~5–15 concurrent orders), this is acceptable. Worth monitoring if operational capacity ever changes, but not a priority concern now.

2. **`update_agent` has no cross-task awareness** — Each call only sees one task's node states + recent messages. If a delivery confirmation message implicitly resolves two tasks, only one gets updated per invocation.

3. **`eval agent` uses a monolithic prompt** — The `testing_prompt.txt` at ~15K tokens works for eval but isn't the production architecture. The gap between eval and prod context design means test scores don't cleanly predict prod behaviour.

---

### Discovered Context Patterns

- **Retention policy is implicit** — `update_agent` passes "last N messages" but N is hardcoded; there's no explicit policy for what gets dropped when a conversation grows.
- **Node states as working memory** — The JSON node states in `update_agent` are the only persistent context between invocations. This is the de facto memory system.
- **No cross-agent state sharing** — `update_agent` and `linkage_agent` run independently; their outputs are reconciled downstream, not in-context.
- **LangSmith tracing live** — EU endpoint confirmed working; all production LLM calls are observable.

---

---

## Step 2: LLM Call Inventory

### Call 1: `update_agent` — Per-task state updater
**✅ Essential**

- **Core purpose:** Update a task's node states when a new message arrives. Handles non-deterministic state transitions (e.g. payment before or after delivery), extracts items, and raises ambiguity flags for human resolution.
- **Why LLM and not logic/rules:** State transitions in this business don't follow a fixed order. The space of message intents and orderings can't be encoded in rules. The LLM handles the combinatorial complexity and knows when to commit vs. escalate.
- **Success criteria:** Node states updated correctly; ambiguity flags raised where evidence is insufficient.
- **Failure modes:**
  - Context bleed on multi-entity messages from internal groups (Layer 2b routes message to multiple tasks; each call sees full raw message with no routing signal — model must infer which part is relevant)
  - Node update missed entirely if routing doesn't fire
- **Quality risk connection:** 8/10 — directly determines whether milestones are tracked correctly; wrong node state = wrong dispatch decision downstream
- **Context gap identified:** No routing metadata passed (matched entity, confidence, routing layer). For internal group messages routed to multiple tasks simultaneously, the model has no signal about which entity/event is its concern.

---

### Call 2: `linkage_agent` — Item-level fulfilment coordinator
**✅ Essential**

- **Core purpose:** Maintain the M:N fulfilment matrix — which items from which supplier orders satisfy which client orders, at what quantity. Triggers `order_ready` when all client order items have confirmed allocations.
- **Why LLM and not logic/rules:** Items are bespoke with no catalogue — described in informal Hindi/Hinglish army nomenclature. Matching "chhota round" to "30mm rounds, 50 units" requires semantic reasoning, not string matching. The model also handles partial fulfilment, QC failures, and reorder logic.
- **Success criteria:** `match_confidence ≥ 0.92` required before link becomes "confirmed" and can trigger dispatch. Below that: candidate status + ambiguity flag raised.
- **Failure modes:**
  - False positive: `order_ready` triggered before supplier items actually collected → premature dispatch (irreversible if goods leave warehouse)
  - False negative: `order_ready` blocked even though items are ready → client waits unnecessarily
- **Quality risk connection:** 9/10 — dispatch correctness is the highest-stakes output; false positives are potentially irreversible
- **Context strengths:** Item descriptions captured at order inception and confirmed during order confirmation — model gets full item lists, not just one-line messages. Scale bounded by Ashish's bandwidth; fulfilment links pruned on delivery completion.

---

### Call 3: `eval agent` — Test harness agent
**🔧 LLM for convenience (eval only — not production)**

- **Core purpose:** Simulate the full agent pipeline on static test cases, producing a task tree from raw message threads.
- **Why different from prod:** Uses monolithic `testing_prompt.txt` (~15K tokens) operating on full `threads.txt` in one shot. Production uses per-task, per-message calls.
- **Quality risk connection:** Indirect — this is the measurement tool, not the production path. Test scores reflect monolith behaviour, not modular prod behaviour.
- **Key concern:** Eval vs. prod context gap means test scores don't cleanly transfer. An improvement to `testing_prompt.txt` may not translate to an equivalent improvement in prod `update_agent` + `linkage_agent` calls.
- **Status:** Keep as-is for Sprint 2 evaluation. Close the gap in Sprint 3 by building incremental eval that runs actual prod agents on test data.

---

### Call 4: `eval judge` — Automated scorer
**🔧 LLM for convenience (eval only)**

- **Core purpose:** Score agent output against expected task tree per criterion (R1, R2, R1-D).
- **Why LLM:** Fuzzy matching on task descriptions and node states — hard to do with exact rules given informal language.
- **Quality risk connection:** Indirect — scoring tool. Stochastic: same run can produce ±5% score variance.
- **Status:** Acceptable for now. Worth noting that judge stochasticity is a measurement noise source, not a product quality issue.

---

### Calls to Remove or Defer

None. All four calls are justified. The two eval calls are explicitly tooling, not production.

**Priority context fix identified:** `update_agent` routing metadata gap — pass `routing_confidence` to the call so the model has signal on how certain the router was about this message belonging to this task. Infrequent but significant (internal group multi-entity messages). Routing layer detail not needed.

---

## Step 3: Input Schemas

### `update_agent` context schema

```json
[
  {
    "context_variable_name": "task_metadata",
    "relevance_to_quality": "Tells the model which entity this task is about (client name, supplier name, task type) — anchors all reasoning to the right order",
    "required": true,
    "type": "object",
    "source": "system"
  },
  {
    "context_variable_name": "task_template_nodes",
    "relevance_to_quality": "Defines the node structure for this task type — the model needs to know which nodes exist and what they represent to produce valid updates",
    "required": true,
    "type": "object",
    "source": "system"
  },
  {
    "context_variable_name": "current_node_states",
    "relevance_to_quality": "Current status of each node — without this the model cannot determine what has already been confirmed vs. what needs updating",
    "required": true,
    "type": "array[object]",
    "source": "system"
  },
  {
    "context_variable_name": "recent_messages",
    "relevance_to_quality": "Conversational context for the task — prevents the model from re-flagging ambiguities already resolved in prior messages",
    "required": true,
    "type": "array[object]",
    "source": "system"
  },
  {
    "context_variable_name": "new_message",
    "relevance_to_quality": "The triggering event — body, sender, group, timestamp. The primary input the model acts on",
    "required": true,
    "type": "object",
    "source": "system"
  },
  {
    "context_variable_name": "order_items",
    "relevance_to_quality": "Item list with descriptions, quantities, specs — needed to correctly interpret item-level references in the message and update item extraction nodes",
    "required": true,
    "type": "array[object]",
    "source": "system"
  },
  {
    "context_variable_name": "routing_confidence",
    "relevance_to_quality": "Router's confidence that this message belongs to this task. Low confidence signals a multi-entity or ambiguous message — model should be more conservative about committing node updates and more likely to raise an ambiguity flag",
    "required": true,
    "type": "float",
    "source": "system"
  }
]
```

**Gap:** `routing_confidence` is not currently passed to `run_update_agent`. It exists in the router output `(task_id, confidence)` but is dropped by the time the agent call is assembled.

---

### `linkage_agent` context schema

```json
[
  {
    "context_variable_name": "open_client_orders",
    "relevance_to_quality": "Full item lists for all open client orders — model needs these to identify which client items need fulfilment and at what quantity",
    "required": true,
    "type": "array[object]",
    "source": "system"
  },
  {
    "context_variable_name": "open_supplier_orders",
    "relevance_to_quality": "Full item lists for all open supplier orders — model matches supplier item descriptions to client item descriptions for allocation",
    "required": true,
    "type": "array[object]",
    "source": "system"
  },
  {
    "context_variable_name": "fulfillment_links",
    "relevance_to_quality": "Current M:N allocation matrix — without this the model cannot know what is already confirmed vs. still outstanding, risking duplicate allocations or missed completions",
    "required": true,
    "type": "array[object]",
    "source": "system"
  },
  {
    "context_variable_name": "new_message",
    "relevance_to_quality": "Triggering event — body, sender, group, timestamp. The model only emits linkage updates when the message provides new evidence",
    "required": true,
    "type": "object",
    "source": "system"
  }
]
```

**No gaps identified.** All four fields are currently passed via `build_user_section(open_orders, fulfillment_links, new_message)`.

---

## Step 4: Data Flow Mapping

### `update_agent` — data flow per field

| Field | Acquire source | Retention | Shape transformation | Deliver format |
|---|---|---|---|---|
| `task_metadata` | SQLite `tasks` table via `get_task(task_id)` | persistent | No transform — passed as task dict to `build_system_prompt` | Embedded in system prompt prose |
| `task_template_nodes` | `src/agent/templates.py` `get_template(order_type)` — static JSON per task type | persistent (static file) | JSON-serialised to indented block, embedded in system prompt | JSON block in system prompt |
| `current_node_states` | SQLite `task_nodes` via `get_node_states(task_id)` | persistent (updated after each call) | Stripped to `{node_id (task_id prefix removed), name, status}` — confidence and message_id dropped | JSON array in user section |
| `recent_messages` | SQLite `messages` via `get_recent_messages(task_id, limit=MAX_CONTEXT_MESSAGES)` | persistent (stored), windowed to last N | Each message formatted to single line: `[timestamp] sender (group): body` | Newline-separated lines in user section |
| `new_message` | Redis ingest queue → `worker.process_message()` | call_only at point of LLM use (written to DB via `append_message` before call) | Formatted to single line with optional `[IMAGE ATTACHED]` suffix | Single line in user section |
| `order_items` | SQLite `order_items` via `get_order_items(task_id)` | persistent | Stripped to `{description, unit, quantity, specs}` — internal IDs dropped | JSON array in user section |
| `routing_confidence` | `router.route()` return value `(task_id, confidence)` | call_only — ephemeral (was dropped before this fix; now passed through) | Formatted as `float:.2f` with inline instruction text | Plain line in user section under `## Routing signal` |

**Output delivery:** JSON (`AgentOutput`) → parsed by Pydantic → written to SQLite:
- `node_updates` → `task_nodes` via `update_node()`
- `ambiguity_flags` → `ambiguity_queue`
- `item_extractions` → `order_items` via `apply_item_extractions()`
- `node_data_extractions` → `task_nodes.node_data` via `apply_node_data_extractions()`
- `new_task_candidates` → `task_event_log` (logged only, not yet auto-created)

**Flow gap:** `current_node_states` strips `confidence` before passing to the LLM. The model cannot see how confident the previous call was when it set a node to "provisional" — it only sees the status, not the uncertainty behind it. Low-risk for now but worth noting.

---

### `linkage_agent` — data flow per field

| Field | Acquire source | Retention | Shape transformation | Deliver format |
|---|---|---|---|---|
| `open_client_orders` | SQLite via `get_open_orders_summary()` — all tasks with `order_type=client_order` and status open | persistent | JSON-serialised with full item lists; no stripping | JSON array in user section |
| `open_supplier_orders` | SQLite via `get_open_orders_summary()` — all tasks with `order_type=supplier_order` and status open | persistent | JSON-serialised with full item lists | JSON array in user section |
| `fulfillment_links` | SQLite `fulfillment_links` table — all current M:N allocations across open orders | persistent (updated by linkage agent output) | JSON-serialised; no stripping | JSON array in user section |
| `new_message` | Redis `task_events` stream — published by `worker._publish_task_event()` after `update_agent` completes | call_only | Formatted to single line: `[timestamp] sender (group): body` | Single line in user section |

**Output delivery:** JSON (`LinkageAgentOutput`) → written to SQLite:
- `linkage_updates` → `fulfillment_links` table
- `client_order_updates` → `task_nodes` via `update_node()`
- `ambiguity_flags` → `ambiguity_queue`
- `new_task_candidates` → `task_event_log`

**Sequencing note:** `linkage_agent` runs *after* `update_agent` completes for a given message (triggered via `task_events` stream). This means linkage always sees post-update node states — correct ordering.

---

## Step 5: Context Waste and Gaps

### Finding 1: Tasks never close — open_orders and fulfillment_links grow unboundedly (HIGH)

`get_open_orders_summary()` and `get_active_tasks()` both filter `stage != 'completed'` — the pruning design is correct. But `stage` is **never set to 'completed'** anywhere in the codebase. Every task remains open indefinitely.

**Full fix required (backlog task #2):**
- New linkage states: `fulfilled` (QC pass), `completed` (delivery confirmed), `invalidated` (cancelled/truncated)
- Two-level pruning:
  - Supplier-side: all links for `supplier_order_id` in {fulfilled/failed/invalidated} → delete links, close supplier task
  - Client-side: all links for `client_order_id` all `completed` → delete links, `close_task(client_order_id)`
- Terminal node in all task templates
- `close_task(task_id)` in task_store

**Until fixed:** linkage_agent context grows with every order. At current scale (bounded by Ashish's bandwidth) tolerable but structurally broken.

---

### Finding 2: routing_confidence dropped before update_agent (FIXED)

Fixed in this session — flows through worker → `run_update_agent` → `build_user_section` → prompt.

---

### Finding 3: Node state confidence stripped in update_agent context (BACKLOG #1)

`build_user_section` strips `confidence` from node states — model cannot distinguish a 0.60 provisional from a 0.74 provisional.

---

### Finding 4: order_items passed unconditionally (ACCEPTED)

~75-100 tokens per call saved by conditional inclusion. ~$0.025/day at current volume. Not worth added complexity.

---

### Finding 5: recent_messages time boundary (DEPRIORITISED)

Business operates at pace; slow-moving task edge case is infrequent.

---

### Summary

| Finding | Status |
|---|---|
| Tasks never close / unbounded linkage context | Backlog task #2 |
| routing_confidence dropped | Fixed |
| Node state confidence stripped | Backlog task #1 |
| order_items unconditional | Accepted |
| recent_messages time boundary | Deprioritised |

# Task Lifecycle Graph — Design Document

> **Terminology note**: This document uses "task graph" rather than "state machine." Although the underlying structure is a directed graph and each node has a status, this is *not* a classical state machine — the system does not have a single current state, transitions are not exclusive, logically later nodes can receive updates before earlier ones complete, and nodes marked complete can be reopened. The graph is a **reference model and monitoring scaffold**, not a control flow enforcer.

**Status**: Pre-implementation design exploration
**Target sprint**: Sprint 3 (contingent on Sprint 2 validation with Ashish)
**Author**: Kunal Chowdhury
**Date**: 2026-03-25

---

## Motivation

The current agent produces a flat hierarchical task list from a snapshot of WhatsApp threads. This is stateless — each extraction pass starts from scratch with no memory of prior state. As a result:

- **Implicit tasks are a detection problem**: the agent must infer from context what should happen next. It often misses cadence-driven actions (payment follow-ups, monthly reconciliation) because they have no signal in the current message window.
- **No continuity**: the agent cannot track whether a task has progressed, stalled, or been completed between extraction runs.
- **No learned expectations**: every order is treated as novel. The agent cannot apply prior knowledge of how a tandoor order normally unfolds to flag deviations in the current one.
- **Next step quality is inference-only**: the agent reasons about next steps from first principles each time, rather than from a proven pattern of what comes next in this type of task.

A task lifecycle graph addresses all of these structurally. Templates encode known patterns. Instances track node status. Divergence is a first-class concept. Implicit tasks are pre-loaded nodes, not inferences.

---

## Core Concepts

### 1. Task Template
A directed graph encoding the *expected* lifecycle of a task type. Nodes are subtasks/milestones. Edges encode *typical* ordering and dependencies. Templates are versioned, shared across instances, and evolve over time as patterns are learned.

**Critical distinction**: edges in the template represent expected logical ordering, not enforced control flow. An update arriving for a logically later node before an earlier one is complete is valid and should be recorded — not rejected. The graph is consulted for monitoring and alerting, not for gating.

### 2. Task Instance
A running instance of a template applied to a specific real-world task (e.g., "SATA Tandoor Order, March 2026"). Each node in the instance has an independent status. The instance also stores a delta of structural deviations from the base template (added nodes, skipped nodes, out-of-order updates).

**Node status is independent and revisable**: a node can move from `pending` → `in_progress` → `complete` → `reopened` at any time, regardless of the status of adjacent nodes. A delivery marked complete can be reopened when a quality issue surfaces two days later.

### 3. Node Types
| Type | Description | Activation |
|---|---|---|
| **Agent action** | A recommended next step the agent or staff should take | Becomes visible when predecessor nodes are sufficiently advanced |
| **Real-world milestone** | An event that happens in the world (delivery completed, payment received) | Status updated when detected in incoming messages |
| **Cadence/implicit** | A time-triggered or condition-triggered subtask not visible in any message | Activated by clock or by another node's status change |
| **Decision point** | A branching node where the path forward depends on an outcome | Activated by incoming evidence; branch chosen by agent or human |
| **Human review gate** | A node flagged for explicit human confirmation | Surfaces an alert to Ashish; does not block other nodes |

### 4. Edge Types
Edges encode *expected* relationships, not hard constraints:

| Type | Description |
|---|---|
| **Logical sequence** | A typically precedes B — used for monitoring and divergence detection, not enforcement |
| **Parallel** | A and B are expected to proceed concurrently |
| **Conditional** | A → B if condition X, A → C if condition Y |
| **Time-triggered** | B is expected N days/hours after A completes — overdue triggers an alert |
| **Event-triggered** | B activates when a specific external event is detected |
| **Escalation** | Fires when a time-triggered edge is overdue — generates a human alert |

### 5. Template + Instance Delta Model
Each instance stores: `(template_id, template_version, instance_delta)`. The delta records:
- Nodes added that weren't in the template
- Nodes skipped or bypassed
- Edges that fired in unexpected sequence
- Timeline deviations (node reached N days late/early)
- Template switch events (with reason)

Divergence is computed by diffing the instance against its base template at any point in time.

---

## Architecture Components

```
┌─────────────────────────────────────────────────────┐
│                   INPUT LAYER                        │
│  WhatsApp messages · Call transcripts · Images/PDFs  │
│  Calendar/clock · Manual updates (Ashish)            │
└─────────────────────────┬───────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│              EXTRACTION & ROUTING LAYER              │
│  Entity resolution · Task identification             │
│  Template matcher · New instance creator             │
└──────┬───────────────────────────┬──────────────────┘
       │                           │
       ▼                           ▼
┌─────────────────┐     ┌──────────────────────────┐
│ TEMPLATE        │     │  INSTANCE STORE           │
│ REGISTRY        │     │  Per-task state machines  │
│                 │     │  + instance deltas        │
│ - Template A    │     │                           │
│ - Template B    │     │  Task-001: state=S4       │
│ - Template C    │     │  Task-002: state=S2       │
│   ...           │     │  Task-003: DIVERGED       │
└────────┬────────┘     └──────────┬───────────────┘
         │                         │
         └──────────┬──────────────┘
                    ▼
┌─────────────────────────────────────────────────────┐
│               TRANSITION ENGINE                      │
│  Processes incoming events · Fires valid transitions │
│  Evaluates conditions · Triggers escalations         │
│  Detects divergence · Annotates instance delta       │
└─────────────────────────┬───────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│                  OUTPUT LAYER                        │
│  Updated task list · Divergence alerts               │
│  Implicit task activations · Human review requests   │
│  Dashboard / WhatsApp notifications to Ashish        │
└─────────────────────────────────────────────────────┘
```

---

## Example Template: Standard Procurement Order

A simplified template for a typical Army supply order (equipment procurement):

```
[Enquiry received]
        │
        ▼
[Quote requested from supplier]
        │
        ├── (no response in 48h) ──► [ESCALATION: follow up with supplier]
        │
        ▼
[Quote received — rate + GST + delivery]
        │
        ▼
[Quote sent to client]
        │
        ├── (no response in 24h) ──► [ESCALATION: follow up with client]
        │
        ▼
[Order confirmed by client]
        │
        ▼
[PO issued to supplier] ──────────────────────┐
        │                                       │
        ▼                                       ▼
[Delivery date committed by supplier]   [Advance payment if required]
        │
        ├── (delivery date missed) ──► [ESCALATION: delivery overdue]
        │
        ▼
[Goods dispatched by supplier]
        │
        ▼
[Goods received at Ashish's end]
        │
        ▼
[Inspection & sorting]
        │
        ├── (quality issue) ──► [BRANCH: rejection / partial acceptance]
        │
        ▼
[Delivery to client]
        │
        ▼
[Client acceptance / sign-off]
        │
        ▼
[Invoice raised]
        │
        ├── (payment not received in 30 days) ──► [CADENCE: payment follow-up]
        │
        ▼
[Payment received]
        │
        ▼
[GST reconciliation]
        │
        ▼
[ORDER CLOSED]
```

---

## Design Tensions

### T1 — Cold Start: Templates Without History

**Problem**: Templates should be learned from historical patterns. But there is no historical data yet. Every template must be bootstrapped from a combination of sources.

**Risks**:
- Templates derived solely from Ashish's verbal descriptions over-fit to cases he can articulate from memory and miss steps he considers obvious
- Ashish has strong operational mental models but does not reliably produce complete, granular process maps when asked directly — he tends to meander into special cases and edge cases he finds interesting, rather than working through the standard flow systematically
- Templates may encode how orders *should* go, not how they *actually* go — missing common deviations that are in fact normal
- Early templates will have low coverage, causing many tasks to fall through to "no template matched" — reducing the system's value during the period when trust is being built

**Recommended source strategy (three complementary inputs)**:
1. **Historical chat log analysis**: mine completed orders from chat logs to extract actual transition sequences — empirical patterns rather than idealised ones
2. **Agent inference**: the agent bootstraps initial templates from historical data, applying domain judgement on granularity, procedural vs reactive classification, and gap-filling where single threads are incomplete
3. **Validation with Ashish**: use the agent-drafted template as the starting artifact in review sessions — do not ask Ashish to generate templates from scratch. Walk through the draft to correct errors and add missing steps. This keeps the discussion anchored and ensures completeness is checked systematically.

**Options**:
- Use completed SATA and Malerkotla cases as the first two template instances
- Run the system in "observation mode" for the first N completed orders, collecting actual transition sequences before locking templates

**Open question**: Who owns template approval — Ashish, the developer, or a collaborative session? Wrong templates are worse than no templates because they generate false divergence alerts.

---

### T2 — Template Granularity

**Problem**: Too coarse a template and divergence is undetectable. Too fine and the graph becomes brittle noise.

**Example**:
- Coarse: `Enquiry → Order → Delivery → Payment` (4 nodes — misses most operational detail)
- Fine: every WhatsApp message exchange is a node (hundreds of nodes — unmaintainable)
- Right level: functional milestones that have operational consequences if missed

**Tension**: The right granularity likely varies by task type. A custom flag order (Malerkotla) has very different milestone resolution than a standard equipment procurement (SATA). Forcing a uniform granularity across templates creates the wrong model for at least one task type.

**Proposed heuristic**: A node should exist if its absence or delay would change what Ashish needs to do next. Informational events that don't alter downstream action can be edges, not nodes.

---

### T3 — Edge Types: Agent Action vs Real-World Milestone

**Problem**: The graph contains two fundamentally different kinds of nodes — things the agent/staff *should do* and things that *have happened* in the world. These have different update mechanisms:

- Agent actions are activated by the state machine reaching a predecessor state
- Real-world milestones are detected from incoming messages

If these are not distinguished, the system cannot tell the difference between "the agent recommended this action" and "this action was confirmed to have happened." A falsely completed milestone is worse than a missed one — it advances the state machine incorrectly and hides the gap.

**Design requirement**: Edges into milestone nodes must require positive evidence (a message confirming the event), not just absence of contrary evidence. Agent action completion should be confirmed separately from recommendation.

---

### T4 — Template Switching: Who Decides and When

**Problem**: As a task progresses, it may evolve beyond the original template. A standard procurement order that turns into a quality dispute and partial rejection + credit note is a structurally different task. The base template no longer fits.

**Risks**:
- If the agent auto-switches templates, it may do so incorrectly — destroying audit trail continuity and confusing Ashish
- If switching requires Ashish's approval, there's friction — and Ashish may not understand what a "template switch" means
- Accumulated divergence that should trigger a switch may instead just appear as a growing list of delta annotations, hiding the structural change

**Options**:
- Define a divergence threshold (e.g., >40% of template nodes bypassed or >3 unplanned node additions) as an automatic switch trigger, with Ashish notified
- Allow the agent to *propose* a template switch with a rationale, requiring human confirmation
- Create a richer template with conditional branches that cover known variant paths (rejection, partial order, split delivery) rather than treating them as different templates

**Open question**: Is "template switching" the right mental model, or is it better framed as "template fork" — creating a new specialised template from the diverged instance for future use?

---

### T5 — Implicit Task Activation: Clock vs Event

**Problem**: Implicit cadence tasks (payment follow-up at 30 days, monthly reconciliation) need a clock input. Reactive implicit tasks (supplier silent → follow-up task) need an event input. These are different trigger mechanisms that require different system components.

A scheduler that checks "has payment been received 30 days after invoice?" requires:
- A persistent clock/cron component
- Access to the current state of the instance
- The ability to inject a new node activation into the running graph

This is architecturally non-trivial and is a different problem from message processing.

**Risk**: If the scheduler is built as an afterthought on top of the message-processing pipeline, it creates an inconsistent update path. State transitions from the scheduler and state transitions from message processing need to follow the same rules and produce the same audit trail.

---

### T6 — Out-of-Order Updates and Node Revisability

**Problem**: In practice, subtask updates do not arrive in the logical order the template encodes. A delivery confirmation may arrive before a dispatch confirmation is logged. A quality issue may reopen a node that was marked complete days earlier. A payment may be partially confirmed before invoice is raised.

**This is not an exception — it is the norm.** The task graph must accommodate:
- Updates to logically later nodes before earlier nodes are complete
- Reopening of nodes already marked complete (e.g., post-delivery quality issue, payment dispute)
- Simultaneous active updates across multiple independent subtasks (AC procurement, fridge procurement, GeM items all in flight at once in the SATA case)

**Design implication**: the graph is a *reference model*, not a control flow enforcer. Node status is independent per node — there is no single "current state" for a task instance. The agent records updates wherever they arrive in the graph and uses the template's expected ordering only for:
- Monitoring: detecting that a logically earlier node has been skipped or is overdue
- Alerting: flagging when an out-of-order update may indicate a missing log entry (see T15)
- Divergence annotation: noting structural deviations in the instance delta

**Complication**: some dependencies are genuine hard constraints (e.g., cannot dispatch before goods are packed). These need to be distinguished from soft ordering preferences. The template schema must support both `soft_sequence` and `hard_dependency` edge types, so the agent knows when to alert vs when to block.

---

### T7 — Instance Identity: Matching Messages to Instances

**Problem**: The state machine can only be updated if incoming messages can be reliably matched to the correct task instance. Entity resolution (supplier names, client references) is already hard — now it must be precise enough to route to the correct instance, not just the correct entity.

**Example**: Ashish runs two concurrent tandoor orders for different clients. A supplier message "maal kal aa jayega" needs to be routed to the correct order instance — not just to "a tandoor task." If entity resolution is uncertain, instance routing is also uncertain.

**Consequence**: A false instance match updates the wrong task's state. This is worse than a missed update — it creates false confidence in one task and hides the gap in another. Instance routing errors need to be surfaced with the same prominence as entity resolution errors.

> **Addressed** — see `message_router_design.md` for the full routing design. The router uses a 4-layer cascade (noise filter → group map + rolling window → composite signal scoring → embedding) to handle M:N routing across concurrent instances. Ambiguous routes produce provisional updates only (written to `provisional_deltas`, not task state) — preventing the false confidence problem. The router feeds back newly discovered aliases and routing context to improve future accuracy.

---

### T8 — Instance Splitting and Merging

**Problem**: Tasks can split and merge in ways the template didn't anticipate:

- **Split**: A combined order for two items from one supplier turns out to be two separate deliveries with different timelines → one instance becomes two
- **Merge**: Two threads that appeared to be separate orders turn out to be the same order (entity resolution failure caught late) → two instances need to be merged with reconciled state

Both operations are destructive if handled incorrectly. A merge must produce a consistent combined state from two potentially contradictory state histories. A split must decide which prior state transitions belong to which child instance.

**Open question**: Should splits and merges be agent-initiated with human confirmation, or human-initiated only?

---

### T9 — Multi-Party Information Asymmetry

**Problem**: The state machine reflects the agent's *knowledge* of task state, not the ground truth. Ashish may know something that hasn't appeared in any message yet (a verbal conversation with a supplier, a bank transfer not yet acknowledged). The client may believe the order is in a different state than Ashish does.

**Risk**: If the agent presents the state machine as ground truth, Ashish may over-rely on it and miss the gap between what the agent knows and what is actually true. The state machine is an inference model, not a ledger.

**Implication**: State nodes need a confidence level. A node reached by strong evidence (explicit confirmation message) has higher confidence than one inferred from context. Low-confidence state transitions should be visually distinct in the dashboard.

---

### T10 — Divergence Signal Calibration

**Problem**: Divergence is only useful if Ashish trusts it. If too many orders are flagged as "diverged from template," he will ignore the signal. If divergence thresholds are too loose, real problems are hidden.

**Risk**: In Ashish's business, deviation from template may be the norm rather than the exception — Army procurement has unpredictable timelines, client requirements change, supplier reliability varies. A template designed for the average case will generate constant divergence noise.

**Options**:
- Separate "structural divergence" (a mandatory node was skipped) from "timeline divergence" (a node was late)
- Weight divergence by operational consequence: a skipped inspection node is more critical than a delayed quote
- Build divergence tolerance into templates as acceptable ranges, not just expected states

---

### T11 — Agent Hallucination Risk in State Transitions

**Problem**: The agent infers state transitions from message content. It can be wrong — a message that *sounds* like delivery confirmation may be a question about delivery, or refer to a different order. A falsely fired transition advances the state machine incorrectly.

**Mitigation options**:
- Require high-confidence evidence for milestone transitions (confirmation keyword + entity match + context check)
- Mark inferred transitions as "provisional" until corroborated by a second signal
- Make it easy for Ashish to roll back a transition with a single action

> **Provisional update tier designed** — see `message_router_design.md §Ambiguity handling — provisional update tier`. Ambiguous-routed messages produce `provisional_updates` only (separate `provisional_deltas` table, not written to task state). Provisionals cannot trigger alerts or stage transitions. They are promoted or discarded by the update agent on subsequent calls as later messages provide clarity. Silent expiry at 10 messages / 4 hours if unresolved.

---

### T12 — Template Evolution and Governance

**Problem**: As completed instances accumulate, patterns emerge that should update templates. But uncontrolled template updates corrupt the shared baseline — making it harder to compare instances over time.

**Tension**: Frequent template updates improve accuracy but break historical comparison. Infrequent updates leave the template stale.

**Proposed approach**:
- Template versions are immutable once deployed
- New instances always start from the latest template version
- Historical instances retain their original template version reference
- Proposed template changes are queued and reviewed before a new version is released
- Template changelog is stored alongside the template

---

### T13 — Context Window Limits for Large Graphs

**Problem**: A complex task with many parallel subtasks, a long history, and accumulated divergence annotations may produce a graph too large to fit in the agent's context window for a single reasoning pass. The agent cannot see the full state and may make locally correct but globally inconsistent updates.

**Mitigation**: Hierarchical summarisation — the agent works on local subgraphs (e.g., "all pending supplier-side subtasks") with a summary of the parent and sibling state rather than the full graph. Critical cross-cutting state (e.g., "client has blocked payment") is always injected into every subgraph context.

---

### T15 — Missing WhatsApp Entries: The Invisible Gap

**Problem**: The agent only knows what was logged in WhatsApp. Staff and Ashish frequently handle tasks verbally, via phone call, or in person — with no corresponding chat entry. The agent has no direct way to detect these gaps.

**Two detection strategies:**
- **Forward inference from downstream evidence**: if a downstream node is updated (e.g., delivery confirmed) but no upstream nodes have been logged (e.g., no dispatch message), flag the gap — "delivery confirmed but no dispatch confirmation found — intervening steps unverified." The template's expected ordering makes these gaps visible.
- **Temporal gap detection**: if a task goes silent longer than the expected interval between nodes (from template), flag as "no updates observed — status unknown." This catches stalls but cannot distinguish genuine stalls from simply unlogged activity.

**Hard limit**: if both an intermediate step *and* all its downstream steps are missing, the task appears stalled — indistinguishable from genuinely stalled. The agent cannot infer events with no downstream signal at all.

**Interview question for Ashish**: how often do tasks proceed entirely off-platform (phone/in-person only)? Is a "last seen N days ago" alert useful, or would it be constant noise given how he actually operates?

---

### T16 — Staff Quality as a Risk Weight

**Problem**: Not all subtasks carry equal risk of failure. Subtasks assigned to staff members with a history of missed follow-ups or delayed responses are higher risk than the same subtask assigned to Ashish directly. The agent currently treats all subtasks equally.

**Implementation approach**:
- Build per-staff completion rate from historical task data: which subtask types, assigned to which staff, have the highest failure/delay rate
- Inject this as a risk weight into the task graph: high-risk staff + high-stakes subtask = lower timeout threshold before escalation alert
- In practice: "Staff 1 has missed supplier follow-ups in 3 of 5 recent cases → set escalation timer to 4h instead of 24h for this node"
- The agent does not need to surface this reasoning explicitly to Ashish — it simply acts earlier, and Ashish will observe the pattern over time

**Sensitivity**: surfacing staff performance data explicitly may affect team dynamics. Only Ashish can determine whether this should be exposed in the dashboard or handled implicitly through alert thresholds.

**Data requirement**: sufficient historical completed task instances with staff attribution to compute meaningful per-staff rates. Not available at Sprint 3; design for it, implement when data exists.

---

### T17 — Warehouse Inventory: Stock-Filled Orders

**Problem**: Many popular items (common stationery, standard equipment) are pre-stocked in Ashish's warehouse. These orders do not require a supplier interaction — the standard procurement template does not apply. If the agent always opens a supplier procurement subtask, it generates false work.

**Two approaches:**

| Approach | How | When |
|---|---|---|
| **Passive detection** | Agent infers stock fulfilment from chat cues ("stock mein hai", no supplier thread opened, delivery arranged directly) | Works if staff consistently log stock usage; brittle if they don't |
| **Active inventory integration** | Agent receives a live or periodic inventory feed; checks stock before creating supplier subtask | More reliable but requires a separate data source and integration |

**Likely right answer**: hybrid. Passive detection for common items where the pattern is learnable from chat. Active integration for high-value items where a stock error is costly. Ashish can identify which items are regularly pre-stocked and how reliably staff log stock fulfilments — this drives the decision.

**Template implication**: the procurement template needs a branch at the start: `[Stock check] → (in stock) → [Direct delivery] or (not in stock) → [Supplier sourcing]`. The branch condition is either inferred from chat or resolved from inventory feed.

---

### T14 — Bootstrapping Trust with Ashish

**Problem**: Ashish needs to trust the state machine before he relies on it. Trust is built through accuracy. But accuracy requires good templates, which require historical data, which requires Ashish to have used the system. This is a circular dependency.

**Practical approach**:
- Start with the agent in "read-only" mode: it proposes state updates but Ashish confirms each one manually for the first N orders
- Build a simple confirmation interface (WhatsApp bot: "Delivery confirmed for SATA AC — correct? Yes/No")
- Each confirmed transition is a training signal for template refinement
- Graduate to automatic transitions for high-confidence, low-stakes events only

---

---

## Operational Cost and Pipeline Architecture

### The Cost Problem

The current pipeline runs a single full-context extraction pass per batch: all enriched threads for a time window → one large prompt → one large output.

**Measured cost — last test run (R1-D-L3-01 SATA, 2026-03-25):**

| Component | Chars | ~Tokens | Cost |
|---|---|---|---|
| System prompt (input) | 10,551 | 2,637 | — |
| Threads input | 72,071 | 18,017 | — |
| **Total input** | | **~20,655** | **$0.062** |
| Agent output | 38,484 | 9,621 | $0.144 |
| **Agent subtotal** | | | **$0.206** |
| Evaluator input (agent output + metadata) | | ~10,121 | $0.030 |
| Evaluator output (score.json) | | ~1,500 | $0.023 |
| **Evaluator subtotal** | | | **$0.053** |
| **Full test run total** | | | **$0.259** |

*Pricing: claude-sonnet-4-6 at $3.00/M input tokens, $15.00/M output tokens. Token estimate: ~4 chars/token.*

Note: the SATA case is a 24-day, 904-message window with 14 image annotations — among the largest possible inputs. Synthetic cases are ~10x smaller. But real production operation will trend toward SATA-scale as orders accumulate context.

**Production projections at current architecture:**

| Active tasks | Runs/day | Cost/day | Cost/month |
|---|---|---|---|
| 5 | 3 | $3.09 | ~$93 |
| 10 | 3 | $6.19 | ~$186 |
| 10 | 6 | $12.38 | ~$371 |
| 20 | 3 | $12.38 | ~$371 |

This is prohibitively expensive. Even the lowest scenario ($93/month) is likely unacceptable for a small business deployment.

Early data point: ~$5 spent on a small number of test runs in Sprint 1 alone, with incomplete supplier threads. Real operation involves more threads, continuous message flow, and multiple concurrent tasks.

### The Architectural Shift Required

**Current**: full-context batch extraction (read everything, extract everything, periodically)
**Target**: incremental event-driven extraction (new message → identify affected task(s) → retrieve only that task's context → update state)

This requires several new components:

| Component | Purpose | Cost implication |
|---|---|---|
| **Task-wise chat splitter** | At ingest, segment continuous chat logs by task and store per-task message history | One-time ingest cost, not per-run |
| **NoSQL task store** | Persist per-task context, current state, message history, and delta | Storage cost only |
| **Message router** | On new message: identify which task(s) it belongs to — cheaply, without a full LLM call | Cheap classifier or embedding similarity |
| **Context retrieval layer** | Fetch only the relevant task's stored context for the prompt | Reduces input tokens dramatically |
| **Rate limiter per task** | Cap prompt calls per task per time window | Direct cost control |
| **Batch scheduler** | Group low-urgency updates; process high-urgency ones (e.g., delivery day) immediately | Smooths cost spikes |

### Design Tensions

**C1 — Message Routing Without Full LLM Cost**

> **Designed** — see `message_router_design.md` for the full specification.

The message router uses a 4-layer cascade with **zero LLM cost**:
1. Noise filter (rule-based)
2. Group → task map + rolling window + context boundary detection
3. Composite signal scoring across 6 reference types (entity, officer, source group, location, date, item) using a live entity alias dictionary — handles Hinglish honorifics and transliteration variants
4. Embedding similarity fallback (`paraphrase-multilingual-MiniLM-L12`, 384-dim, CPU-only, ~8ms/message)

No haiku-class LLM is needed for routing. The cascade approach is consistent with FrugalGPT (Chen et al., 2023, arXiv:2305.05176) and RouteLLM (Ong et al., 2024, arXiv:2406.18665).

**C2 — Task Context Window Growth**

A task that runs for 30 days accumulates hundreds of messages. Storing and re-injecting the full history into each prompt call defeats the purpose of task-scoped retrieval. Options:

- **Full history**: accurate but grows unbounded
- **Rolling summary + recent messages**: compress old history into a state summary, keep last N messages verbatim. Cost-effective but summary quality degrades if the summariser misses nuance
- **State machine as summary**: if the task graph is well-maintained, the current node + edge history *is* the summary — raw messages become redundant for most purposes. This is the strongest argument for building the state machine first.

RAPTOR (Sarthi et al., 2024, arXiv:2401.18059) and MemGPT (Packer et al., 2023, arXiv:2310.08560) are the primary references for hierarchical and tiered memory management respectively.

**C3 — Real-Time vs Batch Cadence**

| Cadence | Latency | Cost | Complexity | Right for |
|---|---|---|---|---|
| Per-message (real-time) | Seconds | Highest | High | Time-critical alerts (delivery day, payment dispute) |
| Small batch (every 30 min) | 30 min | Moderate | Moderate | Most operational updates |
| Daily digest | Hours | Low | Low | Summary reporting, non-urgent tasks |
| On-demand (Ashish requests) | Immediate | Variable | Low | Ad hoc queries |

For Ashish's business, a **hybrid model** is likely right: daily batch for most tasks, real-time trigger only for high-priority state transitions (e.g., delivery day, unresponsive supplier beyond threshold). This matches how he currently operates — he checks WhatsApp periodically, not second-by-second.

**C3a — Incremental Architecture Cost Profile**

Switching to task-scoped incremental updates dramatically changes the cost equation:

| Component | Incremental estimate | vs full-context |
|---|---|---|
| Input (system prompt + state summary + ~30 messages) | ~3,537 tokens | ~6x smaller |
| Output (state update only, not full task list) | ~500 tokens | ~19x smaller |
| **Cost per update** | **~$0.018** | **~11x cheaper** |

**Projected production costs with incremental architecture:**

| Active tasks | Updates/day | Cost/day | Cost/month |
|---|---|---|---|
| 10 | 3 | $0.54 | ~$16 |
| 10 | 6 | $1.09 | ~$33 |
| 20 | 6 | $2.17 | ~$65 |

This makes the product economically viable. The incremental architecture is not just a cost optimisation — it is a prerequisite for production deployment.

**C4 — Prompt Caching for Shared Context**

The system prompt (testing_prompt.txt) and task-type checklists are identical across all calls for the same task type. Anthropic's prompt caching (August 2024) reduces cached input token cost by 90% ($3.00 → $0.30/M tokens).

Measured saving on the current system prompt (~2,637 tokens): $0.007 per run, or $0.11 across a 16-case batch. Small in absolute terms now, but meaningful at production scale — and larger once task-type checklists are added to the stable prefix.

Reference: Anthropic Engineering Blog, "Prompt Caching" (August 2024).

**C5 — Storage Schema for Task-Scoped Logs**

The chat splitter needs to assign each message to one or more task instances at ingest time. This is the same entity resolution and routing problem as C1, but applied to historical data rather than real-time messages. Design decisions:

- **Message-to-task mapping**: one-to-many (a message can contribute to multiple tasks — e.g., a supplier message mentioning two items)
- **Versioning**: if task boundaries are revised (split/merge), the message-task mapping must be updatable without re-ingesting raw logs
- **Index**: tasks need to be queryable by entity (client, supplier), item, status, and date — NoSQL document store (MongoDB, DynamoDB, Firestore) fits better than relational for variable-schema task records

> **Router tables now defined** — `message_router_design.md §Data Structures` specifies the SQLite schema for live processing: `entity_aliases`, `task_routing_context` (includes `source_groups`, `group_aliases`, `delivery_location`, `key_dates`, `item_types`, `officer_refs`, context embedding), `routing_log`, `routing_routes` (M:N), and `provisional_deltas`. These complement the task instance store — the router tables are operational infrastructure; the task store is the business state record.

**C6 — Cost vs Latency vs Quality Tradeoff Surface**

These three are not independently optimisable. The key tradeoff surface:

```
High quality, low latency  →  high cost   (real-time full-context LLM)
High quality, high latency →  lower cost  (batch full-context LLM)
Low latency, low cost      →  lower quality (lightweight router + cached context)
```

The design goal is to find the operating point where quality is good enough for Ashish to trust the output, latency is low enough that it informs decisions before they need to be made, and cost is low enough that the product is viable. This needs empirical measurement against real usage patterns — not just design-time estimation.

### Relevant Research

**Cost optimisation and routing:**
- FrugalGPT — Chen, Zaharia, Zou (arXiv:2305.05176, 2023): LLM cascade routing for cost reduction
- RouteLLM — Ong et al. (arXiv:2406.18665, 2024): learned routing between cheap and expensive LLMs
- Prompt Cache / PagedAttention — vLLM (Kwon et al., SOSP 2023): KV cache reuse for batched inference
- Anthropic Prompt Caching (August 2024): prefix caching, up to 90% cost reduction

**Incremental and streaming context:**
- StreamingLLM — Xiao et al. (arXiv:2309.17453, ICLR 2024): sliding window + attention sinks for infinite-length streams
- Infini-Transformer — Munkhdalai et al. (arXiv:2404.07143, 2024): compressive memory for incremental context updates

**Task-scoped retrieval and memory:**
- MemGPT — Packer et al. (arXiv:2310.08560, 2023): tiered memory with explicit paging into context window
- RAPTOR — Sarthi et al. (arXiv:2401.18059, ICLR 2024): hierarchical retrieval at multiple abstraction levels
- HippoRAG — Gutiérrez et al. (arXiv:2405.14831, NeurIPS 2024): graph-based associative retrieval from large histories
- LightRAG — Guo et al. (arXiv:2410.05779, 2024): graph-structured RAG with entity/relation extraction

**Multi-agent context management:**
- Cognitive Architectures for Language Agents — Sumers et al. (arXiv:2309.02427, TMLR 2024): survey of memory architectures in LLM agents
- AutoGen — Wu et al. (arXiv:2308.08155, 2023): per-agent context partitioning in multi-agent systems

**Practitioner references:**
- LlamaIndex blog: production RAG patterns, chunk sizing, metadata filtering (2024)
- LangChain docs: semantic routing via embedding similarity (2023–2024)

---

## Open Questions for Mentor Discussion

**State machine design:**
1. What graph representation format is most appropriate — JSON adjacency list, a graph DB (Neo4j), or a simpler relational model with edges as rows? The choice affects query patterns and update complexity significantly.
2. Is a full state machine the right granularity for Sprint 3, or should Sprint 3 target a simpler "task status + expected next steps from template" model that achieves 80% of the value at 20% of the complexity?
3. How should the scheduler component be architected relative to the message processing pipeline? Should they share the same event bus or run independently?
4. What is the right UX for Ashish to interact with the state machine — a dashboard, WhatsApp bot commands, or a hybrid? The answer affects how state corrections and human review gates are implemented.
5. How do we handle tasks that span multiple orders or clients (e.g., a single supplier delivery containing goods for three different Army units)? Does one task instance spawn child instances, or is there a many-to-many relationship between instances and clients?

**Cost and pipeline architecture:**

6. ~~What is the recommended approach for message routing in a Hinglish, high-ambiguity context?~~ *Resolved — see `message_router_design.md`. The router uses a 4-layer cascade: noise filter → group map + rolling window → composite signal scoring (entity alias dict, Hinglish-aware) → `paraphrase-multilingual-MiniLM-L12` embedding similarity. Zero LLM cost.*
7. Is a NoSQL document store (e.g., Firestore, DynamoDB) the right choice for the task instance store, or does the graph structure of the state machine argue for a graph database from the start?
8. At what point does the rolling-summary approach for task context management break down in terms of quality? Is there a known threshold (message count, task duration) where summarisation loss becomes operationally significant?
9. Given Anthropic's prompt caching capability, what prompt structure maximises cache hit rate — how should the stable prefix (system prompt + checklists) be separated from the variable per-task context?
10. What is the recommended real-time vs batch cadence for a business like this — is there prior art on hybrid scheduling for operational monitoring agents?

---

## Recommended Next Steps (Pre-Sprint 3)

1. **Validate with Ashish** (Sprint 2): Before designing templates, walk through 2–3 completed orders with Ashish end-to-end to map the actual transition sequences. This is the primary input for template design.

2. **Identify 3–5 core task types** that cover 80%+ of Ashish's order volume. These become the first template set.

3. **Design the template schema** (node + edge structure, metadata fields, versioning) before writing any code.

4. **Discuss with mentors**: graph representation, scheduler architecture, and whether full state machine or simplified "template + expected next steps" is the right Sprint 3 scope.

5. **Prototype on SATA case**: reconstruct the SATA order as a template instance retroactively — this will surface schema gaps and graph design issues before implementation begins.

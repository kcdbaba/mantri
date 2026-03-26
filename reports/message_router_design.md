# Message Router — Design Document

**Status**: Pre-implementation design
**Target sprint**: Sprint 3
**Depends on**: `live_monitoring_design.md` (pipeline context), `task_lifecycle_state_graph_design.md` (task instance schema)
**Author**: Kunal Chowdhury
**Date**: 2026-03-26

---

## Role in the Pipeline

The message router sits between the ingestion API and the stateful update agent. It processes every incoming message from whitelisted WhatsApp groups and produces a routing result: the set of active task instances this message is relevant to, with a confidence score per task.

**Routing cardinality is M:N.** A single message may reference multiple tasks — "SATA ka tandoor aur Eastern Command ka AC dono ready hai" — and this is the normal expected case, not an error. A rolling context window routinely spans several concurrent orders in the same group. The router must produce a list of routes, not a single route.

**"Ambiguous" is reserved for genuinely unclear single-task attribution** — when a vague message ("maal ready hai") could belong to one of several tasks but it is not clear which one. This is different from a message that clearly refers to multiple tasks. Multi-task routing is correct behaviour; ambiguous routing is a quality flag that is carried forward into downstream prompts rather than blocked on.

**The agent is a passive observer.** It reads operational WhatsApp groups but never posts to them. Clarification requests, alerts, and agent-to-human communication go exclusively through a dedicated alerts channel (separate WhatsApp group or dashboard — TBC with Ashish). No message in any operational group should ever be attributed to the agent. If a staff member addresses the agent directly ("agent, is this delivered?"), that message is silently ignored — not processed, not responded to.

The router answers three questions, in order:

1. **Is this message task-relevant?** (noise filter)
2. **Which task instances does it reference?** (M:N routing — may be 0, 1, or many)
3. **Is this a non-order task signal?** (proactive sourcing, client feedback — tasks with no active order context)

```
Ingestion API
     │
     ▼
┌─────────────────────────────────┐
│         MESSAGE ROUTER          │
│                                 │
│  Layer 1: Noise filter          │
│  Layer 2a: Group→task map       │
│  Layer 2b: Entity keyword match │
│  Layer 2c: Embedding similarity │
│  Layer 3: Routing decision      │
└────────────────┬────────────────┘
                 │
                 ▼
        Task-scoped message queue
                 │
                 ▼
        Stateful update agent (LLM)
```

---

## Design Goals

| Goal | Rationale |
|---|---|
| **Zero LLM cost for routing** | Routing happens on every message; LLM cost must be reserved for the update agent only |
| **< 100ms per message** | Near-real-time feel; message queue should not grow under normal load |
| **High recall (prefer over-routing)** | A missed route = silent task blindspot; a false route = one wasted LLM call |
| **Handles Hinglish** | Messages mix Hindi (Roman script), English, and informal names; routing must be robust to this |
| **Runs on CPU, no GPU required** | VPS hosting (DigitalOcean/Linode); no GPU budget at Sprint 3 |
| **Graceful under ambiguity** | Ambiguous messages should be routed to all plausible candidates + flagged, not silently dropped |

---

## Three-Layer Cascade

Routing proceeds through three layers in order. Each layer can fully resolve the routing or pass through to the next.

```
message
   │
   ▼
Layer 1: Noise filter ──── drop? ──► discard
   │
   ▼
Layer 2a: Group → task map ── strong match ──► route (confidence: high)
   │ (weak or no match)
   ▼
Layer 2b: Entity keyword match ── 1+ matches ──► route (confidence: medium-high)
   │ (no entity match)
   ▼
Layer 2c: Embedding similarity ── above threshold ──► route (confidence: medium)
   │ (below threshold)
   ▼
Layer 3: Decision ── discard / new task candidate / manual review
```

Each layer that produces a match returns a `routing_result` with a confidence score and method tag. The update agent uses this metadata to calibrate how much trust to place in the routing.

---

## Layer 1 — Noise Filter

Drop messages that cannot contain task-relevant content. This runs first, before any matching, and is purely rule-based.

**Drop unconditionally:**
- Reactions (👍, ❤️, etc. — WhatsApp reaction events)
- Sticker, GIF, voice note (if audio transcription is not enabled)
- System messages: group join/leave, admin changes, encryption notifications
- Exact duplicate from same sender within 60 seconds (dedup)

**Flag but do not drop:**
- Very short messages (< 4 words) with no entity mention: "ok", "haan", "theek hai", "👍 sir" — these are common confirmations and may be task-relevant via group context. Pass through with `type: confirmation_signal` flag; Layer 2a (group map) resolves them.
- Media-only messages (image, PDF, document): pass through with `type: media` flag and attach the media URL. The update agent handles vision processing; the router's job is only to route the message to the right task.
- Forwarded messages: pass through normally, tag `forwarded: true`. Routing treats the text content as-is — the routing context is the current group, not the original source. No confidence penalty: the content is operationally significant regardless of how it arrived.
- Voice notes (if transcription enabled): the transcript is interleaved into the message stream with a `[Voice note]` marker before routing. Routed as normal text. Tag `source: voice_note`. See §Voice Note Transcription for the transcription pipeline.

---

## Layer 2a — Group → Task Map

Each active task instance maintains a `source_groups` set — the WhatsApp group IDs associated with that task. When a message arrives from group `G`:

1. Look up all task instances where `G ∈ source_groups`
2. **If exactly 1 match** → route directly (confidence: 0.90)
3. **If multiple matches** → disambiguate using the rolling window (see below)
4. **If 0 matches** → the group is not yet linked to any task, or the task is new → fall through to Layer 2b

**Group → task association is maintained dynamically:**
- When the update agent creates a new task instance from a message, it records the source group ID
- When a new thread is linked to an existing task, the group is added to the task's `source_groups`

**Limitation**: the All-Staff Group (general coordination) is used across all orders. Messages from this group cannot be routed by group ID alone — they fall through to Layers 2b and 2c.

### Rolling Window as Active Task Set (multi-task groups)

A group like "Ashish + Eastern Command" may carry multiple concurrent orders simultaneously, and the rolling conversation window may span all of them — different messages in the window referring to different tasks. The window's job is not to identify the single "current topic" but to establish the **set of tasks currently active in this group's conversation**.

When group `G` maps to N > 1 active tasks, fetch the **last K messages from group G** (proposal: K = 10, or last 30 minutes, whichever is shorter).

```
Rolling window (last 10 messages in group, multiple orders in flight):
  [14:00] Ashish: AC units ka advance transfer kar diya
  [14:05] Supplier: AC dispatch kal tak hoga
  [14:10] Ashish: SATA ka tandoor bhi ready hai kya?
  [14:12] Supplier: haan, tandoor bhi ready hai
  [14:15] Ashish: dono ek saath dispatch karo kal
  [14:31] → incoming message: "dispatch confirm hua?"

Window contains: AC order context + Tandoor order context
Active task set for this window: {ac-order, tandoor-order}
Incoming message refers to both (or either) → route to both
```

**Active task set algorithm:**

1. For each candidate task, compute cosine similarity between the rolling window embedding and the task's context embedding
2. Include a task in the **active task set** if its similarity score is ≥ `ACTIVE_THRESHOLD` (proposal: 0.45 — deliberately low, to include tasks that are present in the window even if not dominant)
3. The active task set is the input to per-message routing — not the output

**Per-message routing within the active set:**

For the incoming message, determine which tasks in the active set it references:

1. **Entity match against active set** (primary): if the message contains an entity name belonging to a task in the active set → that task is a route target (confidence: 0.85)
2. **Embedding similarity against active set** (fallback): for messages with no entity match, compute similarity between the message embedding and each active-set task's context embedding → include tasks above `ROUTE_THRESHOLD` (proposal: 0.55) as route targets
3. **All active-set tasks** (last resort for very short/vague messages): if the message is < 4 words with no entity match and no clear embedding winner, route to all tasks in the active set with `ambiguous: true` — the update agent must resolve

```
incoming: "dispatch confirm hua?"  (no entity name)
active set: {ac-order (0.72), tandoor-order (0.68)}
embedding vs ac-order: 0.61  ← above ROUTE_THRESHOLD
embedding vs tandoor-order: 0.59  ← above ROUTE_THRESHOLD
→ routes: [{ac-order, 0.61}, {tandoor-order, 0.59}]
→ multi-task, not ambiguous (message plausibly refers to both dispatches)
```

```
incoming: "SATA ka dispatch confirm hua?"
entity match: "SATA" → tandoor-order
→ routes: [{tandoor-order, 0.85}]
→ single task, high confidence
```

```
incoming: "ho gaya"  (3 words, no entity, no embedding signal)
→ route to all active-set tasks, ambiguous: true
→ update agent generates clarification
```

**Key property**: the rolling window is no longer a disambiguation tool that picks one winner — it is a **context register** that tells the router which tasks are currently in play for this group. Per-message routing happens on top of this register, and correctly produces multiple routes when a message references multiple tasks.

### Context Boundary Detection

The rolling window assumption breaks at topic transitions — the moment within a group where conversation shifts from one order (or no order) to another. Without detecting this boundary, the router will carry the stale window forward and misroute the first message of the new topic.

**Boundary signals (in order of reliability):**

| Signal | Reliability | Description |
|---|---|---|
| Explicit entity switch | High | Message references an entity not present in the current rolling window context (e.g., window is about Eastern Command AC, new message says "SATA ke liye kya update hai?") |
| Explicit transition language | High | Phrases like "ek aur baat", "alag matter hai", "naya order", "different topic" — dictionary-matched |
| Time gap (large) | Low | Significant silence since the last message (e.g., > 4 hours, especially overnight). Can be a simple resume of the same conversation — treat as a weak prior only |
| Combination: time gap + new entity | High | Strong context switch — the pause and the entity shift together are unambiguous |

**Detection algorithm:**

Before applying the rolling window for disambiguation, check for context boundary signals:

```
incoming message M, last window timestamp T_last

1. Time gap signal: gap = now - T_last
   if gap > BOUNDARY_GAP_THRESHOLD (TBC: 4h default):
       boundary_signal = "time_gap"

2. Entity switch signal:
   entities_in_window = extract entities from rolling window messages
   entities_in_M = extract entities from M (Layer 2b fast-path)
   if entities_in_M ∩ active_tasks is non-empty
   AND entities_in_M ∩ entities_in_window is empty:
       boundary_signal = "entity_switch"

3. Transition language:
   if M contains any transition phrase from dictionary:
       boundary_signal = "explicit_transition"
```

**When a boundary is detected:**

- **Discard the rolling window** for this routing decision — the window is stale
- Route the current message on its own content via Layers 2b → 2c
- Tag the routing result with `context_switch: true` and the signal type
- **Reset the rolling window** for this group (start fresh from this message forward)

**The transition message problem:**

The transition message itself (the first message of the new topic) is the hardest to route correctly. Its content establishes the new context, but the window it would normally rely on is from the old context. The correct behavior after boundary detection is to treat the message as if it arrived in a new, empty-window group — relying entirely on Layers 2b and 2c for routing.

If Layer 2b and 2c both fail (no entity match, low embedding similarity), the message is classified as a new task candidate or a non-order task candidate (see below), and flagged for human review. This is preferable to silently routing it to the wrong task using a stale window.

*Open question*: what is the right `BOUNDARY_GAP_THRESHOLD`? A 4-hour gap overnight is almost always a new session; a 2-hour midday gap may be a resume. TBC based on observed message patterns in Ashish's actual groups.

### Non-Order Task Detection

Not all task-relevant messages belong to an active order. Some messages in client groups signal relationship-building or exploratory conversations — proactive order sourcing or post-delivery feedback. These are currently invisible to the router (classified as social or discarded).

**Non-order task signals:**
- Message from a client group with no active order for that client
- Message from a client group after all active orders for that client are completed (payment received)
- Exploratory/relationship language: "kuch chahiye tha?", "delivery kaisi lagi?", "koi naya requirement?", "kya haal hai" (from a business contact, not a personal one)
- Ashish proactively messaging a client group without a prior client message

**Router behavior:**
- Layer 2a returns 0 active tasks for the group (or all tasks are in `completed` state)
- Layer 2b: no active-order entity match
- Layer 2c: low embedding similarity to any active task context
- But message comes from a known business group (client or supplier)

→ Classify as `non_order_task_candidate` rather than discarding or creating a new order task.

The non-order task candidate queue is processed separately from new-order task candidates — it feeds the **relationship and business development task type** (see task type checklists), not the procurement/delivery workflow.

---

## Layer 2b — Reference Signal Extraction and Matching

Client messages — especially from Army units — rarely reference orders by entity name. Instead they use indirect contextual references:

| Reference type | Examples |
|---|---|
| **Entity name** | "Kapoor Steel", "51 Sub Area", "SATA" |
| **Location** | "Ranchi wala", "Tezpur ke liye", "Guwahati delivery" |
| **Date** | "15 March wala", "pichle mahine ka", "woh purana order" |
| **Item type** | "tandoor wala", "chairs ke baare mein", "AC ka kya hua" |
| **Sub-unit** | "2 Corps Supply", "HQ Company", "51 SA" |
| **Officer** | "Colonel Sharma ka order", "CO sahab ne manga tha", "Kapoor sir wala" |
| **Source group reference** | "Eastern Command group ka", "woh army wale group ka maal", "us group mein jo order tha" |

These reference types are **compositional**: "woh Ranchi wala tandoor ka payment" combines location + item + topic. A single signal may be ambiguous; the combination is often unique. The matching layer must extract all signals from a message and score tasks against all of them jointly.

### Entity Dictionary

Maintain a live **entity dictionary** mapping all known name variants (aliases) to canonical entity IDs.

```python
entity_dict = {
    # Supplier aliases
    "kapoor ji":          "entity:supplier:kapoor-steel",
    "kapoor bhai":        "entity:supplier:kapoor-steel",
    "delhi wala":         "entity:supplier:kapoor-steel",
    "kapoor steel":       "entity:supplier:kapoor-steel",

    # Client aliases
    "51 sub area":        "entity:client:51-sub-area",
    "51 sa":              "entity:client:51-sub-area",
    "eastern command":    "entity:client:eastern-command",
    "ec":                 "entity:client:eastern-command",  # only if context-safe

    # Item aliases
    "ac":                 "item:ac-unit",
    "cooler":             "item:ac-unit",
    "tandoor":            "item:tandoor",
}
```

**Matching**: tokenize the incoming message, check each token and bi-gram against the entity dictionary (case-insensitive, normalized). Match if any token/bi-gram hits.

**Entity → task routing**: for each matched entity ID, look up all active task instances that reference that entity. Route to all matching instances.

**Dictionary maintenance**:
- Populated initially from the first extraction pass on a task's threads (the update agent extracts all entity mentions and registers aliases)
- Extended incrementally: when the update agent resolves a new alias during processing (e.g., encounters "Kapoor sahab" for the first time), it adds the alias to the dictionary
- Low-confidence aliases (single occurrence, no corroborating context) are added as `provisional: true` and flagged for human review

**Hinglish handling**:
- Honorifics: strip common suffixes (ji, bhai, sahab, sir) before matching, then match the root. "Kapoor ji" → "kapoor" → check if "kapoor" is a known alias root
- Transliteration variants: "sata" / "SATA" / "s.a.t.a." → normalize to lowercase, strip punctuation
- Informal references ("delhi wala", "tandoor wala"): registered as aliases when first seen in a resolved context — cannot be matched before first resolution
- Short entity references ("51", "EC", "Kapoor") that are also common words: require at minimum one prior co-occurrence with the full entity name in the same group to be added as aliases

### Officer Reference Resolution

Officer names and ranks are person-level references that serve as proxies for Army client units. "Colonel Sharma", "CO sahab", "Kapoor sir" are aliases for a client entity — not a person entity. They are stored in the entity dictionary like any other alias:

```python
# Officer name → client entity
entity_dict = {
    "colonel sharma":    "entity:client:51-sub-area",   # confirmed from extraction
    "col sharma":        "entity:client:51-sub-area",
    "kapoor sir":        "entity:client:eastern-command",
    "co sahab":          None,   # too generic — resolved by group context only
    "commanding officer": None,  # same
}
```

Generic rank references ("CO sahab", "CO ne bola", "commanding officer") cannot be resolved from the message alone — they apply to whichever unit's group the message came from. Layer 2a (group map) provides the resolution: if the message is from the Eastern Command group, "CO sahab" = Eastern Command's CO = entity:client:eastern-command.

**Officer alias discovery**: when the update agent processes a message mentioning a new officer name alongside a known client entity ("Colonel Mehta ne bola, 12 Corps ka delivery kab tak?"), it registers "colonel mehta" as an alias for entity:client:12-corps.

### Source Group Reference Signal

The client group that originated an order becomes an implicit order identifier that flows into other groups. When staff coordinate in the All-Staff Group or with suppliers, they commonly refer to the originating client group by name rather than by order details:

```
All-Staff Group: "Eastern Command group ka maal kab dispatch hoga?"
Supplier Group:  "woh army wale group ka order — kab ready hoga?"
1:1 with Staff:  "us group mein jo tandoor tha, uska kya hua?"
```

In each case, the group name is functioning as an order proxy — it identifies which task is being discussed without naming the item, date, or entity explicitly. This is particularly common in businesses like Ashish's where groups are named after clients and staff use group names as shorthand in daily coordination.

**Group name alias dictionary:**

Store informal group name references alongside group IDs:

```python
group_alias_dict = {
    # group name variants → WhatsApp group_id
    "eastern command group":  "group_id_ec_001",
    "army wale group":        "group_id_ec_001",   # informal; provisional
    "eastern command wala":   "group_id_ec_001",
    "sata group":             "group_id_sata_001",
    "sata wale":              "group_id_sata_001",
    "supplier group":         None,               # too generic; resolve by context
}
```

Group ID → task lookup: `task_routing_context.source_groups` already stores the mapping. A group reference in a message resolves to a group ID, which resolves to all tasks associated with that group.

**Matching logic:**

1. Extract group name mentions from the message (pattern: `[entity_name] + group / wala group / wale / ka group`)
2. Look up the group name in `group_alias_dict` → get group ID
3. Look up all tasks where that group ID is in `source_groups` → candidate task set
4. If multiple tasks (same client group, multiple orders) → combine with other signals (date, item) to narrow further

**Signal strength:** moderate (0.35). A group reference is specific but may map to several tasks if the client has had multiple orders. It is stronger than item alone but weaker than entity name because of this multi-task ambiguity.

**Why this matters across group boundaries:**

The client group is the only persistent cross-group identifier for an order that does not require knowing the order's internal ID, item name, date, or entity. Staff naturally use it as a shorthand precisely because it is unambiguous within the team — everyone knows which client group corresponds to which client. The router must recognise it as a first-class routing signal, not incidental context.

**Generic group references** ("us group mein", "woh group ka", "supplier wale group") cannot be resolved from the message alone — they require either group context (Layer 2a) or additional signals (Layer 2b entity/item match) to disambiguate. Mark as `provisional: true` in the alias dictionary and combine with other signals before routing.

### Secondary Signal Extractors

For messages with no entity name match, extract secondary signals to score tasks:

**Location signal:**
Extract location mentions — city names, cantonment names, delivery addresses. Match against `task.delivery_location` in the task routing context.

```
"Ranchi wala maal" → location_signal = "ranchi"
task_store: {sata-tandoor: delivery_location="Ranchi", ec-ac: delivery_location="Guwahati"}
→ sata-tandoor scores location match; ec-ac does not
```

Hinglish location suffixes to normalise: "wala" / "wali" / "ke liye" / "ka" often follow a location name. Strip suffix before matching: "Ranchi wala" → "ranchi".

**Temporal signal:**
Extract date references — absolute and relative — and match against task key dates.

| Reference type | Example | Resolution |
|---|---|---|
| Absolute date | "15 March wala" | Match tasks with key_date within ±3 days of Mar 15 |
| Month reference | "pichle mahine ka" | Match tasks created or invoiced in prior month |
| Relative (vague) | "woh purana order", "pehle wala" | Low confidence — use as weak prior only |
| Stage reference | "jo deliver hua tha" | Match tasks at or past delivery stage |

Absolute date references are the most discriminating — they typically narrow candidates to 1–3 tasks. Vague relative references ("purana", "pehle wala") are too weak to use alone but boost confidence when combined with other signals.

**Item type signal:**
Extract item keywords and match against `task.item_types`. Item matches are weak signals alone (multiple tasks can have the same item type) but strong when combined with location or date:

```
"tandoor ka payment" alone → may match 3 active tandoor tasks
"Ranchi wala tandoor ka payment" → location + item → likely unique
```

### Multi-Signal Composite Scoring

For each candidate task, compute a composite score across all extracted signals:

```python
def composite_score(task, signals):
    score = 0.0
    weight_sum = 0.0

    if signals.entity_match(task):
        score += 0.50; weight_sum += 0.50   # strongest — named entity directly

    if signals.officer_match(task):
        score += 0.40; weight_sum += 0.40   # strong — officer name implies client unit

    if signals.location_match(task):
        score += 0.25; weight_sum += 0.25   # moderate

    if signals.date_match(task):            # absolute date reference
        score += 0.25; weight_sum += 0.25   # moderate

    if signals.source_group_match(task):
        score += 0.35; weight_sum += 0.35   # moderate-strong — group name as order proxy

    if signals.item_match(task):
        score += 0.10; weight_sum += 0.10   # weak alone; strong in combination

    if signals.embedding_similarity(task) > 0.50:
        score += signals.embedding_similarity(task) * 0.15

    return score / weight_sum if weight_sum > 0 else 0.0
```

Tasks above `ROUTE_THRESHOLD` (0.55) are included as route targets. The score is stored as the TaskRoute confidence.

**Composite scoring examples:**

| Message | Signals extracted | Task A (match) | Task B (no match) | Result |
|---|---|---|---|---|
| "Ranchi wala tandoor ka payment" | location=Ranchi, item=tandoor, topic=payment | location✓ item✓ → 0.78 | item✓ only → 0.12 | Route to A only |
| "15 March wala order ka kya hua" | date=Mar 15 | date✓ → 0.35 | — | Weak; use embedding to disambiguate |
| "Colonel Sharma ka order ready hai" | officer=Sharma→51SA | officer✓ → 0.52 | — | Route to 51SA's active tasks |
| "woh purana order" | vague relative date | all tasks equally | — | No useful signal; fall through to Layer 2c |

### Task Routing Context Schema (updated)

The task context in the store must be enriched to support multi-signal matching:

```sql
CREATE TABLE task_routing_context (
    task_id             TEXT PRIMARY KEY,
    source_groups       TEXT,           -- JSON array of group IDs (primary cross-group identifier)
    group_aliases       TEXT,           -- JSON array of informal group name variants seen in messages
    entity_ids          TEXT,           -- JSON array of canonical entity IDs
    delivery_location   TEXT,           -- normalised location string, e.g. "ranchi"
    key_dates           TEXT,           -- JSON: {order_date, delivery_date, invoice_date}
    item_types          TEXT,           -- JSON array of normalised item keywords
    officer_refs        TEXT,           -- JSON array of known officer aliases for this task
    context_text        TEXT,           -- human-readable summary for embedding
    context_embedding   BLOB,           -- 384-dim float32, serialized
    stage               TEXT,
    last_updated        DATETIME
);
```

`group_aliases` is populated when the update agent encounters a group name reference in a message (e.g., "Eastern Command group ka" seen in the All-Staff Group) — it registers the informal name as an alias for the group ID, associated with this task. This bootstraps the `group_alias_dict` incrementally from real usage rather than requiring manual seeding.

All fields are populated from the initial extraction pass and updated incrementally as the update agent processes new messages. `delivery_location`, `key_dates`, and `item_types` are extracted by the update agent and written back to the routing context — not maintained by the router itself.

---

## Layer 2c — Embedding Similarity

For messages that pass the noise filter but have no entity keyword match (common in the All-Staff Group and in generic status messages), use semantic embedding similarity.

### Embedding Model

**`paraphrase-multilingual-MiniLM-L12`** (sentence-transformers library):
- Multilingual — trained on 50+ languages including Hindi; handles Hinglish mix well
- 384-dimensional embeddings
- ~118 MB model weight, runs on CPU
- ~8ms per message on a 2-core VPS (acceptable for < 100ms target)
- Zero API cost

### What to Embed

**Task context embedding** — a structured text representation of the task, not the raw messages:

```
Task: Eastern Command AC order
Supplier: Kapoor Steel (Delhi)
Client: Eastern Command
Item: 15 AC units, 1.5 ton
Stage: advance paid, delivery pending
Recent: supplier confirmed dispatch, expected 22 Mar
```

This structured summary is more stable and semantically precise than averaging raw message embeddings. It avoids noise from social chatter and off-topic messages in the same thread.

**Rebuild trigger**: recompute the task context embedding whenever the task's stage changes (a major node update). Not on every message — context is stable within a stage.

**New message embedding**: embed the incoming message text as-is (sentence-transformer handles short inputs well).

### Routing Decision

Compute cosine similarity between the incoming message embedding and all active task context embeddings.

| Similarity | Action |
|---|---|
| ≥ 0.75 (HIGH) | Route to top match (confidence: 0.75) |
| 0.50–0.74 (MEDIUM) | Route to top match with `needs_confirmation: true` flag (confidence: 0.55) |
| < 0.50 (LOW) | Do not route; classify as either social/irrelevant or new task candidate (see Layer 3) |

**Multi-match handling**: if two or more tasks score ≥ 0.50 and are within 0.10 of each other (near tie), route to all with `ambiguous: true` flag.

### Cost and Latency

| Step | Latency (est.) | Cost |
|---|---|---|
| Tokenize + embed (MiniLM) | ~8ms CPU | $0 |
| Cosine similarity vs N tasks | ~1ms for N ≤ 100 | $0 |
| Total (layer 2c) | ~10ms | $0 |

The full router (all layers) should complete in < 50ms for typical message loads, leaving 50ms headroom for queue overhead.

---

## Layer 3 — Routing Decision

After all layers, produce a single `routing_result`:

```python
@dataclass
class TaskRoute:
    task_id: str
    confidence: float       # 0.0–1.0, per-task
    method: str             # "entity_match" | "embedding" | "active_set_fallback" | "group_map"
    ambiguous: bool         # True if attribution to THIS task specifically is unclear

@dataclass
class RoutingResult:
    message_id: str
    routes: list[TaskRoute]         # 0, 1, or many — M:N is the normal case
    context_switch: bool            # True if a context boundary was detected before routing
    context_switch_signal: str      # "entity_switch" | "time_gap" | "explicit_transition" | "combined" | ""
    new_task_candidate: bool        # True if 0 routes and message looks order-relevant
    non_order_task_candidate: bool  # True if message looks like proactive sourcing or feedback
    flags: list[str]                # e.g., ["confirmation_signal", "media", "short_message"]
```

**Cardinality examples:**

| Message | routes | Interpretation |
|---|---|---|
| "SATA ka tandoor aur Eastern Command ka AC dono dispatch ho gaye" | [{sata-tandoor, 0.92}, {ec-ac, 0.88}] | Multi-task, both confident — normal case |
| "SATA ka dispatch confirm hua?" | [{sata-tandoor, 0.85}] | Single task, entity-matched |
| "dispatch ho gaya kya?" (window: AC context dominant) | [{ec-ac, 0.72}] | Single task, embedding-resolved from active set |
| "dispatch ho gaya kya?" (window: AC + tandoor both active, close scores) | [{ec-ac, 0.61, ambiguous=true}, {sata-tandoor, 0.59, ambiguous=true}] | Ambiguous — both in active set, no entity, close scores |
| "ho gaya" (3 words) | all active-set tasks, all ambiguous=true | Short message fallback — clarification needed |

**Decision table:**

| Layers matched | Result |
|---|---|
| Condition | Result |
|---|---|
| **Context boundary detected** | Discard rolling window; reset active task set; re-route on message content alone via 2b→2c; context_switch = true |
| Layer 2a: group map → 1 task, no window needed | routes = [{task, 0.90, group_map}] |
| Layer 2a: group map → N tasks; entity match in message | routes = entity-matched tasks at 0.85; non-matched tasks omitted |
| Layer 2a: group map → N tasks; no entity match; embedding resolves (scores spread) | routes = tasks above ROUTE_THRESHOLD (0.55); each with embedding confidence |
| Layer 2a: group map → N tasks; no entity match; all scores close and low | routes = all active-set tasks, all ambiguous=true |
| Layer 2b: 1+ entity hits (any number) | routes = one TaskRoute per matched task, confidence = 0.85, ambiguous = false — multiple hits is correct, not ambiguous |
| Layer 2c: 1+ tasks above 0.75 | routes = those tasks, confidence = score, ambiguous = false |
| Layer 2c: 2+ tasks with close scores (< 0.10 gap) AND message is short/vague | routes = those tasks, ambiguous = true per task |
| Layer 2c: all scores < 0.50 | No routes; proceed to 0-match handling |
| 0 routes, from known business group, relationship language | non_order_task_candidate = true |
| 0 routes, message looks order-relevant* | new_task_candidate = true |
| 0 routes, message looks social | Discard |

*"looks task-relevant": contains known item keywords (tandoor, AC, fridge, flags, etc.), rupee amounts, dates, or delivery/payment verbs — even without a matched entity.

---

## Multi-Task vs Ambiguous Routing

These are two distinct situations that require different downstream handling.

### Multi-task routing (normal case)

A message clearly references multiple tasks — either by naming multiple entities, or by containing content that is demonstrably relevant to several active orders. This is correct behaviour, not an error.

```
"SATA ka tandoor aur Eastern Command ka AC dono dispatch ho gaye"
routes: [{sata-tandoor, 0.92}, {ec-ac, 0.88}]
ambiguous: false on both
```

**Update agent behaviour**: receives the message routed to each task independently. For each task, the agent processes the full message but extracts only the portion relevant to that task. The LLM handles this naturally — given the task context, it will focus on the relevant content and ignore the rest.

### Ambiguous routing (quality flag)

A vague message could refer to one of several active tasks but it is genuinely unclear which one. The message content alone — even combined with the active task set — is insufficient to attribute it confidently to any single task.

```
"maal ready hai"  (no entity, multiple active tasks from same supplier)
routes: [{task-A, 0.61, ambiguous=true}, {task-B, 0.58, ambiguous=true}]
```

**Update agent behaviour**: receives the ambiguous routes, does not update any task node, generates a clarification request: "मैं confirm नहीं कर सका — यह किस order का माल है: [Task A] या [Task B]?" — routed to Ashish's alerts.

### What determines ambiguous vs multi-task?

| Signal | Multi-task (route to both, no flag) | Ambiguous (route to both, flag) |
|---|---|---|
| Entity names | Message names entities from both tasks | Message names no entity |
| Message length | Substantive message, multiple clauses | Short/vague — one status signal |
| Embedding gap | Each task has a distinct high-similarity portion | Both tasks score similarly, no clear separation |
| Operational semantics | Both tasks plausibly benefit from the update | The update can only apply to one task but it's unclear which |

The distinction matters: a multi-task route triggers two independent task updates; an ambiguous route triggers a clarification request and no update until resolved.

---

## Handling Short Confirmation Messages

"ok", "haan", "theek hai", "👍 sir", "noted", "kar deta hoon" — very common in Hinglish business WhatsApp, and operationally significant (they are often acknowledgements of instructions).

**Problem**: these messages have low embedding signal and no entity name. Pure content-based routing fails.

**Solution**: use the **thread context** — the last substantive message in the same group before this one.

```
Last message in group [15:30]: "Staff 1 — Kapoor Steel ko call karo,
  delivery confirm karo 22 Mar ke liye"
Current message [15:32]: "ok sir"
```

The router carries forward the last routing result for each group. If the current message is a short confirmation (< 5 words, no entity match), apply the last routing result for that group with a `confirmation_carry_forward: true` flag and a slight confidence reduction (0.85 × prior confidence).

This is cheap (no embedding needed) and correct for the vast majority of acknowledgements.

---

## Cold Start: New Task Detection

When a message arrives and no task matches, but the message looks task-relevant, the router creates a `new_task_candidate` record:

```python
new_task_candidate = {
    "trigger_message": message,
    "candidate_entities": [],  # entity matches, if any
    "candidate_items": [],     # item keyword matches
    "source_group": group_id,
    "status": "pending_extraction",
    "created_at": timestamp
}
```

The candidate queue is processed by a separate **task creation worker** that:
1. Retrieves the last N messages from the source group (typically the first few messages of a new thread)
2. Calls the full extraction LLM (current testing_prompt.txt approach) to extract the task structure
3. Creates a new task instance and registers the group in `source_groups`
4. Initializes the task context embedding
5. Notifies Ashish: "New task detected: [task name] — confirm to activate"

Until Ashish confirms, the task is in `provisional` state. Messages continue to be routed to it, but alerts do not fire.

---

## Passive Observer Principle

The agent reads operational WhatsApp groups as a passive observer. It has no presence in these groups and never posts to them under any circumstances.

**Rules:**

| Situation | Agent behaviour |
|---|---|
| Normal operational message | Read, route, process silently |
| Message addressed to the agent ("agent, confirm kar") | Silently ignore — do not process, do not respond |
| Ambiguous message — clarification needed | Carry ambiguity forward to update agent; seek clarification from Ashish via the dedicated alerts channel only if it impacts a specific decision |
| Alert or notification to send | Post to the dedicated alerts channel (separate from all operational groups) |
| Any operational group | Never post |

**Why this matters**: the agent has access to sensitive procurement discussions, Army supply details, and supplier pricing. Any agent message in an operational group would expose its existence and capabilities to clients, suppliers, and Army counterparts — none of whom have consented to interact with an AI system. Even a benign "I've noted this" reply would be inappropriate.

**Alerts channel**: a dedicated channel (WhatsApp group with Ashish only, or a dashboard) receives all agent outputs. Format TBC with Ashish. Whether any WhatsApp-based alerting is used at all is an open question — Ashish may prefer a dashboard-only model.

**Agent-directed messages**: if staff address the agent directly in a group ("mantri agent, is this done?"), the message is ignored at the noise filter stage. No processing, no routing, no response. The message is logged as `type: agent_directed` for audit purposes only.

---

## Router → Update Agent Interface

The router produces a `RoutingResult` per message. The update agent consumes it. The interface between them is the task-scoped message queue.

### One agent call per TaskRoute

For each `TaskRoute` in `RoutingResult.routes`, enqueue an independent update job:

```python
for route in routing_result.routes:
    enqueue_update_job(
        message=message,
        task_id=route.task_id,
        routing_confidence=route.confidence,
        routing_method=route.method,
        ambiguous=route.ambiguous,
        full_routing_result=routing_result,   # for context — agent can see other routes
    )
```

Each job is processed independently. The update agent receives the full message and the specific task context it is updating — it does not need to know about other routes unless it needs to flag a cross-task dependency.

**Why independent calls, not one batched call across all routes:**
- Task contexts are independent — the agent for Task A does not need Task B's state
- Failures are isolated — if Task B's update fails, Task A's update is unaffected
- Simpler to retry — a failed job for one task does not block others
- Each call is small (single message + single task context) — cache-friendly for the stable prefix

### Update agent behaviour by route type

| Route type | Agent behaviour |
|---|---|
| Single task, confident | Process message; update relevant node(s); write delta |
| Multi-task, all unambiguous | Independent call per task; each agent extracts the relevant portion for its task |
| One or more routes with `ambiguous=true` | Route to all candidates; agent produces **provisional updates only** — written to `provisional_deltas`, not task state; no alerts or transitions triggered; ambiguity context passed in prompt so agent can promote/discard on subsequent calls |
| New task candidate | Trigger task creation worker; do not block other routing |

### Handling multi-task messages in the update agent

The update agent receives the full message and focuses on the portion relevant to its task:

```
This message was routed to multiple tasks. You are updating: [task name].
Process only the information relevant to this task. Other tasks referenced
in this message will be updated by separate calls.
```

### Cross-Group Context Window

The update agent call must recreate the multi-thread format used in the test case extraction scripts — not just the current group's batch. When messages from different groups are causally linked (supplier group confirms dispatch at 14:00; All-Staff Group acknowledges it at 14:05), processing the All-Staff Group batch in isolation loses the causal connection. The agent sees acknowledgement without knowing what is being acknowledged.

**Fix**: when calling the update agent for task T with a batch from group G, fetch **all** messages from other groups in `task_routing_context.source_groups` since the **last task state update** — no truncation.

```python
def build_update_context(task_id, current_batch, source_group_id):
    # Semantic boundary: the task state captures everything the agent has
    # already processed and written as confirmed/provisional updates.
    # Only fetch what's new since the last processing pass.
    last_updated = db.get_task_last_updated(task_id)  # falls back to task.created_at

    cross_group_msgs = db.fetch_messages(
        task_id=task_id,
        exclude_group=source_group_id,
        since=last_updated,
        order='asc'
    )

    # No truncation. Process the full cross-group context.
    return sorted(cross_group_msgs + current_batch, key=lambda m: m.timestamp)
```

**Why `last_updated` rather than a fixed count or time window:**
- A fixed message count is inadequate for conversations that span hours — it gives the agent only the tail, without the context that explains what is being discussed
- A fixed time window is equally arbitrary — a task last processed 6 hours ago still has 6 hours of new cross-group messages the agent hasn't seen
- `last_updated` is a semantic boundary: it represents the last moment the agent processed and wrote updates for this task. Truncating anything after it would mean those messages are never processed and never reach the task state — they are simply lost

**Why no soft cap is needed at this scale:**
At Ashish's business volume (~200–500 messages/day total, ~10 active orders), the cross-group context since `last_updated` is realistically 10–40 messages per call — well under 5,000 tokens in any plausible scenario. Claude's 200k context window is not a binding constraint here. Introducing a cap would add complexity to defend against a theoretical edge case that does not arise in practice.

**Edge cases:**
- **New task**: `last_updated` is `task.created_at` — include all cross-group messages since the task was first detected
- **Long quiet period followed by burst**: the window expands naturally; all messages are processed in full. This is correct behaviour — they are genuinely new information

**If the business scales significantly**: chunked sequential processing is the right mechanism. Split the cross-group context into token-bounded chunks, process each in order. The task state itself serves as the inter-chunk summary — no separate summary field needed: chunk 1 processes and writes updates to task state; chunk 2 receives the now-updated task state plus the next slice of messages. A `chunked: true` + `chunk_index` flag in the agent call gives it awareness it is seeing a partial view. This is exactly how the historical extraction scripts already handle multi-day threads. For live processing at current scale, it is not needed.

**The existing test cases are the integration test for this behaviour.** Each test case is a multi-group, timestamp-sorted thread assembled in exactly this way. If the update agent call is structured correctly, the test case format is reproduced in live processing.

**No routing change**: the routing layer is unchanged. Cross-group context enrichment is purely an update agent call concern.

---

### Ambiguity handling — provisional update tier

The default behaviour when routing is ambiguous is **not** to seek clarification. The design principle is carry-forward: route to all plausible candidates, write provisional (not confirmed) updates, and resolve naturally as subsequent messages arrive.

This requires two distinct update tiers — confirmed and provisional — which must be enforced in both the data model and the agent output contract.

#### Two-tier update output

The update agent's JSON output distinguishes confirmed from provisional updates:

```json
{
  "task_id": "sata-tandoor-mar26",
  "confirmed_updates": [
    {"node": "delivery", "field": "status", "value": "dispatch_confirmed", "evidence": "..."}
  ],
  "provisional_updates": [
    {"node": "delivery", "field": "dispatch_date", "value": "2026-03-22",
     "evidence": "maal ready hai — inferred, ambiguous route", "ambiguity_id": "amb-001"}
  ],
  "promote_provisionals": [],   // IDs of pending provisionals confirmed by this message
  "discard_provisionals": []    // IDs of pending provisionals contradicted by this message
}
```

**Confirmed updates**: written to the task node store immediately. May trigger alerts and stage transitions.

**Provisional updates**: written to `provisional_deltas` table only. Do **not** trigger alerts or stage transitions. Included in the agent's context on every subsequent call for that task so the agent can reason about them.

#### What determines confirmed vs provisional

| Situation | Update type |
|---|---|
| Unambiguous route (`ambiguous: false`), substantive update | Confirmed |
| Ambiguous route (`ambiguous: true`), any update | Provisional |
| Unambiguous route, but agent expresses low confidence in interpretation | Provisional |
| Short confirmation message ("ok", "haan") carry-forwarded from prior routing | Confirmed (inherits prior route's status) |

#### Resolution — natural carry-forward

On each subsequent update agent call for a task, pending provisional deltas are included in the prompt context:

```
Pending unresolved provisional updates:
  [amb-001] "maal ready hai" (2026-03-26 14:03, All-Staff Group)
  — ambiguous between: sata-tandoor-mar26, ec-ac-order
  — not yet confirmed or discarded
```

When a new message resolves the ambiguity, the agent output includes:
- `"promote_provisionals": ["amb-001"]` — if this message confirms the provisional update belongs to this task
- `"discard_provisionals": ["amb-001"]` — if this message contradicts it

**Cross-task cleanup**: when provisional `amb-001` is promoted to task A, any record of `amb-001` in task B's provisional queue is automatically voided. The same ambiguous message cannot remain provisionally active on two tasks after resolution.

#### Staleness — silent expiry

Provisional deltas that are not resolved within **10 subsequent messages to the task** or **4 hours** (whichever comes first) are silently discarded. No alert fires; a debug-level log entry is written. The information is lost, which is the correct behaviour — if 10 subsequent messages didn't clarify it, the message was likely social noise or operationally irrelevant.

*Whether silent expiry is acceptable in practice is an open question — see Q16 and the Ashish interview.*

#### Clarification as fallback (Sprint 3 consideration)

If Ashish's feedback (via the interview or early deployment) indicates that silent expiry causes material operational gaps, the fallback is a human-in-the-loop clarification step:

- When a provisional delta approaches its expiry threshold (e.g., 8 of 10 messages elapsed without resolution), surface a clarification request to the alerts channel
- Format: chat snippet showing the ambiguous message + the two candidate tasks + a simple disambiguation prompt
- A dedicated clarification UI (task graph overlay or dialog with chat snippets) would be the Sprint 3 dashboard feature to support this

This is not built in Sprint 3 — the system first ships with silent expiry, and the clarification UI is added only if expiry proves lossy in practice. *(Deferred to Sprint 3 consideration — see open questions.)*

#### Clarification criteria (strict, for immediate escalation only)

Even before any clarification UI exists, seek immediate clarification when all three are true:
- The ambiguous message would trigger a node transition (not just a status note)
- The transition leads to a time-sensitive or irreversible action (dispatch, payment)
- The ambiguity cannot plausibly be resolved within 2–3 subsequent messages

**Delivery**: via the dedicated alerts channel only. Never in the operational group.

---

## Feedback Loop: Update Agent → Router

The router's data structures are not static — they improve as the update agent processes more messages. Every time the agent processes a message, it may discover information that enriches the routing context for future messages.

### What the update agent feeds back

| Discovery | Written to |
|---|---|
| New entity alias (e.g., "Kapoor sahab" seen alongside confirmed Kapoor Steel entity) | `entity_aliases` table |
| New officer name associated with a client unit | `entity_aliases` (officer → client entity) |
| New group name reference (e.g., "Eastern Command group ka" seen in All-Staff Group) | `task_routing_context.group_aliases` + `group_alias_dict` |
| Delivery location confirmed from message | `task_routing_context.delivery_location` |
| Key date established (delivery committed, invoice raised) | `task_routing_context.key_dates` |
| Task stage transition | `task_routing_context.stage` + trigger context embedding rebuild |
| New item type on task | `task_routing_context.item_types` |

### Feedback write format

The update agent's JSON output includes a `routing_updates` block alongside the task node updates:

```json
{
  "task_id": "sata-tandoor-mar26",
  "node_updates": [...],
  "routing_updates": {
    "new_aliases": [
      {"alias": "kapoor sahab", "entity_id": "entity:supplier:kapoor-steel", "confidence": 0.85}
    ],
    "group_aliases": [
      {"alias": "sata group", "group_id": "group_id_sata_001"}
    ],
    "delivery_location": "ranchi",
    "key_dates": {"delivery_committed": "2026-03-21"},
    "item_types": ["tandoor"]
  }
}
```

The router's maintenance worker processes these updates asynchronously — after the task update is written, not before. Routing quality improves incrementally with every processed message.

### Cold start quality

At deployment, the entity dictionary and task routing contexts are empty or sparsely populated. Routing quality at cold start depends on:
- Layer 2a (group map): fully functional from day 1 — group IDs are known at ingestion time
- Layer 2b entity match: sparse until first extraction pass populates aliases
- Layer 2b composite signals: empty until first few messages are processed and fed back
- Layer 2c embedding: functional from day 1 — the MiniLM model is general-purpose

**Mitigation**: run a one-time extraction pass over Ashish's last 3–6 months of historical chats before going live. This populates entity aliases, group aliases, location fields, and item types for all historical tasks — giving the router a substantial head start before any live messages arrive. See `bootstrapping_design.md` for the full pipeline design.

---

## Message Batching Policy

Messages from the same group accumulate in a per-group buffer and are flushed as a batch to the update agent. Batching reduces LLM calls by processing a conversation burst as a mini-thread rather than one-message-at-a-time. The batch is passed to the update agent in chronological order — the agent sees a coherent conversation excerpt rather than isolated messages.

The batch may contain messages that route to different task sets. The update agent receives the full batch and produces deltas for all relevant tasks — it is equipped to handle multi-task batches, as this mirrors the current batch-mode extraction model.

### Batch flush triggers (in priority order)

| Trigger | Description |
|---|---|
| **Context boundary detected** | Same signals as Layer 2a context boundary detection: entity switch, explicit transition language, or significant time gap. Flush the current batch before processing the boundary-crossing message as the start of a new batch. |
| **1-hour maximum horizon** | Any message that has been waiting more than 1 hour in the group buffer triggers an immediate flush, regardless of whether a natural boundary has been detected. This guarantees a maximum processing lag. *(TBC with Ashish — confirm based on operational cadence)* |
| **Conversation end detection** | A message that signals conversation closure ("ok done", "theek hai", "noted", end of an exchange) may indicate a natural batch boundary. Feasible to detect using the same context boundary signals — a trailing confirmation + subsequent silence. Useful for flushing completed exchanges promptly rather than waiting for the 1-hour horizon. See open question below. |

### What goes in a batch

All messages from a group buffer, regardless of:
- Which task(s) they route to — multi-task batches are the expected case
- Whether they contain media — media annotations (transcripts, image text) are interleaved before flushing
- Whether any individual message has low-confidence routing — carry-forward routing applies; do not block the batch on ambiguity

A context switch message is **not** included in the batch it ends — it starts the next batch.

### Batch → update agent

For each batch flush:
1. Collect all buffered messages for the group in chronological order
2. Identify the union of task sets across all messages in the batch
3. For each task in the union, build a cross-group context window (see §Cross-Group Context Window) and call the update agent with: the merged multi-thread + the specific task's context
4. Each call produces a task delta independently; calls for the **same task are serialized** (per-task queue), calls for different tasks run in parallel

```
Batch (Eastern Command group, flushed at context boundary):
  [13:58] Kapoor Steel group — Supplier: "dispatch ho gaya kal subah"  ← cross-group context (all msgs since task.last_updated)
  [14:00] Eastern Command: 15 AC units deliver karo 22 Mar tak
  [14:05] Ashish: ok sir, confirm kar deta hoon
  [14:10] Ashish: Staff 1 — Kapoor Steel se delivery confirm karo
  [14:12] Staff 1: ok sir

Task set: {eastern-command-ac-order}
→ 1 update agent call, merged cross-group thread as context
→ delta: delivery deadline noted, dispatch already confirmed by supplier, Staff 1 assigned coordination task
```

**Serialization**: update agent calls for the same `task_id` are queued and processed one at a time — never in parallel. This prevents write race conditions when two groups flush near-simultaneously for the same task (e.g., the supplier group and the All-Staff Group both flush within seconds of each other). Calls for different tasks remain parallel.

### Open questions on batching

- **1-hour horizon confirmation**: is 1 hour the right maximum lag for Ashish's business? Real-world operational cadence in a brick-and-mortar sales/ops business typically works in half-day horizons — Ashish is likely reviewing status in the morning and evening rather than minute-by-minute. Confirm with Ashish.
- **Conversation end detection as flush trigger**: is it feasible and necessary to detect natural conversation endings (trailing "ok", "done", long silence) as batch flush triggers? This would reduce average lag below the 1-hour maximum. The context boundary detection mechanism (Layer 2a) could double as a flush trigger — when a boundary is detected, flush the preceding batch. Open question: does this add enough value over the time-based flush to justify the complexity?

---

## Voice Note Transcription

Voice notes are common in Hinglish WhatsApp business communication. They carry the same operational content as text messages and must be processed by the router.

### Pipeline

```
Baileys captures voice note → audio file downloaded to VPS
    │
    ▼
Transcription service (e.g. Whisper API / Google STT)
    │
    ▼
Transcript text injected into message stream with marker:
  "[Voice note — Staff 1, 0:23]: maal ready hai, kal subah dispatch karenge"
    │
    ▼
Routed through normal router pipeline (Layer 1 → 2a → 2b → 2c → 3)
```

The transcript is interleaved into the chat log in timestamp order, identically to how image annotations are handled in the current batch system (the Sprint 1 SATA case processed 14 image annotations this way). The router sees plain text with a `[Voice note]` prefix — no special handling required beyond the tagging.

### Transcription service options

| Service | Language support | Cost | Notes |
|---|---|---|---|
| **OpenAI Whisper API** | Strong multilingual including Hindi | ~$0.006/min | Best quality for Hinglish; handles code-switching well |
| **Google Speech-to-Text** | Explicit Hindi (hi-IN) support | ~$0.004/min | Better for pure Hindi; may struggle with Roman-script Hinglish |
| **Local Whisper (open source)** | Same as Whisper API | $0 (compute only) | Viable on a VPS with adequate RAM; ~3–5x slower than API |

**Recommendation for Sprint 3**: OpenAI Whisper API. Best quality for the Hinglish use case; cost at the expected message volume (< 10 voice notes/day, average 30 seconds each) is < $1/month. Revisit local Whisper if cost scales.

**Open question**: transcription latency. Whisper API typically returns in 1–3 seconds per minute of audio. A 30-second voice note adds ~1.5 seconds to processing. Acceptable for the 1-hour batch horizon; may need caching if real-time alerting is required.

---

## Cross-Order Contextual References

Messages sometimes reference a different order not to update it, but to use it as context for the current one:

```
"woh Ranchi wala tandoor jaise hi quality chahiye" — spec reference to a past order
"same as pichle mahine wala" — using a completed order as a template
"jo Eastern Command ko gaya tha, waise hi packaging karo" — operational reference
```

These are **contextual references to historical or other active orders** — they are not task updates for the referenced order. Routing them to the referenced order would create spurious updates on completed tasks.

### Detection

A message is a cross-order contextual reference when:
1. It references a task that is **completed or belongs to a different active thread** in the group
2. The reference verb is **comparative or instructional** ("jaise", "same as", "waise hi", "waise karo", "jitna tha") rather than a status update verb ("ho gaya", "aa gaya", "dispatch hua")
3. The primary routing — via entity, group, or embedding signals — points to a **different active task** than the referenced one

### Router behaviour

- Route the message to the **currently active task** (the one being worked on in this group context), not the referenced historical task
- Tag the routing result with `contextual_ref: {referenced_task_id}` — the update agent should fetch the referenced task's context for background
- Do NOT create a route to the referenced task — it is context, not a recipient of an update

### Update agent behaviour

The update agent receives: current message + current task context + `contextual_ref` pointing to the referenced task. It fetches the referenced task's relevant fields (spec, item details, delivery notes) and uses them to enrich the current task update — without modifying the referenced task.

```
Prompt context includes:
  "This message references a past order for context: [referenced task summary].
   Use this as background for the current task update. Do not update the referenced task."
```

**Open question**: detection reliability. The comparative verb patterns ("jaise hi", "same as") are fairly reliable signals. The harder case is an implicit contextual reference with no comparative marker — "Ranchi wala packing" in a context where Ranchi delivery is both a past order and an active one. In this case, the active task set (from the rolling window) should take precedence.

---

## Data Structures

### Entity Dictionary (SQLite table)

```sql
CREATE TABLE entity_aliases (
    alias       TEXT NOT NULL,          -- normalized lowercase, no punctuation
    entity_id   TEXT NOT NULL,          -- e.g. "entity:supplier:kapoor-steel"
    entity_type TEXT NOT NULL,          -- "supplier" | "client" | "item" | "staff"
    confidence  REAL DEFAULT 1.0,       -- 1.0 = confirmed, 0.5 = provisional
    source      TEXT,                   -- "extraction" | "manual" | "inferred"
    added_at    DATETIME,
    PRIMARY KEY (alias, entity_id)
);
```

### Task Context (SQLite table, JSON column for embedding)

```sql
CREATE TABLE task_routing_context (
    task_id             TEXT PRIMARY KEY,
    source_groups       TEXT,           -- JSON array of group IDs (primary cross-group identifier)
    group_aliases       TEXT,           -- JSON array of informal group name variants seen in messages
    entity_ids          TEXT,           -- JSON array of canonical entity IDs
    delivery_location   TEXT,           -- normalised location string, e.g. "ranchi"
    key_dates           TEXT,           -- JSON: {order_date, delivery_date, invoice_date}
    item_types          TEXT,           -- JSON array of normalised item keywords
    officer_refs        TEXT,           -- JSON array of known officer aliases for this task
    context_text        TEXT,           -- human-readable summary used for embedding
    context_embedding   BLOB,           -- 384-dim float32 array, serialized
    stage               TEXT,
    last_updated        DATETIME
);
```

`group_aliases`, `delivery_location`, `key_dates`, `item_types`, and `officer_refs` are populated from the initial extraction pass and updated incrementally via the `routing_updates` feedback block from the update agent. The router reads these fields for composite signal scoring (Layer 2b) but does not write to them directly.

### Provisional Deltas (SQLite table)

```sql
CREATE TABLE provisional_deltas (
    id              TEXT PRIMARY KEY,       -- ambiguity_id, e.g. "amb-001"
    task_id         TEXT NOT NULL,          -- candidate task this provisional belongs to
    message_id      TEXT NOT NULL,          -- the ambiguous message
    node            TEXT NOT NULL,          -- e.g. "delivery"
    field           TEXT NOT NULL,          -- e.g. "dispatch_date"
    value           TEXT NOT NULL,          -- proposed value
    evidence        TEXT,                   -- agent's reasoning
    ambiguous_tasks TEXT,                   -- JSON array: all candidate task_ids for this message
    status          TEXT DEFAULT 'pending', -- "pending" | "promoted" | "discarded" | "expired"
    created_at      DATETIME,
    resolved_at     DATETIME,
    messages_since  INTEGER DEFAULT 0,      -- incremented on each subsequent update call; expire at 10
    FOREIGN KEY (message_id) REFERENCES routing_log(message_id)
);
```

`messages_since` is incremented each time the update agent is called for `task_id` without resolving this provisional. At `messages_since = 10` (or 4 hours elapsed, whichever first), status is set to `expired` and the record is no longer included in agent context.

### Routing Log (SQLite tables)

```sql
-- One row per message
CREATE TABLE routing_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id              TEXT NOT NULL,
    context_switch          BOOLEAN DEFAULT FALSE,
    context_switch_signal   TEXT,
    new_task_candidate      BOOLEAN DEFAULT FALSE,
    non_order_candidate     BOOLEAN DEFAULT FALSE,
    flags                   TEXT,           -- JSON array
    created_at              DATETIME
);

-- One row per (message, task) route — M:N
CREATE TABLE routing_routes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    confidence      REAL,
    method          TEXT,
    ambiguous       BOOLEAN DEFAULT FALSE,
    created_at      DATETIME,
    FOREIGN KEY (message_id) REFERENCES routing_log(message_id)
);
```

---

## Performance Characteristics

| Metric | Estimate | Basis |
|---|---|---|
| Messages per day (typical) | ~200–500 | Ashish's business: ~10 active threads, ~20-50 messages/thread/day |
| Layer 1 drop rate | ~40% | Reactions, stickers, social chatter |
| Layer 2a hit rate | ~50% of remaining | Most messages from order-specific groups |
| Layer 2b hit rate | ~30% of remaining | Entity names present in most substantive messages |
| Layer 2c invocations | ~20% of total | Only ambiguous messages from general groups |
| LLM calls per day | ~120–300 | Remaining after routing (passed to update agent) |
| Router latency (p50) | < 20ms | Layers 1+2a+2b only |
| Router latency (p95) | < 60ms | Including Layer 2c embedding |
| Memory (model loaded) | ~250MB | MiniLM-L12 + sentence-transformers overhead |

---

## Implementation Notes

### Model loading
Load the MiniLM model once at process startup (not per-message). Keep it in memory. Model load takes ~2–3s; amortized across the process lifetime, this is negligible.

```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
```

### Embedding updates
Do not recompute all task context embeddings on every message. Trigger a context embedding rebuild only when:
- A task node status changes (stage transition)
- A new entity alias is added to the task's entities
- The task is first created

Embeddings are cached in SQLite. Recomputation is async (does not block routing).

### Sprint 3 simplifications
The full design above is the target. Sprint 3 should ship:
- Layer 1 (noise filter) — complete
- Layer 2a (group map) — complete
- Layer 2b (entity keyword match) — basic entity dict, manually seeded
- Layer 2c (embedding) — implement but with a higher confidence threshold (prefer routing via 2a/2b; only use 2c as a fallback)
- Layer 3 (decision) — complete

Entity dictionary auto-maintenance (alias discovery from update agent) is a post-Sprint 3 enhancement. In Sprint 3, aliases are seeded manually from the first extraction pass per task.

---

## Open Questions

1. **Entity alias bootstrapping**: the entity dictionary starts empty. How quickly can it be seeded from Ashish's existing chat history? One pass of the extraction agent over 3–6 months of historical chats should populate most common aliases. Effort estimate TBC.

2. **All-Staff Group routing quality**: the All-Staff Group carries traffic for all active orders. Layer 2c (embedding) will be doing a lot of work here. What is the acceptable false-routing rate — how often can a message be routed to the wrong task before Ashish loses confidence?

3. **Entity collision risk**: "Sharma" may refer to multiple people. Short aliases ("EC", "51") may collide with non-entity words. What is the right policy — require bi-gram minimum for short aliases, or allow unigrams with a confidence penalty?

4. **Confirmation carry-forward window**: how long should the "last routing result per group" be valid for carry-forward? Proposal: 10 minutes. After 10 minutes of silence, a short confirmation message is not carry-forwarded and goes to Layer 2c instead.

5. **New task creation latency**: a new task candidate is detected but not confirmed until Ashish responds. Messages continue arriving before the task instance exists. Should these messages be buffered and replayed after confirmation, or processed cold?

6. **Context boundary threshold**: what is the right `BOUNDARY_GAP_THRESHOLD` for time-gap-based boundary detection? Proposal: 4 hours. But Ashish's groups likely have characteristic patterns — a supplier group may go silent for 12 hours mid-order with no context switch, while an All-Staff Group may switch context in 5 minutes. Threshold may need to be per-group-type, not global. TBC once real message logs are available.

7. **Transition message routing accuracy**: when a context boundary is detected, the transition message is routed without the rolling window (cold-routed via Layers 2b/2c). What is the expected miss rate here? If the transition message has no entity name ("ek aur baat — kal ki delivery ke baare mein"), it will likely fall through to a low-confidence embedding route or a new task candidate. This is a known gap — the system flags it for human review rather than guessing wrong.

8. **Non-order task classification precision**: relationship language ("kya haal hai") is common in both business and personal contexts. The same phrase from a client group may signal proactive sourcing; from a social contact it is irrelevant. The classifier needs to know whether the source group is a business group — which is established at whitelist time. Non-whitelisted personal groups are never ingested, so this is only a risk for business contacts who also send social messages in whitelisted groups.

9. **Date window tolerance**: for absolute date references ("15 March wala"), what is the right match window? Proposal: ±3 days. Army procurement has lead times; the referenced date may be approximate. Too tight = misses; too wide = multiple matches.

10. **Officer name corpus bootstrapping**: officer names ("Colonel Sharma", "Kapoor sir") are not in the entity dictionary at system start. The first mention of an officer name alongside a known client entity creates the alias. Until then, officer references are unresolvable. What is the expected density of officer-named messages before the dictionary populates? And do officer aliases persist across unit postings (a colonel at 51 Sub Area may be posted elsewhere within the year)?

11. **Vague relative references**: "woh purana order", "pehle wala", "last time" are common in Hinglish but carry no useful routing signal alone. Current design: treat as low-confidence, use only in combination with other signals. Is this the right policy, or should vague references always trigger a clarification?

12. **Item type collision across tasks**: multiple active tasks can share the same item type (two tandoor orders for different clients). Item signal alone is insufficient — but combined with location or date it is often sufficient. What is the practical rate of same-item concurrent orders in Ashish's business? If it is high, item signal has less marginal value than designed.

13. **Group alias bootstrapping**: informal group name references ("army wale group", "woh supplier group") need to be seen in context before they can be registered as aliases. Before the first occurrence, a group reference in the All-Staff Group is unresolvable. Is the initial extraction pass over historical chats sufficient to seed most group aliases, or will the dictionary remain sparse early in deployment?

16. **Silent expiry of provisional deltas** ⚑ *Validate with Ashish*: when an ambiguous update is not resolved by subsequent messages, the current design silently discards it after 10 messages or 4 hours. The assumption is that unresolved ambiguity means the message was operationally irrelevant — if it mattered, the conversation would have clarified it. But if Ashish finds this causes real operational gaps (a delivery update that was genuinely ambiguous and never clarified by chat, but still needed to be tracked), the fallback is a human-in-the-loop clarification step. The preferred channel and format for clarification (alert group vs dashboard dialog with chat snippet) should be confirmed before building. **If silent expiry is not acceptable, a clarification UI becomes a Sprint 3 feature** — not an afterthought. Confirm with Ashish in the interview.

14. **Generic group references across group types** ⚑ *Validate with Ashish*: "supplier group ka kya hua?" could refer to any active task's supplier group. The originating client group is a stronger identifier than the supplier group because supplier groups are often task-specific but named after the supplier, not the client — "Kapoor Steel group" identifies a supplier, not an order. Should supplier group references be treated as entity references (matching the supplier entity) rather than order-proxy references? **Design decision pending**: confirm with Ashish whether staff typically use the client group name or the supplier group name when referring to an order cross-group. This determines whether supplier group references belong in the source-group-reference signal path or the entity-match path.

15. **Staff willingness to resolve ambiguity** ⚑ *Validate with Staff and Ashish*: when routing is ambiguous (vague message, multiple plausible tasks), the agent may need human input to resolve it. Who should receive the clarification request — all staff in the relevant group, a designated senior staff member, or Ashish only? Would staff be willing to answer occasional disambiguation questions at all, or would they find it intrusive? What channel is acceptable (dedicated alert group, dashboard, direct message)? And what is the tolerable frequency — once a day is probably fine; ten times a day is likely to be ignored or resented. The answer directly shapes whether the agent can rely on staff-in-the-loop disambiguation or must rely entirely on carry-forward and downstream resolution.

---

## Relationship to Other Design Docs

| Topic | Where documented |
|---|---|
| Full pipeline (ingestion → update agent → alerts) | `live_monitoring_design.md` |
| Task instance schema, node types, delta model | `task_lifecycle_state_graph_design.md` |
| Stateful update agent prompt structure | `live_monitoring_design.md §3` |
| Alert engine and delivery | `live_monitoring_design.md §4` |
| **Message router (this document)** | `message_router_design.md` |

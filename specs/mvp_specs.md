# MVP Technical Specifications

**Status**: Sprint 3 target
**Author**: Kunal Chowdhury
**Date**: 2026-03-27
**Depends on**: `reports/implementation_design.md`, `reports/task_lifecycle_state_graph_design.md`, `reports/message_router_design.md`

---

## Platform Selection

**Custom Python** — not n8n or LangGraph.

**Rationale**: The full stack is Python (FastAPI, Redis, SQLite, sentence-transformers, Claude API). The update agent is a single structured LLM call with a fixed prompt structure — not a multi-step agent flow that benefits from LangGraph's graph abstraction. The router is domain-specific logic (keyword matching, alias lookup, composite scoring) that a visual workflow tool cannot express. LangGraph adds a framework dependency without architectural benefit at this stage.

**Platform trade-offs considered:**

| | Custom Python | LangGraph | n8n |
|---|---|---|---|
| Update agent complexity | Single LLM call + JSON parse — no framework needed | Overhead for a single-node "graph" | Cannot express domain routing logic |
| Router logic | Native Python dicts, rapidfuzz, sqlite3 | No benefit | No benefit |
| State management | SQLite is the state store | LangGraph checkpointing is redundant | No benefit |
| Existing codebase | Continuous with `scripts/`, `prompts/` work | New dependency | New dependency |
| Debug/iterate | Direct Python, pytest, pdb | Additional abstraction layer | Visual but cannot inspect Python state |

Revisit LangGraph if the update agent evolves into a multi-step reasoning loop (e.g. evidence gathering → hypothesis → update → verify). Not the case for Sprint 3.

---

## Development Requirements

### Environment

```
Python 3.11+
Node.js 18+ (Baileys)

Python dependencies:
  fastapi
  uvicorn
  anthropic          # Claude API
  redis              # message queue + session state
  sentence-transformers  # MiniLM (Layer 2c, defer for MVP but install now)
  rapidfuzz          # entity alias fuzzy match (Layer 2b)
  pydantic           # LLM output validation
  httpx              # async HTTP (media download)
  openai-whisper     # voice transcription (defer for MVP but install now)
  ffmpeg-python      # audio prep for Whisper (defer)
  datasette          # audit log browser (zero-config)

Node.js dependencies (Baileys process):
  @whiskeysockets/baileys
  express            # thin HTTP wrapper to POST to FastAPI /ingest
```

### Repository structure for Sprint 3

```
src/
├── ingestion/
│   ├── baileys/          # Node.js Baileys process
│   │   ├── index.js      # connect, listen, POST to FastAPI
│   │   └── package.json
│   └── ingest.py         # FastAPI /ingest endpoint + media enrichment stub
├── router/
│   ├── router.py         # Layer 1 noise filter, Layer 2a group map, Layer 2b entity match
│   ├── alias_dict.py     # entity alias lookup + rapidfuzz fuzzy match
│   └── worker.py         # Redis queue consumer, calls router then update agent
├── agent/
│   ├── update_agent.py   # LLM call, prompt assembly, JSON parse, pydantic validation
│   └── prompt.py         # system prefix + template injection
├── store/
│   ├── db.py             # SQLite connection, schema init
│   ├── task_store.py     # read/write task_instances, task_nodes
│   └── usage_log.py      # write usage_log records
├── alerts/
│   └── cron_worker.py    # 15-min polling loop, cadence alert detection, log output
└── config.py             # hardcoded MVP config (group JIDs, task seed, entity aliases)
```

---

## Integration Specifications

### Baileys (monitor number, Node.js)

```javascript
// src/ingestion/baileys/index.js

// On each message event:
const payload = {
  message_id: msg.key.id,
  group_id: msg.key.remoteJid,      // group JID e.g. "120363xxxxxxx@g.us"
  sender_jid: msg.key.participant,
  timestamp: msg.messageTimestamp,
  body: msg.message?.conversation
     || msg.message?.extendedTextMessage?.text
     || null,
  media_type: detectMediaType(msg), // 'text' | 'image' | 'audio' | 'sticker' | 'reaction'
  media_url: null                   // Baileys media URL if applicable (Sprint 3: pass through)
}
// POST to http://localhost:8000/ingest
```

Baileys session is persistent via auth state stored to disk (`baileys_auth/`). QR code re-auth needed if session expires — no automated recovery for Sprint 3.

### Claude API (update agent)

```python
# anthropic SDK, Sonnet 4.6
client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=system_prompt,          # cached: system prefix + task template
    messages=[{"role": "user", "content": variable_section}]
)
```

Auth: `ANTHROPIC_API_KEY` env var.
Output format: structured JSON — validate with pydantic before writing to store.
Usage: log `input_tokens`, `output_tokens`, computed cost to `usage_log` on every call.

### Redis (message queue)

```python
# Ingestion side (FastAPI worker thread):
redis_client.lpush("ingest_queue", json.dumps(enriched_message))

# Router worker:
while True:
    _, raw = redis_client.brpop("ingest_queue", timeout=5)
    process(json.loads(raw))
```

Connection: `redis://localhost:6379`. AOF persistence enabled in `redis.conf`.

### SQLite

Single database file: `data/mantri.db`.
Schema: see `reports/implementation_design.md §Data Schema`.
Initialised on first run from `src/store/db.py:init_schema()`.
WAL mode enabled for concurrent reads during cron worker + router worker overlap.

---

## Hardcoded MVP Config (`src/config.py`)

```python
# Sprint 3 MVP — single seeded order, hardcoded routing

MONITORED_GROUPS = {
    "120363SATA_CLIENT_JID@g.us":    "task_001",   # SATA client group → task_001
    "120363ALL_STAFF_JID@g.us":       None,         # All-Staff → use Layer 2b
    "120363KAPOOR_SUPPLIER_JID@g.us": None,         # Supplier group → use Layer 2b
}

# Replace JID values with real group JIDs after Baileys connects

SEED_TASK = {
    "id": "task_001",
    "order_type": "standard_procurement",
    "client_id": "entity_sata",
    "supplier_ids": ["entity_kapoor_steel"],
    "stage": "quote_requested",
    "source": "manual_seed"
}

ENTITY_ALIASES = {
    # SATA variants
    "sata": "entity_sata",
    "51 sub area": "entity_sata",
    "eastern command": "entity_sata",
    "51 sa": "entity_sata",

    # Kapoor Steel variants
    "kapoor": "entity_kapoor_steel",
    "kapoor steel": "entity_kapoor_steel",
    "kapoor ji": "entity_kapoor_steel",
    "kapoor bhai": "entity_kapoor_steel",
}
```

After Baileys connects, print all group JIDs to stdout and update `MONITORED_GROUPS` with real values. One-time setup step before first message is processed.

---

## Data Flow Specifications

### Message ingestion to update agent

```
1. Baileys emits message event
2. Node.js handler POSTs to FastAPI /ingest
3. FastAPI:
   a. Validate payload (pydantic)
   b. Media enrichment (Sprint 3: text only — stub for image/audio, pass through)
   c. Push enriched message to Redis "ingest_queue"
   d. Return 200 immediately

4. Router worker (separate process, brpop loop):
   a. Layer 1: drop reactions, stickers, system messages (media_type check)
   b. Layer 2a: check MONITORED_GROUPS[group_id]
      - direct task_id found → route with confidence 0.90
      - None → proceed to Layer 2b
   c. Layer 2b: score message body against ENTITY_ALIASES
      - tokenise body (lowercase, strip punctuation)
      - rapidfuzz partial_ratio against each alias key
      - match score >= 0.80 → resolve to entity_id → look up active tasks for that entity
      - no match → push to dead_letter_queue (log, don't crash)
   d. Emit: [(task_id, confidence)]

5. For each routed (task_id, confidence):
   a. Pull from SQLite: current node states + last 20 messages for task
   b. Build prompt (see Prompt Specifications below)
   c. Call Claude API, log usage
   d. Parse + validate JSON response
   e. Write node updates to task_nodes
   f. Append message to task message log
```

### Prompt structure (update agent)

```
SYSTEM (cached prefix, ~800 tokens):
  [role + output format spec]
  [task template: node definitions for standard_procurement order type]
  [task type checklist: cadence nodes that activate at each stage]
  [business context: Uttam Enterprise, entity list, staff roster]

USER (variable section, ~500–800 tokens):
  Current node states (JSON)
  Last 20 messages (timestamp, sender, group, body)
  Instruction: update nodes based on messages; activate cadence nodes for current stage
```

Expected output (pydantic-validated):

```python
class NodeUpdate(BaseModel):
    node_id: str
    new_status: Literal["pending", "active", "completed", "blocked", "provisional"]
    confidence: float         # 0.0–1.0
    evidence: str             # message excerpt that triggered this update

class AgentOutput(BaseModel):
    node_updates: list[NodeUpdate]
    new_task_candidates: list[dict]   # empty for MVP
    ambiguity_flags: list[str]        # empty for MVP
```

If pydantic validation fails: log raw LLM output to `logs/agent_errors.log`, skip write, continue.

### Cron worker (cadence alert detection)

```python
# src/alerts/cron_worker.py — runs every 15 minutes

def check_cadence_alerts():
    tasks = db.get_active_tasks()
    for task in tasks:
        template = get_template(task.order_type)
        for node in template.cadence_nodes:
            if node.should_activate(task) and task.node_status(node.id) == "pending":
                log_alert(
                    f"CADENCE ALERT | task={task.id} | node={node.name} "
                    f"| stage={task.stage} | overdue={node.overdue_by(task)}"
                )
```

Alert output: `logs/alerts.log` (Sprint 3). No WhatsApp send yet.

---

## Task Template Specification (Standard Procurement, Sprint 3)

Defined inline in `src/agent/prompt.py` for Sprint 3. Becomes a DB-loaded template in production.

```python
STANDARD_PROCUREMENT_TEMPLATE = {
    "order_type": "standard_procurement",
    "nodes": [
        # Reactive nodes (triggered by messages)
        {"id": "enquiry_received",      "type": "real_world_milestone", "stage": "enquiry"},
        {"id": "quote_requested",       "type": "agent_action",         "stage": "quote"},
        {"id": "quote_received",        "type": "real_world_milestone", "stage": "quote"},
        {"id": "po_raised",             "type": "agent_action",         "stage": "order_placed"},
        {"id": "dispatch_confirmed",    "type": "real_world_milestone", "stage": "dispatch"},
        {"id": "delivery_confirmed",    "type": "real_world_milestone", "stage": "delivered"},
        {"id": "invoice_received",      "type": "real_world_milestone", "stage": "payment"},
        {"id": "payment_made",          "type": "real_world_milestone", "stage": "payment"},

        # Cadence nodes (activate on stage/time trigger — NOT from messages)
        {"id": "quote_followup_48h",    "type": "cadence",
         "activates_when": "stage=quote AND hours_since_quote_requested >= 48"},
        {"id": "predispatch_checklist", "type": "cadence",
         "activates_when": "stage=order_placed AND po_raised=completed"},
        {"id": "payment_followup_30d",  "type": "cadence",
         "activates_when": "stage=payment AND days_since_delivery_confirmed >= 30"},
        {"id": "delivery_photo_check",  "type": "cadence",
         "activates_when": "stage=dispatch AND dispatch_confirmed=completed"},
    ]
}
```

---

## Quality Risk Testing Specifications

### Test scenario 1 — reactive node update (regression)

**Input**: Text message in SATA client group: "sir dispatch ho gaya, vehicle number HR 38 C 4521"
**Expected**: `dispatch_confirmed` node → `completed`; evidence captured
**Pass criteria**: node status updated in SQLite within 5 seconds of message receipt

### Test scenario 2 — cadence node activation (the Sprint 1 gap)

**Input**: PO has been raised (po_raised=completed in DB). No WhatsApp message about pre-dispatch checklist.
**Expected**: `predispatch_checklist` cadence node → `active`; cron fires alert within 15 minutes
**Pass criteria**: alert appears in `logs/alerts.log` without any message having mentioned the checklist

### Test scenario 3 — Layer 2b routing (All-Staff group)

**Input**: Text message in All-Staff group: "Kapoor ji ka material aaj dispatch hoga"
**Expected**: "Kapoor" alias → `entity_kapoor_steel` → `task_001` → update agent called
**Pass criteria**: message appears in task_001 message log in SQLite; relevant node updated

### Test scenario 4 — Layer 2a routing (client group)

**Input**: Text message in SATA client group: "payment kar diya hai, check karo"
**Expected**: direct group → task route; `payment_made` node → `completed` or `provisional`
**Pass criteria**: node updated; no Layer 2b call needed (route confidence = 0.90)

### Test scenario 5 — end-to-end no manual intervention

**Input**: Sequence of 3 messages across SATA group and All-Staff covering dispatch → delivery → invoice
**Expected**: all three corresponding nodes updated; payment_followup_30d cadence node pending (not yet due)
**Pass criteria**: SQLite node states correct; no human intervention required at any point

These map directly to the Sprint 1 eval set failure mode (SATA case, 89/100 — cadence gap). Pass on scenario 2 is the key milestone.

---

## Human-in-Loop Requirements (Sprint 3 MVP)

Minimal for MVP — ambiguity handling is passive (log only, no resolution flow):

- Messages that fail routing (Layer 2b score < 0.80): written to `logs/unrouted.log`. No Ashish alert.
- Provisional node updates (agent confidence < 0.75): written to SQLite with `status='provisional'`. No correction UI.
- New task candidates flagged by agent: written to `logs/new_task_candidates.log`. No creation flow.

All three become active human-in-loop flows in production (dashboard correction, ambiguity resolution via WhatsApp, new task approval). Deferred to post-Sprint 3.

---

## Error Handling Requirements

| Failure | Handling |
|---|---|
| Baileys session drops | Log + stdout alert. Manual restart. No automated recovery. |
| FastAPI /ingest receives malformed payload | Return 400, log, drop message. |
| Redis unavailable | FastAPI /ingest returns 503. Baileys handler retries 3× then logs dropped message. |
| Claude API timeout / 5xx | Retry 3× exponential backoff (1s, 4s, 16s). If still fails: log to `agent_errors.log`, skip this message. |
| LLM output fails pydantic validation | Log raw output to `agent_errors.log`, skip write, continue. |
| SQLite write error | Log, raise exception, stop worker (don't silently drop updates). |
| Group JID not in MONITORED_GROUPS | Layer 2b attempt. If still no route: `unrouted.log`. |

All workers run as persistent loops. Unhandled exceptions: log full traceback, sleep 5s, restart loop iteration. Do not crash the process on a single bad message.

---

## Success Criteria

The MVP is complete when all five test scenarios above pass end-to-end on real WhatsApp messages (not synthetic). Specifically:

1. Scenario 2 passes — cadence node activates without a message mentioning it. This closes the Sprint 1 quality risk gap.
2. Scenarios 1, 3, 4 pass — routing and reactive updates work across both Layer 2a and 2b paths.
3. Scenario 5 passes — no human intervention required in a 3-message sequence.

Secondary: `usage_log` has correct entries for every Claude API call with accurate cost computation.

---

## Sprint 3 Implementation Order

Suggested build sequence (each step independently testable):

1. **SQLite schema + seed script** — `db.py`, `config.py`, seed `task_001` and entity aliases
2. **Baileys → FastAPI ingestion** — `baileys/index.js`, `ingest.py`; verify messages arrive in Redis
3. **Router Layer 2a + 2b** — `router.py`, `alias_dict.py`; unit test against message fixtures
4. **Update agent** — `update_agent.py`, `prompt.py`; test against Sprint 1 SATA case messages
5. **Store writes** — `task_store.py`; verify node states in SQLite after agent output
6. **Cron worker** — `cron_worker.py`; seed an overdue cadence node, verify alert fires
7. **End-to-end** — run all five test scenarios with real Baileys messages

Each step has a clear pass/fail before moving to the next. Baileys JID discovery (step 2) is a prerequisite for steps 3–7 — do this first.

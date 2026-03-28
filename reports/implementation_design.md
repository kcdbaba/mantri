# Implementation Design

**Status**: In progress
**Sprint**: Sprint 2 → Sprint 3
**Author**: Kunal Chowdhury
**Date**: 2026-03-27

---

## Learning Since Last Interaction

### User Research Insights
User research interviews scheduled for 27–28 March with Ashish (90 min, design session) and staff (45 min, group walkthrough). Not yet completed. Key assumptions about trust, task taxonomy thresholds, and router parameters will be validated in these sessions. Outputs will feed directly into task taxonomy updates and router parameter calibration.

### Evaluation Testing Results
Sprint 1 ran 17 test cases (16 synthetic + 1 real SATA case). Key findings:
- Synthetic cases: improved from 11/16 → 16/16 PASS through 3 rounds of prompt iteration
- SATA real case: 89/100 — reactive implicit tasks handled well; cadence/procedural implicit tasks confirmed as the gap
- Model evaluation (17 cases): Sonnet 4.6 17/17 PASS (88.9 avg) vs Gemini 2.5 Flash 13/17 (81.4 avg). Gemini fails on complex entity resolution and long-context. Decision: keep Sonnet for update agent.

### Prompt Experimentation Findings
Three prompt calibration rounds produced measurable improvements:
1. Separation-default rule: when uncertain whether messages are one order or multiple, default to separate parent tasks
2. Entity resolution calibration: tentative merge with flag rather than hedging
3. Unidentified-client structural decomposition: separate parent task per order even when client is unknown

Task-type subtask checklists added to prompt as Sprint 2 mitigation for cadence implicit task gap. These become the seed data for Sprint 3 template graphs.

### Implementation Progress Status
Built and working in Sprint 1:
- `scripts/case_extractor.py`: parses WhatsApp .txt exports, filters time window, annotates images via Claude Vision
- `scripts/run_test.py`: runs extraction agent against test cases, scores output
- `scripts/run_synthetic_batch.py`: batch runs across all synthetic cases
- Full evaluation dataset: 20+ cases across 6 risk categories with automated scoring
- 4 architecture design documents: live monitoring, message router, task lifecycle graph, bootstrapping

Not yet built: live ingestion, message router, task store, alert engine, dashboard.

### Updated Quality Risk Focus
**Primary confirmed risk**: cadence/procedural implicit task detection — validated by SATA case testing. The agent handles reactive implicit tasks well but misses procedural steps that always happen at a certain order stage regardless of messages (pre-dispatch checklist, payment confirmation milestone). Mitigation: task-type subtask checklists injected into prompt.

**Secondary risk**: trust and adoption — being validated in user interviews (27–28 Mar). Not yet a confirmed risk but architecturally significant.

---

## Delivery Context Design

### Workflow Analysis

**Ashish's work pattern:**
- Morning sweep of all pending tasks (structured review of what needs attention today)
- Mid-day and end-of-day reviews via phone with senior staff
- Continuous fire-fighting throughout the day — verbal in office, phone-based remote
- Constantly on phone with clients, staff, suppliers, contractors
- Staff call him regularly for OTP codes (portal logins, delivery confirmations) — these calls become status update + guidance opportunities. He uses every call as a management touchpoint.

**Staff work pattern:**
- Morning sweep of their own task lists
- Continuous coordination throughout the day via WhatsApp and phone
- Call Ashish for OTPs, guidance, approvals — these are natural status-sync moments

**Key insight**: This is a high-interruption, phone-dominant operation. The agent must fit into existing communication patterns, not create new ones. WhatsApp is the primary delivery surface. A dashboard is used for structured review and corrections, not daily operation.

**Pain points** (where the agent adds value):
- Tasks from message threads that never get logged anywhere → missed follow-ups
- Status of concurrent orders visible only to whoever handled the last message → coordination gaps
- Cadence tasks (payment follow-up, stock reconciliation) have no trigger in messages → silent misses
- Ashish informed on operational detail only when staff choose to mention it → information asymmetry

**Flow points** (where the agent must not interfere):
- Direct WhatsApp conversations between Ashish and clients/suppliers — agent reads, never posts
- Staff ↔ Ashish phone calls — unrecorded and unmonitorable; agent fills gaps from what is in WhatsApp
- In-office verbal coordination — agent cannot observe; human review gates surface gaps

### Delivery Mechanism

**Primary: WhatsApp (dedicated alerts channel)**
- A dedicated "Mantri Alerts" WhatsApp group separate from all operational groups
- Ashish and relevant staff are members
- Morning digest delivered here: structured task list, prioritised by urgency
- Intraday push alerts delivered here: fires when a specific condition is met
- Agent-initiated ambiguity resolution requests delivered here (Ashish only)
- Agent never posts to operational groups under any circumstances

**Secondary: Dashboard (web)**
- Structured task view: full task graph per order, node statuses, message history
- Correction interface: status updates, mandatory reasoning, role-differentiated approval flow
- Ambiguity resolution: Ashish sees the message snippet + agent's candidate tasks, confirms or corrects
- Audit log: all corrections, all agent actions, all alert firings
- Sprint 3 target: Streamlit or simple static HTML + JSON API (DigitalOcean Bangalore VPS)

### Interaction Model

| Touchpoint | Who | Channel | Agent role |
|---|---|---|---|
| Morning digest | Ashish + senior staff | WhatsApp Alerts | Push: structured pending task list, ordered by urgency |
| Intraday alert | Ashish or relevant staff member | WhatsApp Alerts | Push: single alert, specific condition, specific task |
| Ambiguity resolution | Ashish only | WhatsApp Alerts + Dashboard | Push: message snippet + candidate tasks; Ashish confirms |
| Status correction | Ashish + staff (role-differentiated) | Dashboard only | Pull: structured form, mandatory reasoning, audit trail |
| On-demand status query | Ashish | WhatsApp (dedicated bot number — separate from monitoring number) | Pull: structured drill-down command shell. Keyword entry or entity name → indexed list → number to drill down. Alias matching with clarification on near misses. No NLP parsing. Zero LLM cost. |
| Full task review | Ashish + staff | Dashboard | Pull: complete task graph view per order |

### Agency vs Autonomy

| Task | Model | Rationale |
|---|---|---|
| Message ingestion and routing | Fully autonomous | Every message, continuous, no human needed |
| Task node status updates (clear signal) | Fully autonomous | Confident updates from clear messages don't need approval |
| Task node status updates (ambiguous signal) | Provisional + flag | Agent makes best guess, marks provisional, Ashish confirms |
| New task creation | Autonomous + notification | Agent creates, notifies Ashish via alerts |
| Ambiguity resolution | Human in loop (Ashish) | Agent asks, Ashish decides — never resolved silently |
| Alert delivery | Fully autonomous | Time-triggered and message-triggered, no approval needed |
| Status corrections (staff) | Controlled: mandatory reasoning + approval flow for junior staff | Corrections are audited and feed prompt improvement |
| Status corrections (Ashish) | Controlled: mandatory reasoning | Ashish corrections are authoritative but still audited |
| Prompt improvement | Human in loop | Corrections + ambiguity resolutions feed an improvement pipeline; changes reviewed before deployment |

### Correction Flow Design

**Manual corrections (staff or Ashish overriding agent):**
- Access: dashboard only — no NLP/WhatsApp feedback accepted
- Required fields: new status, reason (free text, mandatory)
- Junior staff (default — all except designated seniors): correction submitted → approval request routed to any available senior → applied on approval
- Senior staff (1–2 designated, e.g. Pramod) + Ashish: correction applied immediately
- Approval routing: any senior can act on any approval request; task-type-based routing (e.g., accounts corrections → Pramod preferentially) is a v2 consideration — TBC with Ashish
- Absent/on-leave staff handling: open question — inactivity detection vs manual dashboard tagging vs both — TBC with Ashish (interview Q added)
- All corrections: timestamped, actor-tagged, reason recorded, added to audit log
- Pipeline: audit log feeds a future prompt improvement flow (batch, not live)

**Agent-initiated ambiguity resolutions:**
- Access: WhatsApp Alerts + dashboard confirmation
- Liberally allowed — low friction, Ashish just confirms which task a message belongs to
- Required: Ashish selection from presented options (no free text needed for simple cases)
- All resolutions: audited, added to entity alias dictionary, feed prompt improvement pipeline

**Rationale for dashboard-only corrections:**
- NLP correction via WhatsApp is ambiguous (agent may misinterpret the correction)
- Structured corrections produce clean training signal for prompt improvement
- Audit trail is a trust-building feature for both Ashish and staff

### WhatsApp Query Shell Design

The on-demand query interface is a **structured command shell over WhatsApp**, not an NLP agent. All matching is keyword or alias-based. Zero LLM cost.

**Two separate WhatsApp numbers (hard requirement):**
- **Monitor number** (Baileys, Ashish's existing number): reads operational groups. Never sends messages to operational groups. Receives messages from Ashish and staff for processing only.
- **Bot number** (dedicated business number): Ashish messages this for queries. Alerts and digests are sent from this number. Staff also receive alerts here. This number never posts to operational groups.

Mixing both on the same number would conflate operational messages with agent-generated ones — no separation of concerns and confusing for staff.

**Entry points (keyword-matched, case-insensitive):**

| Keyword | Response |
|---|---|
| `orders` / `tasks` | Full indexed list of all active parent tasks, ordered by priority |
| `recent` | Active + completed tasks from the last 30 days |
| `blocked` | Tasks with at least one blocked node — full list |
| `alerts` | All unresolved open alerts |
| `issues` | All ambiguity flags + dependency violations |
| `[entity name]` | Tasks matching that client or supplier (see entity query below) |

**Entity query (client / supplier name):**

Ashish types a name or partial name — "Kapoor", "SATA", "Eastern Command", "51 SA".

Matching against the entity alias dictionary:
1. **Exact match** → return all active tasks for that entity, indexed
2. **Single near miss** (edit distance ≤ 2, or alias prefix match) → "Did you mean *Kapoor Steel*? Reply Y to confirm."
3. **Multiple hits** (e.g., "sharma" matches Sharma Steel + Col. Sharma) → "Found 2 matches: 1. Kapoor Steel  2. Col. Sharma (51 Sub Area). Reply 1 or 2."
4. **No match** → "No entity found matching '[name]'. Try 'orders' to see all active tasks."

This reuses the same entity alias dictionary built for the message router — no separate data store needed.

**Navigation within a session:**

```
[number]           → drill into item N from the current list
"detail"           → full summary of current item (all fields)
"sub tasks"        → node breakdown of current task
"messages"         → recent messages associated with current task (last 10)
"back"             → return to previous list
"done" / "bye"     → end session (session state cleared)
```

**Session state:** `{current_view, current_list, current_item_id, previous_view}` — held in Redis with a 30-minute inactivity timeout. After timeout, any message restarts a new session.

**Response format:**
- No item cap — show all results. WhatsApp supports long messages and scrolling; capping creates frustration when Ashish knows an order exists but can't see it.
- Each list item: `N. [Client/Supplier] — [Item] — [Stage] — [Priority]`
- Drill-down summary: task name, current stage, next expected step, any blocked nodes, open alerts
- Sub-task list: `N. [Node name] — [Status]` for all nodes in the task graph

**Zero LLM cost:** all matching is dictionary lookup + fuzzy string match (rapidfuzz, CPU). Session state management is Redis. Response formatting is template-based string construction from task store data.

### User Journey Notes

**Ashish's morning (7–8am):**
Morning digest arrives in Mantri Alerts WhatsApp group. Structured list: 3–5 high priority items, then medium. Each item: task name, current status, what's blocked, suggested next step. Ashish scans it over chai. Anything that needs his decision gets flagged. He either acts on WhatsApp directly (calls the relevant person) or opens the dashboard to correct/update a status.

**Mid-day (remote, needs a status check):**
Ashish is out of the office and wants a quick picture of what's open. He messages the agent bot: "orders". Agent replies with a full indexed list of active parent tasks — one line each (status, priority, what's blocked). No item cap. He replies "3" to drill into task 3, gets a summary, then "sub tasks" to get the node breakdown. Or he types "Kapoor" — agent matches against entity alias dictionary, returns all active tasks involving Kapoor Steel. No NLP, no ambiguity. Same data as the dashboard, delivered without opening a browser.

**Intraday alert:**
Supplier hasn't responded to a quote request in 48 hours. Alert fires: "No response from [Supplier] — [Order X] — quote sent 48h ago. Follow up." Relevant staff member (Samita, who handles supplier liaisons) receives the alert and acts.

**End of day:**
Ashish does a phone review with Pramod (senior staff). Both have the same task list visible. No information asymmetry — both working from the same picture.

---

## Backend Architecture Design

### System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  INGESTION LAYER                                                     │
│                                                                      │
│  WhatsApp Groups ──► Baileys (Node.js)  ──► FastAPI /ingest         │
│  (monitor number)     unofficial WA API      POST per message        │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ raw message JSON (body/media_url/type)
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  MEDIA ENRICHMENT LAYER                                              │
│                                                                      │
│  image (document)  ──► PaddleOCR/Surya (local) ──► text            │
│                         └── low confidence? ──► Gemini Flash (API)  │
│  image (photo)     ──► Gemini 1.5 Flash 8B (API) ──► caption        │
│  audio (voice note)──► ffmpeg transcode ──► Whisper local ──► text  │
│  text              ──► pass through                                  │
│                                                                      │
│  Output: enriched message with body = original OR derived text       │
│  Usage logged: {call_type, tokens_in, tokens_out, cost_usd, ts}     │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ enriched message JSON
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ROUTING LAYER                                                       │
│                                                                      │
│  Redis message queue  ──►  Message Router (Python)                  │
│  (async decoupling)          Layer 1: noise filter                  │
│                              Layer 2a: group → task map             │
│                              Layer 2b: entity keyword match         │
│                              Layer 2c: embedding similarity         │
│                                                                      │
│  Output: [(task_instance_id, confidence_score), ...]                │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ routed message + task context
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  UPDATE AGENT LAYER                                                  │
│                                                                      │
│  Stateful update agent  ──►  Claude Sonnet 4.6 API                  │
│  ~3.5K tokens/call           cached system prefix + template        │
│  structured JSON output      + last 20 messages + node states       │
│                                                                      │
│  Output: {node_updates[], new_tasks[], ambiguity_flags[]}           │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ structured updates
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  TASK STORE + ALERT ENGINE                                           │
│                                                                      │
│  SQLite task store  ◄──►  Alert engine                              │
│  task instances           message-triggered alerts (sync)           │
│  node states              time-triggered alerts (cron)              │
│  routing context                                                     │
│  entity aliases                                                      │
└──────────────────────┬───────────────────────┬──────────────────────┘
                       │                       │
                       ▼                       ▼
           ┌───────────────────┐   ┌───────────────────────┐
           │  Dashboard (web)  │   │  WhatsApp Bot Number   │
           │  Streamlit /      │   │  Meta Cloud API        │
           │  FastAPI + HTML   │   │  alerts, digests,      │
           │  corrections,     │   │  query shell           │
           │  audit log        │   │                        │
           └───────────────────┘   └───────────────────────┘
```

---

### Data Flow: Single Message End-to-End

**Input**: WhatsApp message arrives in a monitored group.

```
1. Baileys captures message → emits event to Node.js handler
2. Handler POSTs to FastAPI /ingest:
   {
     "message_id": "...",
     "group_id": "whatsapp_group_jid",
     "sender_jid": "...",
     "timestamp": 1711500000,
     "body": "sir dispatch kal hoga",
     "media_url": null
   }

3. FastAPI enriches message based on type (synchronous, before queuing):

   TEXT: no enrichment; body already present

   IMAGE (delivery photos, invoices, payment screenshots, PO PDFs):
     → Download media from Baileys-provided URL (short-lived, ~10 min TTL)
     → Detect image type: document (high text density, aspect ratio, greyscale)
       vs photo (scene image)

     DOCUMENT (invoices, PO PDFs, payment screenshots):
       → Run PaddleOCR-VL (local, CPU, ~1–2s) or Surya OCR
       → If confidence >= 0.70: use extracted text as body — zero API cost
       → If confidence < 0.70 (unclear layout, handwriting, mixed script):
           → Escalate to Gemini 1.5 Flash 8B (API)
           → Log: {call_type='vision_gemini', tokens_in, tokens_out, cost_usd, message_id, ts}

     PHOTO (delivery photos, site images):
       → Call Gemini 1.5 Flash 8B directly (no local model suitable for scene understanding)
       → Prompt: "Describe this image for a procurement operations log.
                  Extract: subject, key entities, amounts, dates, status if visible.
                  Reply in English."
       → Log: {call_type='vision_gemini', tokens_in, tokens_out, cost_usd, message_id, ts}

     → Store extracted text/caption as body; preserve original media_url
     → Note: ffmpeg is NOT used for images — it is only used for audio transcoding

   AUDIO (voice notes — common in Indian WhatsApp groups):
     → Download audio file from Baileys URL
     → Transcode to 16kHz WAV if needed (ffmpeg subprocess)
     → Run Whisper large-v3 locally (CPU, ~30–60s for a 1–2 min voice note)
     → Store transcript as body; tag message with media_type='audio_transcribed'
     → Log: {call_type='whisper', duration_secs, model, message_id, ts}
     → No external API cost; only compute time

   If media download fails (URL expired, network error):
     → Tag message as media_unavailable=True
     → Push through with empty body — router will likely score low (ambiguity queue)
     → Log failure; do not crash pipeline

4. FastAPI pushes enriched message to Redis queue (key: "ingest_queue")
   → Async decoupling: routing/LLM work happens off the request path

5. Router worker pops from queue
   Layer 1 — noise filter: reactions, stickers, system messages dropped
   Layer 2a — group_task_map lookup:
     group_id → [(task_id, confidence=0.90), ...]
     If dedicated group (one task): direct route
     If shared group (All-Staff): proceed to 2b
   Layer 2b — entity keyword match:
     Composite score across: entity_aliases, officer_refs,
     source_group_mentions, location, date, item_type
     Hinglish-aware: "Kapoor ji" → "kapoor" → entity_id=42
   Layer 2c — embedding similarity (if 2b score < threshold):
     Embed message with MiniLM (CPU, ~8ms)
     Cosine similarity against task_routing_context embeddings
     Top-k matches above sim threshold

6. Router output: [(task_id_1, 0.92), (task_id_2, 0.71)]
   M:N by design — one message → multiple tasks allowed
   If all scores < ambiguity_threshold AND no direct route:
     → Push to ambiguity_queue (Ashish resolution needed)
     → Continue processing other messages; don't block

7. For each (task_id, confidence) above threshold:
   Pull task context from SQLite:
     - Current node states
     - Last 20 messages already associated with this task
     - Task template (node definitions + expected sequence)
   Construct LLM prompt:
     [cached system prefix + template] + [variable section]
     Variable section: ~500–800 tokens (messages + node states)
   Call Claude Sonnet 4.6 API
   // TODO (pre-live): enable Anthropic prompt caching on the system prompt.
   // Use cache_control: {"type": "ephemeral", "ttl": "1h"} (not the default 5-min TTL).
   // Rationale: messages arrive sporadically throughout the day; 5-min window gives near-zero
   // cache hit rate at Ashish's volume. 1-hour TTL matches realistic burst patterns.
   // Cost: write = 2× input tokens (once per hour); read = 0.1× input tokens (all subsequent).
   // System prompt is ~3.5K tokens and identical per order_type — strong caching candidate.
   // Implementation: wrap system_prompt string in a list with cache_control block in
   // _call_with_retry() in src/agent/update_agent.py.
   Log: {call_type='update_agent', tokens_in, tokens_out, cost_usd, task_id, message_id, ts}
   Parse structured JSON response:
     {
       "node_updates": [{"node_id": "dispatch", "status": "completed", ...}],
       "new_task_candidates": [],
       "ambiguity_flags": []
     }

8. Write updates to SQLite task store
   If confidence < confirmed_threshold: mark node update as provisional
   Trigger alert engine (message-triggered check):
     - Dependency violation? (node completed out of sequence)
     - New task candidate? → create + notify Ashish
     - Node reopened? → alert

9. Alert engine cron (runs independently):
   Every 15 minutes: scan for time-triggered conditions
   - supplier_silence_hours >= 48 → alert
   - delivery_overdue (expected_date + buffer) → alert
   - payment_days >= 30 → alert
   Fires to WhatsApp bot number (Meta Cloud API send)
```

---

### External Integrations

| Integration | Purpose | Library/API | Notes |
|---|---|---|---|
| WhatsApp (Sprint 3 only) | Read messages from groups — temporary during Meta onboarding | Baileys (Node.js, monitor-only, no sends) | Replaced by Meta Cloud API webhook after Sprint 3 |
| WhatsApp (production) | Inbound: all group + 1:1 messages via webhook. Outbound: 1:1 alerts only — never to groups | Meta WhatsApp Business Cloud API | Bot number added to each group as member (receive only); outbound restricted to personal JIDs at code level; template pre-approval for proactive sends |
| HTMX | Data-light partial page updates in dashboard | `htmx.js` (CDN, ~14KB, cached) | Swaps only changed HTML fragments; server renders; no JS framework needed |
| Cytoscape.js + cytoscape-dagre | Task graph visualisation | `cytoscape`, `cytoscape-dagre` (~1MB total, cached) | DAG layout for task lifecycle graph; colour-coded node status; click-to-drill-down |
| PaddleOCR-VL (local) | Document OCR — invoices, PO PDFs, payment screenshots | `paddleocr` or `surya` | CPU; ~1–2s/image; free; primary path for documents; zero API cost |
| Gemini 1.5 Flash 8B | Image captioning for photos; OCR fallback for low-confidence documents | `google-generativeai` SDK | ~$0.000056/image (80× cheaper than Claude Vision); primary for scene images; escalation path for documents |
| Claude API (text) | Update agent LLM calls | `anthropic` Python SDK | Sonnet 4.6; ~$0.024/message-routed-call |
| Whisper (local) | Voice note transcription | `openai-whisper`, `large-v3` | CPU; no API cost; ~30–60s per 1–2 min note; ffmpeg for audio transcode only (not images) |
| Redis | Message queue + session state | `redis-py` | Decouples ingestion from routing; query shell sessions |
| SQLite | Task store, entity aliases, routing context, usage log | `sqlite3` stdlib | Sprint 3; migrate to Firestore later |
| MiniLM (`paraphrase-multilingual-MiniLM-L12-v2`) | Embedding similarity routing | `sentence-transformers` | CPU only; ~8ms/embed; no GPU needed |
| rapidfuzz | Entity alias fuzzy matching (query shell) | `rapidfuzz` | CPU; edit distance + prefix match |
| FastAPI + Jinja2 HTML | Task dashboard + correction forms | `fastapi` + `jinja2` | Mobile-friendly; REST API reused by future clients; FastAPI already in stack |
| Datasette | Audit log + usage log browse (read-only) | `datasette` | Zero build effort; auto-generates web UI from SQLite; available Sprint 3 day 1 |

### Cost and Usage Tracking

All external API calls write a usage log record to SQLite immediately after the call returns.

```sql
CREATE TABLE usage_log (
    id          TEXT PRIMARY KEY,
    call_type   TEXT,       -- 'vision' | 'update_agent' | 'embedding' (if API-based later)
    message_id  TEXT,
    task_id     TEXT,       -- null for vision calls before routing
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    cost_usd    REAL,       -- computed at write time from known pricing
    duration_ms INTEGER,    -- wall time for the call
    model       TEXT,       -- e.g. 'claude-sonnet-4-6'
    ts          INTEGER
);
```

Cost computation at write time:

```python
PRICING = {
    "claude-sonnet-4-6":      {"input": 3.00,   "output": 15.00},   # per 1M tokens
    "gemini-1.5-flash-8b":    {"input": 0.0375, "output": 0.15},    # per 1M tokens
}

def compute_cost(model, tokens_in, tokens_out):
    p = PRICING[model]
    return (tokens_in * p["input"] + tokens_out * p["output"]) / 1_000_000
```

Whisper and PaddleOCR calls log duration and model only (no monetary cost).

**Dashboard cost view** (Sprint 3): a simple table in the dashboard showing:
- Total API spend today / this week / this month
- Breakdown by call_type
- Top 5 costliest tasks (by total update_agent tokens)
- Flag if daily spend exceeds a configurable threshold (default: $5/day)

**API cost estimates (per day):**

| Call type | Model | Estimated volume/day | Cost/call | Est. daily cost |
|---|---|---|---|---|
| Document OCR (local) | PaddleOCR/Surya | 10–15 documents | $0 | $0 |
| Document OCR (escalation) | Gemini 1.5 Flash 8B | 2–4 documents | ~$0.000056 | ~$0.0002 |
| Photo captioning | Gemini 1.5 Flash 8B | 5–10 photos | ~$0.000056 | ~$0.0006 |
| Update agent | Claude Sonnet 4.6 | 50–100 messages | $0.024 | $1.20–$2.40 |
| Voice transcription | Whisper local | 5–15 notes | $0 | $0 |
| **API total** | | | | **~$1.20–$2.40/day** |

**Infrastructure costs (fixed monthly):**

| Resource | Provider | Monthly cost |
|---|---|---|
| VPS (2 vCPU, 4GB RAM) | DigitalOcean Bangalore | ~$24 |
| Backup storage (daily SQLite) | DigitalOcean Spaces | ~$1 |
| Domain | Namecheap / Google Domains | ~$1 |
| Meta Cloud API (bot messages) | Meta | 1,000 business-initiated convos/month free; ~$0.0147/convo after (India utility rate) — well within free tier at current volume |
| **Infra total** | | **~$26/month** |

**Infra cost tracking tool**: DigitalOcean has a built-in monthly billing alert (Settings → Billing → Spending Notifications). Set a cap at $40/month — covers VPS + storage with headroom. No custom tooling needed for Sprint 3.

Dashboard infra view: a static config block in the dashboard (`infra_costs.json`) with monthly fixed costs, updated manually. Combined with the dynamic API usage log this gives a complete cost picture.

**Total estimated running cost: ~$62–$98/month** (API costs at 30 days + infra).

---

### Decision Points and Branching Logic

```
Router decision tree:
├── Is message noise? (reaction, sticker, system) → DROP
├── Is group_id in group_task_map?
│   ├── Single task → route directly (confidence 0.90), skip 2b/2c
│   └── Multiple tasks → score with 2b, use 2c as tiebreaker
├── Entity composite score >= ROUTE_THRESHOLD (0.65)?
│   ├── Yes → route to matched task(s)
│   └── No → embed and try 2c
├── Embedding similarity >= SIM_THRESHOLD (0.70)?
│   ├── Yes → route (lower confidence, may be provisional)
│   └── No → push to ambiguity queue
└── Multiple tasks above threshold?
    └── Route to ALL (M:N) — update agent handles each independently

Update agent decision tree:
├── Node status change (clear signal, e.g. "dispatched") → confirmed update
├── Node status change (ambiguous signal) → provisional update + flag
├── New entity / order mentioned → new_task_candidate flag → alert Ashish
├── Message references multiple tasks → agent processes per task independently
└── Contradictory update (node already completed, now re-opened) → alert
```

**Confidence thresholds** (hardcoded for Sprint 3, calibrated post-bootstrapping):

| Parameter | Value | Calibration source |
|---|---|---|
| Direct group route confidence | 0.90 | Fixed; dedicated groups are unambiguous |
| Entity keyword route threshold | 0.65 | To be calibrated against bootstrapping pass |
| Embedding similarity threshold | 0.70 | To be calibrated against bootstrapping pass |
| Ambiguity threshold (below = queue) | 0.50 | TBC with Ashish interview |
| Confirmed vs provisional update | 0.75 (agent output field) | Prompt-controlled |
| Context boundary gap | 4 hours | Assumption — needs calibration against Ashish's message patterns (Q in interview) |

---

### Human Judgment Points

**1. Ambiguity resolution (Ashish)**

Trigger: message scored below ambiguity threshold after all router layers.

```
Flow:
  Push to ambiguity_queue (Redis)
  →  Alert engine picks up
  →  Sends WhatsApp to bot number:
       "Unclear which order this belongs to:
        '[message snippet]'
        1. Kapoor Steel — MS Angle order (Oct batch)
        2. Eastern Command — pending quote
        3. New order (not in system)
        Reply 1, 2, or 3."
  →  Ashish replies with number
  →  Bot receives reply, resolves assignment
  →  Message retroactively processed against correct task
  →  Resolution added to audit log + entity alias dictionary

State persistence: ambiguity_queue in Redis with message_id key.
Timeout: if Ashish doesn't respond in 24h, mark message as unresolved,
         include in next morning digest as pending ambiguity.
```

**2. Provisional update confirmation (Ashish via dashboard)**

Trigger: update agent marks a node update as provisional (low-confidence signal).

```
Flow:
  Node written to SQLite with status=provisional
  Dashboard shows provisional nodes highlighted (e.g., yellow)
  Morning digest includes count of provisional updates pending review
  Ashish opens dashboard → sees message snippet + proposed update
  Confirms (→ status=confirmed) or corrects (→ enters correction flow)
```

**3. Status corrections (staff / Ashish via dashboard)**

Already designed in §Correction Flow Design. Technical implementation:

```python
# Correction API endpoint
POST /corrections
{
  "task_id": "...",
  "node_id": "...",
  "new_status": "...",
  "reason": "...",  # mandatory
  "actor_id": "..."
}

# Role check:
if actor.role in ["ashish", "pramod"]:  # designated seniors
    apply_immediately(correction)
else:
    route_to_approval_queue(correction, preferred_approver=task_type_routing)

# All corrections:
write_audit_log(correction)
append_to_improvement_pipeline(correction)
```

---

### Error Handling

| Failure | Impact | Recovery |
|---|---|---|
| Media URL expired (Baileys ~10 min TTL) | Image/audio not downloadable | Tag message `media_unavailable=True`, push with empty body, log. Router scores low → likely ambiguity queue. No crash. |
| Claude Vision API timeout / 5xx | Image not captioned | Retry 3× with backoff. If still fails: push message with body=`[image: caption unavailable]`, log, continue. |
| Whisper OOM / crash | Voice note not transcribed | Catch exception, push with body=`[voice note: transcription failed]`, log. ffmpeg subprocess timeout: 120s hard kill. |
| Baileys session drops (monitor) | Ingestion stops; messages missed | Alert fires to bot number: "Monitor offline since [time]. Restart needed." Manual restart. Missed messages not recoverable (WhatsApp has no message history API); gap logged. |
| Meta Cloud API down / 5xx | Alerts and query responses fail | Meta SLA is 99.9%. Retry 3× with backoff. Log failure. If persistent, alerts queue in Redis and send in batch when restored. |
| Claude API timeout / 5xx | Node update delayed for one message | Retry with exponential backoff (3 attempts, max 30s total). If still fails: push message to dead_letter_queue, log error, continue. Process on next run. |
| Redis down | Message queue and sessions unavailable | FastAPI /ingest returns 503 (don't drop messages). Baileys buffers are not persistent — this is a data loss risk. Mitigation: Redis persistence (AOF mode) on VPS. |
| SQLite write contention | Task store unavailable briefly | SQLite WAL mode for concurrent reads. Single writer (update agent worker) reduces contention. |
| LLM output malformed JSON | Node update lost for one call | Validation with `pydantic` before write. If validation fails: log raw output, push to review queue, don't crash pipeline. |
| VPS restart | All processes down | Systemd service units for Baileys (Node.js), FastAPI, Redis, cron worker. Auto-restart on failure. Recovery time: <2 minutes. |

---

### WhatsApp Architecture: Two Numbers, Two Mechanisms

**Architecture decision: full Meta Cloud API, no Baileys**

Monitoring does not need to be silent. All monitored groups are owned by Ashish or staff. The bot number is added to each group as a member — visible to other members, which is acceptable. This enables a single mechanism for both monitoring (inbound webhook) and sends (outbound API calls), eliminating Baileys entirely from production.

**Enforced constraint: agent never sends to groups or external parties.** The bot number is added to groups for receive-only. The code path for sending is restricted to 1:1 messages with Ashish and staff. This is enforced architecturally: the send function takes a `recipient_jid` (must be a personal JID) and explicitly rejects group JIDs. No alert, digest, or query response is ever sent to a group.

| | Monitor (inbound) | Send (outbound) |
|---|---|---|
| Mechanism | Meta Cloud API webhook | Meta Cloud API REST (graph.facebook.com) |
| From | Bot number (added to each operational group) | Bot number → Ashish or staff 1:1 only |
| Cost | Free — receiving messages has no charge | ₹0.11/utility message. ~10–15 alerts/day = ~₹33–50/month (~$0.50) |
| Session messages | — | Free: Ashish messages bot first → reply within 24h window. Query shell responses fall here. |

**Meta Cloud API setup:**
1. New dedicated phone number (cannot reuse an existing WhatsApp number)
2. Meta Business Manager account + business verification: tax ID, incorporation docs — typically 3–7 business days
3. Template pre-approval for proactive outbound (morning digest, intraday alert, ambiguity resolution prompt) — typically 24 hours
4. Ashish adds bot number to each operational group manually (one-time setup, ~15 minutes)

**Sprint phasing:**
- **Sprint 3** (Apr 12–26 build): Baileys monitor-only (no sends). Focus is ingestion → routing → update agent → task store. No alerts sent yet.
- **Final demo** (May 1): Meta Cloud API bot live. Start verification immediately — 3–7 day timeline means verification must begin by Apr 18 at latest to be ready for May 1.

**Action item**: start Meta business verification before Sprint 3 build begins. This is on the critical path for the final demo.

---

### Deployment Architecture

**Single VPS: DigitalOcean Bangalore (BLR1)**

```
VPS (2 vCPU, 4GB RAM, ~$24/month)
├── FastAPI server           (uvicorn, systemd, port 8000)
│   ├── /webhook/whatsapp    Meta Cloud API inbound webhook (verify + receive)
│   ├── /dashboard           HTMX + Jinja2 HTML task view + correction forms
│   └── /api/*               REST endpoints for Cytoscape.js graph data
├── Router + update agent worker  (Python, systemd, reads Redis queue)
├── Cron alert worker        (Python, systemd, 15-min polling loop → Meta Cloud API sends)
├── Redis                    (systemd, AOF persistence, port 6379)
├── Datasette                (systemd, port 8002, read-only audit log + usage log browse)
└── SQLite db file           (local, daily backup to DigitalOcean Spaces)

Meta WhatsApp Business Cloud API (external, managed by Meta)
├── Inbound                  group messages + Ashish/staff messages → webhook → FastAPI
└── Outbound                 alerts, digests, query shell responses → 1:1 only, never to groups
                             enforced: send function rejects group JIDs at call site

Sprint 3 (temporary, Baileys only):
└── Baileys monitor process  (Node.js, systemd — monitor only, no sends)
    replaces Meta webhook during Sprint 3 while API verification is in progress
```

Nginx terminates TLS (Let's Encrypt). Dashboard and Datasette on HTTPS. No public exposure of FastAPI internals or Redis.

**Dashboard: HTMX + FastAPI + Jinja2 + Cytoscape.js**

Stack choice rationale:

| Requirement | Approach |
|---|---|
| Mobile-friendly, Ashish on phone | HTMX partial updates — only changed fragment over wire, not full page reload |
| Compute offloading | FastAPI + Jinja2 renders on server; client gets HTML |
| Data-light for low connectivity | HTMX sends only JSON/form data; returns only the HTML fragment that changed |
| Offline asset caching | Service worker caches CSS, JS (Cytoscape bundle), Jinja2 base template on first load |
| Task graph visualisation | Cytoscape.js + cytoscape-dagre plugin (DAG layout; ~1MB, cached after first load) |
| Correction forms | Standard HTML forms — mandatory fields, role-based routing enforced server-side |
| Audit log browse | Datasette (zero build effort, SQLite auto-UI, available Sprint 3 day 1) |
| "Add to home screen" | PWA manifest + service worker — Ashish installs dashboard as phone app |

Streamlit was considered and rejected: not mobile-friendly, fights custom form UX, requires `st.rerun()` for state updates, adds a framework dependency that doesn't fit the stack.

**Why not a full PWA JS framework (React/SvelteKit)?** SvelteKit would be the choice if needed — SSR built-in, smaller bundle than React, good PWA support. Rejected for Sprint 3: separate build system, npm pipeline, significantly more setup. HTMX achieves 80% of the data-light benefit with the existing FastAPI stack.

**Datasette** (self-hosted, read-only SQLite viewer) provides a full browsable UI over `audit_log` and `usage_log` with zero build effort — available the moment the database exists.

**Why single VPS:**
- Baileys requires persistent process (not serverless)
- SQLite is zero-ops, sufficient for Sprint 3 message volumes
- Total infra cost: ~$26/month all-in (VPS + backup storage + domain)
- Migration path: FastAPI → serverless, SQLite → Firestore as volume grows

---

### Data Schema (SQLite, Sprint 3)

```sql
-- Core task store
CREATE TABLE task_instances (
    id TEXT PRIMARY KEY,
    template_id TEXT,
    order_type TEXT,  -- 'standard_procurement', 'gem_portal', 'custom_order', ...
    client_id TEXT,
    supplier_ids TEXT,  -- JSON array
    created_at INTEGER,
    last_updated INTEGER,
    stage TEXT,
    history_partial BOOLEAN DEFAULT 0,
    source TEXT  -- 'bootstrap' | 'live'
);

CREATE TABLE task_nodes (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES task_instances(id),
    node_type TEXT,  -- 'agent_action', 'real_world_milestone', 'cadence', 'decision', 'human_review'
    name TEXT,
    status TEXT,  -- 'pending', 'active', 'completed', 'blocked', 'provisional'
    confidence REAL,
    last_message_id TEXT,
    updated_at INTEGER,
    updated_by TEXT  -- 'agent' | actor_id for manual corrections
);

-- Router tables
CREATE TABLE entity_aliases (
    alias TEXT,
    entity_id TEXT,
    entity_type TEXT,  -- 'client', 'supplier', 'officer', 'item'
    confidence REAL,
    source TEXT,  -- 'bootstrap' | 'live' | 'manual'
    PRIMARY KEY (alias, entity_id)
);

CREATE TABLE task_routing_context (
    task_id TEXT PRIMARY KEY REFERENCES task_instances(id),
    source_groups TEXT,  -- JSON array of group JIDs
    entity_ids TEXT,     -- JSON array
    delivery_location TEXT,
    key_dates TEXT,      -- JSON
    item_types TEXT,     -- JSON array
    officer_refs TEXT,   -- JSON array
    context_text TEXT,
    context_embedding BLOB  -- serialised numpy float32 array
);

-- Audit + improvement
CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    event_type TEXT,  -- 'correction', 'ambiguity_resolution', 'alert_fired', 'node_update'
    actor_id TEXT,
    task_id TEXT,
    node_id TEXT,
    before_state TEXT,  -- JSON
    after_state TEXT,   -- JSON
    reason TEXT,
    timestamp INTEGER
);

CREATE TABLE ambiguity_queue (
    message_id TEXT PRIMARY KEY,
    group_id TEXT,
    body TEXT,
    candidates TEXT,  -- JSON: [{task_id, label}, ...]
    status TEXT,  -- 'pending' | 'resolved' | 'expired'
    resolved_task_id TEXT,
    created_at INTEGER,
    resolved_at INTEGER
);
```

---

### Primary Quality Risk: Cadence/Implicit Tasks

The confirmed primary risk (cadence/procedural implicit tasks missed by extraction) is addressed structurally in this architecture:

1. **Template pre-loading**: cadence nodes (payment follow-up at 30d, pre-dispatch checklist, monthly stock reconciliation) are in the task template, not inferred from messages. They activate on time or predecessor-node-completion triggers — the alert engine fires them, not the update agent.

2. **Task type checklists** (Sprint 2 mitigation, already in prompt): until templates are built, the prompt injects expected subtasks per order type. These become the seed data for Sprint 3 templates.

3. **Alert engine independence**: cadence alerts fire from cron, not from message receipt. No message needs to exist for "payment due in 5 days" to trigger.

Sprint 3 template bootstrapping (T1 in lifecycle design): after the historical extraction pass, run a template-derivation analysis over the completed task instances to identify common node sequences per order type. First version reviewed with Ashish before deployment.

---

### What is NOT Being Built Yet

| Component | Status | Rationale |
|---|---|---|
| Call recording transcription pipeline | Post-Sprint 3 | Whisper script exists; live pipeline needs Ashish's call setup to be clarified first |
| Prompt improvement pipeline | Post-Sprint 3 | Audit log feeds it, but automated batch improvement is Sprint 4+ |
| WhatsApp Business API migration | Post-Sprint 3 | Only after Baileys is proven and Ashish agrees to dedicated business number |
| Firestore migration | Post-Sprint 3 | SQLite sufficient at current message volumes |
| Staff dashboard (mobile) | Post-Sprint 3 | Staff interaction is WhatsApp-first; dashboard is Ashish-primary for Sprint 3 |
| Assamese NLP support | Post-Sprint 3 | MiniLM is multilingual but Assamese coverage unverified; monitor in production |


---

## MVP Scope Definition

### North Star

Primary quality risk: **cadence/procedural implicit task detection** — the agent handles reactive tasks (explicitly mentioned in messages) well but misses procedural nodes that activate at a given order stage regardless of message content (pre-dispatch checklist, payment follow-up at 30 days, stock reconciliation). The template graph architecture addresses this structurally. The MVP must prove it works on real messages.

### Core Path

One complete routing + update cycle covering real message patterns:

```
Text message arrives in client group (SATA)
  → Layer 2a: direct group → task route
  → Update agent: reactive node updated + cadence node activated

Text message arrives in shared group (All-Staff) referencing same order
  → Layer 2b: entity alias match → same task
  → Update agent: further node updates; cadence node still active

Cron check fires
  → Overdue cadence node detected → log-level alert
```

### Component Decisions

| Component | Decision | Rationale |
|---|---|---|
| Baileys monitor (read-only) | Build | Real messages required to test the risk |
| Router Layer 2a (group → task map) | Build | Client group messages route here |
| Router Layer 2b (entity keyword + alias dict) | Build | All-Staff / supplier group messages route here |
| Router Layer 2c (embedding similarity) | Defer | Entity matching sufficient for 1 known order |
| Update agent (text messages) | Build | Core of the quality risk test |
| Task template with cadence nodes | Build | This IS the fix being tested |
| SQLite task store (nodes + routing context) | Build | Need to persist and inspect node states |
| Time-triggered alert engine (cron, log output) | Build minimal | Cadence alert is the proof point |
| Entity alias dict (manual seed for 1 order) | Hardcode | Full bootstrapping pass deferred; 1 order is enough to test |
| Media enrichment (images, voice notes) | Defer | Text messages sufficient to test quality risk |
| Dashboard / correction UI | Defer | Log and SQLite inspection sufficient for v1 |
| Meta Cloud API / bot sends | Defer | Sprint 3 is Baileys monitor-only; sends are not needed to test the risk |
| Query shell | Defer | |
| Morning digest | Defer | |
| Full bootstrapping pass | Defer | Replace with 1 manually seeded task instance |

### What is Hardcoded for v1

- 1 task instance: one active SATA-type procurement order, manually inserted into SQLite
- Entity alias dict: manually seeded with the known aliases for that order's client and supplier
- Group → task map: 2 entries hardcoded (SATA client group JID + All-Staff group JID → same task_id)
- Task template: defined inline in code (not loaded from a dynamic template store)
- Alert output: log line to file (not WhatsApp send)
- No user auth, no multi-user, no correction UI

### Definition of Done

- [ ] Real WhatsApp text message arrives in SATA client group → Layer 2a routes to the seeded task instance
- [ ] Real WhatsApp text message arrives in All-Staff group referencing the same order entity → Layer 2b routes to the same task instance
- [ ] Update agent correctly updates the reactive node described in the message
- [ ] Update agent also activates the cadence node(s) appropriate for the current order stage (the Sprint 1 gap)
- [ ] SQLite `task_nodes` reflects both node state changes
- [ ] Cron worker detects the overdue cadence node and writes a log-level alert
- [ ] End-to-end with no manual intervention after message is sent

### Deferred to v2 (Post-Sprint 3)

- Router Layer 2c (embedding similarity) — needed when orders are numerous and entity matching becomes ambiguous
- Full historical bootstrapping pass — needed before going live with Ashish's real groups
- Media enrichment (image OCR, voice transcription)
- Dashboard with correction UI and task graph visualisation
- Meta Cloud API bot (sends, query shell, morning digest)
- Multi-task concurrency testing (multiple live orders simultaneously)
- Assamese language coverage validation

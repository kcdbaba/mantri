# Live Monitoring Design

**Status**: Pre-implementation design
**Target sprint**: Sprint 3
**Depends on**: `task_lifecycle_state_machine_design.md` (task graph model, templates, cost architecture)
**Author**: Kunal Chowdhury
**Date**: 2026-03-26

---

## Scope

The task lifecycle graph design document (`task_lifecycle_state_machine_design.md`) defines the **data model** — templates, instances, node types, edge types, and the incremental cost rationale. This document defines the **operational pipeline** — how the system receives WhatsApp messages in near-real time, routes them to the right task instances, updates state, and delivers alerts to Ashish and staff.

The two documents should be read together. This document does not repeat the data model concepts; it assumes them.

---

## Current State vs Target State

### Current (Sprint 1/2)

```
WhatsApp chats
      │
      ▼ (manual export)
  raw chat logs (txt files)
      │
      ▼ (run_test.py on demand)
  single full-context LLM call
      │
      ▼
  flat task list + agent output (markdown)
      │
      ▼ (manual review by Kunal/Ashish)
  no persistent state, no alerts
```

**Limitations**: stateless, batch, expensive, no alerting, requires manual trigger.

### Target (Sprint 3+)

```
WhatsApp groups
      │
      ▼ (automated ingestion — see §3)
  message stream
      │
      ▼ (message router)
  task-scoped message queue
      │
      ▼ (stateful update agent)
  task instance store (NoSQL)
      │
      ├──► alerts → Ashish (WhatsApp / dashboard)
      └──► daily digest → Ashish
```

**Properties**: always-on, incremental, cheap per-update, persistent state, proactive alerts.

---

## 1. WhatsApp Access

### The Problem

WhatsApp does not provide an official API for reading group messages as a background agent. The three real options are:

| Option | How | Reliability | Cost | Risk |
|---|---|---|---|---|
| **WhatsApp Business API (Meta Cloud API)** | Official API for businesses; send/receive messages via HTTP | High | $0.005–$0.08/conversation | Requires business phone number; designed for customer service bots, not group monitoring |
| **Unofficial libraries (Baileys, whatsmeow)** | Reverse-engineered WhatsApp Web protocol; runs as a Node.js or Go process | Moderate | Free | ToS violation; account ban risk; protocol changes break it |
| **WhatsApp-to-Telegram bridge (mautrix-whatsapp)** | Mirrors WhatsApp messages to Telegram; agent reads from Telegram API | Moderate | Free | Requires always-on bridge; latency; bridge can disconnect |
| **Periodic manual export** | Ashish or staff export chat txt files and upload | Low automation | Free | Requires human action; not "live" |
| **WhatsApp Web scraping / selenium** | Browser automation to read web.whatsapp.com | Fragile | Free | Very fragile; blocked by WhatsApp anti-bot |

### Recommendation for Sprint 3

**Baileys (unofficial Node.js library)** is the pragmatic choice for a single-business deployment:

- Battle-tested for individual/small business use; widely used by WhatsApp bots in India
- Runs as a Node.js process; scans QR code once (on Ashish's phone), then maintains a persistent session
- Emits message events in real time that can be forwarded to the Python processing pipeline via a local HTTP or Redis queue
- Risk is manageable: the agent is read-only (never sends messages from the monitored account); Ashish's number is not exposed as a business API endpoint

**Longer-term path**: once the system is proven, migrate to WhatsApp Business API for a dedicated business number (Ashish maintains separate personal and business numbers already). This removes ToS risk at the cost of Meta's conversation fees.

**Alternative if Baileys is blocked**: WhatsApp-to-Telegram bridge (mautrix-whatsapp). More infrastructure but fully open-source and actively maintained.

### Architecture for Baileys-Based Ingestion

```
Ashish's phone (WhatsApp)
      │
      │  (WhatsApp Web protocol)
      ▼
Baileys session (Node.js process, always-on server)
      │
      │  POST /ingest  (JSON: {group_id, sender, timestamp, body, media_url})
      ▼
Python ingestion API (FastAPI)
      │
      ▼
message queue (Redis or in-process queue)
      │
      ▼
message router → task update pipeline
```

**Whitelisting**: only groups that Ashish explicitly whitelists are forwarded. The Baileys process filters by group ID. Personal messages and non-whitelisted groups are dropped at the source — never reach the Python layer.

---

## 2. Message Ingestion and Classification

Every incoming message goes through three fast classification steps before any LLM is called:

### Step 1 — Noise filter (no LLM)

Discard messages that cannot contain task-relevant content:
- Reactions, stickers, voice notes (if transcription not enabled), status updates
- Messages from system senders (group join/leave notifications)
- Messages identical to prior message from same sender within 60 seconds (dedup)

### Steps 2–3 — Task relevance classification and routing

> **This section is superseded by `message_router_design.md`**, which contains the full routing design. Summary below for orientation; refer to the router design for the authoritative specification.

The message router is a four-layer cascade:

- **Layer 1 — Noise filter**: drops reactions, stickers, system messages, duplicates. Passes media (with URL), voice notes (after transcription), forwarded messages.
- **Layer 2a — Group → task map + rolling window**: direct group-to-task mapping where possible; rolling window establishes the active task set for groups with concurrent orders. Detects context boundaries (entity switch, transition language, time gap) and resets the active set accordingly.
- **Layer 2b — Reference signal extraction**: composite scoring across 6 signal types (entity name, officer reference, source group, location, date, item). Handles Hinglish honorifics, transliteration variants, and indirect Army client references. No LLM needed.
- **Layer 2c — Embedding similarity**: `paraphrase-multilingual-MiniLM-L12` (384-dim, CPU, ~8ms/message) for messages with no keyword signal.

**Routing cardinality is M:N** — a single message routes to multiple tasks simultaneously; this is the expected case, not an error. "Ambiguous" is reserved for genuinely unclear single-task attribution.

The router produces a `RoutingResult` with a list of `TaskRoute` objects (per-task confidence + method + ambiguous flag), not a single route. Zero LLM cost for routing. See `message_router_design.md` for the full design including passive observer principle, batching policy, voice note transcription, and cross-order contextual references.

---

## 3. Stateful Update Agent

For each message successfully routed to a task instance, the update agent:

1. **Retrieves task context**: current node states, cross-group message history since `task.last_updated` (all groups associated with this task, merged by timestamp — see `message_router_design.md §Cross-Group Context Window`), current divergence delta, and the relevant template
2. **Calls LLM** (sonnet-class): determine which node(s) this message updates and in what direction (in_progress, complete, reopened, blocked)
3. **Validates the update**: checks against hard dependency edges; flags if the update implies a gap (e.g., delivery confirmed but no dispatch logged)
4. **Writes to task store**: updates node status, appends to message history, updates divergence delta if needed
5. **Evaluates alert conditions**: checks if any alert rules fire as a result of the update (see §4)

### Prompt structure (incremental)

```
[STABLE PREFIX — cached]
System prompt: agent role + task graph concepts + alert rules

[SEMI-STABLE — partially cached]
Task template: standard procurement template (or relevant type)
Task type checklist: expected subtasks for this order type

[VARIABLE — not cached]
Current task state:
  - Node statuses (summary table)
  - Recent messages (last 20)
  - Instance delta so far

New message(s) to process:
  [message content]

Instructions:
  1. Which node(s) does this message update?
  2. What is the new status for each node?
  3. Does this message imply any gap (upstream node unlogged)?
  4. Should any alert fire?
  5. Output: structured JSON update
```

**Output format**: structured JSON, not prose. Cheaper to produce, trivial to parse, no downstream LLM needed to interpret.

```json
{
  "task_id": "sata-tandoor-mar26",
  "confirmed_updates": [
    {
      "node_id": "delivery_to_client",
      "new_status": "complete",
      "confidence": 0.92,
      "evidence": "message: 'maal pahunch gaya station pe'"
    }
  ],
  "provisional_updates": [],
  "promote_provisionals": [],
  "discard_provisionals": [],
  "gap_flags": [
    {
      "type": "missing_upstream",
      "description": "Delivery confirmed but no dispatch message found",
      "severity": "medium"
    }
  ],
  "alerts": [],
  "routing_updates": {
    "new_aliases": [],
    "group_aliases": [],
    "delivery_location": null,
    "key_dates": {},
    "item_types": []
  },
  "routing_confidence": 0.88
}
```

---

## 4. Alert Engine

Alerts fire from two sources: **message-triggered** (something just happened) and **time-triggered** (a deadline was missed).

### Message-triggered alerts

| Condition | Alert | Severity |
|---|---|---|
| Hard dependency violated — downstream node updated before upstream | "Delivery confirmed but no PO/dispatch found — verify manually" | High |
| Task routed with `ambiguous: true` and provisional update approaches expiry (8/10 messages without resolution) | "Unresolved ambiguity — please clarify which order: [Task A] or [Task B]?" | Medium — *only if silent expiry is not acceptable; see `message_router_design.md §Ambiguity handling` and open question Q16* |
| New task candidate detected (no template match) | "New order thread detected — assign to task type" | Medium |
| Node reopened (was complete, now active again) | "[Node] reopened — [reason from message]" | High |
| Quality issue node activated (conditional branch) | "Quality issue flagged — rejection/partial acceptance flow" | High |

### Time-triggered alerts (cron-based)

| Condition | Alert | Frequency |
|---|---|---|
| Supplier hasn't responded in 48h after quote request | "Follow up with [supplier] — no response to quote request" | Once, then daily |
| Client hasn't confirmed in 24h after quote sent | "Follow up with [client] — quote sent, no confirmation" | Once, then daily |
| Delivery date passed, no delivery confirmation | "Delivery overdue — [supplier] committed [date]" | Daily |
| Payment not received 30 days after invoice | "Payment follow-up — [client], invoice [date]" | Weekly |
| Task silent for N days (no new messages) | "No update in [N] days — [task name]" | Once per period |

### Alert delivery

**Phase 1 (Sprint 3)**: alerts written to a simple flat file / SQLite log that Ashish reviews on a dashboard.

**Phase 2**: WhatsApp bot delivers alerts directly to Ashish in a dedicated "Mantri Alerts" WhatsApp group (separate from operational groups). Uses Baileys write capability on a dedicated bot number — never on Ashish's personal number.

**Alert format** (WhatsApp):
```
🔔 SATA Tandoor Order
Delivery confirmed — no dispatch logged
→ Verify: was vehicle dispatched without logging?

Reply YES to mark dispatch logged | NO to flag gap
```

Reply handling: Ashish's responses in the Alerts group are processed by the same ingestion pipeline, routing them as manual state updates.

---

## 5. Deployment Architecture

### Sprint 3 target: minimal viable deployment

**Hosting preference: India-based datacenter** — keeps business data (Army supply chain messages, procurement details) within Indian jurisdiction. Reduces latency to Guwahati. Preferred options:

| Provider | Datacenter | VPS cost | Notes |
|---|---|---|---|
| **DigitalOcean** | Bangalore (BLR1) | ~$6–12/month | Simplest ops; good for Sprint 3 |
| **Linode / Akamai** | Mumbai | ~$5–12/month | Comparable to DigitalOcean |
| **AWS EC2** | Mumbai (ap-south-1) | ~$15–20/month (t3.small) | More complex; better long-term scaling |
| **Google Cloud** | Mumbai (asia-south1) | ~$12–18/month | Good if Firebase/Firestore used for task store |

**Recommendation for Sprint 3**: DigitalOcean Bangalore or Linode Mumbai — lowest ops overhead, straightforward SSH + supervisord setup.

```
┌─────────────────────────────────────────────────────┐
│        Single Linux VPS — India datacenter           │
│        (DigitalOcean Bangalore / Linode Mumbai)      │
│        ~$6–12/month                                  │
│                                                      │
│  ┌──────────────┐  ┌─────────────────────────────┐  │
│  │  Baileys     │  │  Python backend              │  │
│  │  (Node.js)   │──│  FastAPI ingestion           │  │
│  │  port 3000   │  │  Message router              │  │
│  └──────────────┘  │  Update agent (LLM calls)   │  │
│                    │  Alert engine                │  │
│                    │  SQLite task store           │  │
│                    └──────────────┬──────────────┘  │
│                                   │                  │
│                    ┌──────────────▼──────────────┐  │
│                    │  Simple dashboard (Streamlit  │  │
│                    │  or static HTML + JSON API)  │  │
│                    └─────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Process management**: `supervisord` or `pm2` keeps both Baileys and FastAPI running, auto-restarts on crash.

**Task store**: SQLite for Sprint 3 (zero ops overhead). Migrate to Firestore (Mumbai region) or DynamoDB (ap-south-1) when multi-device or multi-user access is needed.

**Scheduler**: Python `APScheduler` for time-triggered alerts (cron jobs within the process). Avoids external cron/Lambda complexity in Sprint 3.

### Why not serverless (Lambda/Cloud Functions)?

Baileys requires a persistent process to maintain the WhatsApp Web session (WebSocket connection). A serverless function that cold-starts per message would lose the session. An always-on VPS is the right deployment model for the ingestion layer, at least until the WhatsApp Business API replaces Baileys.

### Longer-term production architecture

```
Baileys session (persistent VPS — India)
        │
        ▼
Message queue (Redis Streams or SQS ap-south-1)
        │
        ▼
Worker pool (Docker containers, auto-scaled)
  - Router workers (embedding, cheap)
  - Update agent workers (LLM calls)
        │
        ▼
Task store (Firestore Mumbai / DynamoDB ap-south-1)
        │
        ▼
Alert delivery (WhatsApp Business API)
Dashboard (React + REST API)
```

---

## 6. Sprint 3 MVP Scope

The full design above is the target. Sprint 3 should build the smallest slice that proves the live monitoring loop end-to-end. Recommended scope:

| Component | Sprint 3 | Post-Sprint 3 |
|---|---|---|
| WhatsApp ingestion | Baileys on VPS, whitelisted groups | WhatsApp Business API migration |
| **Whitelist UX/UI** | **Staff-facing UI to whitelist/delist conversations (groups + 1:1s)** | Refined permissions model |
| **Model evaluation** | ~~Run 16-case eval against Gemini 2.0 Flash~~ **Done — see findings below** | Revisit after incremental architecture |
| **Message router** | **Embedding similarity (paraphrase-multilingual-MiniLM-L12), no API cost** | Fine-tuned classifier on own data |
| Noise filter | Keyword-based, no embedding | Embedding similarity fallback |
| Task routing | Entity keyword match | Embedding + LLM cascade |
| Update agent | LLM call per routed message | Batching + rate limits |
| Task store | SQLite, single task type (standard procurement) | Multi-type, full schema |
| Templates | 1–2 task types (standard procurement + GeM) | All 8 task types |
| Alert engine | Time-triggered alerts only (cron) | Message-triggered alerts |
| Alert delivery | Dashboard (read-only log) | WhatsApp bot reply loop |
| Scheduler | APScheduler in-process | Decoupled scheduler service |

**Whitelist UX/UI — Sprint 3 requirement**: Staff must be able to view which conversations the agent is currently reading and add/remove them without developer intervention. This covers both **group chats** and **1:1 business chats** (e.g., staff ↔ transporter, staff ↔ supplier). This is a trust and adoption prerequisite — staff were explicitly told during onboarding that only whitelisted conversations are read. The UI must make that promise visible and enforceable by them. Minimum viable: a simple list view with toggle per conversation (groups and 1:1s listed separately), accessible via the dashboard.

**The one thing Sprint 3 must prove**: a new WhatsApp message is automatically ingested, routed to the correct task instance, and updates a node — without any manual intervention. Everything else is incremental on top of that.

---

## 7. Model Evaluation: Gemini 2.5 Flash vs Claude Sonnet 4.6

**Date**: 2026-03-26 | **Eval set**: 17 cases with threads.txt | **Evaluator**: Claude Sonnet 4.6 (fixed, for consistency)

### Results

| Case type | Sonnet PASS | Gemini PASS | Sonnet avg | Gemini avg |
|---|---|---|---|---|
| R3-C (order separation, 7 cases) | 7/7 | 6/7 | 90.0 | 86.9 |
| R4-A/B (entity resolution, 8 cases) | 8/8 | 6/8 | 89.1 | 82.5 |
| R1-D (large multi-item SATA, 1 case) | 1/1 | 0/1 | 88 | 52 |
| R6 (implicit task detection, 1 case) | 1/1 | 1/1 | 88 | 82 |
| **Total** | **17/17** | **13/17** | **88.9** | **81.4** |

### Failure analysis

| Case | Gemini score | Root cause |
|---|---|---|
| R1-D (SATA, large context) | PARTIAL 52 | Output truncated mid-sentence on a ~20K-token input. Not a capability gap — a token budget issue. |
| R3-C-L3-02 (multiclient flags) | PARTIAL 62 | Reverted to 1 parent task instead of 3. "Client delivery unit" structural rule less sticky in Gemini than Sonnet. |
| R4-A-L2-02 (honorific vs product ref) | PARTIAL 62 | Hedged instead of tentative merge — entity resolution calibration rule didn't hold. |
| R4-A-L3-01 (3–4 name variants) | PARTIAL 72 | Only resolved 2 of 3–4 variants. Harder multi-variant entity resolution. |

### Conclusion

Gemini 2.5 Flash is **not a drop-in replacement** at the current prompt. On simple cases (order separation, basic entity resolution) it is nearly equivalent. The failures are on complex entity resolution and long-context cases — exactly where the highest operational risk lies.

The truncation failure on R1-D disappears once the incremental architecture is in place (per-message inputs of ~3.5K tokens vs 20K). The entity resolution failures would require Gemini-specific prompt tuning.

**Decision**: keep Claude Sonnet 4.6 for the update agent. Revisit Gemini after the incremental architecture is in place — at ~3.5K token inputs the cost argument (~40x cheaper) strengthens significantly and the truncation risk disappears.

**Message router**: use `paraphrase-multilingual-MiniLM-L12` embedding similarity from day one regardless of update agent model choice. Zero API cost, no quality dependency on frontier models for routing.

---

## 8. Open Questions

1. **Baileys session management**: the QR code scan ties the session to Ashish's phone. If his phone is offline or the session expires, ingestion stops. What is the recovery UX — does Ashish re-scan, or is there a secondary session mechanism?

2. **Media handling**: WhatsApp groups frequently contain images (delivery photos, PO PDFs, GST invoices). The update agent needs to handle these. Baileys can forward media URLs; the agent needs a vision step before the update call. What is the right handling for messages where the media is the primary signal (e.g., photo of delivered goods)?

3. ~~**Hinglish in the router**~~: *Resolved — see `message_router_design.md §Layer 2b`.* The router uses a live entity alias dictionary (Hinglish-aware: honorific stripping, transliteration normalisation, provisional aliases for single-occurrence references) + composite signal scoring across 6 reference types. No LLM needed for routing.

4. **Task store schema for out-of-order updates**: the task graph allows nodes to be updated in any order. The store needs to record the actual sequence of updates (with timestamps) separately from the template's expected ordering. What is the right schema for this — event log per node, or a single event stream per instance?

5. **Ashish's phone as the session anchor**: Baileys works by mirroring Ashish's WhatsApp on a server. Is Ashish comfortable with this? Does he understand what it means for his messages to pass through a VPS? This needs to be an explicit, informed consent conversation — not an assumption.

6. **Graceful degradation**: if the LLM API is unavailable, messages should be queued and processed when the API recovers — not dropped. What is the acceptable queue depth and maximum lag before an alert is considered stale?

---

## Relationship to Other Design Docs

| Topic | Where documented |
|---|---|
| Task graph data model (templates, instances, node types, edge types) | `task_lifecycle_state_machine_design.md` |
| Cost architecture and incremental vs batch comparison | `task_lifecycle_state_machine_design.md` §Operational Cost |
| Design tensions (T1–T17) including missing entries, staff quality, inventory | `task_lifecycle_state_machine_design.md` |
| User research plan and interview guides | `user_research_plan.md` |
| Evaluation methodology and quality risks | `evaluation_design_report.md` |
| **Live monitoring pipeline (this document)** | `live_monitoring_design.md` |

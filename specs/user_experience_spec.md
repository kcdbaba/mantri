# User Experience Specification

## User Context Schema

The UserContext captures what Ashish (primary user) provides, tracks, validates, and controls when interacting with the Mantri agent system.

### What Ashish Provides

| Field | Type | Description | Ownership |
|-------|------|-------------|-----------|
| `monitored_chats` | `list[str]` | WhatsApp groups AND 1:1 chats opted in for monitoring. Ongoing — new contacts created daily. | User-owned |
| `entity_corrections` | `list[dict]` | "This message belongs to entity X, not Y." Corrects agent routing/assignment. | User-owned |
| `entity_metadata` | `dict[str, dict]` | Entity name corrections, aliases, classifications (supplier/client/transporter/internal). | User-owned |
| `entity_crm` | `dict[str, dict]` | Per-entity relationship data. Co-authored: agent learns from messages, Ashish corrects/enriches. | Co-authored |
| `difficulty_overrides` | `dict[str, str]` | task_id or node_id to "low"/"medium"/"high". Determines escalation behavior. | User-owned |
| `staff_assignments` | `dict[str, dict]` | Loose, dynamic staff responsibilities for tasks and nodes. Ashish assigns by capability/availability, can reassign mid-task. | User-owned |

**Entity CRM detail:**
- **Suppliers**: reliability score, typical delay days, payment terms, product specialties, price competitiveness, quality track record
- **Clients**: order patterns, payment behavior, priority level, key contacts, special requirements (packaging, delivery windows)
- **Transporters**: routes covered, reliability, rates, capacity

### What Ashish Tracks

| Field | Type | Description |
|-------|------|-------------|
| `task_overview` | `dict` | All active tasks by priority, importance, difficulty. The operational snapshot. |
| `blockers` | `list[dict]` | Tasks blocked by: external party, internal issue, or awaiting Ashish's decision. |
| `delays` | `list[dict]` | Predicted or confirmed delays with expected date, predicted slip, and reason. |
| `task_graph` | `dict[str, dict]` | Per-task node graph: node statuses, message history, items, fulfillment links. |

### What Ashish Validates

| Field | Type | Description |
|-------|------|-------------|
| `pending_ambiguities` | `list[dict]` | Agent uncertain about entity/task assignment. Shows message snippet + candidate tasks + confidence. Ashish confirms or corrects. Primary human-in-the-loop touchpoint. |
| `pending_corrections` | `list[dict]` | Agent outputs awaiting review — node status changes, item extractions, entity classifications. |
| `alert_history` | `list[dict]` | Alerts fired, whether acted on, and outcome. Feeds alert quality tuning. |

### What Ashish Controls

| Field | Type | Description |
|-------|------|-------------|
| `escalation_profile` | `dict` | Severity thresholds, rate limits per task per hour, category overrides. |
| `availability_windows` | `list[dict]` | Known unreachable periods (army visits 9AM-1PM, travel). Agent pre-surfaces high-difficulty items before these windows. |
| `alert_preferences` | `dict` | Morning digest time, intraday alert frequency, quiet hours. |

### Known Issues and Mental Model

| Field | Type | Description |
|-------|------|-------------|
| `known_gaps` | `list[str]` | Acknowledged system limitations. E.g., "call summaries miss 20-30% of details", "cadence tasks still need work". |
| `trust_calibration` | `dict` | Per-capability trust level. Entity extraction: high (89/100 tested). Cadence/procedural tasks: low (confirmed gap). Helps agent signal confidence appropriately. |

---

## Context Mapping

### User-Facing Component

The system has two background agents (`update_agent`, `linkage_agent`) plus conversation routing. Neither agent interacts with the user directly. The user-facing layer is a **dashboard + WhatsApp alerts channel** that reads from the task store and ambiguity queue.

### Field Mapping: UserContext to System Context

| UserContext Field | System Field(s) | Relationship | Data Flow | Notes |
|---|---|---|---|---|
| `monitored_chats` | `MONITORED_GROUPS` config, Baileys chat list | 1:1 config | user → system | Drives what the ingestion layer watches |
| `entity_corrections` | `entity_aliases` table, router alias_dict | transformed | user → agents | Correction invalidates alias cache, affects all future routing |
| `entity_metadata` | `entity_aliases`, `discovered_entities` tables | merged | user → agents | User corrections override agent-discovered aliases |
| `entity_crm` | `entity_crm` table (new) | co-authored | bidirectional | Agent learns from messages, user enriches/corrects. Table does not exist yet. |
| `difficulty_overrides` | `task_instances.difficulty`, escalation profile | 1:1 | user → system | Drives alert severity thresholds |
| `staff_assignments` | `node_owner_registry` table | 1:1 | user → system | Table already exists in DB schema |
| `task_overview` | `task_instances` + `task_nodes` + items tables | aggregated | agents → user | Dashboard view built from multiple DB tables |
| `blockers` | `ambiguity_queue` (blocking=true) | derived | agents → user | Created by update_agent and linkage_agent |
| `delays` | `task_nodes` (deadline vs status) | derived | agents → user | Agent detects from timeline events; not yet implemented |
| `task_graph` | `task_nodes` + `task_messages` + items + `fulfillment_links` | aggregated | agents → user | Full task state graph per order |
| `pending_ambiguities` | `ambiguity_queue` (status=pending) | direct | agents → user | Created by agents, resolved by Ashish |
| `pending_corrections` | `ambiguity_queue` + provisional nodes | filtered | agents → user | Provisional status nodes = low-confidence updates awaiting review |
| `alert_history` | `task_alerts_fired` table | direct | system → user | Already exists in DB |
| `escalation_profile` | `ESCALATION_PROFILES` config | 1:1 config | user → system | Currently hardcoded; needs dashboard editing |
| `availability_windows` | Not yet stored | new field | user → system | Agent buffers escalations around these windows |
| `alert_preferences` | Not yet stored | new field | user → system | Morning digest time, frequency caps |
| `known_gaps` | Implicit in eval baselines | informational | — | Not stored; shapes trust_calibration |
| `trust_calibration` | `routing_confidence`, `PROVISIONAL_THRESHOLD` | indirect | user → agents | Affects how agent signals confidence to user |

### Gaps Identified

| Gap | Type | Impact |
|-----|------|--------|
| `entity_crm` table | Missing DB table | No structured place to store supplier reliability, client preferences, transporter data |
| `availability_windows` | Missing storage | Agent cannot pre-surface escalations before known unavailability |
| `alert_preferences` | Missing storage | No user control over digest timing or alert frequency |
| `delays` detection | Missing logic | No comparison of timeline events vs current status to predict delays |

---

## UX Gaps and Risks

### LLM Risk Mitigation

| Risk | Where It Occurs | User Oversight Mechanism |
|------|-----------------|--------------------------|
| Hallucination | update_agent node updates, item extractions; linkage_agent item matching | Provisional nodes (low confidence) await review. `pending_ambiguities` surfaces uncertain decisions. Item extractions visible in task_graph. |
| Stochastic behavior | Same messages could produce different outputs | Low risk — stateful system, outputs committed to DB. Agent cache makes test runs deterministic. |
| Context loss | update_agent sees only last 20 messages per task | `trust_calibration` acknowledges limitation. No user visibility into what context agent had. |
| Instruction following | Entity misclassification, wrong task assignment, wrong node status | `entity_corrections`, `pending_ambiguities`, provisional nodes. Well covered by ambiguity escalation system. |
| Prompt injection | WhatsApp message bodies injected into prompts verbatim | Low practical risk — messages from known business contacts, not adversarial users. |
| Overconfidence | Agent sets node status with high confidence on ambiguous evidence; linkage creates questionable matches | `routing_confidence`, `PROVISIONAL_THRESHOLD`, `ambiguity_queue` with severity. Linkage confidence exists in DB but not surfaced. |

### Risks Needing Additional Oversight

| Gap | Risk | Recommendation |
|-----|------|----------------|
| No source citation for extractions | User sees "10x LG Refrigerator extracted" but not which message it came from | Add `source_message_id` to item extractions so user can verify against original message |
| Linkage confidence not surfaced | `fulfillment_links.match_confidence` exists in DB but no dashboard view | Surface questionable links (confidence < threshold) in dashboard for user review |
| No agent reasoning trail | Confident node status changes have no explanation visible to user | Add `evidence` field display — update_agent already produces evidence text per node update, but it's not surfaced in any user-facing view |

---

## Context Ownership Rules

### Ownership Types

- **USER-OWNED**: User writes, agent reads only
- **AGENT-OWNED**: Agent writes, user views only
- **CO-AUTHORED**: Both write with explicit rules

**Conflict resolution**: When user and agent disagree, user always wins. Agent records the override and adjusts future behavior. No silent re-overriding.

### Per-Field Ownership

| Field | Ownership | Co-authoring Pattern | Rules |
|-------|-----------|---------------------|-------|
| `monitored_chats` | USER-OWNED | — | User opts in chats. Agent never adds/removes. |
| `entity_corrections` | CO-AUTHORED | User corrects, agent learns | User correction is authoritative and immediate. Agent incorporates into future routing. Agent never overrides a user correction. |
| `entity_metadata` | CO-AUTHORED | User controls, agent suggests | Agent discovers new entities from messages. User confirms/rejects/edits. Once user edits an alias, that version is locked — agent cannot override. |
| `entity_crm` | CO-AUTHORED | Agent tracks, user corrects | Agent builds profiles from message evidence (payment patterns, delay history). User can override any field. User overrides persist until user changes them again. |
| `difficulty_overrides` | USER-OWNED | — | Ashish sets, agent reads. Drives escalation thresholds. |
| `staff_assignments` | CO-AUTHORED | Agent tracks, user corrects | Most tasks auto-assigned to all regular staff. Agent detects role-specific assignments from chat ("Raju will handle collection"). Specialized roles (finance, inspection) get targeted assignments. Ashish can override any assignment. User overrides persist — agent doesn't reassign unless Ashish does. |
| `task_overview` | AGENT-OWNED | — | Agent maintains from DB state. User views only. |
| `blockers` | CO-AUTHORED | Agent tracks, user resolves | Agent creates blockers from ambiguity queue. User resolves by providing the answer. Resolved blockers feed back into agent learning. |
| `delays` | AGENT-OWNED | — | Agent detects from timeline vs status comparison. User views only. |
| `task_graph` | AGENT-OWNED | — | Agent maintains full node/item/link state. User views only. |
| `pending_ambiguities` | CO-AUTHORED | Agent proposes, user decides | Agent writes candidates + confidence. User's decision is final and permanent for that message. Agent uses decision to improve future routing. |
| `pending_corrections` | CO-AUTHORED | Agent proposes, user approves | Provisional node updates shown for review. User can accept (confirmed) or reject (reverted). Unreviewed provisionals auto-expire based on escalation profile. |
| `alert_history` | AGENT-OWNED | — | Agent records alerts fired. User views only. |
| `escalation_profile` | USER-OWNED | — | Ashish configures thresholds and rate limits. Agent reads only. |
| `availability_windows` | USER-OWNED | — | Ashish sets known unavailability. Agent reads to pre-surface escalations. |
| `alert_preferences` | USER-OWNED | — | Ashish sets digest time, frequency, quiet hours. Agent reads only. |
| `known_gaps` | USER-OWNED | — | Informational. User acknowledges system limitations. |
| `trust_calibration` | USER-OWNED | — | User sets per-capability trust. Shapes how agent signals confidence. |

### Staff Assignment Rules

Default assignments are rule-based, not manual:
- **All regular staff** → auto-assigned to orders (all task types)
- **Delivery staff** → auto-assigned to collection, delivery, QC nodes
- **Finance staff** → auto-assigned to billing, payments, bookkeeping tasks

Multiple staff per task/node (not 1:1). Ashish reassigns by adding/removing staff. Agent also detects assignments from chat ("Raju will handle this collection").

---

## Interaction Flow

### 1. Background Processing (continuous, no user action)

Agent monitors all `monitored_chats` continuously:
- Ingests new WhatsApp messages → routes to entities → update_agent processes → node states updated
- Linkage agent matches supplier↔client items
- Conversation router groups shared-group messages into conversations
- Entity learner discovers new entities from contacts/patterns
- All outputs written to DB — `task_overview`, `task_graph`, `delays` update automatically

User sees nothing during this phase. System works silently.

### 2. Proactive Alerts (agent → user, WhatsApp Alerts channel)

**Morning digest** (configurable via `alert_preferences`):
- Prioritized task list: what needs attention today
- Blockers requiring Ashish's input
- Predicted delays
- Staff assignment gaps

**Intraday push alerts** (when conditions fire):
- Milestone approaching with no progress evidence
- Supplier silent past expected response window (informed by `entity_crm` reliability data)
- High-difficulty task needs Ashish's decision
- New ambiguity requiring resolution

**Pre-unavailability sweep** (before `availability_windows`):
- Agent surfaces all pending high-difficulty items before Ashish goes unreachable

### 3. Validation Checkpoints (agent proposes, user decides)

**Ambiguity resolution** (primary checkpoint):
- Agent shows: message snippet + candidate entities/tasks + confidence score
- Ashish responds: confirms one candidate, or corrects ("this is actually entity X")
- Agent learns: correction feeds into `entity_corrections`, improves future routing

**Provisional node review:**
- Dashboard shows provisional status changes (low-confidence updates) with evidence text
- Ashish can: accept (confirms as-is) or reject (reverts change)
- If Ashish wants to edit details: accept first, then make a separate manual correction
- Simple atomic actions — no combined accept-with-edit form
- Unreviewed provisionals auto-expire based on escalation profile

**Entity discovery confirmation:**
- Agent discovers new entity from contacts/messages
- Dashboard shows: proposed name, aliases, classification
- Ashish confirms, edits metadata, or rejects

### 4. User-Initiated Actions (dashboard)

**On-demand review:**
- Open task_graph for any order — see all nodes, statuses, message trail, items, fulfillment links
- Filter by: blocker type, delay status, entity, staff assignment

**Corrections:**
- Entity corrections: "this message belongs to entity X, not Y"
- Entity metadata: add/edit aliases, reclassify (supplier↔client↔transporter)
- Entity CRM: override agent-learned reliability, add payment terms, special requirements
- Staff assignment: add/remove staff from task/node (multiple staff per task/node)
- Difficulty override: change task difficulty level
- Node status: manual status correction with mandatory reasoning

**Configuration:**
- Escalation profile: adjust severity thresholds, rate limits
- Availability windows: set upcoming unreachable periods
- Alert preferences: change digest time, frequency, quiet hours

### 5. Error and Uncertainty Handling

| Situation | What user sees | User action |
|-----------|---------------|-------------|
| Agent can't determine entity | Ambiguity alert on WhatsApp + dashboard | Confirm/correct entity |
| Wrong node status applied | Provisional flag in dashboard with evidence | Accept or revert |
| Missed message (not routed) | Dead letter count in dashboard | Review dead letters, correct routing |
| Agent hallucinated item | Item list in task_graph doesn't match reality | Delete/edit item |
| Linkage mismatch | Low-confidence fulfillment link flagged | Confirm or remove link |
| Staff overloaded | Alert: staff X has N pending tasks | Reassign tasks across staff |

# Historical Extraction Pass — Bootstrapping Design

**Status**: Pre-implementation design
**Target**: Pre-Sprint 3 (must complete before live monitoring goes live)
**Depends on**: `message_router_design.md` (router tables), `task_lifecycle_state_graph_design.md` (task instance schema)
**Author**: Kunal Chowdhury
**Date**: 2026-03-26

---

## Purpose

The message router starts cold. Its three data stores — `entity_aliases`, `group_alias_dict`, and `task_routing_context` — are empty at deployment. Without pre-seeding:

- Layer 2b entity matching produces zero hits on day 1
- Group alias references ("Eastern Command group ka") are unresolvable
- Layer 2c embedding similarity has no task context embeddings to compare against
- Only Layer 2a (direct group → task map) works — and only after the first live task is created

The result: the first few weeks of live operation have degraded routing quality at exactly the moment Ashish is forming his trust judgement of the system.

The historical extraction pass fixes this by running the extraction pipeline over Ashish's existing chat history **before** going live, pre-populating all router tables and creating a set of historical task instances. The router starts warm, not cold.

**Secondary output**: historical completed task instances are the primary input for template bootstrapping (see `task_lifecycle_state_graph_design.md §T1`) — the bootstrapping pass serves both purposes.

---

## What Gets Bootstrapped

| Table | What is populated | Cold start quality without it |
|---|---|---|
| `entity_aliases` | All known entity name variants for suppliers, clients, items, officers | Layer 2b returns 0 matches for any Hinglish alias or informal reference |
| `group_alias_dict` | Informal group name references seen in All-Staff / supplier groups | Cross-group order references unresolvable |
| `task_routing_context` | Per-task: source_groups, entity_ids, location, key_dates, item_types, officer_refs, context_text, context_embedding | Layer 2b composite scoring has no fields to score against; Layer 2c has no embeddings |
| Task instances (historical) | Completed orders as task instance records | No template validation data; no additional test cases |

---

## Input: Data Required from Ashish

### Chat exports

WhatsApp allows chat export as a `.txt` file: *Settings → Chats → Chat Backup / Export Chat → Without Media*.

**Groups to export** (all that will be whitelisted for live monitoring):

| Group type | Examples | Priority |
|---|---|---|
| Client groups | "SATA group", "Eastern Command group", each Army unit group | High — order anchors |
| All-Staff Group | The main coordination group | High — cross-group references |
| Supplier groups | "Kapoor Steel group", each major supplier's group | High — delivery/dispatch context |
| 1:1 with key staff | Samita, other active staff | Medium — informal task assignments |
| 1:1 with key suppliers | Regular transporter, Malerkotla contact | Medium — supplementary |

**Time window**: **3 months minimum, 6 months preferred.** Three months captures the current active roster of entities and typical seasonal patterns. Six months adds more completed orders (better template coverage) and surfaces entities that appear infrequently.

**Export format**: "Without Media" is acceptable. Media files (delivery photos, PO PDFs) are not needed for bootstrapping — their operational content is typically confirmed in adjacent text messages anyway.

**Ashish's action**: export each group separately, collect all `.txt` files in a shared folder. Estimated effort: 15–30 minutes depending on number of groups.

### Metadata Ashish provides alongside exports

Before the extraction pass, ask Ashish to confirm:

1. The canonical name for each group (what he calls the client/supplier in normal speech — this becomes the primary alias key)
2. Which groups are client-facing, which are supplier-facing, which are internal
3. Any groups that should be excluded even if they appear in the whitelist (e.g., personal contacts that are also business contacts)
4. Rough count of orders he expects to find in the time window (sanity check for extraction output)

---

## Pipeline

```
Ashish's chat exports (.txt files, one per group)
        │
        ▼
Phase 1: Parse + Merge
  Parse WhatsApp txt format → structured messages
  Tag each message with: timestamp, sender, body, group_id, group_type
  Merge all groups into a single chronological stream
        │
        ▼
Phase 2: Order Segmentation
  For each group: segment chat history into per-order threads
  Using: temporal gap detection + entity/topic shift signals
  Output: list of (group_id, start_timestamp, end_timestamp, anchor_entities[])
        │
        ▼
Phase 3: Cross-Group Thread Assembly
  For each identified order thread: pull in messages from other groups
  covering the same timeframe and entity set
  Output: multi-group, timestamp-sorted thread per order
  (same format as test cases and live update agent calls)
        │
        ▼
Phase 4: Extraction Agent Pass
  For each assembled order thread: run extraction agent
  (reuses testing_prompt.txt + task type checklists)
  Output per order:
    - Structured task (type, entities, stage, key dates, items, location)
    - Entity alias list (all name variants seen in this thread)
    - Officer references
    - Group aliases discovered
        │
        ▼
Phase 5: Table Population
  Write entity aliases → entity_aliases table
  Write group aliases → group_alias_dict
  Write task routing context → task_routing_context (+ compute embeddings)
  Write task instance records → task instance store
        │
        ▼
Phase 6: Validation
  Sample check: spot-review 5–10 extracted orders with Ashish
  Flag low-confidence extractions for manual review
  Resolve conflicts (entity alias collisions, duplicate task instances)
```

---

## Phase 2: Order Segmentation

Segmentation is the hardest step. The goal is to identify where one order ends and another begins within each group's chat history.

### Client groups (primary anchors)

Client group conversations naturally segment by order — each order has a clear opening enquiry and, eventually, a closing payment. Segmentation signals:

| Signal | Description |
|---|---|
| **Item keyword after silence** | A new item type appears after a gap > 4 hours (same logic as context boundary detection in the router) |
| **Explicit new enquiry language** | "sir, ek aur order chahiye", "new requirement", "phir se chahiye" |
| **Temporal gap** | > 4 hours between messages, especially overnight or weekend gaps |
| **Stage closure** | Payment received / "shukriya" / "done" followed by silence |

Each segment becomes an order-thread anchor. Its `entity` set (item, client) is used in Phase 3 to pull in messages from other groups.

### All-Staff Group and supplier groups

These groups interleave messages from multiple concurrent orders. Segment by entity/topic, not by time:

- Group messages by the entity names they contain (using the entity alias dict being built in parallel)
- Where a message contains no clear entity reference, assign to the most recent entity context (confirmation carry-forward, same as live router logic)
- Messages that span multiple entities → include in all relevant threads

### In-progress orders at the window boundary

Some orders will have started before the export window begins (no opening enquiry in the export). These produce incomplete threads — partial order history, no initial context. Handle as:

- Extract what's available; mark the task instance as `history_partial: true`
- Include in router tables (the entity aliases and routing context are still valid)
- Do not use as template training data (incomplete lifecycle)

---

## Phase 4: Extraction Agent — Reuse Strategy

The extraction agent for bootstrapping **reuses `testing_prompt.txt` without modification**. The input format (multi-group, timestamp-sorted thread) is identical to the live update agent call format.

**What this gives us for free:**
- No new prompt engineering needed
- The bootstrapped outputs are directly comparable to Sprint 1/2 test case outputs
- Quality is already validated against the Sprint 1/2 eval set

**What it does not give us:**
- Stage-by-stage incremental updates (the bootstrap runs full-thread extraction, not incremental)
- Provisional update tracking (all bootstrap outputs are treated as confirmed)

These limitations are acceptable — this is a one-time seeding pass, not ongoing processing.

### Batch execution

Process orders in batches of 10–20 (to manage API rate limits). Each order is one LLM call. Parallelise across orders; no dependency between them.

**Estimated cost:**

| Order count | Avg input tokens | Avg output tokens | Cost/order | Total |
|---|---|---|---|---|
| 50 orders (3 months) | ~4,000 | ~800 | ~$0.024 | ~$1.20 |
| 100 orders (6 months) | ~4,000 | ~800 | ~$0.024 | ~$2.40 |
| 50 complex orders (SATA-scale) | ~20,000 | ~2,000 | ~$0.090 | ~$4.50 |

Realistic total: **$3–8** for the full bootstrapping pass, depending on order volume and complexity. Well within budget.

---

## Phase 5: Table Population

### Entity aliases

For each entity name variant seen in an extracted order thread:

```python
# confidence: 1.0 for variants seen in multiple threads (confirmed)
# confidence: 0.5 for variants seen in only one thread (provisional)
INSERT INTO entity_aliases (alias, entity_id, entity_type, confidence, source)
VALUES (alias, canonical_id, type, confidence, 'bootstrap')
```

**Collision handling**: if two orders use the same alias for different entities (e.g., "Kapoor sir" appears in one thread as a supplier and in another as an officer), flag both and defer to manual resolution. Add to a `needs_review` table.

### Task routing context

For each extracted order, populate `task_routing_context`:
- `source_groups`: all group IDs where this order's messages were found
- `entity_ids`: canonical entity IDs from the extraction
- `delivery_location`, `key_dates`, `item_types`, `officer_refs`: extracted fields
- `context_text`: the extraction agent's task summary (already produced by the extraction)
- `context_embedding`: compute once after population using the MiniLM model
- `stage`: final stage as of the export date (`completed` for closed orders, last known stage for partials)

### Task instance records

Historical completed orders are written as task instance records in the task store, with:
- All nodes marked at their final stage
- `history_partial: true` if the thread was incomplete
- `template_version: null` (template assignment happens separately, during template bootstrapping)

---

## Phase 6: Validation

### Automated checks

Before the Ashish review session:
- **Alias collision count**: how many aliases resolved to multiple canonical entities?
- **Low-confidence alias count**: how many provisional aliases (single occurrence)?
- **Unresolved entity count**: how many entities appeared in chats but couldn't be canonicalised?
- **Extraction failure count**: orders where the agent output was malformed or incomplete

### Ashish spot-check (30 min session)

Present 5–10 extracted orders to Ashish:
- Is the entity resolution correct? (Are "Kapoor ji" and "Kapoor Steel" both pointing to the right entity?)
- Is the task type classification right?
- Are the key dates and locations correct?
- Is the final stage accurately assessed?

Use this session to also confirm:
- Which entity aliases are correct vs should be flagged as collisions
- Any entities that appear frequently but were missed (not in the alias dict)

---

## Outputs and Their Role in Sprint 3

| Output | Used by | Role |
|---|---|---|
| `entity_aliases` table | Message router Layer 2b | Day-1 entity matching; eliminates cold-start period |
| `group_alias_dict` | Message router Layer 2b | Cross-group order references resolvable from day 1 |
| `task_routing_context` (completed tasks) | Message router Layer 2c | Embedding similarity has reference set; improves routing for similar future orders |
| Historical task instances | Template bootstrapping (T1) | Empirical basis for template design; ground truth for node sequences and timeline patterns |
| Historical task instances | Additional test cases | Expand eval set beyond current synthetic cases; real-world coverage |
| Entity alias quality report | Developer + Ashish | Flags entities that need manual seeding or disambiguation before go-live |

---

## Active Orders at Go-Live

Orders that are still in-progress at the time of go-live require special handling:

1. **Identify**: after bootstrapping, flag any task instances with `stage != completed`
2. **Review with Ashish**: confirm which are genuinely active vs completed without WhatsApp signal
3. **Transition to live**: active orders become the first set of live-monitored tasks; their routing contexts are already populated from bootstrapping
4. **Routing context freshness**: compute context embeddings and run a final extraction pass on the most recent messages for each active order before live monitoring begins — the bootstrap extraction used the full thread, but the live update agent will start from `last_updated`, so this needs to be current

---

## Open Questions

1. **Number of active groups**: how many groups does Ashish have that would be whitelisted? This determines export effort and processing volume. A rough estimate before scheduling the data collection session would help size the work.

2. **3 months vs 6 months**: 3 months is likely sufficient to seed all active entity aliases (most suppliers and clients appear monthly). 6 months adds seasonal coverage and more completed orders for template training. Recommend 6 months if Ashish's exports are available that far back — the marginal processing cost ($1–3 extra) is trivial.

3. **Media files**: the "without media" export loses delivery photos and invoice PDFs. For bootstrapping purposes this is acceptable. But for the live monitoring pipeline, we will want media handling. Should we request one group's "with media" export as a test of the media handling pipeline before Sprint 3 build begins?

4. **Orders spanning the window boundary**: orders that started before the export window will have incomplete history. Can Ashish identify 2–3 of these upfront so we can design the partial-history handling correctly before running the full pass?

5. **Template bootstrapping timing**: the historical task instances produced here are the primary input for template design (T1 in lifecycle design). Should the bootstrapping pass run before or in parallel with template design discussions with Ashish? Running first is cleaner — it gives Ashish concrete examples to react to rather than abstract templates.

---

## Relationship to Other Design Docs

| Topic | Where documented |
|---|---|
| Router tables being populated | `message_router_design.md §Data Structures` |
| Cold start routing quality | `message_router_design.md §Feedback Loop: Cold start quality` |
| Template bootstrapping (T1) | `task_lifecycle_state_graph_design.md §T1` |
| Task instance schema | `task_lifecycle_state_graph_design.md §Core Concepts` |
| Extraction agent prompt | `prompts/testing_prompt.txt` |
| Task type checklists | `prompts/task_type_checklists.txt` |
| **Historical bootstrapping (this document)** | `bootstrapping_design.md` |

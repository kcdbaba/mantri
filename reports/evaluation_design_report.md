# Evaluation Design Report

## Prompt Testing Experience

### Testing Scope
No live testing completed yet. Evaluation dataset is being designed proactively using synthetic examples built from deep domain knowledge of Ashish's business, while awaiting real WhatsApp samples from Ashish. Synthetic examples will be replaced/augmented with real data when available.

### Quality Observations
No empirical observations yet. Quality dimensions derived from domain analysis and solution design.

### Edge Cases Anticipated (Pre-Testing)
- Multi-order threads with interleaved items from different clients/suppliers
- Implicit tasks (situation described but action never explicitly stated)
- Cross-thread correlation failures (same order across multiple groups)
- Hinglish spelling variation causing entity duplication or miss
- Short/ambiguous messages with pronouns and no explicit entity names

---

## Quality Dimensions

Based on solution design and domain knowledge, the following quality dimensions matter most for this workflow:

1. **Recall (task completeness)**: Are all tasks — including implicit ones — identified? Missing a task entirely is the most costly failure mode. A delivery can slip with no alert.

2. **Entity accuracy**: Are the right customers, suppliers, items, and orders linked to each task? Misattribution is costly but detectable; users can catch and correct it.

3. **Cross-thread correlation accuracy**: Are messages about the same order correctly unified across multiple WhatsApp threads? Fragmentation creates duplicate tasks; conflation merges separate orders.

4. **Next step quality**: Are suggested next steps correct and actionable? Under staff stress or high workload, wrong suggestions will be executed without scrutiny — making this a high-stakes quality dimension.

5. **Priority correctness**: Is urgency correctly assessed from context? A high-priority task marked as low will not get timely attention.

6. **Implicit task detection**: Does the agent recognise situations that imply a required action even when no action is explicitly stated? Two categories:
   - **Reactive implicit tasks**: Implied by a situation in communications (e.g., supplier silence → follow-up task)
   - **Cadence implicit tasks**: Triggered by schedule regardless of communications (e.g., weekly stock taking, monthly invoicing, payment cycles, accounts reconciliation)
   The agent must monitor both communication context and operational calendar to surface the full task picture. This is the highest-frequency real-world failure mode.

**Priority ordering**: Recall > Entity accuracy > Next step quality > Cross-thread correlation > Priority correctness

*Note: This is a living document. Additional quality risks will be added as real data from Ashish becomes available and new failure modes are discovered during testing.*

---

## Sprint 1 Testing Observations (updated 2026-03-25)

### What testing revealed

Sprint 1 ran 16 synthetic cases + 1 real case (R1-D-L3-01, SATA multi-item multi-supplier order). Key findings:

- **Entity resolution and cross-thread correlation** performed better than anticipated once prompt calibration was applied. Three rounds of prompt iteration (separation-default rule, entity resolution calibration, unidentified-client structural decomposition) brought synthetic cases from 11/16 to 16/16 PASS.
- **Reactive implicit tasks** were handled well in the SATA case (89/100) — the agent correctly inferred vehicle arrangement, account reconciliation need, GST bill gaps, and dropped Amazon orders from contextual signals.
- **Cadence/procedural implicit tasks** were the confirmed gap — the agent missed a pre-dispatch checklist review and a final payment confirmation milestone. These were not triggered by any message; they are things that *should* happen at a certain stage of any complex order. This validates cadence implicit tasks as the highest-priority unresolved quality risk.

### Sprint 2 mitigation: task-type subtask checklists

To address cadence implicit task detection before the full state machine is built, inject task-type subtask checklists into the agent prompt as additional context. The checklist specifies:
- Expected subtasks for each task type (procurement, delivery, custom order, etc.)
- The order and trigger for each subtask
- Which subtasks are procedural/cadence (always expected) vs reactive (only if triggered by events)

This is the lightweight precursor to the Sprint 3 task lifecycle state machine. The checklists become the seed data for the template graphs.

### Source strategy for checklist content

Three complementary sources — all three are needed:

1. **Historical chat log analysis**: mine completed orders from Ashish's chat logs to extract the actual sequence of subtasks that occurred. This gives empirical patterns rather than idealised ones.
2. **Agent inference**: the agent can bootstrap initial checklists from historical data, applying domain judgement to identify the right granularity, distinguish procedural from reactive steps, and fill gaps not visible in any single chat thread.
3. **Validation with Ashish**: use the agent-generated checklists as the starting artifact. Walk through them with Ashish to correct errors, add missing steps, and surface edge cases. **Do not rely on Ashish to generate task graphs from scratch** — he has strong mental models but will meander into special cases and may not produce complete, granular process maps unprompted. The agent-drafted checklist keeps the discussion anchored and ensures completeness is checked systematically.

This triangulated approach produces more reliable checklists than any single source alone.

---

## Test Case Design Methodology

### Chosen Generation Approach
**Progressive Complexity Building** — starting with the simplest case and incrementally adding complexity layers. Chosen because:
- Reveals exactly *at what point* the agent breaks down, not just *that* it breaks down
- Immediately executable with synthetic data while waiting for Ashish's real examples
- Builds a clear difficulty ladder that can be extended as real data arrives

### Test Case Framework
Each test case contains:
- **ID**: [Risk]-[Framework]-L[Level]-[Sequence]
- **Input**: Synthetic WhatsApp thread(s) in Hinglish
- **Expected output**: Correct entity resolution or order separation
- **Challenge**: What makes this case difficult
- **Pass criteria**: Specific measurable condition for success
- **Sprint**: S1 (synthetic, immediate) or S2+ (real data from Ashish)

### Three Evaluation Frameworks

#### Data Source Strategy

All levels will include synthetic or augmented test cases — real data from Ashish alone cannot be relied upon to naturally contain edge cases (e.g., historical references, same-name-different-entity). The data source approach per level:

| Source Type | Description | When Used |
|---|---|---|
| `fully-synthetic` | Fictional entity names, fully constructed scenarios | Sprint 1 (no real data yet) |
| `synthetic-with-real-entities` | Real entity names from Ashish's data, synthetically constructed scenarios | Sprint 2+ (once real data available) |
| `real` | Actual WhatsApp conversation samples from Ashish | Sprint 2+ (when provided) |

L4-L7 cases will be constructed as `synthetic-with-real-entities` once Ashish's data is available — using real supplier/client names to make scenarios realistic while engineering the specific edge case deliberately.

#### Framework A: R4 — Supplier/Vendor Entity Resolution
| Level | Scenario | Sprint | Data Source |
|-------|----------|--------|-------------|
| L1 | Same supplier, 2 name variants, single thread | S1 | fully-synthetic |
| L2 | Same supplier, 2 variants, 2 threads | S1 | fully-synthetic |
| L3 | Same supplier, 3-4 variants (honorific + abbreviated + product-based), 3 threads | S1 | fully-synthetic |
| L4 | Two different suppliers with similar names — must NOT merge | S2 | synthetic-with-real-entities |
| L5 | Mixed: multiple suppliers, multiple variants, some merge/some don't | S2 | synthetic-with-real-entities |
| L6 | Historical supplier reference in context — must not create new entity or task | S2 | synthetic-with-real-entities |
| L7 | Similar name to active supplier but historical — distinguish current from past | S2 | synthetic-with-real-entities |

#### Framework B: R4 — Army Client Entity Resolution
| Level | Scenario | Sprint | Data Source |
|-------|----------|--------|-------------|
| L1 | Same unit, 2 reference styles (formal unit name vs officer name), single thread | S1 | fully-synthetic |
| L2 | Same unit, 2 variants, 2 threads | S1 | fully-synthetic |
| L3 | Same unit referenced by unit number, officer rank, and location across 3 threads | S1 | fully-synthetic |
| L4 | Two different Army units with similar names/locations — must NOT merge | S2 | synthetic-with-real-entities |
| L5 | Mixed: multiple units, multiple reference styles | S2 | synthetic-with-real-entities |
| L6 | Historical Army client referenced in context — must not create new entity or task | S2 | synthetic-with-real-entities |
| L7 | Relocated unit — same unit in new location, must link to existing client not create new | S2 | synthetic-with-real-entities |

#### Framework C: R3 — Order/Item Conflation
| Level | Scenario | Sprint | Data Source |
|-------|----------|--------|-------------|
| L1 | Two orders, same item, clearly different clients, single thread each | S1 | fully-synthetic |
| L2 | Same, but client names are informal/abbreviated | S1 | fully-synthetic |
| L3 | Two concurrent orders, references interleaved in shared staff group | S1 | fully-synthetic |
| L4 | Same item, same client, two orders at different times — must stay separate | S2 | synthetic-with-real-entities |
| L5 | Mixed: multiple concurrent orders, same item/different clients + different items/same client | S2 | synthetic-with-real-entities |
| L6 | Historical order/item referenced in context — must not trigger new order or tasks | S2 | synthetic-with-real-entities |

### Success Criteria Design
- **Framework A & B (Entity Resolution)**: Pass = single unified entity record; Fail = duplicate records or incorrect merge
- **Framework C (Order Conflation)**: Pass = separate parent tasks per order; Fail = tasks merged under one parent or attributed to wrong client
- **Ambiguous cases**: Pass = agent flags for human review; Fail = agent silently guesses wrong
- **Sprint 1 threshold**: All L1-L3 cases must pass at 95%+

---

## Learning Objectives

### Learning Outcomes

**R4-A — Supplier Entity Resolution**
- At what name variant distance does the agent start creating duplicate records? (e.g., honorific variants easy vs company-name/proprietor-name hard)
- Does cross-thread entity resolution work as reliably as within-thread resolution?
- Can the agent handle product/city-based informal references ("Delhi wala", "tandoor wala") or does it require a proper name?

**R4-B — Army Client Entity Resolution**
- Does the agent correctly distinguish officer rank references from unit name references within the same thread?
- How well does it handle Army-specific naming patterns (unit numbers, ranks, locations)?
- Does cross-thread correlation work when the client is identified by rank in one thread and unit name in another?

**R3-C — Order Conflation**
- Can the agent keep concurrent same-item orders separate when client context is clear?
- Does it handle informal/abbreviated client names well enough to avoid conflation?
- When two orders are discussed in interleaved messages in a shared group, does it maintain independent tracking for each?

**Across all frameworks**
- What is the agent's failure mode — does it silently guess wrong, or flag uncertainty for human review? Silent wrong guesses are far more dangerous.
- At which complexity level (L1-L7) does performance degrade below 95%? This identifies exactly where to invest prompt engineering effort first.
- Are failure patterns consistent across runs, or non-deterministic? Non-deterministic failures require different mitigation strategies than consistent ones.

---

## Evaluation Artifacts Generated

| Artifact | Location | Purpose |
|---|---|---|
| `evaluations_data.csv` | `data/` | 13 Sprint 1 test cases across 3 frameworks (R4-A, R4-B, R3-C), fully-synthetic |
| `evaluation_design_report.md` | `reports/` | Full evaluation methodology, risk hypotheses, framework definitions, learning objectives |
| `testing_prompt.txt` | `prompts/` | Multi-thread extraction prompt for Claude/ChatGPT playground testing |

### How to Use These Artifacts

1. **Run Sprint 1 test cases**: Use `testing_prompt.txt` as the base prompt. For each row in `evaluations_data.csv`, paste the `input_threads` content and evaluate output against `pass_criteria`.
2. **Score results**: Mark each test case pass/fail. Target: 95%+ pass rate across all L1-L3 cases.
3. **Identify failure complexity level**: Note which level (L1/L2/L3) failures first appear — this guides prompt engineering focus.
4. **Add S2 test cases**: Once Ashish shares real data, extract real entity names and construct L4-L7 synthetic-with-real-entities scenarios using the framework in this report.
5. **Re-run for other risks**: Use this workflow again to create test cases for R1 (proactive milestone tracking) and remaining risks once Sprint 1 evaluation is complete.

### Next Steps After Evaluation
- If L1-L2 pass but L3 fails: cross-thread correlation needs work — add explicit linking instructions to the prompt
- If L1 fails: fundamental entity resolution is broken — revisit base extraction approach
- If agent silently guesses wrong (no uncertainty flags): add explicit instructions to flag low-confidence matches
- Once 95%+ achieved on S1 cases: move to S2 (synthetic-with-real-entities) test cases for L4-L7

---

## Test Case Index

Complete list of all test cases. Sorted by framework and level. Score = latest run against `claude-sonnet-4-6`.

**Summary**: 29/29 active cases PASS as of 2026-03-26.

### R1-D — Task Extraction (Deep)
*Dimensions: task_recall, implicit_task_detection, next_step_quality, cross_thread_correlation, ambiguity_flagging*

| Case ID | Name | Level | Sprint | Data Source | Score | Status |
|---|---|---|---|---|---|---|
| R1-D-L3-01 | sata_multi_item_multi_supplier | L3 | S1 | real | 88 | PASS |

---

### R2 — Implicit Task Detection
*Dimensions: task_recall, implicit_task_detection, next_step_quality*

| Case ID | Name | Sub-risk | Level | Sprint | Data Source | Score | Status |
|---|---|---|---|---|---|---|---|
| R2a-L1-01 | delivery_confirmed_invoice_subtask_missing | R2a (periodic cadence) | L1 | S2 | fully-synthetic | 96 | PASS |
| R2a-L1-02 | end_of_month_accounts_stock_cadence_missed | R2a (calendar-triggered cadence) | L1 | S2 | fully-synthetic | 92 | PASS |
| R2b-L1-01 | army_client_payment_followup_wrong_tone | R2b (client payment nuance) | L1 | S2 | fully-synthetic | 95 | PASS |
| R2c-L1-01 | supplier_optimistic_deadline_no_conservative_milestone | R2c (conservative deadline) | L1 | S2 | fully-synthetic | 92 | PASS |
| R2d-L1-01 | supplier_deadline_set_no_preemptive_reminder | R2d (pre-emptive reminder) | L1 | S2 | fully-synthetic | 95 | PASS |
| R2e-L1-01 | large_army_order_invoice_financing_not_flagged | R2e (invoice financing) | L1 | S2 | fully-synthetic | 97 | PASS |
| R2f-L1-01 | proactive_client_outreach_not_surfaced | R2f (proactive sourcing) | L1 | S2 | fully-synthetic | 82 | PASS |
| R2f-L1-02 | post_delivery_feedback_not_surfaced | R2f (feedback collection) | L1 | S2 | fully-synthetic | 88 | PASS |

---

### R3-C — Order / Item Conflation
*Dimensions: task_recall, entity_accuracy, cross_thread_correlation, ambiguity_flagging*

| Case ID | Name | Level | Sprint | Data Source | Score | Status |
|---|---|---|---|---|---|---|
| R3-C-L1-01 | two_tandoor_orders_clearly_different_clients | L1 | S1 | fully-synthetic | 93 | PASS |
| R3-C-L1-02 | two_different_item_orders_same_client_separate_threads | L1 | S1 | fully-synthetic | 92 | PASS |
| R3-C-L2-01 | two_tandoor_orders_informal_client_references | L2 | S1 | fully-synthetic | 91 | PASS |
| R3-C-L2-02 | same_item_order_client_name_abbreviated_differently | L2 | S1 | fully-synthetic | 88 | PASS |
| R3-C-L3-01 | two_concurrent_orders_references_interleaved | L3 | S1 | fully-synthetic | 88 | PASS |
| R3-C-L3-02 | multiclient_flag_orders_in_single_supplier_thread | L3 | S1 | fully-synthetic | 88 | PASS |

---

### R4-A — Supplier Entity Resolution
*Dimensions: entity_accuracy, cross_thread_correlation, ambiguity_flagging*

| Case ID | Name | Level | Sprint | Data Source | Score | Status |
|---|---|---|---|---|---|---|
| R4-A-L1-01 | same_supplier_2_name_variants_single_thread | L1 | S1 | fully-synthetic | 91 | PASS |
| R4-A-L1-02 | same_supplier_company_name_vs_proprietor_name | L1 | S1 | fully-synthetic | 85 | PASS |
| R4-A-L2-01 | same_supplier_2_name_variants_across_2_threads | L2 | S1 | fully-synthetic | 91 | PASS |
| R4-A-L2-02 | same_supplier_honorific_vs_productbased_reference | L2 | S1 | fully-synthetic | 88 | PASS |
| R4-A-L3-01 | same_supplier_3_4_variants_across_3_threads | L3 | S1 | fully-synthetic | 92 | PASS |

---

### R4-B — Army Client Entity Resolution
*Dimensions: entity_accuracy, cross_thread_correlation, ambiguity_flagging*

| Case ID | Name | Level | Sprint | Data Source | Score | Status |
|---|---|---|---|---|---|---|
| R4-B-L1-01 | same_army_unit_formal_name_vs_officer_reference | L1 | S1 | fully-synthetic | 82 | PASS |
| R4-B-L1-02 | same_army_unit_location_vs_unit_number | L1 | S1 | fully-synthetic | 88 | PASS |
| R4-B-L2-01 | same_army_unit_2_reference_styles_across_2_threads | L2 | S1 | fully-synthetic | 88 | PASS |
| R4-B-L3-01 | same_army_unit_3_reference_styles_across_3_threads | L3 | S1 | fully-synthetic | 91 | PASS |

---

### R5 — Wrong Next Step Under Ambiguity
*Dimensions: next_step_quality, ambiguity_flagging*

| Case ID | Name | Level | Sprint | Data Source | Score | Status |
|---|---|---|---|---|---|---|
| R5-L1-01 | supplier_ready_ambiguous_which_item | L1 | S2 | fully-synthetic | 92 | PASS |
| R5-L1-02 | staff_confirms_delivery_client_silent | L1 | S2 | fully-synthetic | 88 | PASS |
| R5-L2-01 | conflicting_payment_signals_across_threads | L2 | S2 | fully-synthetic | 91 | PASS |
| R5-L2-02 | spec_change_in_1on1_not_yet_sent_to_supplier | L2 | S2 | fully-synthetic | 91 | PASS |

---

### R6 — Post-Delivery Issue Detection
*Dimensions: task_recall, implicit_task_detection, ambiguity_flagging*

| Case ID | Name | Level | Sprint | Data Source | Score | Status |
|---|---|---|---|---|---|---|
| R6-L1-01 | payment_confirmed_delivery_shortfall_and_quality_rejection | L1 | S1 | real | 88 | PASS |

---

### Not yet built (S2+)
| Framework | Level | Description |
|---|---|---|
| R4-A | L4–L7 | Two different suppliers with similar names; historical references; mix of merge/split |
| R4-B | L4–L7 | Two different Army units with similar names; relocated units |
| R3-C | L4–L6 | Same client two orders at different times; mixed concurrent orders; historical references |
| R2f | L1+ | Proactive client outreach not surfaced — client silent, all orders complete |
| R2f | L1+ | Post-delivery feedback task not surfaced — payment received, no feedback visible |
| R7a | L1+ | Off-platform instruction gap — verbal instruction with no WhatsApp trail |
| R7b | L1+ | Execution plan dissonance — staff act on 1:1 chats, groups not updated |
| R9 | L1+ | Missing WhatsApp entries — task appears stalled vs genuinely stalled |
| R10 | L1+ | Staff quality variance — assignee reliability affecting escalation urgency |
| R11 | L1+ | Warehouse inventory blindness — stock-filled orders triggering false supplier tasks |

---

## Quality Risk Hypotheses

### Input Variability Risks

**R1 — Proactive milestone tracking failure**
Agent fails to generate a scheduled follow-up plan from a committed supplier deadline. It waits for silence to become obvious rather than proactively scheduling check-ins from the moment a commitment is recorded.
- *When it occurs*: Any time a supplier commitment is captured in a WhatsApp message
- *Consequence*: Delivery slips with no early warning — same failure mode as today

### Output Quality Risks

**R2b — Client payment nuance failure**
Agent chases Army clients for payment using the wrong tone, timing, or framing. Army clients can delay payments for months due to unit relocations or internal financial timelines. Aggressive or poorly-timed chasing damages relationships.
- *When it occurs*: When payment follow-up tasks are generated for Army client invoices
- *Consequence*: Reputational damage with sensitive clients; Ashish has to repair relationship

**R5 — Wrong next step under ambiguity**
Agent confidently suggests a specific next step in a genuinely ambiguous situation. Staff under workload stress execute it without questioning.
- *When it occurs*: Incomplete information in chats, partial updates from suppliers, conflicting signals across threads
- *Consequence*: Wrong action taken, escalating the problem; staff blame the agent

### Context Sensitivity Risks

**R3 — Cross-thread order conflation**
Two orders for similar items (e.g., two tandoor orders for different clients) get merged into one parent task because agent matches on item name alone rather than client+item combination.
- *When it occurs*: Concurrent orders for the same item type across different clients
- *Consequence*: Tasks attributed to wrong client or delivery deadline — absolute reputational and financial disaster

### Boundary / Scope Risks

**R2a — Periodic cadence tasks missed**
Invoicing, payment collection follow-up, stock-taking, and accounts reconciliation are regular activities tracked at fixed intervals (EOD, weekly, monthly, quarterly, annual). Agent fails to surface these during busy periods when they are most likely to be forgotten.
- *When it occurs*: Delivery confirmed (→ invoice), invoice outstanding 30/45/60 days (→ payment follow-up), end of month (→ accounts reconciliation, GST), end of period (→ stock-take). No explicit message triggers these — they are calendar/stage-driven.
- *Consequence*: Invoice delayed, payment cycle delayed, cash flow impact; inventory and financial blind spots accumulate silently

**R2c — Conservative internal delivery deadline not set**
Supplier gives an optimistic committed delivery date. Agent creates a follow-up task at the supplier's stated date rather than an earlier conservative internal milestone. The adjustment should vary by supplier (past reliability) and lead time (inter-state vs local).
- *When it occurs*: Any time a supplier commits a delivery date — especially inter-state suppliers (Malerkotla, Delhi) where transit time is variable
- *Consequence*: No early warning when supplier slips; Ashish discovers the delay on the day it was supposed to arrive
- *Parameters*: [UNVALIDATED — specific buffer rules to be confirmed with Ashish]

**R2d — Pre-emptive supplier reminder not created**
Agent does not schedule proactive reminder task(s) in the days leading up to a delivery deadline. Reminder cadence may vary by supplier behaviour and lead time.
- *When it occurs*: Any time a supplier commits a delivery date and the lead time is ≥2 days
- *Consequence*: Supplier drifts without a nudge; delivery slips without early warning
- *Parameters*: [UNVALIDATED — reminder timing and frequency to be confirmed with Ashish]

**R2e — Invoice financing not flagged for long-payment-term orders**
For large orders (high value) with Army clients whose payment terms are typically 60–90 days, the agent does not surface invoice financing as an option to improve cash flow.
- *When it occurs*: Invoice raised on a large-value Army order; payment terms are long
- *Consequence*: Cash flow gap not managed proactively; Ashish may not be aware of financing options
- *Parameters*: [TBC with Ashish — value threshold, payment term threshold, financing options available]

**R2f — Proactive sourcing and feedback tasks not surfaced**
Agent processes all active orders correctly but never surfaces non-order implicit tasks — specifically, proactive client outreach (when a client has been silent for an extended period) and post-delivery feedback collection (after payment received). These tasks are part of Ashish's normal operating cadence and are almost never mentioned in WhatsApp.
- *When it occurs*: Client group silent for extended period with all orders completed; order reaches payment-received stage with no feedback message
- *Consequence*: Relationship management and business development tasks are invisible to the agent; Ashish must remember these manually as before
- *Parameters*: [UNVALIDATED — silence threshold, which clients, feedback timing TBC with Ashish]

**R6 — Post-delivery issue detection failure**
Agent marks an order as complete after payment confirmation, missing a delivery shortfall or quality defect raised in the same thread days later. Confirmed from real data: Malerkotla supplier thread — payment confirmed ("paid 28000 all cleared"), car flags missing and quality rejected 2 days later in same thread.
- *When it occurs*: Any time a payment confirmation precedes a shortfall or quality complaint in the same supplier thread
- *Consequence*: Missing items and quality rejections go untracked; Ashish has no alert; client rejection risk unmanaged

**R7a — Off-platform instruction gap blocks downstream actions**
A critical action is verbally instructed by Ashish to staff but never posted to WhatsApp — the agent has no visibility into the instruction or its execution status. Confirmed from real data: Ashish verbally asked Mrs Samita Roy to update the supplier about missing items on WhatsApp; she forgot; 2 days passed with no group update; missing items escalated by urgent phone call (also off-platform); still no WhatsApp update. Supplier eventually shared courier note image confirming dispatch — the only visible signal the agent could detect.
- *When it occurs*: Verbal instructions given during physical handovers, collection day, or in-person coordination — especially under time pressure
- *Consequence*: Follow-up tasks never created; agent task state diverges from reality; Ashish assumes the agent (or staff) is tracking something that is tracked nowhere

### Consistency Risks

**R4 — Entity fragmentation**
Same supplier/client/contractor referred to by multiple name variants across threads (e.g., "Sharma ji", "Sharma bhai", "S. Sharma") — agent creates duplicate entities.
- *When it occurs*: Any Hinglish conversation where parties are referred to informally
- *Consequence*: Duplicate tasks, incomplete history per entity, erroneous double delivery or payment

**R7b — Execution plan dissonance**
Staff act on information from 1:1 chats and skip ahead in the standard execution plan. Agent's task state diverges from reality, triggering incorrect or redundant alerts.
- *When it occurs*: Staff coordinate directly with field parties and update groups late or partially
- *Consequence*: Agent alerts for tasks already completed; staff lose confidence in the system

**R8 — Image content blindness**
A significant portion of business-critical information is communicated via images — screenshots of Excel files, PDFs (quotations, invoices, delivery challans), handwritten lists and notes, courier consignment notes, payment QR codes, payment confirmation screenshots. Confirmed by Ashish: this is the norm, not the exception. A text-only agent is structurally blind to this data.
- *When it occurs*: Any order involving quotation, invoicing, delivery confirmation, or payment — which is every order
- *Consequence*: Agent cannot extract item quantities, prices, or delivery dates from Excel screenshots; cannot confirm payment from payment screenshots; cannot read dispatch details from courier notes; task list is systematically incomplete
- *Note*: This is an architectural constraint, not just a quality risk. Image support (OCR + vision) must be designed into the pipeline, not added later. Prioritise Excel screenshots and courier notes first — these carry the most structured operational data.
- *Status*: **Resolved in Sprint 1** — Claude vision pipeline implemented; images annotated inline before prompt. Confirmed effective on SATA case (14 image annotations including handwritten notes, PDFs, payment screenshots).

**R9 — Missing WhatsApp entries**
Staff and Ashish frequently handle tasks verbally, by phone, or in person with no corresponding WhatsApp entry. The agent has no direct way to detect these gaps and cannot infer events with no downstream signal.
- *When it occurs*: Physical handovers, collection day, in-person coordination, phone calls — especially under time pressure
- *Detection strategy*: Forward inference from downstream evidence (delivery confirmed but no dispatch logged → flag gap); temporal gap detection (task silent longer than expected interval → flag as unverified). Both require the task graph template as a reference.
- *Hard limit*: If both an intermediate step and all downstream steps are missing, the task appears stalled — indistinguishable from genuinely stalled. No mitigation possible without out-of-band input from Ashish.
- *Interview question*: How often do tasks proceed entirely off-platform? Is a "last seen N days ago" alert useful or constant noise?

**R10 — Staff quality variance**
Subtasks assigned to staff members with a history of missed follow-ups carry higher failure risk than the same subtask handled by Ashish directly. The agent currently treats all subtasks equally regardless of assignee.
- *When it occurs*: Any delegated subtask — supplier follow-ups, logistics coordination, client updates
- *Mitigation*: Per-staff completion rate from historical data → risk weight in task graph → lower escalation timeout for high-risk staff + high-stakes subtasks. Does not need to be surfaced explicitly to Ashish.
- *Data requirement*: Sufficient completed task history with staff attribution — not available at Sprint 3; design now, implement later.
- *Sensitivity*: Surfacing staff performance data may affect team dynamics. Only Ashish can determine appropriate handling.

**R11 — Warehouse inventory blindness**
Pre-stocked items do not require supplier interaction. If the agent always creates a supplier procurement subtask, it generates false work and erodes trust.
- *When it occurs*: Any order for commonly pre-stocked items (standard stationery, common equipment)
- *Detection strategy*: Passive — infer stock fulfilment from chat cues ("stock mein hai", no supplier thread, direct delivery arranged). Active — provide agent with inventory feed; check stock before creating supplier subtask.
- *Right approach*: Hybrid. Passive for common items; active integration for high-value items where stock error is costly.
- *Template implication*: Procurement template needs a branch at the start — stock check → (in stock) direct delivery or (not in stock) supplier sourcing.

### Risk Impact Analysis

---

## Priority Quality Risk

### Risk Statement
**R4 — Entity Fragmentation**: The same supplier, client, or contractor is referred to by multiple name variants across WhatsApp threads (e.g., "Sharma ji", "Sharma bhai", "S. Sharma", "Sharma supplier"). The agent creates duplicate entity records, leading to fragmented task history, duplicate tasks, and potential double deliveries or erroneous payments.

*Scenario*: Staff in one group refer to a supplier as "Sharma ji" while Ashish in another group calls the same person "Sharma bhai". The agent creates two supplier profiles, splits the order history between them, and potentially triggers duplicate follow-up tasks or payments.

*Consequence for adoption*: Even when caught and corrected by human supervision, entity fragmentation completely destroys staff trust in the agent. If they can't trust that "Sharma ji" and "Sharma bhai" are the same person, they can't trust any of the agent's task attributions, alerts, or summaries.

### Prioritization Rationale
- **Trust is the foundation**: Entity fragmentation is trust-breaking even when caught. A single instance of a duplicated supplier record would make staff question every task, every alert, every suggested action.
- **R1 is more tolerant**: Missing some proactive alerts is acceptable in early stages — most being provided is sufficient. R4 has zero tolerance.
- **R3 is rarer**: Same-item concurrent orders are infrequent and detectable under human supervision. R4 occurs in virtually every conversation (informal name variants are the norm in Hinglish communication).

### Testing Approach
- Create test cases with the same entity referred to by 2-4 different name variants across multiple threads
- Test both within-thread and cross-thread variant resolution
- Include common Hinglish patterns: honorifics (ji, bhai, sahab), abbreviations, partial names, transliteration variants
- Test disambiguation: two genuinely different people with similar names (e.g., two "Sharma" suppliers)

### Success Criteria
- 95%+ of entity variant pairs correctly resolved to the same entity
- Zero cases of two different entities incorrectly merged
- Consistent entity IDs across all threads for the same real-world party
- Agent flags low-confidence matches for human review rather than silently guessing

---

### Risk Impact Analysis

*Note: Priority is based on severity only. Frequency estimates are not available without real operational data from Ashish and should not be treated as accurate. Frequency will be revisited once real data is available.*

| Risk | Severity | Priority |
|------|----------|----------|
| R8 — Image content blindness | Critical | 0 (architectural — must resolve before Sprint 2) |
| R1 — Proactive milestone tracking failure | Critical | 1 |
| R3 — Cross-thread order conflation | Critical | 2 |
| R4 — Entity fragmentation | Critical | 3 |
| R6 — Post-delivery issue detection failure | High | 4 |
| R5 — Wrong next step under ambiguity | High | 5 |
| R7a — Off-platform instruction gap | High | 6 |
| R7b — Execution plan dissonance | High | 7 |
| R2b — Client payment nuance failure | High | 8 |
| R2a — Post-delivery invoicing missed | Medium | 9 |
| R2c — Stock taking/accounts cadence missed | Medium | 10 |

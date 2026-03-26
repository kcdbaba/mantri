# Task Taxonomy

**Status**: Living document — update whenever a new task type is identified or a step is validated/changed
**Authority**: This is the canonical reference for task types. `prompts/task_type_checklists.txt` and the inline tables in `prompts/testing_prompt.txt` are derived from this document and must be kept in sync.
**Last updated**: 2026-03-26

---

## Overview

Tasks in Ashish's Army supply business fall into two top-level categories:

| Category | Types | Description |
|---|---|---|
| **Order tasks** | Types 1–8 | Tasks triggered by a client enquiry or confirmed order — have a defined start (enquiry/order) and end (payment received) |
| **Non-order tasks** | Types 9–11 | Tasks not triggered by a specific order — relationship management, business development, feedback collection, period-end administration |

**11 task types total.** Each type has a defined step sequence. Steps are tagged by validation status and trigger class.

### Validation Status Tags

| Tag | Meaning |
|---|---|
| `[CONFIRMED]` | Validated against real cases (SATA real case, Malerkotla real case) |
| `[CADENCE]` | Procedural step that happens regardless of what is written in WhatsApp — primary target for implicit task detection |
| `[UNVALIDATED]` | Inferred from business context; requires Ashish confirmation before use in production |

---

## ORDER TASKS

### Type 1: Standard Equipment Procurement
*Tandoor, AC, fridge, furniture, general equipment — ordered from a supplier and delivered to client*

| Step | Description | Tag |
|---|---|---|
| 1 | Enquiry received / order logged | — |
| 2 | Quote requested from supplier | — |
| 3 | Quote received (rate + GST + delivery) | — |
| 4 | Quote sent to client | — |
| 5 | Order confirmed by client | — |
| 6 | PO / advance payment issued to supplier (if required) | `[UNVALIDATED]` when required |
| 7 | Delivery date committed by supplier | — |
| 7a | **Conservative internal deadline set** (earlier than supplier's stated date) | `[CADENCE]` `[UNVALIDATED]` buffer duration TBC |
| 7b | **Pre-emptive supplier reminder** (morning of, or day before, delivery) | `[CADENCE]` `[UNVALIDATED]` timing + frequency TBC |
| 8 | Goods dispatched by supplier | — |
| 9 | Goods received at Ashish's end | — |
| 10 | **Inspection and sorting** | `[CADENCE]` rarely in WhatsApp |
| 11 | **Pre-dispatch checklist** | `[CADENCE]` almost never in WhatsApp |
| 12 | Delivery to client arranged (transport/vehicle) | — |
| 13 | Delivery to client completed | — |
| 14 | **Client acceptance / sign-off** | `[CADENCE]` often verbal only |
| 15 | Invoice raised | — |
| 16 | **Payment follow-up (Day 30)** | `[CADENCE]` most commonly missed |
| 17 | Payment received | — |
| 18 | **Post-delivery feedback follow-up** | `[CADENCE]` `[UNVALIDATED]` timing + which clients TBC |
| 19 | **GST reconciliation** | `[CADENCE]` monthly; almost never in WhatsApp |

---

### Type 2: Custom / Made-to-Order Items
*Flags, printed signage, embroidered items, custom uniforms — manufactured to spec*

| Step | Description | Tag |
|---|---|---|
| 1 | Enquiry received with initial specification | — |
| 2 | **Spec confirmation with client** | `[CADENCE]` must happen before quoting |
| 3 | Sample / design proof approval (if applicable) | `[UNVALIDATED]` — when required? |
| 4 | Quote from manufacturer (includes production time) | — |
| 5 | Quote sent to client | — |
| 6 | **Order confirmed + final spec locked in writing** | `[CADENCE]` spec must be written, not verbal |
| 7 | Production timeline committed by manufacturer | — |
| 7a | **Conservative internal deadline set** | `[CADENCE]` `[UNVALIDATED]` |
| 7b | **Production midpoint follow-up** | `[CADENCE]` proactive check |
| 8 | **QC inspection of finished goods before dispatch** | `[CADENCE]` critical for custom items |
| 9 | Delivery to client | — |
| 10 | **Client acceptance / rejection handling** | `[CADENCE]` rejection risk higher for custom |
| 11 | Invoice raised | — |
| 12 | **Payment follow-up** | `[CADENCE]` |
| 13 | Payment received | — |
| 14 | **Post-delivery feedback follow-up** | `[CADENCE]` `[UNVALIDATED]` |

---

### Type 3: Army Direct Delivery
*Delivery direct to Army unit / cantonment — standard procurement steps apply, plus Army-specific documentation*

Standard Type 1 steps 1–9, then:

| Step | Description | Tag |
|---|---|---|
| 10 | **Army unit coordination** (receiving officer, delivery address confirmed) | `[CADENCE]` |
| 11 | **Gate pass / entry documentation** | `[CADENCE]` Army-specific; often overlooked |
| 12 | **Pre-dispatch checklist** | `[CADENCE]` |
| 13 | Delivery vehicle arranged or Army transport confirmed | — |
| 14 | Dispatch confirmation | — |
| 15 | Delivery to Army unit confirmed | — |
| 16 | **DO / GRN / signed delivery note received from Army unit** | `[CADENCE]` required for formal payment processing |
| 17 | Invoice raised (with DO reference) | — |
| 18 | **Payment follow-up — Army client protocol** | `[CADENCE]` Ashish contacts CO/accounts directly; patient tone; 60–90 day cycle is normal |
| 19 | Payment received | — |
| 20 | **Post-delivery feedback follow-up** | `[CADENCE]` `[UNVALIDATED]` |

**Army payment dynamics** (confirmed): payment cycles 60–90 days normal; Ashish contacts CO/accounts section directly — never via staff; aggressive follow-up risks the relationship.

---

### Type 4: GeM Portal Orders
*Orders placed through Government e-Marketplace — portal has its own deadlines and documentation requirements*

| Step | Description | Tag |
|---|---|---|
| 1 | GeM order received | — |
| 2 | **Order details verified on portal** | `[CADENCE]` |
| 3 | **GeM order accepted on portal (within portal deadline)** | `[CADENCE]` missing = auto-rejection |
| 4–6 | Supplier sourcing (standard procurement) | — |
| 7 | **Delivery timeline updated on portal** | `[CADENCE]` portal requires this |
| 8 | Goods dispatched | — |
| 9 | **Delivery confirmation uploaded to portal** | `[CADENCE]` frequently missed; blocks payment |
| 10 | **Invoice raised on portal (e-invoice format)** | `[CADENCE]` portal-specific format required |
| 11 | **GeM payment timeline tracked** | `[CADENCE]` longer cycle than direct payment |
| 12 | Payment received via GeM | — |

---

### Type 5: Inter-State Procurement
*Malerkotla, Delhi, other out-of-state suppliers — adds transit logistics layer*

Standard Type 1 steps 1–6, then:

| Step | Description | Tag |
|---|---|---|
| 7 | **Transporter identified and booked** | `[CADENCE]` |
| 8 | **E-way bill / transport documentation arranged** | `[CADENCE]` required for interstate goods |
| 9 | Goods dispatched by supplier to transporter | — |
| 10 | **Transit follow-up** (Malerkotla→Guwahati: 3–4 days typical) | `[CADENCE]` transporter often goes silent |
| 11 | **Expected arrival date tracked** | `[CADENCE]` if delayed, escalation task |
| 12 | Goods received at Guwahati | — |
| 13 | **Transit damage inspection** | `[CADENCE]` higher risk for inter-state |
| 14+ | Standard steps from inspection onward (Type 1, steps 10–19) | — |

---

### Type 6: Stock-Filled Orders
*Items fulfilled from Ashish's warehouse — no supplier contact required*

| Step | Description | Tag |
|---|---|---|
| 1 | Enquiry / order received | — |
| 2 | **Stock check — item and quantity available?** | `[CADENCE]` must happen before committing |
| 3 | **Stock availability confirmed to client** | `[CADENCE]` |
| 4 | Delivery arranged from warehouse | — |
| 5 | **Pre-dispatch checklist** | `[CADENCE]` |
| 6 | Goods dispatched | — |
| 7 | **Stock deduction logged** | `[CADENCE]` frequently missing; causes inventory blindness |
| 8 | Delivery to client confirmed | — |
| 9 | Invoice raised | — |
| 10 | **Payment follow-up** | `[CADENCE]` |
| 11 | Payment received | — |
| 12 | **Inventory replenishment check (if stock low)** | `[CADENCE]` proactive; almost never in WhatsApp |
| 13 | **Post-delivery feedback follow-up** | `[CADENCE]` `[UNVALIDATED]` |

---

### Type 7: Service Orders (Repairs / Maintenance)

| Step | Description | Tag |
|---|---|---|
| 1 | Service request received | — |
| 2 | **Site visit / assessment scheduled** | `[CADENCE]` timing often not logged |
| 3 | **Diagnosis confirmed with client** | `[CADENCE]` often verbal only |
| 4 | **Scope of work and parts list confirmed** | `[CADENCE]` |
| 5 | Quote for parts + labour | — |
| 6 | Client approval | — |
| 7 | Parts sourcing (if required) | — |
| 8 | **Service date scheduled** | `[CADENCE]` |
| 9 | Service / repair executed | — |
| 10 | **Job completion sign-off from client** | `[CADENCE]` verbal only; rarely written |
| 11 | Invoice raised | — |
| 12 | **Payment follow-up** | `[CADENCE]` |
| 13 | Payment received | — |
| 14 | **Post-service feedback / warranty period** | `[UNVALIDATED]` — follow-up period TBC |

---

### Type 8: Collections / Payment Follow-Up
*Applies in parallel to all order types once invoice is raised*

| Milestone | Expected action | Tag |
|---|---|---|
| Day 0 | Invoice raised; due date communicated | — |
| Day 30 | **First follow-up if payment not received** | `[CADENCE]` most commonly missed |
| Day 45 | **Second follow-up** | `[CADENCE]` |
| Day 60 | **Escalation to Ashish for direct intervention** | `[CADENCE]` |
| Day 90 | **Formal notice or account hold (if applicable)** | `[UNVALIDATED]` |
| On receipt | Payment confirmation sent to client | — |
| On receipt | **Receipt / acknowledgement issued** | `[CADENCE]` often skipped |
| Monthly | **GST reconciliation across all receipts** | `[CADENCE]` almost never in WhatsApp |
| Large order + Army/GeM client | **Invoice financing / cash flow flag** | `[CADENCE]` `[UNVALIDATED]` value threshold + financing options TBC |

**Army vs commercial distinction**: Army clients have 60–90 day normal cycles; Day 45 is not an overdue trigger for Army clients — it is awareness only. Ashish contacts Army unit CO/accounts section directly, never via staff, with a courtesy framing. Standard commercial follow-up cadence does not apply.

---

## NON-ORDER TASKS

### Type 9: Proactive Client Engagement
*Ashish proactively reaches out to existing clients to generate new orders — not triggered by any incoming enquiry. Client-side only; no supplier outreach task type is needed (supplier relationships are established and order-driven).*

| Trigger | Expected action | Tag |
|---|---|---|
| Client silent for N weeks after order closure | **Check in with client** — "kuch chahiye tha?" | `[CADENCE]` `[UNVALIDATED]` N weeks TBC |
| Seasonal / procurement cycle known for client | **Proactive quote push** — lead with relevant items | `[UNVALIDATED]` which clients, which seasons TBC with Ashish |
| New stock item available | **Outreach to clients likely to need it** | `[UNVALIDATED]` |

**Implicit task detection rule**: client silent for > N weeks post-completion AND all prior orders completed → surface "Proactive check-in — [client]" task.

**Open parameters (confirm with Ashish)**:
- N weeks threshold — commercial vs Army clients may differ
- Which clients Ashish proactively calls vs which call him
- Whether phone-only outreach (no WhatsApp signal) counts for the agent's tracking purposes

---

### Type 10: Post-Delivery Feedback Collection
*Ashish collects feedback from clients after order completion — relationship management, almost never logged in WhatsApp*

| Step | Description | Tag |
|---|---|---|
| 1 | Payment received — order fully closed | Trigger |
| 2 | **Feedback call / WhatsApp message to client** | `[CADENCE]` `[UNVALIDATED]` timing TBC |
| 3 | Quality issue noted from feedback → reopen delivery task | `[CADENCE]` if applicable |
| 4 | Relationship note logged (client preference, pain point) | `[UNVALIDATED]` likely informal/mental |
| 5 | Client added to proactive outreach rotation | `[UNVALIDATED]` |

**Implicit task detection rule**: payment received on an order AND no feedback message visible within N days → surface "Post-delivery feedback — [client]" task.

**Open parameters (confirm with Ashish)**:
- Feedback timing (same day? 1–2 days? 1 week after payment?)
- Which clients always get feedback follow-up vs which are skipped
- Phone vs WhatsApp — does it matter for the agent's task tracking?

---

### Type 11: Period-End Administrative Cadence
*Regular accounting, compliance, and operations tasks that fall due at the end of each calendar period — not triggered by any order or message. Entirely calendar-driven. Almost never visible in WhatsApp.*

All steps below are `[UNVALIDATED]` — confirm with Ashish which tasks he does, at what frequency, and who is responsible.

| Period | Task | Tag |
|---|---|---|
| **Daily** | Cash reconciliation (if cash transactions occur) | `[UNVALIDATED]` |
| **Daily** | Outstanding order status review | `[UNVALIDATED]` |
| **Weekly** | Open payment follow-ups review — which invoices are past due | `[UNVALIDATED]` |
| **Weekly** | Open orders / supplier commitments review | `[UNVALIDATED]` |
| **Monthly** | GST reconciliation — all invoices raised and received | `[CADENCE]` `[UNVALIDATED]` |
| **Monthly** | Accounts payable reconciliation — outstanding supplier payments | `[UNVALIDATED]` |
| **Monthly** | Accounts receivable reconciliation — outstanding client payments | `[UNVALIDATED]` |
| **Monthly** | Invoice audit — any invoices raised but not yet sent or acknowledged | `[UNVALIDATED]` |
| **Quarterly** | Advance tax assessment and payment (if applicable) | `[UNVALIDATED]` |
| **Quarterly** | Accounts review with CA (if applicable) | `[UNVALIDATED]` |
| **Annually (Mar 31)** | Stock-take — physical count against inventory records | `[CADENCE]` `[UNVALIDATED]` |
| **Annually (Mar 31)** | Year-end accounts closure | `[UNVALIDATED]` |
| **Annually (Mar 31)** | Annual GST return / reconciliation | `[UNVALIDATED]` |
| **Annually (Mar 31)** | Pending invoice clearance before financial year end | `[CADENCE]` `[UNVALIDATED]` |

**Implicit task detection rule**: when thread timestamps indicate proximity to a period boundary (end of month, end of quarter, year-end), surface the relevant tasks from the list above as calendar-triggered implicit tasks — even if no one has mentioned them in any thread.

**Open parameters (confirm with Ashish)**:
- Which of the above tasks does he actually do, and which are handled by CA/accountant externally?
- Who is responsible for each — Ashish personally, a staff member, or outsourced?
- What is the actual cadence for stock-take — annual only, or more frequent?
- Are there additional period-end tasks specific to Army supply (e.g., GeM portal annual reconciliation, MSME compliance)?
- Does he use any accounting software (Tally, etc.) that handles some of these — if so, the agent should not duplicate them

---

## IMPLICIT TASK TRIGGER TAXONOMY

Implicit tasks are categorised by what triggers them. The agent must handle all three categories.

| Trigger class | Examples | Detection method |
|---|---|---|
| **Reactive** | Supplier silence → follow-up; conflicting signals → clarify | Detected from message content and gaps in expected responses |
| **Stage-driven cadence** | Delivery confirmed → invoice; payment received → feedback | Detected by matching current stage against type checklist |
| **Calendar-driven cadence** | End of month → GST reconciliation; year-end → accounts closure | Detected by comparing thread timestamp against calendar |
| **Absence-driven** | Client silent N weeks → proactive outreach; no Day-30 follow-up → surface it | Detected by absence of expected message within a time window |

---

## UNVALIDATED PARAMETERS — SUMMARY

All items below require confirmation with Ashish before the implicit task rules are finalised. See `interviews/user_research_plan.md` (Ashish session, Part 3b) for the interview questions.

| Parameter | Used in | Current assumption | TBC |
|---|---|---|---|
| Conservative deadline buffer (days) | Types 1–3, 5 | Not implemented — buffer = 0 | How many days? Does it vary by supplier? |
| Pre-emptive reminder timing | Types 1–3 | Morning of delivery day | Day before? Multiple? Depends on supplier? |
| Proactive outreach silence threshold (weeks) | Type 9 | ~4 weeks (commercial) | Actual threshold? Army vs commercial differ? |
| Post-delivery feedback timing (days) | Types 1–3, 10 | Within a few days of payment | Same day? Next day? Week? Which clients? |
| Invoice financing value threshold | Type 8 | Rs 1L+ (rough) | Actual threshold? Financing options available? |
| Army payment follow-up escalation path | Type 3, Type 8 | Ashish → CO/accounts section | Who exactly? At what point escalate beyond courtesy? |
| Formal notice threshold (Day 90+) | Type 8 | Mentioned but unvalidated | Does this ever happen? What form does it take? |
| Service order warranty / follow-up period | Type 7 | Not defined | Is there a standard follow-up period? |
| Period-end tasks (daily/weekly/monthly/quarterly/annual) | Type 11 | All unvalidated | Which tasks? Who does them? What cadence? Outsourced to CA? |
| Stock-take frequency | Type 11 | Annual (Mar 31) | Annual only, or more frequent? |

---

## RELATIONSHIP TO OTHER DOCUMENTS

| Document | Role |
|---|---|
| **This document** (`task_taxonomy.md`) | Canonical source of truth for all task types, steps, and validation status |
| `prompts/task_type_checklists.txt` | Derived from this document — formatted for LLM prompt injection. Sprint 2 interim artefact, to be deprecated when task lifecycle graph is built |
| `prompts/testing_prompt.txt` | Inline checklist tables derived from this document. Must be updated when taxonomy changes |
| `reports/evaluation_design_report.md` | Test cases are organised around this taxonomy — framework R2 maps to implicit task detection across all types |
| `reports/task_lifecycle_state_graph_design.md` | Sprint 3 target — graph-based representation that replaces the linear checklists in this taxonomy |
| `interviews/user_research_plan.md` | Ashish session Part 3 covers all 10 task types; Part 3b covers unvalidated parameters |

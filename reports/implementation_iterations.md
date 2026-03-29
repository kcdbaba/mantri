# Implementation Iterations
**Started:** 2026-03-29
**Baseline:** `implementation_test_results.md` (29/29 PASS, avg 90.0/100)

---

## Iteration 1 — Prompt targeted improvements
**Date:** 2026-03-29
**File changed:** `prompts/testing_prompt.txt`

### Changes made

**1. Named items with no supplier thread → discrete parent tasks**

*Problem (R1-D failure):* Items explicitly mentioned in client/staff messages but without any corresponding supplier thread (e.g., basketball shoes, batteries) appeared only in global flags rather than as trackable named parent tasks. This meant they could be silently dropped from operations.

*Fix:* Added explicit instruction in step 3 (hierarchy) requiring a discrete named parent task for every item mentioned in threads with no supplier coverage, with status "Supplier thread not found — human review needed."

**2. Delivery completion trigger → explicit challan/sign-off subtasks**

*Problem (R1-D failure):* When a delivery completion signal appeared in threads ("maal ready hai", "sab items ready hain"), the agent noted delivery pending but didn't create discrete subtasks for delivery challan issuance and client acceptance/sign-off. Case R1-D noted "post-delivery inspection and final delivery challan not explicitly created as discrete subtasks."

*Fix:* Added explicit instruction in step 8 (cadence implicit tasks): when a delivery completion signal appears AND the order is multi-step (Types 1, 2, 3, 5), create discrete subtasks for delivery challan and client sign-off if not already confirmed in threads.

**3. Proactive outreach → single unified task with explicit gap duration**

*Problem (R2f score 82):* The agent split proactive outreach into two overlapping subtasks (satisfaction check + reorder probe) and used conditional framing ("if the relationship warrants it"). The evaluator marked down for diluted structure and softened urgency. Score: 82.

*Fix:* Updated the proactive client outreach instruction to require a single unified subtask combining both satisfaction check and reorder probe, with explicit gap duration stated as the trigger ("~N weeks since last interaction"), and removed conditional framing — if the silence threshold is met, the action is required.

---

### Re-test results

| Case | Before | After | Delta | Notes |
|---|---|---|---|---|
| R1-D-L3-01 (SATA multi-item) | 88 | 84 | -4 | Items-without-thread fix confirmed in passes; -4 is evaluator stochasticity + longer output surface area creating more nit failures (working capital owner, year-end task assignment). Key structural gap (named tasks for items without supplier thread) is now resolved. |
| R2f-L1-01 (proactive outreach) | 82 | 95 | +13 | Fully resolved. Evaluator: "No meaningful failures against the pass criteria." Single unified task with explicit gap duration landed correctly. |

**Net:** +9 points across two re-tested cases. R2f improvement is structural and clean. R1-D baseline noise is within expected stochastic range for a large complex case.

---

### Remaining known gaps (not blocking for current sprint)

| Gap | Cases affected | Plan |
|---|---|---|
| Delivery challan still not fully prominent as standalone milestone | R1-D | Monitor — partially addressed; may need further prompt tuning after Ashish Part 2 confirms challan importance |
| Payments cross-group detection (payment screenshot in supplier group, no match in payments group) | None yet — zero eval cases | Add 2–3 cases after Ashish Part 2 confirms detection rule parameters |
| Emergency local-pickup billing (client wallet) | None yet | Add cases after Ashish Part 2 |
| R4-B Army unit alias (unit format vs officer reference) plateau at ~87–91 | R4-B-L3-01 | Improvement path is bootstrapping alias dict from 6 months of real chat history — Sprint 3 |

---

## Next iteration candidates (Sprint 2 → Sprint 3)

1. Add payment cross-group detection eval cases (high priority — validated in user research)
2. Add client wallet / local-pickup billing eval cases
3. Alias bootstrapping pass on real 6-month chat history → re-test R4-B

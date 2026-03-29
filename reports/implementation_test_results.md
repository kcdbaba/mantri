# Implementation Test Results
**Date:** 2026-03-29
**Cases run:** 29 / 31 in `tests/evals/` (2 pending — from evaluations_data.csv rows not yet scaffolded)
**Model:** claude-sonnet-4-6
**Evaluator:** claude-sonnet-4-6 (LLM-as-judge)

---

## Summary

| Metric | Value |
|---|---|
| Cases run | 29 |
| PASS | 29 (100%) |
| FAIL | 0 |
| Average overall score | 90.0 / 100 |
| Lowest score | 82 (2 cases) |
| Highest score | 97 |

All 29 cases passed. The quality risk hypothesis — that the agent would miss cadence implicit tasks and struggle with entity resolution — is partially confirmed but the agent performs better than the pre-implementation baseline across all frameworks.

---

## Results by Framework

| Framework | Description | Cases | Avg Score | Min | Max |
|---|---|---|---|---|---|
| R1-D | Real-world multi-item multi-supplier (SATA) | 1 | 88.0 | 88 | 88 |
| R2 | Cadence implicit tasks (invoice, accounts, payment, tone, milestone, proactive) | 8 | 92.1 | 82 | 97 |
| R3-C | Client separation / order disambiguation | 6 | 90.0 | 88 | 93 |
| R4-A | Supplier entity resolution | 5 | 89.4 | 85 | 92 |
| R4-B | Army unit entity resolution | 4 | 87.2 | 82 | 91 |
| R5 | Ambiguity detection | 4 | 90.5 | 88 | 92 |
| R6 | Payment + delivery shortfall | 1 | 88.0 | 88 | 88 |

---

## Quality Risk Assessment

### Cadence implicit tasks (R2 framework) — PRIMARY RISK
**Status: BETTER THAN EXPECTED**

Average 92.1 across 8 cases covering: delivery invoice generation, end-of-month accounts, payment follow-up tone, supplier deadline milestones, preemptive delivery reminders, invoice financing, proactive client outreach, post-delivery feedback.

The agent reliably detects:
- Invoice creation as an implicit post-delivery task
- Missing payment entries (two-step logging validated in user interviews)
- Missed proactive outreach obligations
- Post-delivery reconciliation tasks

Weakest case: R2f proactive outreach (82) — agent flags the need but does not generate a specific outreach message draft.

### Entity resolution (R4-A, R4-B) — SECONDARY RISK
**Status: SOLID — minor alias persistence gaps**

Supplier entity resolution (R4-A): avg 89.4. Handles same supplier with 2-4 name variants across threads. Weakest case (85): company name vs. proprietor name disambiguation at L1 — agent correctly clusters but with lower confidence notation.

Army unit resolution (R4-B): avg 87.2. Lowest performers (82) are unit-format vs officer-reference disambiguation. These are structurally hard — "SATA" vs "Col. Sharma" pointing to the same unit is context-dependent. Acceptable for MVP; improvement path is bootstrapping the alias dictionary from Ashish's 6-month chat history.

### Client separation (R3-C) — TERTIARY RISK
**Status: STRONG**

Average 90.0 across L1–L3 difficulty. Even L3 (interleaved references across shared supplier thread) scores 88. No cases of cross-client task merger observed.

### Ambiguity flagging (R5) — DESIGN VALIDATION
**Status: VALIDATES ASHISH'S REQUIREMENT**

Average 90.5. Agent surfaces ambiguous items with explicit flagging sections rather than silently resolving. Consistent with Ashish's stated requirement: "Hide ambiguity — that is the main point. It has to be highlighted." The dedicated ambiguity section with numbered items (seen in R1-D with 7 flagged items) is the correct pattern.

---

## Failure Patterns

No FAIL verdicts. Recurring score deductions (not blocking):

1. **Post-delivery lifecycle milestones** — delivery challan, final inspection sign-off, GST bill confirmation — often implied but not created as discrete named tasks. Affects R1-D, R2a, R6.

2. **Named items with no supplier thread** — items explicitly listed in case metadata as "no supplier chat available" are flagged globally but not created as named pending tasks. Affects R1-D (basketball shoes, batteries).

3. **Proactive outreach specificity** — agent detects that outreach is needed but does not draft the message. Affects R2f cases.

4. **Off-platform obligation inference** — local-pickup billing pattern (Ashish interview) not yet represented in eval dataset. This is a gap in the eval dataset, not in the agent — to be added after Ashish Part 2.

---

## Improvement Areas for Next Iteration

1. **Add post-delivery checklist as a cadence implicit task type** — delivery challan, inspection sign-off, payment confirmation window. Validated by both R1-D failures and Ashish interview.

2. **Add emergency local-pickup task type to test set** — currently no eval cases cover this; add 2-3 cases after Ashish Part 2 and payments cross-check design is finalised.

3. **Alias dictionary bootstrapping** — R4-B L3 cases show improvement plateau at ~91 without seeded unit aliases. Feeding 6 months of Ashish's chat history into a bootstrapping pass should close this gap.

4. **Payments cross-group detection** — zero eval cases currently cover the payment-screenshot cross-referencing pattern (Payments group vs. supplier group). This is now the highest-priority gap given user research findings.

---

## Cases Not Yet Run

2 cases from `evaluations_data.csv` do not have scaffolded `tests/evals/` directories:
- Run `python scripts/run_synthetic_batch.py` to scaffold remaining rows
- Then run `python scripts/run_test.py --case <dir> --evaluate` for each

# Sprint 1 Demo Script — Mantri
**Duration**: ~4 minutes | **Date**: 2026-03-26

---

## Slide 1 — Title (12s)
**Visual**: Title card

**Voiceover**:
"This is the Sprint 1 demo for Mantri — an AI operations agent for an Army supply business."

---

## Slide 2 — The Problem (35s)
**Visual**: Problem statement

**Voiceover**:
"Ashish runs an Army supply business in Guwahati. He manages procurement, delivery, and client coordination entirely through WhatsApp — across dozens of groups and one-on-ones. At any moment he's tracking 10 to 20 concurrent orders involving multiple suppliers, Army units, and staff members.

The problem: there's no system. Tasks fall through the gaps. A supplier confirms delivery in one group, payment is discussed in another, and Ashish has to mentally correlate it all in real time — in Hinglish, with informal names and abbreviations. At scale, things get missed."

---

## Slide 3 — The Solution (30s)
**Visual**: Agent architecture overview

**Voiceover**:
"Mantri is a background AI agent that monitors Ashish's WhatsApp messages. It extracts tasks, tracks their status across groups, and surfaces what needs attention — without ever posting or interfering with Ashish's existing workflows.

The agent speaks the same informal language Ashish's team uses: Hinglish, location shorthand, officer titles, supplier nicknames. It doesn't require Ashish to change how he works."

---

## Slide 4 — The Prototype (35s)
**Visual**: SATA order diagram + agent output snippet

**Voiceover**:
"In Sprint 1 we built and tested the extraction agent on the SATA order — a real, complex multi-item procurement from a real Army unit. The order spanned four WhatsApp groups: a client group, two supplier groups, and an internal coordination group. 14 items. 5 suppliers. Multiple concurrent payment and delivery threads.

We ran the agent on the complete four-thread context. It identified all major items, correctly attributed them to the right suppliers, flagged 7 ambiguous correlations for human review, and surfaced implicit tasks — like a 21-day gap on an Amazon order with no follow-up — without any explicit mention in the messages."

---

## Slide 5 — Evaluation Framework (35s)
**Visual**: 6 quality dimensions + test case levels diagram

**Voiceover**:
"To measure quality systematically, we designed an evaluation framework with six dimensions: task recall, entity accuracy, cross-thread correlation, next step quality, implicit task detection, and ambiguity flagging. The hardest and most important is recall — missing a task entirely is the most costly failure mode.

We built 31 test cases across three complexity levels. Level 1 covers single-thread, single-entity scenarios. Level 2 adds abbreviation and cross-thread challenges. Level 3 handles the hardest cases: interleaved messages, concurrent orders, and real multi-supplier complexity.

Each run is automatically scored by a second LLM acting as judge, evaluating against structured pass criteria."

---

## Slide 6 — Sprint 1 Results (35s)
**Visual**: Score table — Claude vs Gemini, prompt iteration chart

**Voiceover**:
"On the 16 synthetic test cases, we ran three rounds of prompt iteration. The first run scored 11 out of 16. After calibrating entity resolution, adding a separation-default rule, and fixing the structural decomposition for unidentified clients, we reached 16 out of 16.

On the real SATA case — the hardest case in the set — Claude Sonnet scored 88 out of 100. The main gaps were at the margin: two item categories not surfaced as discrete tasks, and post-delivery checklist items not made explicit. The core logic worked."

---

## Slide 7 — Gemini Comparison (30s)
**Visual**: Claude 88 vs Gemini 52 comparison card

**Voiceover**:
"We also evaluated Gemini 2.5 Flash as a cheaper alternative. On the same SATA case, Gemini scored 52 out of 100 — a PARTIAL fail. The critical failure was output truncation: the response cut off mid-sentence, meaning roughly half the task list was never delivered. Items like batteries and basketball shoes were absent entirely. No supplier-thread gap flags were raised.

The verdict: Gemini 2.5 Flash is not reliable enough for this task at current complexity. Claude Sonnet remains the production model."

---

## Slide 8 — Gap Found: Cadence Tasks (25s)
**Visual**: Gap callout — cadence vs reactive implicit tasks

**Voiceover**:
"Testing also revealed a confirmed gap: cadence implicit tasks. The agent handles reactive implicit tasks well — inferring a follow-up from a supplier's silence, for example. But procedural milestones — things that should always happen at a certain stage of every order, regardless of what messages say — were missed. Pre-dispatch checklist review. Final payment confirmation.

This is the highest-priority quality risk going into Sprint 2."

---

## Slide 9 — Sprint 2 Direction (20s)
**Visual**: Sprint 2 roadmap card

**Voiceover**:
"Sprint 2 builds on this. We're injecting task-type subtask checklists into the prompt — empirically derived from Ashish's historical orders — to catch procedural milestones. We're also designing the live monitoring system and conducting user research with Ashish and his staff.

The goal by Sprint 2 end: reliable extraction on live data, with Ashish validating in real conditions."

---

## Slide 10 — Wrap-up (10s)
**Visual**: Summary + Sprint 3 target

**Voiceover**:
"Sprint 1 delivered: problem defined, extraction prototype validated on real data, evaluation framework live, and the first model comparison done. Sprint 3 target is full live deployment — Ashish using it. Thank you."

---

**Total estimated runtime**: ~4 min 7 sec

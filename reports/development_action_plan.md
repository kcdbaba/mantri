# Development Action Plan — Post User Research
**Date:** 2026-03-29
**Based on:** user_research_synthesis.md (Ashish Part 1 + Staff session, 2026-03-27)

---

## Immediate (Sprint 3 — by Apr 26)

### 1. Add "Payments group cross-check" as a cadence implicit task
**What**: When a payment screenshot is detected in any supplier or client group, check whether a matching entry (same approximate amount, same timeframe) exists in the Payments group within a configurable window (default: 2 hours). If absent, fire: "Payment to [supplier] ~₹[amount] — Payments group entry not found. Post narration."

**Why**: Ashish confirmed this is missed regularly. It is the most clearly articulated, actionable pain point from the interview. The detection rule is derivable from cross-group message analysis, which Mantri already supports.

**Owner**: Router worker — new cadence rule in `task_type_checklists.txt`

---

### 2. Add emergency local-pickup task type to taxonomy
**What**: New task type in `task_taxonomy.md`. Steps: (1) Direct payment to local shop by Ashish, (2) Screenshot shared with shopkeeper, (3) Client collects locally, (4) **IMPLICIT TASK: create client invoice at marked-up rate** — no message trigger; fires cadence-style on detection of payment + local-shop pattern.

**Why**: Ashish confirmed this causes direct financial loss ("I lose the money"). Zero WhatsApp signal means the obligation is entirely invisible to the current system.

**Owner**: Task taxonomy + linkage worker (new pattern in agent prompt)

---

### 3. Revise clarification policy in all agent prompts
**What**: Replace any "silently carry forward low-ambiguity items" logic with: always flag immediately to Ashish + senior staff (Samitha); continue processing with flagged assumption; block and alarm only at critical irreversible action points (payment / supplier order / delivery).

**Why**: Ashish was emphatic. The prior assumption that noise filtering is a binding constraint is invalidated — false positive tolerance is full.

**Files to update**: Router worker agent prompt, ambiguity worker prompt, any notes in `implementation_design.md`

---

### 4. Implement two-tier alert delivery
**What**:
- **Staff channel**: morning digest (7–8 AM) + evening wrap-up (6–7 PM) + real-time only for critical-urgency items (delivery due today, payment overdue by N days). Use dashboard or dedicated bot contact, not the same noisy WhatsApp groups.
- **Ashish channel**: real-time for high/medium ambiguity and all irreversible action points; batched for routine items.

**Why**: Staff explicitly rejected real-time defaults. A batched model is not a compromise — it is what staff said would make the system usable. Ignoring this will cause staff to tune out alerts.

**Files to update**: `cron_worker` schedule logic; alert routing in ambiguity worker; `live_monitoring_design.md`

---

### 5. Build group whitelist / onboarding UI as the first trust-establishing step
**What**: The first thing a new staff member sees must be: "Select which WhatsApp groups Mantri may read." Personal groups (family, friends) must be excluded and must remain excluded by default. This is not a settings page — it is the onboarding flow.

**Why**: Staff's first spontaneous concern was personal messages being accidentally processed. Ashish resolved it by pointing to the whitelist mechanism. This must be made concrete and visible, not assumed.

**Files to update**: Dashboard design (planned Sprint 3+ feature)

---

## Before Sprint 3 Evaluation

### 6. Measure off-platform task gap baseline
**What**: Ask staff to keep a one-week lightweight log: each time a task is resolved via phone call that is never written back to WhatsApp, make a tick. Count at end of week.

**Why**: Staff confirmed off-platform resolutions happen "almost always daily." Without this baseline, accuracy metrics will be misleadingly pessimistic (Mantri will appear to "miss" tasks that were simply resolved off-channel). This single number is critical for calibrating the evaluation dataset.

**Owner**: Kunal — coordinate with Ashish to instruct staff

---

## Open / Deferred

### 7. Resolve alert delivery channel
**Problem identified but not yet solved**: Staff correctly pointed out that WhatsApp-delivered alerts suffer the same overload problem as WhatsApp messages. No decision has been made on whether to use (a) a separate WhatsApp bot contact that staff treat as high-priority, (b) a dashboard on an office screen, or (c) a lightweight mobile app. This decision must be made before Sprint 3 alert delivery is built.

**Recommendation**: A dedicated WhatsApp bot number (separate from all business groups) combined with a dashboard is the lowest-friction path for the Guwahati office context. Staff already live in WhatsApp; a separate channel from all groups is easy to treat as "the Mantri contact."

---

### 8. Ashish Part 2 — topics not yet covered
From the interview guide, the following are deferred to Part 2:
- Warehouse inventory blindness
- Approval authority structure
- Concurrent same-item order frequency
- Group alias bootstrapping
- Context boundary gap threshold
- Officer alias persistence

Schedule Ashish Part 2 before Apr 12 to feed the Architecture workflow (bootcamp due Apr 12).

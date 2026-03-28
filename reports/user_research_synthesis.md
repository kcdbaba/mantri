# User Research Synthesis — Mantri
**Date:** 2026-03-29
**Interviews:** Ashish Part 1 (2026-03-27, ~44 min), Staff session (2026-03-27, ~67 min)
**Participants:** Ashish Chhabra (business owner), 2 frontline staff (Samita / Moushmi + one other), Ashish also present in staff session

---

## Quality Risk Validation Summary

| Risk hypothesis | Status | Evidence source |
|---|---|---|
| Cadence implicit tasks | **VALIDATED — STRONG** | Ashish: payment logging step 2, local-pickup billing. Staff: simultaneous order phases with no message triggers |
| Staff quality variance | **VALIDATED — MODERATE** | Both staff and Ashish confirmed "both assumed the other handled it" as a daily pattern |
| Missing WhatsApp entries | **VALIDATED — STRONG** | Phone-call resolutions confirmed as daily norm by staff; off-platform billing by Ashish |
| Warehouse inventory blindness | **NOT DISCUSSED** | Neither interview covered this — deferred to Ashish Part 2 |
| Trust threshold | **VALIDATED — with nuance** | Ashish: full false-positive tolerance, zero tolerance for silent drops. Staff: tolerance is conditional on low noise volume |

---

## Cross-Interview Patterns

### 1. Task slip-through is confirmed as a daily operational reality
Both interviews independently validated that tasks fall through gaps every day. The mechanism differs by role:
- **Ashish (owner)**: financial obligations with no message trigger — payment step 2 missed, local-pickup billing forgotten
- **Staff (execution layer)**: single-line action items buried under message floods, cross-assumption failures ("both thought the other would do it")

Neither framed this as occasional or exceptional. Both treated it as a structural feature of the current workflow.

### 2. Off-platform resolutions are the norm, not the exception
Staff confirmed: "This almost always happens daily" — tasks resolved via phone call, verbally reported to Ashish, never written back to WhatsApp. Ashish confirmed a parallel pattern: billing obligations created by direct shopkeeper payments with no WhatsApp signal at all.

**Design implication:** Mantri's task state will be systematically stale for a non-trivial fraction of tasks. The system must tolerate unknown completion states gracefully — not treat every unconfirmed task as an overdue risk within hours.

### 3. Alert design has a real tension between owner and staff preferences
- **Ashish**: wants everything surfaced immediately; "reminders are always appreciated"; false positive tolerance is "full"
- **Staff**: explicitly rejected real-time defaults; want morning digest + evening wrap-up; constant alerts "add more noise"

**Resolution**: Two-tier alert design. Staff receive batched digests (morning + optional mid-day critical-only + evening wrap-up). Ashish receives real-time on high/medium ambiguity and all critical irreversible action points; batched on routine items.

### 4. Never silently drop — confirmed from both sides
Ashish was emphatic: "Hide ambiguity — that is the main point. It has to be highlighted." Staff want help and will act on alerts when they are relevant and sparse. Neither party wants the agent to silently guess and wait for correction. The disagreement is on cadence, not on surfacing.

---

## New Discoveries (not in original hypothesis set)

### ⚠️ Emergency local-pickup payment — systematic off-platform financial loss
Ashish pays a neighbourhood shopkeeper directly for urgent Army client needs, then must bill the client at a marked-up rate. The billing step has zero WhatsApp signal and is frequently forgotten — Ashish confirmed he "loses the money." This is one of the clearest instances of a cadence implicit task found so far and it causes direct financial harm.

### ⚠️ Two-step payment logging — every payment creates a cadence implicit task
Every payment requires (1) screenshot to supplier/client group and (2) narrated screenshot to Payments group. Step 2 is "sometimes missed" — specifically because there is no message that reminds staff to do it. Detection rule: payment screenshot in supplier/client group should trigger a cross-check in the Payments group within ~2 hours.

### ⚠️ Personal phones = business devices — privacy is a concrete product constraint
Staff use personal mobile numbers and personal phones for business WhatsApp. Their first unsolicited concern was: will the AI accidentally process personal family messages? Ashish reassured them that group whitelist/selection resolves this, and staff accepted this. But this must be a prominent, trust-establishing feature in onboarding — not a footnote.

### ⚠️ Staff have no prior experience with business task reminder tools
Staff have only used reminder apps for personal events (birthdays, anniversaries). This is the first time they will interact with any business task alert system. Mantri sets the baseline expectation — first impressions and initial accuracy matter more than in a market where users have alternatives to compare.

### ⚠️ "Both assumed the other handled it" — invisible to message-level analysis
This coordination failure leaves no message trace. It can only be inferred from the absence of acknowledgment over time. Mantri must detect stale unacknowledged tasks by time-based escalation, not by reading a message.

---

## Assumption Updates

| Prior assumption | New status |
|---|---|
| False positives erode trust rapidly — must filter aggressively | CHALLENGED. Ashish: full tolerance. Staff: tolerance is real but bounded by noise volume. Shift design priority from precision to recall on owner channel. |
| Low-ambiguity items can be silently carried forward with best guess | REVISED. Always flag immediately. Block and alarm only at irreversible action points (payment / supplier order / delivery). |
| Static role → staff mapping can drive task routing | INVALIDATED. No pre-declared staff roles. Routing must use chat engagement signals, not declared ownership. |
| Real-time alerts are better than batched for staff | INVALIDATED. Staff explicitly prefer morning digest + evening wrap-up. Real-time only for critical/urgent. |

---

## Direct Quotes

**Ashish — on false positive tolerance:**
> "I have full tolerance. Reminders are always appreciated. I will just — it is a done task — I will put a tick and move on." [Ashish, 00:37:10]

**Ashish — on silent ambiguity:**
> "Hide ambiguity — that is the main point. It has to be highlighted." [Ashish, 00:40:18]

**Ashish — on off-platform financial loss:**
> "I made the payment down the line. I forget. And then I lose the money because they will never remind me." [Ashish, 00:14:32]

**Staff — on message overload:**
> "It gets overshadowed — so many come back-to-back that we forget — and later that's the one that comes back to bite us." [Staff session, 00:39:03]

**Staff — on preferred alert cadence:**
> "Do it two or three times a day — in the morning when we open [the app], see what's ours for today." [Staff session, 00:43:01]

**Staff — on off-platform resolutions:**
> "This almost always happens daily." [Staff session, 00:51:27]

---

## Open Questions (for Ashish Part 2)

1. Warehouse inventory blindness — does stock-on-hand tracking exist? How are warehouse items allocated?
2. Approval authority — who can authorise a supplier order vs. payment?
3. Concurrent same-item orders — how often does the same item appear in multiple open orders simultaneously?
4. Group alias bootstrapping — how does a new staff member learn which WhatsApp group maps to which client/supplier?
5. Context boundary gaps — how does the agent know when one order ends and a new one begins in a running group chat?

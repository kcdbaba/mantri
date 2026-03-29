# User Research Synthesis — Mantri
**Date:** 2026-03-29 (updated from definitive Sarvam Roman transcripts)
**Interviews:** Ashish Part 1 (2026-03-27, ~44 min), Staff session (2026-03-27, ~67 min)
**Participants:** Ashish Chhabra (business owner), 2 frontline staff (Smita / Moushmi), Ashish also present in staff session

---

## Speaker Key

**Ashish Part 1 (evening interview):**
- Speaker 1 = Kunal (interviewer)
- Speaker 2 = Ashish

**Staff session (morning):**
- Speaker 1 = Kunal (interviewer/presenter)
- Speaker 0 = Ashish (present in room)
- Speaker 2 = Staff (Smita, senior; Moushmi, junior)

---

## 1. Quality Risk Validation Table

| Risk hypothesis | Status | Evidence |
|---|---|---|
| Cadence implicit tasks | **VALIDATED — STRONG** | Staff confirmed off-platform resolution (phone calls, in-person) as constant: "Ye hamesha hi hota hai sir." [Staff ~00:51:25]. Ashish's ad-hoc payment flow creates invisible financial obligations with zero WhatsApp signal. |
| Staff quality variance | **VALIDATED** | Cross-assumption failure confirmed with exact mechanism: "Hum agar ek miss kar gaye toh dusra banda dekh sakta hai toh ye sab mein ho jata hai ki haan vo bhi toh dekh sakta tha." [Staff ~00:40:14] |
| Missing WhatsApp entries | **VALIDATED — STRONG** | Off-platform resolution is the default for intermediate steps, not an edge case. Completion reports almost never go back to WhatsApp — only physical deliveries do. [Staff ~00:51:44] |
| Warehouse inventory blindness | **NOT DISCUSSED** | Neither interview covered stock levels or inventory allocation. Deferred to Ashish Part 2. |
| Trust threshold | **VALIDATED — HIGH TOLERANCE** | Ashish: "I have full tolerance. Reminders are always appreciated." Explicit threshold: "If it is less than 5% of the whole deal, I'm okay with that." [Ashish ~00:37:10, ~00:38:28] |

---

## 2. Cross-Interview Patterns

### 1. Volume overload as root cause of all slip-ups

Both interviews independently described simultaneous multi-group WhatsApp traffic as the primary mechanism of failure. Tasks are not forgotten because people are careless — they are forgotten because a higher-priority item arrives in the window between reading and acting.

Smita (~00:38:50–00:39:05, Staff session):
> "Hum ye second group check karte karte first mein aisa koi message aa gaya jo hum miss kar jaate hain. Toh vo overtake ho jata hai phir uske baad back to back bahut sare aa jaate hain toh kabhi hum vo bhul jaate hain aur vahi jaake baad mein humein hit karta hai."

Ashish (~00:32:33, Ashish Part 1):
> "Even if the client is not asking me to chase the order, the inquiry has to be closed today. We can't keep it pending for tomorrow."

### 2. Notebooks + Excel as primary task tracking — a real system that breaks under load

A structured, colour-coded shared Excel task sheet already exists (green=done, orange=in-progress, red=urgent) and is actively maintained. This is not a chaotic operation. But under peak load the notebook-to-Excel transfer breaks down and tasks get lost even after being written down.

Ashish (~00:35:08, Staff session):
> "Again when the load is too high this transfer gets messed up. I will not lie about it, it does get messed up."

Staff confirmed (~00:41:20): "Haan, hota hai sab bhul jaate hain matlab."

**Design implication:** Mantri enters a context where structure already exists. Frame it as augmenting the existing system, not replacing it.

### 3. Off-platform resolution is pervasive and systematic

Appears independently in both sessions. Intermediate task resolution via phone call is the default; WhatsApp is for initiation and delivery confirmation only. The gap between "task assigned on WhatsApp" and "completion known to system" is large and structural.

Smita (~00:51:44, Staff session):
> "WhatsApp mein completion report maximally nahi hota. Bas hum delivery jab karte hain, tab hum completion report dete hain."

**Design implication:** The agent will have systematically incomplete completion data. Never treat unconfirmed tasks as overdue within hours. Time-based staleness escalation must use a generous window, not a tight one.

### 4. Alert design has a real tension between owner and staff preferences

- **Ashish**: wants everything surfaced immediately; "Reminders are always appreciated"; full false-positive tolerance
- **Staff**: explicitly rejected real-time defaults; want morning digest + evening wrap-up; immediate pings during work hours get crowded out by higher-priority items arriving at the same moment

**Resolution:** Two-tier alert design. Staff receive morning + evening batched digests (critical-only interstitial available). Ashish receives real-time on medium+ ambiguity and all irreversible action points; batched on routine items.

### 5. Never silently drop — confirmed from both sides

Ashish was emphatic. Staff will act on alerts when they are sparse and relevant. The disagreement is on cadence, not on surfacing.

---

## 3. New Discoveries

### ⚠️ Discovery A: "Client Wallet" — ad-hoc payment creates invisible financial obligation

Ashish pays a neighbourhood shopkeeper directly for urgent army client needs, then must: (1) get the shopkeeper's bill in Ashish's name, (2) create his own bill in the client's name with markup, (3) credit the client's account. The billing step has zero WhatsApp signal and is frequently forgotten.

Ashish (~00:16:16, Ashish Part 1):
> "Because we were also in a rush and chaos, this payment that has been transferred is overlooked to be entered into the book of accounts for that client by us."

He named this a "client wallet" (~00:20:11) — a ledger/credit account model. Direct financial loss confirmed: "I have made the payment. I forget. I lose the money."

### ⚠️ Discovery B: Two-step payment logging protocol — systematic failure point

Every payment has a designed two-step log protocol:
1. Screenshot in the supplier/client group (usually done)
2. Same screenshot + narration ("Kunal paid ₹10,000 on account of…") in the dedicated **payments group** (frequently missed under pressure)

Step 2 is what enables WhatsApp search to surface payments by name. When it is skipped the payment effectively disappears during reconciliation. Ashish discovered a ₹495 January payment months later by random search.

Ashish (~00:22:39–00:23:00, Ashish Part 1):
> "So I just randomly searched in my WhatsApp the whole log that exponential payments. Then I could identify in a different group, in a task group, I have made that payment wala entry which is not reflected there."

**Design implication:** The agent must recognise the same payment screenshot appearing in two groups as one event. A payment screenshot in a supplier/client group with no matching entry in the payments group within ~2 hours should trigger an alert.

### ⚠️ Discovery C: Supplier business hours boundary is exactly 7:00 PM

Anything sent to a supplier after 7:00 PM should not expect a response until 10:30 AM next day. This is a concrete parameter for "unresponsive supplier" alert logic.

Ashish (~00:34:30–00:34:43, Ashish Part 1):
> "Anything post 7:00 in the evening, you can't expect any answers coming your way... I kept a window of 24 hours. And if the inquiry is made today itself within a decent working time good enough."

### ⚠️ Discovery D: Two hard agent constraints — pricing and disputes are human-only

Even in a future agentic mode, the agent must NEVER:
1. Disclose pricing or rate codes to clients
2. Comment on or respond to disputed messages — Ashish only

These are business-critical controls protecting Ashish's competitive edge (pricing) and legal/relationship risk (disputes). [Ashish ~00:35:37–00:36:44]

### ⚠️ Discovery E: Personal privacy on personal phones is staff's #1 concern

Staff's first unsolicited concern: will the AI accidentally read personal family messages on their personal phones? Business WhatsApp groups exist on the same personal device. This is an adoption blocker.

Smita (~00:56:41–00:56:55, Staff session):
> "Toh officially aur jo apna personal message ye mix ho ke koi issue na aa jaaye toh vo vo sabse zyada concern vali baat hai sir."

Dedicated-SIM idea was floated but rejected due to loss of historical chat history. Group whitelist/selection is the working resolution — must be prominent in onboarding, not a footnote.

### ⚠️ Discovery F: Amazon/online marketplace orders are invisible to the agent

Ashish mentioned a case where a client was chasing delivery status and Ashish had no tracking information:
> "If I don't have such updates from my supplier, then how will I update my client?" [~00:33:00, Ashish Part 1]

The SATA prototype flagged "21 days with no WhatsApp communication on an Amazon order" as anomalous (~00:22:03, Staff session). Online orders create a gap between client expectations and Ashish's ability to respond that WhatsApp monitoring alone cannot bridge.

### ⚠️ Discovery G: Staff want relief from guilt, not just efficiency

Staff expressed personal responsibility concern — they don't want to be the cause of company problems. Motivation is not just efficiency but psychological safety.

Smita (~00:53:53–00:54:14, Staff session):
> "Genuinely agar hum bhi matlab as a staff member bhi hum bhi yah nahi chahte ki company humare vajah se kuch problem mein aaye."

> "Toh ye miss karne ka problem solve ho jaaye toh ye toh isse better news toh kuch ho hi nahi sakta sir."

---

## 4. Assumption Updates

| Prior assumption | New status |
|---|---|
| Agent should wait and watch on ambiguity, resolve from future messages | **INVALIDATED.** Ashish explicitly rejected this: "Make it faster. Make it faster." [~00:40:49] Medium+ ambiguity must be escalated immediately. Low ambiguity goes to senior staff (Smita). Never silently hold. |
| False positives erode trust — filter aggressively | **REVISED.** Ashish: full tolerance, 5% error threshold accepted. Design should err on over-alerting, not under. Staff tolerance is real but bounded by noise volume — hence digest rhythm for staff. |
| Staff assignment needs to be pre-defined per task type | **PARTIAL.** Most tasks are ad-hoc assigned based on current workload. Only specialist/repair tasks can be pre-assigned. Agent should not enforce fixed assignment for generic tasks. |
| WhatsApp is primary channel for all coordination including completion | **REVISED.** WhatsApp is primary for task initiation; off-platform resolution (phone/in-person) is the dominant path for intermediate steps. Completion reports almost never return to WhatsApp. |
| The business has minimal existing task tracking infrastructure | **INVALIDATED.** A structured, colour-coded shared Excel task sheet exists and is actively maintained. Mantri augments this, it does not replace it. |
| Task deduplication is a data hygiene problem | **CONFIRMED AND EXPANDED.** The two-step payment protocol means the same screenshot legitimately appears in two groups and must be recognised as one event. |
| Real-time alerts are better than batched for staff | **INVALIDATED.** Staff explicitly prefer morning + evening digest. Real-time only for critical/urgent. |

---

## 5. Direct Quotes

**Ashish — full false-positive tolerance [Ashish Part 1, ~00:37:10]:**
> "I have full tolerance. It's a simple thing that you have reminded. Reminders are always appreciated. I will just, it is a done task. I will put a tick and move on, rather than not being reminded of something the AI agent thinks that maybe and maybe not. Wherever there is a maybe question, just remind me."

**Ashish — rejecting wait-and-see ambiguity design [Ashish Part 1, ~00:40:49]:**
> "Make it faster. Make it faster."

**Ashish — ambiguity routing rule [Ashish Part 1, ~00:38:46]:**
> "Everything which is above medium or medium and above higher risk rating must be directly prompted to me. Otherwise anything low and up to medium might be just asked by the senior staff member."

**Ashish — on silent ambiguity [Ashish Part 1, ~00:40:18]:**
> "It has to be highlighted."

**Ashish — client wallet payment loss [Ashish Part 1, ~00:16:16]:**
> "Because we were also in a rush and chaos, this payment that has been transferred is overlooked to be entered into the book of accounts for that client by us."

**Smita — the core missed-message mechanism [Staff session, ~00:38:50]:**
> "Hum ye second group check karte karte first mein aisa koi message aa gaya jo hum miss kar jaate hain. Toh vo overtake ho jata hai phir uske baad back to back bahut sare aa jaate hain toh kabhi hum vo bhul jaate hain aur vahi jaake baad mein humein hit karta hai."

**Smita — "both thought the other handled it" [Staff session, ~00:40:14]:**
> "Hum agar ek miss kar gaye toh dusra banda dekh sakta hai toh ye sab mein ho jata hai ki haan vo bhi toh dekh sakta tha ya dekh sakti thi."

**Smita — off-platform resolution is constant [Staff session, ~00:51:25]:**
> "Ye hamesha hi hota hai sir, ye hamesha hi lagbhag regularly hota hai... WhatsApp mein completion report maximally nahi hota. Bas hum delivery jab karte hain, tab hum completion report dete hain."

**Smita — staff's core motivation [Staff session, ~00:53:53]:**
> "Toh ye miss karne ka problem solve ho jaaye toh ye toh isse better news toh kuch ho hi nahi sakta sir."

**Smita — personal privacy concern [Staff session, ~00:56:41]:**
> "Toh officially aur jo apna personal message ye mix ho ke koi issue na aa jaaye toh vo vo sabse zyada concern vali baat hai sir."

---

## 6. Open Questions for Ashish Part 2

### Operations gaps
1. **Off-platform volume estimate** — roughly what percentage of tasks each day are resolved entirely by phone/in-person with no WhatsApp update? Need a rough number to size the invisible-work problem.
2. **Completion loop mechanism** — would staff accept posting a brief WhatsApp update for off-platform resolutions? Or is there a lighter mechanism (dashboard button) that would work?
3. **Amazon/online order tracking** — how does Ashish currently get delivery tracking for marketplace orders? Could the agent be pointed at an email inbox or order confirmation thread?
4. **Inventory / warehouse** — does Ashish hold physical stock? What triggers a stock check? Where are stock levels recorded?

### Process design
5. **Client wallet end-to-end checklist** — what are all the follow-up steps after an ad-hoc shopkeeper payment? Which steps fail most often?
6. **Payments group ownership** — is there a designated person responsible for the step-2 payments group narration, or does Ashish handle it personally? How far behind is it typically?
7. **Dispute workflow** — what does a typical disputed message look like and what is the current escalation path?

### Agent design
8. **Two-digest model** — Ashish operates remotely on different hours. Does he want real-time escalations separate from the digest rhythm, or batched separately by priority?
9. **Dedicated SIM feasibility** — what would it take to migrate key WhatsApp groups to a second business number? Which groups are too personal/mixed to move?
10. **Supplier hours configuration** — are there supplier-specific exceptions to the 7 PM / 10:30 AM window?
11. **Smita as escalation target for low-ambiguity items** — what is the fallback when Smita is unavailable? Is there a formal staff hierarchy?

---

## Top 3 Design Actions (from this synthesis)

1. **Ambiguity routing must escalate immediately** — the wait-and-see design is explicitly rejected. Medium+ ambiguity → Ashish, real-time. Low ambiguity → Smita. No silent holding.

2. **Cross-group payment deduplication is a named, financially costly pain point** — the agent must recognise the same payment screenshot in two groups as one event; flag orphaned payments (supplier group only, no payments group match) within ~2 hours.

3. **Off-platform resolution is the dominant workflow gap** — the agent structurally has incomplete completion data. Design must tolerate unknown completion states gracefully and provide a low-friction mechanism for staff to close the loop from off-platform resolutions.

# User Research Plan

**Status**: Draft
**Sprint**: Sprint 2
**Date**: 2026-03-26

---

## Quality Risk Focus for User Research

### Current Quality Risk

The primary unresolved quality risk is **cadence/procedural implicit task detection** — the agent correctly infers reactive implicit tasks (supplier silence → follow-up) but misses tasks that should happen at a certain stage of any order regardless of what is in the messages (pre-dispatch checklist, post-delivery payment confirmation, monthly reconciliation). Validated by Sprint 1 SATA case scoring (89/100 on implicit task detection, with both failures being procedural rather than reactive).

The secondary risk cluster is **trust and adoption** — whether staff will trust the agent enough to act on its alerts, and whether Ashish will trust it enough to rely on it for operational decisions.

### Why User Validation

The following assumptions can only be validated by talking to real users:

1. **Task type taxonomy**: which order types have distinct enough workflows to warrant separate templates — and what the expected subtask sequence actually is for each type
2. **Acceptable vs unacceptable errors**: which failure modes Ashish would catch manually vs rely on the agent for — determines the quality bar
3. **Trust threshold**: at what confidence level is Ashish comfortable with the agent acting without confirmation — determines where human review gates go
4. **Staff quality variance**: which staff/subtask combinations fail most often — determines risk weights and escalation thresholds
5. **Missing WhatsApp entries**: how often tasks proceed entirely off-platform — determines whether gap detection alerts would be useful or noise
6. **Warehouse inventory**: which items are pre-stocked and how reliably staff log stock fulfilments — determines whether passive or active inventory integration is needed

---

## Interview Plan

### Target Users for Research

**Two distinct groups with different research goals:**

| Group | Who | Size | Goal |
|---|---|---|---|
| **Staff** | Samita Roy + other active staff members | All available | UX and trust validation; surface ground-level friction and concerns |
| **Ashish Chhabra** | Business owner | 1 | Business process design; task taxonomy; architecture decisions as customer |

Staff and Ashish should be interviewed separately. Staff in a group walkthrough session; Ashish in a dedicated 1:1 design session.

### Recruitment Approach

**Lead Warmth: Hot | Setup Effort: Low**

Both groups are existing collaborators — no cold outreach required. Ashish is already engaged in the project and has been supplying chat logs. Staff are already part of Ashish's operation.

Coordination approach:
- Ask Ashish to gather available staff for a 45-minute group session
- Schedule a separate 90-minute session with Ashish alone for design discussion
- Use fixed time buckets per topic to prevent meander — especially for task taxonomy

### Value Exchange

**For staff**: The agent is being built to reduce their workload, not add to it. The session gives them a direct say in how it behaves — specifically around noise levels, privacy (whitelisted chats only), and the ability to override any task details. Their input directly shapes what gets built.

**For Ashish**: Builds confidence in the solution. Gets to prioritise what matters most to his business before Sprint 3 builds it. Becomes a co-designer rather than just a validator. Edge cases he identifies get recorded and included in the task taxonomy documentation — his domain knowledge is preserved, not lost.

### Session Format

| Session | Duration | Participants | Format |
|---|---|---|---|
| Staff walkthrough + interview | 45 min | All available staff | Group walkthrough of agent output (brief) → UX demo → structured interview |
| Ashish design session | 90 min | Ashish only | Full agent output walkthrough → UX questions → business process + design discussion |

---

## Conversation Guide: Staff Session

### Purpose
Validate UX and trust assumptions. Surface ground-level friction, concerns, and workflow patterns the agent needs to respect.

### Framing (5 min)
Open with:
- "We're building something to help reduce the follow-up burden on you — not to monitor or replace you."
- "We only read the WhatsApp groups we're given explicit permission for. Nothing else."
- "The agent's job is to send you helpful reminders when something needs attention — it won't spam you with updates you don't need."
- "You'll always be able to override anything it suggests — task status, details, priority."
- "Today we want to show you what it does and hear what concerns you have."

### Part 1: Brief Agent Output Walkthrough (10 min)
Walk through the SATA case agent output at a high level:
- Show a sample parent task with subtasks
- Show a sample ambiguity flag
- Show a sample implicit task that was detected

Listen for: confusion, scepticism, recognition ("yes this happens"), surprise

### Part 2: UX and Trust Questions (25 min)

**Current workflow:**
- "When Ashish assigns you a task over WhatsApp, how do you currently keep track of it?"
- "What do you do when you're waiting on a supplier and they haven't replied — how do you remember to follow up?"
- "Walk me through the last time a task slipped through — what happened?"

**Listen for:** manual tracking methods (notes, memory, pinned messages), patterns in what gets dropped, time pressure as a factor

**Pain points:**
- "What part of coordinating orders takes most of your time?"
- "When do you feel most overwhelmed — what's happening when that is?"
- "Is there something you have to do repeatedly that feels like it should be automatic?"

**Listen for:** follow-up fatigue, context-switching, information gaps between groups

**Trust and concerns:**
- "If something like this sent you a reminder about a task — what would make you trust it enough to act on it?"
- "What would make you ignore it or switch it off?"
- "Are there situations where you'd want to tell it 'I know, I'm handling it' — and have it back off?"

**Listen for:** false alarm fatigue, desire for control, scenarios where agent intrusion would be unwelcome

**Agent clarification and ambiguity** *(Router design Q15 — staff willingness to resolve ambiguity)*:
- "Sometimes the agent might not be sure which order a message is about — for example, if someone says 'maal ready hai' without naming the order. If it sent you a quick question — 'is this about the SATA order or the Eastern Command order?' — would that feel okay or annoying?"
- "If it asked you to clarify once or twice a day, would that be manageable? What would make it feel like too much?"
- "Would you prefer those kinds of questions to come to you directly, or should they go to Ashish or a senior team member?"
- "What channel would feel least disruptive — a separate WhatsApp group for the agent, a dashboard, a direct message?"
- "Is there a situation where you'd want to say 'I don't know — ask Ashish'?"

**Listen for:** willingness threshold, preferred channel, preference to escalate to Ashish rather than resolve themselves, situations where staff feel unqualified to answer

**Privacy:**
- "Are there any chats or conversations you'd be uncomfortable with the agent reading?"
- "If the agent only had access to the groups Ashish explicitly whitelists, does that feel okay?"

**Listen for:** concerns about personal chats, supplier relationship sensitivity, salary/HR discussions

### Closing (5 min)
- "Is there anything about how you work that we should understand before we build this?"
- "What's the one thing that would make you actually use this?"

---

## Conversation Guide: Ashish Session

### Purpose
Validate task taxonomy, business process design, and architecture decisions. Ashish is both a user and a customer — frame accordingly.

### Framing (5 min)
- "Today is about making sure what we build matches how your business actually works."
- "We'll walk through what the agent produced on the SATA case — get your reaction."
- "Then we'll go through each major order type to map the expected workflow. We'll use fixed time boxes so we don't go too deep on any one type."
- "If edge cases come up that we don't have time for, we'll capture them separately — they're valuable and we want them in the documentation."

### Part 1: Full Agent Output Walkthrough — SATA Case (20 min)
Walk through the full SATA agent output with Ashish:
- Does the task list match what he would expect?
- Are there tasks the agent identified that aren't real?
- Are there tasks it missed that he would have caught?
- Are the suggested next steps correct for how his business works?
- Is the priority/urgency assessment right?

**Specific questions:**
- "Here the agent flagged [X] as high priority — is that accurate?"
- "The agent suggested [next step Y] — is that what you'd actually do?"
- "The agent missed a pre-dispatch checklist before the vehicle was sent — is that something you normally do, or does Abhisha handle it informally?"

**Listen for:** calibration on quality bar, implicit procedures not visible in chat, trust signals

### Part 2: UX and Trust (15 min)
Same trust questions as staff, plus:
- "At what point would you be comfortable with the agent flagging a supplier as unresponsive without you approving it — 24 hours? 48?"
- "Are there task types where you'd want the agent to never act automatically — always ask you first?"
- "What would a false positive look like — the agent alerting on something that turned out to be fine — and how much tolerance do you have for those?"
- "If the agent associated a message with the wrong order — for example, it linked a delivery update to the SATA tandoor when it was actually about the Eastern Command AC — how quickly would you catch that? Would you want it to flag when it's unsure, or is it better to let it try silently and you correct it later?" *(Router design Q2 — wrong-routing tolerance vs false-routing visibility)*
- "When the agent isn't sure which order a vague message belongs to, would you prefer it makes its best guess and flags the uncertainty to you, or asks you to confirm before doing anything? At what point does being asked to clarify become more annoying than helpful?" *(Router design Q11 — clarification vs carry-forward policy)*

### Part 3: Task Taxonomy — Fixed Time Buckets (40 min)

**Ground rules**: 5 minutes per task type. Edge cases get captured in a running list, not discussed in session.

For each task type, ask:
1. "Walk me through this order type from first message to final payment — what are the key steps?"
2. "Which of those steps most often get skipped or delayed?"
3. "Which steps does staff handle vs you personally?"
4. "Are there steps that always happen but never appear in WhatsApp?"

**Task types to cover** (5 min each, 8 types = 40 min):

| # | Task Type | Key question |
|---|---|---|
| 1 | Standard equipment procurement (e.g. tandoor, AC) | What's the standard flow from enquiry to payment? |
| 2 | Custom/made-to-order items (e.g. flags, signage) | How does spec confirmation work? What's the rejection risk? |
| 3 | Army direct delivery (client picks up or Army arranges transport) | How does this differ from standard delivery? |
| 4 | GeM portal orders | What's the portal-specific workflow? Where does it break? |
| 5 | Inter-state procurement (Malerkotla, Delhi suppliers) | What additional steps does distance add? |
| 6 | Stock-filled orders (items from warehouse) | How do you and staff signal that stock is being used? |
| 7 | Service orders (repairs, maintenance) | Does this follow the same workflow as goods? |
| 8 | Collections and payment follow-up | When and how do you chase Army clients vs civilian clients? |
| 9 | Proactive client engagement | Which clients do you proactively call? How often? What triggers it? |
| 10 | Post-delivery feedback | Do you follow up after every order? When, and how? |
| 11 | **Period-end administration** | **See dedicated questions below — this type needs the most validation** |

**Type 11 — Period-End Administration (dedicated 10 min):**

This is the task type we know least about and that is most likely to be handled outside of WhatsApp entirely. The goal is to understand what Ashish or his CA actually does at each period end, so the agent surfaces the right reminders at the right time.

- "At the end of each month, what financial tasks do you personally do — or does your CA do?"
- "Do you do a stock count periodically? When — end of year, end of each quarter, or something else?"
- "Is there a weekly routine — like reviewing which invoices are overdue, or which orders are still open?"
- "Do you use Tally or any accounting software? If yes, which tasks does it handle — does the agent need to worry about those?"
- "For GST — do you file yourself or through a CA? When in the month?"
- "Are there any Army-supply-specific compliance tasks on a calendar — GeM portal renewals, MSME filings, anything like that?"
- "Is there anything you consistently have to do at year-end (March 31) that a reminder would help with?"

*Capture the actual list of tasks and their frequency — this becomes the Type 11 step list in `task_taxonomy.md`.*

**Edge case capture**: keep a running note during the session. After each task type: "Any edge cases we should know about? I'll note them — we won't go deep now but I'll follow up."

### Part 3b: Unvalidated Parameters — Implicit Task Thresholds (15 min)

These parameters directly affect when and how the agent fires implicit tasks. They are currently hardcoded as assumptions. Getting Ashish's input here is the most technically impactful part of the session — wrong thresholds generate noise (false positives) or silence (missed tasks).

**Ground rule**: there are no right answers — these are about what feels operationally right for his specific business.

**Pre-emptive supplier reminder:**
- "If a supplier promises delivery on a specific date — say, 5 days from now — would you want a reminder to check in with them the day before? Or the morning of the day itself? Or both?"
- "Does your answer change depending on the supplier? For example, would you treat a reliable local supplier differently from a first-time inter-state supplier?"
- "How many reminders, and when?"

**Conservative internal deadline buffer:**
- "When a supplier gives you a delivery date, do you mentally add a buffer before you commit that date to a client or staff? How many days?"
- "Does this vary by supplier — e.g., do you trust Malerkotla suppliers less than local ones on timelines?"
- "If the agent set an internal 'expected by' date that was earlier than the supplier's committed date, would that be helpful or confusing?"

**Proactive client outreach:**
- "After you close an order with a commercial client — payment received, everything done — how long do you typically wait before checking in with them again for new business?"
- "Is this different for Army clients vs commercial clients?"
- "Would you want the agent to remind you to reach out if a client has been silent for a certain period? How long?"

**Post-delivery feedback:**
- "After a client confirms delivery and pays, do you typically follow up with them to check satisfaction? When — same day, next day, a week later?"
- "Is this a WhatsApp message or a phone call? Does the client expect it?"
- "Which clients would you always do this for, and which would you skip?"

**Invoice financing / cash flow flag:**
- "If you have a large order — say above Rs 1 lakh — where you have to pay the supplier upfront but the client (especially Army) pays in 60-90 days, do you have a financing mechanism you use? Bank OD? Bill discounting? Something else?"
- "At what order size do you start worrying about the cash flow gap?"
- "Would you want the agent to flag this automatically, or is it something you always keep in mind yourself?"

**Army client payment follow-up:**
- "For Army clients with overdue invoices — when would you personally reach out, and who would you contact? The unit CO? Accounts section? Someone else?"
- "Is there a point at which you'd escalate beyond a courtesy reminder — and if so, what does that look like?"

**Army officer contacts** *(Router design Q10 — officer alias persistence across postings)*:
- "When you deal with an Army unit, you often know the specific officer — the CO or the accounts officer. How long does a typical officer stay at a posting before they're transferred?"
- "When the contact changes, how quickly does your team get updated? Do you update records, or does it just get known informally?"
- "Has there been a case where a message or order got delayed because you were still dealing with a transferred officer? Does the agent need to worry about this?"

**Concurrent same-item orders** *(Router design Q12 — item type collision)*:
- "How often do you have two active orders for the same type of item at the same time — say, tandoors for two different clients, or AC units for two Army units?"
- "When that happens, how do you and your staff keep track of which order is which? Is there a risk of confusion internally?"

*Capture answers verbatim or as close as possible. These become the threshold parameters in the agent's implicit task detection rules.*

---

### Part 4: Design Questions (10 min)

- "The agent currently produces a full task list after reading a batch of messages. In the future, we'd like it to update individual tasks as new messages arrive — does that match how you'd want to use it?"
- "If the agent knew which items you typically keep in stock, it could skip the supplier step automatically. Would you be willing to give it a basic inventory list?"
- "Staff members handle different tasks with different reliability. Would you be comfortable with the agent being more aggressive about follow-up reminders for certain task types — without surfacing the reason why?"
- "How would you want to tell the agent it's wrong — if it flags something that's already handled, what's the easiest way for you to correct it?"
- "When you or staff mention an order in the All-Staff Group or a supplier group, do you typically refer to it by the client group name ('Eastern Command group ka order') or by the supplier group name ('Kapoor Steel group wala') or something else?" *(Router design Q14: determines whether cross-group order references are treated as client-group proxies or supplier entity matches)*
- "In your WhatsApp groups, how quickly does the conversation shift between different orders in a single day? In the All-Staff Group, for example — would you say you discuss several different orders in quick succession, or does each day's activity tend to focus on one or two things at a time?" *(Router design Q6 — context boundary gap threshold: helps calibrate how long a gap in a group before the agent treats it as a topic change)*
- "If the agent read your last 6 months of chat history, do you think it would pick up the shorthand your team uses — like 'army wale group ka maal' or 'Kapoor ji ka'? Or are there references that would only make sense if you explained the background?" *(Router design Q13 — group alias bootstrapping: whether historical extraction is sufficient to seed routing context or needs manual seeding)*
- "Sometimes a message will be genuinely unclear — the agent can't tell which order it's about, and neither the next few messages nor the chat context resolves it. Our current plan is to quietly drop that piece of information after a few messages, on the assumption that if it really mattered, the conversation would have clarified it. Would that feel okay to you? Or would you rather the agent ask you or your staff to clarify — even if it means occasional interruptions?" *(Router design Q16 — silent expiry vs clarification: if expiry is not acceptable, a clarification UI with chat snippets becomes a Sprint 3 feature)*

### Closing (5 min)
- "What's the one thing you most want this to do well that we haven't covered today?"
- "What would make you confident enough to rely on this in a busy week?"

---

## Edge Case Capture Process

During both sessions, edge cases that arise but can't be addressed in the time box should be:
1. Noted in a running list during the session
2. Sent to Ashish after the session as a voice note or WhatsApp message prompt: "Here are the edge cases we noted — can you add any context when you have a moment?"
3. Incorporated into the task taxonomy documentation as they arrive

This respects the time constraint while ensuring Ashish's domain knowledge is captured incrementally rather than lost.

---

## Quality Risk Signals to Listen For

During both sessions, watch for signals that validate or disprove the primary risks:

| Risk | Signal that validates it | Signal that disproves it |
|---|---|---|
| Cadence implicit tasks missed | Ashish mentions steps that "always happen" but are never in WhatsApp | Everything important is in WhatsApp |
| Staff quality variance | Staff mention tasks they "always have to be reminded about" | Staff say they track everything fine |
| Missing WhatsApp entries | "We sorted that on the phone" / "I told her in person" | All coordination happens in chat |
| Inventory blindness | "That one we always have in stock" said casually | Everything is ordered fresh |
| Trust threshold too low | "I'd want to approve everything" | "Just do it, I'll check later" |
| Off-platform instruction gap | "I told [staff] to handle it" with no WhatsApp follow-up | Instructions always go into group |

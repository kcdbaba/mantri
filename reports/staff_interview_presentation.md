# Staff Interview: Presentation & Script
**Session**: Group walkthrough + structured interview
**Audience**: Ashish's staff (Samita Roy + others)
**Facilitator**: Kunal (remote, via Google Meet screen share)
**Co-present**: Ashish (in room with staff)
**Duration**: 45 minutes
**Date**: TBD

---

## SESSION AGENDA

| # | Section | Duration |
|---|---|---|
| 1 | Introduction & Background | 3 min |
| 2 | Purpose & Values | 3 min |
| 3 | UX Walkthrough | 8 min |
| 4 | SATA Agent Output Walkthrough | 7 min |
| 5 | Intermediate Q&A | 5 min |
| 6 | Trust Building | 4 min |
| 7 | Interview Questions | 10 min |
| 8 | Final Q&A + Conclusion | 5 min |
| **Total** | | **45 min** |

---

---

## SLIDE 1 — TITLE

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                                                             │
│            MANTRI — Operations Assistant                    │
│                                                             │
│         A conversation about how we work together          │
│                                                             │
│                                                             │
│   Kunal Chowdhury          Ashish Chhabra                  │
│   [City]                   Guwahati                        │
│                                                             │
│                        [Date]                              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Namaste everyone. My name is Kunal. I'm working with Ashish on a software project called Mantri — which means assistant or advisor. I'm joining you remotely from [city] today.

Thank you for making the time. This session is about 45 minutes. We're going to show you what we've been building, walk through a real example, and then we really want to hear from you — your thoughts, your concerns, and your ideas.

Nothing here is fixed yet. Your feedback today will directly shape what gets built."

---

---

## SLIDE 2 — THE PROBLEM WE'RE SOLVING

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   The challenge                                             │
│   ─────────────                                             │
│                                                             │
│   Ashish manages operations from [city]                    │
│                                                             │
│   100s of WhatsApp messages across                         │
│   multiple groups every day                                 │
│                                                             │
│   Tasks get missed.  Follow-ups are forgotten.             │
│   Updates stay in 1:1 chats and never reach the group.     │
│                                                             │
│   Not because anyone is careless —                         │
│   because the volume is too high for any person to track.  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"You all know how this business works. There are orders coming in from multiple Army clients. Suppliers to follow up with. Deliveries to coordinate. Payments to track.

All of this happens across many WhatsApp groups simultaneously — and Ashish is watching all of it from another city.

The honest truth is: when there are hundreds of messages a day across ten groups, things slip. Not because anyone isn't doing their job. But because no single person — not Ashish, not any of you — can hold all of it in their head at once.

That's what we're trying to solve."

---

---

## SLIDE 3 — WHAT MANTRI DOES

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Mantri reads your WhatsApp groups                        │
│   and creates a task list                                   │
│                                                             │
│   ┌──────────────┐                    ┌─────────────────┐  │
│   │  WhatsApp    │                    │   Task List     │  │
│   │  Groups      │  ──── Mantri ────► │   + Next Steps  │  │
│   │              │                    │   + Reminders   │  │
│   │  [multiple]  │                    │   + Flags       │  │
│   └──────────────┘                    └─────────────────┘  │
│                                                             │
│   It does NOT send messages.                               │
│   It does NOT make decisions.                              │
│   It surfaces what needs attention — you decide what to do.│
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Mantri reads the WhatsApp groups it's given permission to read. That's all it does — it reads.

From those messages, it builds a task list: what orders are active, what's pending, what's at risk of being missed, and what the logical next step is for each task.

It does not send messages on your behalf. It does not make any decisions. It cannot write anything to WhatsApp. It only reads and summarises.

Think of it as a very attentive assistant sitting in the background — one that never gets tired, never loses track, and always has the full picture."

---

---

## SLIDE 4 — OUR VALUES

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   What we are committed to                                 │
│   ──────────────────────────                               │
│                                                             │
│   🔒  Privacy first                                        │
│       Only whitelisted groups. Nothing else. Ever.         │
│                                                             │
│   🔕  Minimal noise                                        │
│       Only helpful reminders. Not a flood of alerts.       │
│                                                             │
│   ✋  You are always in control                            │
│       Override any task, any status, any suggestion.       │
│                                                             │
│   🤝  Built with you, not for you                          │
│       Your feedback changes what gets built.               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Before we show you what it does, we want to be clear about what we stand for.

First — privacy. Mantri only reads the groups that Ashish explicitly gives it permission to read. Your personal chats, your 1:1 conversations — none of that. A specific whitelist of groups, nothing else, ever.

Second — minimal noise. We are not building something that sends you 50 alerts a day. The goal is a small number of genuinely useful reminders — the kind that save you from a problem, not the kind that distract you from your work.

Third — you are always in control. If Mantri flags a task incorrectly, you can correct it. If it marks something as pending that you've already handled, you can update it. Nothing it says is final.

And fourth — this is built with you. What you tell us today will change what we build. We're not here to tell you how to work. We're here to build something that fits how you actually work."

---

---

## SLIDE 5 — HOW IT WORKS: STEP 1

```
┌─────────────────────────────────────────────────────────────┐
│  Step 1: Mantri reads the groups                           │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  WhatsApp Groups (permitted only)                   │   │
│  │                                                     │   │
│  │  [24 Mar 09:00] Ashish: Sharma bhai confirm karo   │   │
│  │  [24 Mar 09:15] Supplier: haan bhai, aa jayega     │   │
│  │  [24 Mar 10:30] Staff 1: delivery kal subah        │   │
│  │  [24 Mar 11:00] Ashish: client ko batao            │   │
│  │       ...                                          │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│         ▼  Mantri reads and understands this               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Here's how it works step by step. Mantri reads the permitted WhatsApp groups — the actual messages, in Hinglish, just as you write them. It understands the context — who is talking to whom, what order is being discussed, what has been promised and by when."

---

---

## SLIDE 6 — HOW IT WORKS: STEP 2

```
┌─────────────────────────────────────────────────────────────┐
│  Step 2: It builds a task                                  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  📦 Tandoor Delivery — Sharma Supplier              │   │
│  │     Status: In Progress                             │   │
│  │     Due: Today, afternoon                           │   │
│  │     Priority: HIGH                                  │   │
│  │                                                     │   │
│  │     ↳ Confirm exact delivery time         PENDING  │   │
│  │        → Call Sharma bhai, get window              │   │
│  │                                                     │   │
│  │     ↳ Notify client of delivery window   PENDING  │   │
│  │        → Post update in client group               │   │
│  │                                                     │   │
│  │     ↳ Arrange receiving staff             PENDING  │   │
│  │        → Confirm who will be on site               │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"From those messages, it builds a task card. You can see the supplier, the item, the status, the deadline, and the priority. And underneath — the specific actions that still need to happen, with a suggested next step for each one.

Notice that 'arrange receiving staff' — that was never explicitly discussed in any message. Mantri inferred it from the context. Someone needs to be at the delivery location. That's an implicit task it surfaces automatically."

---

---

## SLIDE 7 — HOW IT WORKS: STEP 3

```
┌─────────────────────────────────────────────────────────────┐
│  Step 3: It flags what needs attention                     │
│                                                             │
│  ⚠️  FLAGS                                                  │
│                                                             │
│  • Exact delivery time not confirmed —                     │
│    "dopahar tak" is vague. Ashish asked for                │
│    precision. No reply yet.                                │
│                                                             │
│  • No client notification found in client group           │
│    — has client been told delivery is today?               │
│                                                             │
│  • "Sharma ji" and "Sharma bhai" used in same thread      │
│    — assumed same person. Please confirm.                  │
│                                                             │
│  ❓  Needs human review                                     │
│  • Are Sharma ji and Sharma bhai the same supplier?        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"And finally, it flags things that need attention — information that's missing, commitments that are vague, or things it's uncertain about.

Notice the last one: 'Sharma ji and Sharma bhai — assumed same person, please confirm.' Mantri doesn't guess silently. When it's not sure, it tells you and asks for human confirmation. This is important — it's honest about what it doesn't know."

---

---

## SLIDE 8 — HOW IT WORKS: STEP 4

```
┌─────────────────────────────────────────────────────────────┐
│  Step 4: You stay in control                               │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  📦 Tandoor Delivery — Sharma Supplier              │   │
│  │                                                     │   │
│  │     ↳ Confirm exact delivery time    ✅ DONE        │   │
│  │        [You marked this complete]                   │   │
│  │                                                     │   │
│  │     ↳ Notify client of delivery      ✅ DONE        │   │
│  │        [You marked this complete]                   │   │
│  │                                                     │   │
│  │     ↳ Arrange receiving staff        🔄 IN PROGRESS │   │
│  │        [You updated this]                           │   │
│  │                                          [UPDATE]   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│         Nothing is final. You correct it at any time.      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"At any point, you can update a task. Mark it done. Change the status. Correct something Mantri got wrong. It's not smarter than you — it has less context than you do. It can be wrong, and you will always be able to fix it.

Think of it like a shared whiteboard that Mantri writes on automatically. You can always erase and rewrite."

---

---

## SLIDE 9 — REAL EXAMPLE: SATA CASE (OVERVIEW)

```
┌─────────────────────────────────────────────────────────────┐
│  Real example: SATA Artillery Regiment order               │
│                                                             │
│  What Mantri read:                                         │
│  • 4 WhatsApp groups                                       │
│  • 904 messages over 24 days                               │
│  • 14 images (invoices, photos, payment screenshots)       │
│                                                             │
│  What it produced:                                         │
│  • 12 parent tasks identified                              │
│  • Multiple suppliers correctly separated                  │
│  • Payments without matching GST bills flagged             │
│  • Items at risk of being missed — surfaced                │
│  • Ambiguous messages flagged for human review             │
│                                                             │
│  Evaluation score: 91 / 100  ✅                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Let's look at a real example. We ran Mantri on a real set of WhatsApp chats from the SATA Artillery Regiment order — 4 groups, 904 messages over 24 days. We also gave it the images that were shared — invoices, payment screenshots, supplier proformas.

It identified 12 separate tasks, correctly linked messages across different groups to the same orders, flagged payments that had no matching GST bill, and surfaced items that were at risk of being missed.

We evaluated its output and it scored 91 out of 100. We'll walk through a few highlights now."

---

---

## SLIDE 10 — SATA OUTPUT: SAMPLE PARENT TASK

```
┌─────────────────────────────────────────────────────────────┐
│  Sample: Window AC order (Voltas / JRBK supplier)          │
│                                                             │
│  📦 Window AC x3 — SATA Artillery Regiment                 │
│     Supplier: Voltas via JRBK                              │
│     Status: In Progress — dispatch not confirmed           │
│     Due: Before 24 Mar (unit relocates)                    │
│     Priority: HIGH ⚠️ URGENT                               │
│     Sources: Thread 1, Thread 2                            │
│                                                             │
│     ↳ Confirm dispatch with Punit (JRBK)    PENDING       │
│        → Call Punit directly. Last update: 15 Mar.        │
│        → 9 days without confirmation = high risk.         │
│                                                             │
│     ↳ Arrange delivery vehicle              PENDING       │
│        → Book only after dispatch confirmed               │
│                                                             │
│     ↳ Client receipt confirmation           PENDING       │
│        → Ensure Sub Arvind Sir signs off                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Here's a real task from the SATA case. The AC order for the regiment. Mantri correctly identified that the last update from the supplier was 9 days ago, that the unit is about to relocate, and that this is a high-risk situation.

The suggested next step: call Punit directly. Not a WhatsApp message — a phone call, because the message trail has gone quiet.

It also flagged that the delivery vehicle should not be booked until dispatch is confirmed — the sequencing is correct."

---

---

## SLIDE 11 — SATA OUTPUT: WHAT IT CAUGHT

```
┌─────────────────────────────────────────────────────────────┐
│  What Mantri caught that could have been missed            │
│                                                             │
│  ✅  Amazon orders silent for 21 days — high risk          │
│      No one had flagged this in any group                  │
│                                                             │
│  ✅  Payments made without matching GST bills              │
│      Listed with amounts and supplier names                │
│                                                             │
│  ✅  Mini fridge: client asked for 75-100L                 │
│      Supplier delivering 45L — mismatch flagged            │
│                                                             │
│  ✅  Parallel stabilizer quotes across 2 threads           │
│      Risk of double order — flagged                        │
│                                                             │
│  ✅  DY GOC OTG quote gap — 20 days, no response          │
│      Escalation recommended                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"These are five things Mantri caught from the SATA case that had not been explicitly flagged by anyone in any group.

The Amazon orders had been silent for 21 days — no one had noticed. The mini fridge size mismatch — the client asked for 75-100 litres, the supplier was delivering 45 litres. If that had reached the client unnoticed, it would have been a serious problem.

These are the kinds of gaps that fall through in a busy operation. Mantri's job is to surface them before they become crises."

---

---

## SLIDE 12 — INTERMEDIATE Q&A

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                  Questions so far?                         │
│                                                             │
│      We'd love to hear your first reactions.               │
│                                                             │
│      ─────────────────────────────────────────             │
│                                                             │
│      Things to reflect on:                                 │
│                                                             │
│      • Does this match how you actually work?              │
│      • Is there something important it wouldn't see?       │
│      • Does anything about this concern you?               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Let's pause here for questions before we go further. We've shown you what Mantri does — I'd love your first reactions.

[Pause. Let responses come. Don't rush.]

A few things worth thinking about as you respond: Does this match how orders actually flow in your work? Is there something important that happens off WhatsApp that Mantri would never see? And is there anything about this that concerns you?

[Take notes on responses. Acknowledge each one. If something is a valid concern, say so honestly — don't dismiss it.]"

---

---

## SLIDE 13 — TRUST: WHAT WE WILL NEVER DO

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   What Mantri will never do                                │
│                                                             │
│   ✗   Send messages on your behalf                        │
│   ✗   Read personal or unlisted chats                     │
│   ✗   Share your conversations with anyone else           │
│   ✗   Make decisions without human approval               │
│   ✗   Tell Ashish who made a mistake                      │
│   ✗   Score or rank staff performance                     │
│                                                             │
│   Your WhatsApp is your workspace.                         │
│   Mantri is a guest — it reads what it's invited to read, │
│   and nothing more.                                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"I want to be direct about something important. There are things Mantri will never do — and I want you to hear this clearly.

It will never send a message on your behalf. It will never read personal chats or any group it hasn't been explicitly given permission to access. It will never share your conversations with anyone.

And specifically — it will not be used to evaluate staff, score performance, or report back to Ashish about who made a mistake. That is not what this is for. This is an operations tool, not a monitoring tool.

Your WhatsApp is your workspace. Mantri is a guest in that workspace. It reads what it's invited to, does its job quietly, and stays out of everything else."

---

---

## SLIDE 14 — TRUST: YOU ARE IN CONTROL

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   You control Mantri — not the other way around            │
│                                                             │
│   ┌───────────────────────────────────────────────────┐   │
│   │                                                   │   │
│   │  Mantri says:  "Follow up with supplier — 48h"   │   │
│   │                                                   │   │
│   │  You say:      "Already handled it on the phone" │   │
│   │                   [Mark as done]                  │   │
│   │                                                   │   │
│   │  Mantri says:  "Client delivery pending"          │   │
│   │                                                   │   │
│   │  You say:      "This client has 2 months to pay" │   │
│   │                   [Update: not urgent]            │   │
│   │                                                   │   │
│   └───────────────────────────────────────────────────┘   │
│                                                             │
│        Your knowledge always overrides Mantri.             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"You will always know more than Mantri does. You were in the room. You made the phone call. You know the client's situation.

Whenever Mantri gets something wrong — and it will sometimes get things wrong — you correct it. Mark it done. Update the status. Tell it the task is not urgent. Your knowledge overrides Mantri, always.

The system is designed to learn from your corrections over time. Every time you update a task, Mantri gets a little more accurate about how your business actually works."

---

---

## SLIDE 15 — INTERVIEW: SECTION TITLE

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                                                             │
│              Now we'd like to hear from you                │
│                                                             │
│         10 minutes — your honest answers only              │
│                                                             │
│         There are no right or wrong answers.               │
│         The more honest you are, the better                │
│         what we build will be.                             │
│                                                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Now the most important part of today. We want to hear from you directly.

I'm going to ask a few questions. There are no right answers. If something doesn't work for you, tell me. If you think this would create more work, not less — tell me. The only way we build something useful is if you tell us the truth.

Ashish, I'd ask you to let the team answer first for each question before you add your view — sometimes the team has a different experience than what comes through on WhatsApp."

---

---

## SLIDE 16 — INTERVIEW QUESTION 1

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Question 1                                               │
│   ──────────                                               │
│                                                             │
│   "When Ashish gives you a task on WhatsApp —             │
│    how do you currently keep track of it                   │
│    until it's done?"                                       │
│                                                             │
│                                                             │
│   [Leave this on screen while they answer]                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"First question — and this is about how you work today, not about Mantri.

When Ashish sends you a task on WhatsApp — follow up with this supplier, arrange a delivery, confirm a rate — how do you keep track of it until it's done? Do you write it down somewhere? Pin the message? Keep it in your head?

[Let them answer fully. Follow up: "And what happens when there are 5 or 6 tasks at the same time?"]"

---

---

## SLIDE 17 — INTERVIEW QUESTION 2

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Question 2                                               │
│   ──────────                                               │
│                                                             │
│   "Tell me about a time a task slipped —                  │
│    something got missed or delayed.                        │
│    What happened? What caused it?"                         │
│                                                             │
│                                                             │
│   [Leave this on screen while they answer]                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Second question. Everyone has had a moment where something slipped — a follow-up that was forgotten, a delivery that was late because someone didn't get the message. Can you think of a recent example? What happened, and what caused it?

[This question surfaces real failure modes. Listen carefully. Don't rush past the answer. Follow up: "How often does something like that happen?", "What was the impact?"]"

---

---

## SLIDE 18 — INTERVIEW QUESTION 3

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Question 3                                               │
│   ──────────                                               │
│                                                             │
│   "If Mantri sent you a reminder about a task —           │
│    what would make you trust it enough to act on it?       │
│    What would make you ignore it?"                         │
│                                                             │
│                                                             │
│   [Leave this on screen while they answer]                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Third question — about trust. If Mantri sent you a reminder — 'this supplier hasn't replied in 48 hours, follow up' — what would it need to look like for you to trust it and act on it? And what would make you think 'this is wrong' and ignore it?

[Follow up: "How many reminders a day would feel helpful? How many would feel like noise?", "Has anyone used any reminder or task tool before? What happened?"]"

---

---

## SLIDE 19 — INTERVIEW QUESTION 4

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Question 4                                               │
│   ──────────                                               │
│                                                             │
│   "Are there situations where you handle something        │
│    outside WhatsApp — a phone call, in person —           │
│    and the group never gets updated?                       │
│    How often does that happen?"                            │
│                                                             │
│                                                             │
│   [Leave this on screen while they answer]                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Fourth question — and this is really important for us to understand. Are there times when something gets resolved outside WhatsApp — a quick phone call, a conversation in person — and no one updates the group? How often does that happen?

[This is a key question for the missing-entries risk. Don't lead them. Listen for: 'haan, Ashish calls me directly', 'sometimes I just go to the supplier', 'phone pe ho jaata hai'. Follow up: 'When that happens, does Ashish know it's been handled?']"

---

---

## SLIDE 20 — INTERVIEW QUESTION 5

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Question 5                                               │
│   ──────────                                               │
│                                                             │
│   "What's the one thing you'd most want Mantri            │
│    to help you with?                                       │
│                                                             │
│    And is there anything about it that                     │
│    you're still worried about?"                            │
│                                                             │
│                                                             │
│   [Leave this on screen while they answer]                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Last question — and it has two parts. First: if Mantri worked perfectly, what's the one thing you'd most want it to help you with? What would make the biggest difference in your day?

And second: is there anything you're still worried about, even after everything we've discussed today?

[Let every person in the room answer both parts. Don't rush. This is where the most honest feedback comes.]"

---

---

## SLIDE 21 — FINAL Q&A

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│              Any final questions for us?                   │
│                                                             │
│      ─────────────────────────────────────────             │
│                                                             │
│      Things you can ask us:                                │
│                                                             │
│      • How will we use what you've told us today?          │
│      • When will this be ready to use?                     │
│      • How do you give us feedback later?                  │
│      • Anything else on your mind                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Now it's your turn to ask us anything.

[Anticipated questions and honest answers:]

'How will you use what we said today?' — Everything you told us goes directly into what we build. Your concerns become our design constraints. Your suggestions become features.

'When will this be ready?' — We're targeting a working version by late April. You'll be among the first to try it.

'How do we give feedback later?' — We'll set up a simple way for you to send us feedback as you use it. Ashish will also pass on your feedback directly.

[Take whatever questions come. Be honest if you don't know the answer.]"

---

---

## SLIDE 22 — CONCLUSION

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   What we heard today                                      │
│   ─────────────────────                                    │
│                                                             │
│   [FACILITATOR: fill in 2-3 points from the session]      │
│                                                             │
│   • [Key theme from Q&A]                                   │
│   • [Key concern raised]                                   │
│   • [Key thing they want]                                  │
│                                                             │
│   ─────────────────────────────────────────────────────   │
│                                                             │
│   What happens next                                        │
│                                                             │
│   • Your feedback shapes the next version                  │
│   • We'll share progress with Ashish regularly             │
│   • You'll be the first to use the real system             │
│                                                             │
│   Thank you. Your time matters to us.                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**SCRIPT:**
"Before we close, let me reflect back what I heard today.

[FILL IN LIVE: pick 2-3 of the most important things that came up in the interview section. Paraphrase back to the room — 'I heard that [X] is a concern', 'It sounds like [Y] would be most useful', 'The question about [Z] is something we'll take seriously.' This shows you actually listened and the session wasn't performative.]

Here's what happens next. Everything you told us goes into the next build. We'll keep Ashish updated on progress. And when we have something working, you'll be the first team to use it for real — before anyone else.

Thank you. Genuinely. Your honesty today is what makes this worth building."

---

---

## FACILITATOR NOTES

### Before the session
- Share the Google Meet link with Ashish 24 hours ahead
- Ask Ashish to ensure all staff are in a quiet room with one device showing the screen
- Test screen share and audio 15 minutes before the call
- Have the interview analysis prompt open in a separate tab — paste transcript immediately after

### During the session
- Keep slides brief on screen — don't read from them
- After each interview question: wait for silence before moving on. Silence means they're thinking.
- Take running notes on the side — especially quotes and anything that surprises you
- If anyone is quiet: "Aap ka kya lagta hai?" — direct them gently, don't skip them
- If a concern comes up that you can't address honestly: "That's a fair concern. We don't have a full answer yet — but I want to make sure we come back to it."

### After the session
- Run Google Meet transcript through Whisper for clean output
- Feed transcript into `prompts/interview_analysis_prompt.txt`
- Share key findings with Ashish within 24 hours
- Note any edge cases or design decisions that came up — add to task taxonomy docs

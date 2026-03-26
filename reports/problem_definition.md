# Problem Definition

## Problem Statement

Ashish Chhabra, owner of an Army supply business in Guwahati, cannot effectively oversee operations when traveling to secure contracts. Staff forget or get overloaded and drop critical follow-up steps, while Ashish only learns of failures after the fact — typically from clients, not staff. The gap between what needs to happen and what actually gets done is a visibility and capacity problem, not a motivation or training problem.

## Target Users

**Primary**: Ashish Chhabra — business owner, frequently traveling, needs operational awareness and intelligent escalation without being in the loop for every task.

**Secondary**: Ashish's staff — trained, motivated, but limited in capacity; need task prioritization, reminders, and guidance rather than micromanagement.

**External parties** (not users of the system): Army clients, suppliers, transporters, contractors — communicate via WhatsApp and phone calls.

---

## Current State

### User Process (Step-by-Step)

1. Business communications happen across multiple WhatsApp groups: one per supplier (Ashish + relevant staff + supplier), internal staff groups, and client groups.
2. Ashish has established discipline: all commitments, updates, and call summaries must be posted to the relevant WhatsApp group.
3. When Ashish is away, staff are expected to monitor these groups, follow up with suppliers/clients, and manage operational tasks independently.
4. When something goes wrong, Ashish typically finds out from the client calling him — not from staff proactively flagging it.
5. Ashish then raises the issue in the relevant WhatsApp group, works with staff to identify the root cause, and defines the solution.

### Key Operational Activities

1. Sales enquiries — market research, price follow-ups
2. Tender creation — quotations from multiple suppliers, comparative analysis, bill generation on e-marketplace
3. Invoicing — pending bills, follow-ups
4. Client liaison — future requirements, relationship management
5. Inventory management — stock taking, refilling
6. Accounts and taxation — supplier/client accounts
7. Banking tasks
8. Collections from local suppliers
9. Supply tracking — inter-state/inter-city suppliers, collection from transporters
10. Inspection and quality control — updates to suppliers/clients
11. Sorting, packaging, delivery planning and execution
12. Remedial actions — missing or defective items

### Top Pain Points (Highest Failure Rate)

1. **Quotations**: Staff must collect product info and rates from multiple suppliers, create a comparative with pictures, and support a judgment call (by Ashish or client). Many steps, high coordination overhead.
2. **Collection, inspection, sorting, packaging**: Sequential steps that get overlooked when staff are busy.
3. **Delivery planning and execution**: Can be completely overlooked by staff under load.

### Existing Tools

- WhatsApp groups (primary communication and record-keeping channel)
- Phone calls with mandatory summaries posted to WhatsApp groups
- No dedicated operations management tool

### Trigger

Ongoing — operational activities run continuously throughout every business day.

### Frequency

5-10 active deliveries/supplier commitments per day. Multiple operational activities running in parallel at all times.

### Friction Points

- **Visibility gap**: Ashish has no real-time view of task status; learns of failures reactively from clients
- **Capacity/attention gap**: Staff are trained and motivated but get overloaded and forget follow-up steps
- **No proactive escalation**: Nothing nudges staff or Ashish when a milestone is approaching or a commitment is at risk
- **Dependency on Ashish**: High-difficulty decisions require Ashish's input but there's no structured way to flag and route these to him
- **Call summary gaps**: Phone call summaries posted to WhatsApp may omit context, nuance, or non-commitments discussed verbally

---

## Desired State

### Ashish's Experience

- **Operations dashboard**: All tasks listed, categorized by importance, priority, and difficulty; each task summarized from communications and activity history
- **Chat correction interface**: Ashish can correct the agent's categorization or summaries via a chat tool
- **Blockers view**: Current issues blocked by external parties, internal issues, or requiring Ashish's direct attention
- **Delays view**: Tasks predicted to face delay or confirmed as delayed in communications

### Staff Experience

- **Daily task dashboard**: Task list sorted by importance/priority at start of day and updated throughout; tasks include a suggested course of action that staff can adjust
- **Difficulty tagging**: Staff tag tasks as low/medium/high difficulty
  - Low/medium: staff work independently; agent monitors progress
  - High: agent flags to Ashish; staff must follow up with Ashish in a timely manner
- **Progress monitoring**: Agent detects when tasks are not progressing as expected and alerts staff (and Ashish if urgent)
- **Availability-aware escalation**: For high-difficulty tasks, agent tracks Ashish's availability to ensure timely escalation
- **Task allocation**: Based on designated staff responsibility, current availability, self-initiative, and shared responsibility

### What No Longer Needs Ashish's Attention

- **All incoming communications**: WhatsApp groups and messages checked and categorized automatically; low/medium difficulty items handled by staff independently
- **Routine operational tasks**: Collection, inspection, packaging, delivery, invoicing, accounts, payments — planned and scheduled by the agent without explicit Ashish input
- **Staff judgment calls**: Staff can self-escalate to Ashish when needed; otherwise operate with agent guidance

### User Success Criteria

- Ashish receives proactive alerts for high-risk items before they become client complaints
- Staff complete critical follow-up steps without needing Ashish to prompt them
- High-difficulty tasks are always routed to Ashish with sufficient lead time
- Ashish has a complete operational overview without being in every WhatsApp conversation

### Expected Impact

- Reduction in client complaints caused by missed follow-ups
- Reduction in Ashish's reactive firefighting while traveling
- Staff operate more independently and confidently on low/medium complexity tasks
- Ashish's focus shifts from operational firefighting to strategic decisions

---

## Constraints

### Technology

- WhatsApp is the primary and non-negotiable channel for all external communications (clients, suppliers, transporters)
- Internal communications also primarily on WhatsApp for convenience and consistency
- Ashish and staff are open to new tools/dashboards as long as they complement WhatsApp

### Privacy

- **Staff**: Not open to monitoring of personal WhatsApp; will accept an opt-in model where they choose which chats to share with the agent
- **Call recordings**: Strongly resisted by both Army clients and staff; calls will NOT be recorded. Only WhatsApp call summaries (posted manually to groups) are available as data
- **Data storage**: Business communications data stored externally will require trust-building with Ashish and staff

### Ashish's Availability

- Generally available at all times
- Unreachable during Army area visits: max 1-2 times/week, typically 9AM-1PM
- Occasionally unreachable during international travel or long-haul flights
- Agent must handle these windows gracefully — buffer high-difficulty escalations around known unavailability

---

## Assumptions Analysis

### Initial Assumptions Identified

1. WhatsApp call summaries are sufficient as a data source
2. Staff will consistently opt-in and share the right WhatsApp chats
3. The agent can reliably extract structured information from Hinglish WhatsApp messages
4. The agent can learn and recognize item-wise plans of action from historical communications
5. Staff will trust and act on agent-generated task suggestions
6. Ashish can be reliably reached for high-difficulty escalations within a reasonable time window

### Validated Constraints

- **No call recording**: Strongly resisted by Army clients and staff. Call summaries posted to WhatsApp are the only call data available. This is non-negotiable.
- **WhatsApp is the only channel**: Cannot be replaced for external communications. All agent inputs must derive from WhatsApp data.
- **No fallback decision-maker**: When Ashish is unreachable, high-difficulty tasks wait. No senior staff member has authority to substitute. Agent must proactively surface escalations before known unavailability windows.
- **SOP consistency**: The plan of action for all orders follows the same process template. Items vary but the process is predictable — reliable SOP extraction is feasible.
- **Staff will embrace agent guidance**: Staff motivation aligns with system goals — they want prioritized task lists and reduced personal accountability for judgment calls. Adoption risk is low.

### Flexible Assumptions

- **Call summary completeness**: 20-30% of summaries have missing critical details. The agent can detect incomplete summaries and prompt staff to fill gaps — same behavior Ashish exhibits today. Summaries are a starting point, not ground truth.
- **Staff opt-in reliability**: Staff are trusted to opt-in consistently. New contacts are created daily — opt-in is an ongoing activity. The agent needs metadata access (contact list) for all chats even if non-whitelisted, to detect gaps and prompt staff to share missing contacts.
- **Ashish's own chats must also be opted in**: Ashish is a participant in many key WhatsApp groups. His chats are equally (if not more) critical to monitor. His opt-in is required and must be managed alongside staff opt-ins.
- **Assamese language support**: Assamese is used only in verbal communications and calls, which are not monitored. WhatsApp chats are exclusively Hinglish (Hindi in Roman/English script). Assamese NLP is not required.

---

## Solution Hypotheses

### Hypothesis 1 — AI as Assistant (Level 1)

**Autonomy level**: Low — AI informs, humans decide and act.

**What AI does autonomously**: Monitors opted-in WhatsApp chats, extracts entities and commitments, detects deadlines and gaps in call summaries, generates structured daily briefings.

**Human touchpoints**: All decisions, prioritization, task assignment, and follow-ups remain with Ashish and staff.

**Interaction pattern**: Scheduled daily report (e.g., 8AM) + on-demand status queries.

**Scope boundaries**: Does not suggest next steps, assign tasks, alert proactively, or contact any parties.

---

### Hypothesis 2 — AI as Collaborator (Level 2) ⭐ Selected

**Autonomy level**: Medium — AI monitors, alerts, and suggests; humans approve and execute.

**What AI does autonomously**: Monitors opted-in chats, extracts entities and commitments, tracks task status and milestones, detects approaching deadlines and silent suppliers, generates suggested courses of action, sends proactive alerts via WhatsApp and dashboard.

**Human touchpoints**: Staff and Ashish review suggestions, approve or modify plans, tag task difficulty, execute follow-ups, make judgment calls.

**Interaction pattern**: Proactive WhatsApp alerts + task dashboard with suggested actions; staff acknowledge, adjust, or override.

**Scope boundaries**: Does not execute follow-ups directly, send messages on behalf of staff, contact suppliers/clients autonomously, or make commercial decisions.

---

### Hypothesis 3 — AI as Agent (Level 3)

**Autonomy level**: High — AI acts autonomously within boundaries, humans review exceptions.

**What AI does autonomously**: Everything in H2, plus — drafts and sends WhatsApp follow-up messages for low/medium difficulty tasks, marks tasks complete based on communication evidence, re-assigns tasks when staff are overloaded.

**Human touchpoints**: Set rules and boundaries upfront; review agent-sent message logs; handle high-difficulty escalations; override when needed.

**Interaction pattern**: Agent acts first, humans review after (exception-based model).

**Scope boundaries**: Does not contact Army clients directly, make pricing/commercial decisions, or handle high-difficulty tasks without Ashish.

---

---

## Selected Solution

### Chosen Hypothesis
**H2: AI as Collaborator** — selected for its balance of proactive value delivery and human control. H1 is too passive to address the capacity/attention gap. H3 introduces autonomous message-sending risk before sufficient trust is established. H2 delivers the core value (alerts + suggested actions) while keeping humans in the execution loop.

### Solution Logic
*If we implement the AI Collaborator, it will reduce delivery failures and client complaints because SOPs and milestone deadlines will be well known and continuously monitored — surfacing potential difficulties and delays before they become client-facing problems.*

### Autonomous Capabilities

**Communication monitoring**:
- Monitor all opted-in WhatsApp chats (Ashish + all staff) continuously
- Parse Hinglish (Hindi in Roman script + English) messages
- Process call summaries posted to WhatsApp groups
- Detect new incoming messages (client enquiries, supplier updates, complaints) and create tasks automatically

**Entity extraction and knowledge base**:
- Extract and maintain entities: customers, orders, items, vendors, contractors, staff
- Deduplicate entities across conversations
- Detect and flag incomplete call summaries (missing price, GST, delivery timeline, etc.)

**Task management**:
- Create tasks from all opted-in communications (Ashish + staff + external parties)
- Organise tasks hierarchically (e.g., Order → Quotation, Collection, Inspection, Packaging, Delivery)
- Categorise tasks by type, importance, priority, and difficulty
- Track task status from communications evidence

**Timeline tracking**:
- Track two timelines per supplier task: vendor-committed and internal realistic
- Learn vendor reliability patterns over time (typical delay, variance per vendor)
- Use internal realistic timelines for alert triggers; reference vendor commitments for pressure conversations
- Predict delays based on vendor reliability history and current communication signals

**Proactive alerting**:
- Alert staff and Ashish when tasks approach milestones without progress evidence
- Flag tasks with no recent activity relative to expected timeline
- Surface blockers (external party unresponsive, missing information, awaiting Ashish decision)
- Pre-surface high-difficulty tasks before Ashish's known unavailability windows (Army visits, travel)

**Suggested course of action**:
- For each alert, provide a text description of the recommended next step
- Staff read and apply the suggestion using their own judgment and communication style
- No draft messages generated — staff compose their own follow-ups

### Human Touchpoints

- **Staff**: Review task dashboard daily, tag task difficulty (low/medium/high), acknowledge or adjust suggested actions, execute follow-ups independently, flag issues to Ashish when needed
- **Ashish**: Review operations dashboard (task overview, blockers, delays), correct agent categorizations or summaries via chat interface, handle all high-difficulty escalations, set vendor reliability expectations when agent is uncertain
- **Both**: Opt-in WhatsApp chats for monitoring on an ongoing basis as new contacts are created

### Interaction Pattern

- Agent monitors continuously in background
- Proactive WhatsApp alerts sent to relevant staff/Ashish when action is needed
- Dashboard available for full operational overview at any time
- Chat interface for Ashish to query agent or correct its outputs
- Staff acknowledge, adjust, or override suggestions — agent learns from corrections over time

### Success Metrics

**Lagging indicators** (4+ weeks):
- Reduction in client complaints caused by missed follow-ups
- Fewer confirmed delivery delays
- Improved client sentiment (qualitative, from Ashish)

**Leading indicators** (measurable from day one):
- Reduction in tasks that slip past their milestone without an alert being raised
- % of incomplete call summaries detected and filled
- Staff alert acknowledgement rate (are alerts being acted on?)

### Scope Boundaries

**In scope**:
- All opted-in WhatsApp chats (Ashish + staff)
- All task types derived from communications (supplier, client, internal operational)
- Hierarchical task creation and tracking
- Dual timeline tracking (vendor-committed vs internal realistic)
- Proactive alerts with suggested text actions
- Ashish's dashboard + staff dashboard + WhatsApp alerts

**Out of scope**:
- Sending WhatsApp messages on behalf of staff or Ashish
- Contacting external parties (clients, suppliers) directly
- Making commercial decisions (pricing, terms, vendor selection)
- Monitoring non-opted-in chats (content) — metadata only for gap detection
- Call recordings (summaries only)

---

## Process Requirements

### Process Inputs

**WhatsApp thread types (all opted-in):**
- Client groups (Ashish + relevant staff + client) — formal commitments, primary record
- Supplier groups (Ashish + relevant staff + supplier) — procurement and delivery coordination
- All-staff group (Ashish + all staff) — general task coordination
- Department sub-groups (staff subsets) — internal coordination by function
- Ashish's 1:1 chats with clients/suppliers — sensitive discussions; Ashish moves to groups when feasible
- Staff and Ashish 1:1 chats with transporters/contractors — ad hoc, not moved to groups; staff expected to post relevant updates to internal groups

**Other inputs:**
- Call summaries posted to WhatsApp groups
- Staff task acknowledgements and difficulty tags
- Ashish's corrections and overrides via chat interface
- Vendor commitment data (from messages)
- Internal realistic timeline estimates (initially from Ashish/staff, learned over time)

**Cross-thread correlation (fully automatic — no staff tagging required):**
- A single order or issue leaves traces across multiple threads simultaneously
- Agent must correlate information across threads using entity resolution (item names, party names, Hinglish spelling variants, pronouns/co-references)
- Must handle ambiguity: two different orders for the same item type from different clients must stay separate
- Transporter/contractor 1:1 chats are a partial data source — staff are expected to update internal groups, but 1:1 content provides supplementary context

### Process Outputs
- Hierarchical task list with priority, importance, difficulty categorisation
- Proactive alerts via WhatsApp to relevant staff/Ashish
- Suggested course of action (text) per task
- Ashish's operations dashboard (task overview, blockers, delays)
- Staff task dashboard (live, continuously updated task list — staff treat this as their primary work view throughout the day)
- Vendor reliability profiles (learned over time)
- Incomplete summary detection flags

---

---

## Experiment Design

### Core Assumption
An LLM can reliably read Hinglish WhatsApp conversations and extract structured, hierarchically organised tasks with correct entities (orders, deliveries, items, customers, vendors), priorities, and suggested next steps — at 90%+ accuracy with minimal human correction.

### Test Approach
Use Claude or ChatGPT playground with anonymized real WhatsApp conversation samples from Ashish. Feed raw conversation threads and prompt the LLM to extract a structured task list. Evaluate output against manually created ground truth.

### Mock Data Examples
To be provided by Ashish (expected within Sprint 1). Required samples:
1. **Typical case 1**: Routine supplier follow-up thread (delivery commitment + status updates)
2. **Typical case 2**: Client enquiry leading to quotation task chain
3. **Edge case 1**: Single thread with multiple items/orders interleaved
4. **Edge case 2**: Ambiguous or incomplete commitment (missing price, timeline, or delivery terms)
5. **Edge case 3**: Thread with conflicting information (vendor says one thing, staff says another)

All samples to be anonymized: real names replaced with "Client A", "Supplier B", "Staff 1", etc.

### Test Scenarios
1. **Basic extraction**: Does the LLM correctly identify all tasks in a clean thread?
2. **Hierarchy**: Does it correctly group subtasks under parent orders/deliveries?
3. **Entity tagging**: Are customers, vendors, items, orders correctly identified and linked?
4. **Priority/urgency detection**: Does it correctly flag time-sensitive tasks?
5. **Hinglish handling**: Does spelling variation in romanized Hindi cause extraction errors?
6. **Edge case handling**: Multi-item threads, ambiguous commitments, conflicting info

### Success Criteria
- **Primary**: 90%+ of tasks correctly identified, hierarchically grouped, and entity-tagged across all test samples
- **Secondary**: Suggested next steps are actionable and contextually appropriate in 80%+ of cases
- **Acceptable correction overhead**: Staff should need to correct fewer than 1 in 10 tasks during steady state

### Learning Goals
- What types of Hinglish expressions cause extraction failures?
- Does hierarchy (parent/child task grouping) emerge naturally or need explicit prompting?
- How well does the LLM handle multi-item threads without conflating separate orders?
- What level of prompt specificity is needed to get consistent structured output?
- Are there task types the LLM systematically misses or misclassifies?

---

### Beliefs to Test

- **Hinglish extraction accuracy**: Hindi written in English script has high spelling variation (e.g., "kal"/"kaal"/"kl"). LLMs handle this in context but needs experimental validation with real Hinglish WhatsApp messages.
- **SOP pattern recognition from history**: Can the agent reliably infer the standard process template from historical WhatsApp conversations? Needs testing with real historical data from Ashish.
- **Incomplete summary detection**: Can the agent reliably identify when a call summary is missing critical fields (price, GST, delivery timeline, etc.) for a given item type? Needs prompt engineering and testing.
- **Confidence calibration**: Will staff over-rely on agent recommendations and stop applying judgment? The agent needs clear confidence signaling to prevent blind trust in uncertain recommendations.
- **Cross-thread entity resolution**: Can the agent reliably link messages about the same order/issue across multiple WhatsApp threads (client group, supplier group, staff group, 1:1 chats) without human tagging? This includes resolving Hinglish spelling variants, pronouns ("uska order", "woh maal"), and ambiguous references. Two different orders for the same item type must stay separate. This is a significant technical risk — needs testing with multi-thread real data.

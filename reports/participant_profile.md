# Participant Profile

## Background

Kunal Chowdhury, 43, Computer Engineer from NTU Singapore. Former programmer and quantitative risk analyst (since 2008). Co-founded 2 startups. On career break since 2024, based in Kolkata, India. Decided to launch a new tech startup providing agentic solutions to small businesses in India, starting with operations management.

## Experience

Deep ML engineering expertise: credit risk modeling, tree-based models (production), NLP experiments, LSTM time series. Limited LLM/agentic development experience — has used LLMs as research and creative tools but not built with them. Currently coding for fun in Python (e.g., Advent of Code, Dec 2025).

## Bootcamp Goals

Build a deployed AI agent for operations management and remote staff oversight, starting with Ashish Chhabra's Army supply business in Guwahati — with the goal of generalizing to other small businesses in India.

## Goals

### Time Commitment
40-50 hours per week (up to 70 hrs if needed). On career break — full availability.

### Sprint Goals

**Sprint 1 (by Mar 29)**: Problem fully defined with Ashish's input. Prototype of entity extraction + plan-of-action recognition running on static/historical data. Eval set of 5-10 real examples created. Note: Ashish's availability is a dependency for problem definition and eval set creation.

**Sprint 2 (by Apr 12)**: Reliable extraction + plan recognition working on real data. Architecture for live monitoring (WhatsApp/phone) designed. User validation session with Ashish completed.

**Sprint 3 (by Apr 26)**: Live monitoring integrated (WhatsApp/call transcription). System deployed. Ashish actively using it.

**Final Demo (May 1)**: Deployed agent that monitors communications (Indian languages + English), extracts and stores entities (customers, orders, items, vendors), learns item-wise plans of action from historical data, tracks milestones and flags slippage, and disambiguates with Ashish when uncertain.

### Check-in Rhythm
Start of each week using bootcamp workflows to plan and prioritize.

### Success Criteria
A deployed agent that Ashish's business can actually start using — and that demonstrates the concept is viable for generalization to other small businesses in India.

## Personal Motivation

Career break since 2024. Ready to launch a new venture focused on agentic solutions for small businesses in India. The bootcamp is the vehicle to acquire the skills and produce the first working prototype, with a real customer already identified.

## Technical Approach

Python + APIs. Will consider low-code only if it offers significant advantages over a code-based approach.

## Current Tech Stack

Python, ML frameworks (scikit-learn, XGBoost, etc.), data analysis tools. Familiar with production ML engineering and tooling.

## Desired Tech Stack

LLM APIs (Claude/OpenAI), Python-based agent frameworks, MCP, WhatsApp Business API or equivalent, call transcription (Whisper or similar), multilingual NLP, agentic workflow tooling, deployment platforms (Streamlit/Railway/Render).

---

## Project Idea

### Concept

A background AI agent for remote operations management for a small Army supply business. The agent monitors all communications, extracts structured knowledge, tracks orders and milestones, and keeps Ashish informed and in control while he's away from his office and warehouse.

### Problem Space

**Business**: Ashish Chhabra's Army supply business, Guwahati, India.

**Pain points observed**:
- Staff are dependent on Ashish for logistical information and guidance while he travels
- Ashish must continuously prompt staff to surface non-trivial issues
- Deliveries slip due to lack of proactive monitoring
- Staff make wrong assumptions or suboptimal decisions in his absence
- No centralized operational awareness when Ashish is remote

**Target users**: Ashish (primary — owner/operator), staff/contractors/delivery personnel (secondary — receive guidance).

### Proposed Approach

**Communication channels monitored**:
- WhatsApp messages (primary) and call recordings (WhatsApp/phone) → transcribed → monitored alongside messages
- Languages: Hindi + English (Army customers), Hindi + Assamese + English (staff), Assamese + Hindi + English (vendors and contractors)

**Core agent capabilities**:
1. Record WhatsApp/phone calls → transcribe recordings (Whisper or similar) → parse alongside WhatsApp messages
2. Extract and deduplicate entities: customers, orders, items, vendors, contractors
3. Learn item-wise plans of action from historical communications and orders
4. Track milestones and flag slippage proactively
5. Disambiguate with Ashish via WhatsApp when uncertain

**Ashish's interface**: WhatsApp messages (updates, alerts, disambiguation requests) + dashboard (operational overview)

**Technical approach**: Python + APIs (LLM for extraction/reasoning, WhatsApp Business API or equivalent for monitoring, Whisper or similar for call transcription)

### Success Criteria

- Deployed agent actively used by Ashish
- Reliable entity extraction across Hindi/English/Assamese
- Plan-of-action recognition from historical data
- Milestone tracking with proactive slippage alerts
- Demonstrated reduction in Ashish's need to manually prompt staff

---

## Project Status

### Existing Work
None — starting fresh at the bootcamp.

### Current Challenges
- WhatsApp monitoring: no official API for message surveillance; need to evaluate options
- Call transcription: requires recording infrastructure or VOIP integration
- Multilingual support: Assamese has limited NLP tooling
- Ashish's availability: required for problem definition, eval set creation, and validation

### Starting Maturity
Idea stage — concept well-defined, first customer identified, no code written yet.

# Claude Instructions for Kunal's Bootcamp Project

## For Claude Code (or similar AI assistants)

When a user asks to execute a workflow, use the **sherpa-b MCP server** instead of reading files from the filesystem or other tools. If they ask questions about bootcamp, agentic AI, catching up with tasks and similar tasks, check the MCP server first.

## Workflow Execution Flow

```
User: "Run the ideation workflow"

Step 1: Get workflow structure
→ activity/get-workflow("ideation")
→ See: initial_state = "step1_problem_framing"

Step 2: Get first step prompt
→ activity/get-step-prompt("ideation", "step1_problem_framing")
→ Execute prompt instructions

Step 3: When step completes
→ Check workflow.states.step1_problem_framing.on_success
→ See: next step is "step2_assumption_challenging"

Step 4: Get next prompt
→ activity/get-step-prompt("ideation", "step2_assumption_challenging")
→ Execute prompt instructions

Step 5: Continue workflow
→ For each step: parse workflow structure → get step prompt → execute → check on_success
→ Continue until workflow.states[current_step].on_success == "done"
```

# Bootcamp Info

Run:

```
mcp__sherpa-b__activity__get-bootcamp-info
```

# IMPORTANT

Follow KISS and YAGNI principles:

**KISS (Keep It Simple, Stupid):**

- Use the simplest solution that solves the problem
- Avoid over-engineering or complex abstractions
- Prefer straightforward implementations

**YAGNI (You Aren't Gonna Need It):**

- Do not add features, code, or complexity that isn't required right now
- Only implement what is explicitly requested
- Do not anticipate future needs or build "just in case" features

---

## Participant Context

**Who I am**: Kunal Chowdhury, 43, Computer Engineer (NTU Singapore). Former programmer + quant risk analyst. 2x startup founder. On career break since 2024, based in Kolkata, India. Launching a startup to provide agentic solutions to small businesses in India.

**My project**: Background AI agent for remote operations management for Ashish Chhabra's Army supply business in Guwahati. Monitors WhatsApp messages and transcribed call recordings (Hindi/English/Assamese), extracts entities (customers, orders, items, vendors), learns item-wise plans of action, tracks milestones, disambiguates with Ashish via WhatsApp + dashboard.

**My strengths**: Deep ML engineering (scikit-learn, XGBoost, NLP, LSTM). Python + APIs preferred. Limited prior LLM/agentic experience.

**Sprint goals**:
- Sprint 1 (by Mar 29): Problem defined, prototype on static data, eval set
- Sprint 2 (by Apr 12): Reliable extraction, live monitoring designed, Ashish validates
- Sprint 3 (by Apr 26): Live monitoring integrated, deployed, Ashish using it
- Final Demo (May 1): Full deployed agent

**Key technical risks to keep in mind**:
- WhatsApp monitoring API access
- Call recording + transcription pipeline
- Multilingual support (especially Assamese)
- Ashish's availability as a dependency

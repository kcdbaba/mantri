# LLM Cost Optimisation Log

**Date:** 2026-03-30

---

## Summary

Cumulative **87% cost reduction** from baseline, achieving the <$1/day target.

| Metric | Baseline | Current |
|---|---|---|
| Cost per message | $0.0177 | **$0.0023** |
| Daily cost (350 msgs) | $6.21 | **$0.80** |
| Monthly cost (350 msgs) | $186 | **$24** |
| LLM calls per 100 msgs | 27 (×5 extrapolated) | **19** |

---

## Optimisation Steps

### Step 1: System Prompt Caching (33% reduction)

**Change:** Added `cache_control: {"type": "ephemeral"}` to the system prompt block in both `update_agent` and `linkage_agent` API calls.

**Why it works:** The system prompt (~3,700 tokens for standard_procurement, ~1,000 for linkage) is identical across all calls for the same order type. Anthropic's prompt caching charges 0.1× input price on cache hits vs 1× for uncached tokens. The 5-minute cache TTL covers burst message processing easily.

**Files changed:** `src/agent/update_agent.py`, `src/linkage/agent.py`

**Measured impact (20 messages):**
- Before: $0.355 ($0.254 update + $0.109 linkage)
- After: $0.239 ($0.129 update + $0.110 linkage)
- Update agent cost down 49% (large system prompt); linkage minimal change (small prompt)

**Pricing update:** `src/store/db.py` — added `cache_write` (1.25×) and `cache_read` (0.1×) rates. `src/store/usage_log.py` — passes `cache_creation_input_tokens` and `cache_read_input_tokens` to cost computation.

---

### Step 2: Empty Message Skip (additional ~15%)

**Change:** Messages with no text and no image (`<Media omitted>` in WhatsApp exports) are dropped at the router's Layer 1 noise filter. No routing, no storage, no LLM call.

**Why it works:** In the SATA test case, ~22% of routed messages were empty-body with no image. The LLM can't extract any information from these — every call produced empty arrays or a low-severity "no content" ambiguity flag.

**Files changed:** `src/router/router.py` — added empty message check after noise type filter.

**Measured impact:** 2 fewer update_agent calls per 20 messages (14→12). Combined with caching, 20-message cost dropped to $0.121.

---

### Step 3: Linkage Agent Skip (additional ~46%)

**Change:** `linkage_worker.py` skips the linkage agent call when no client orders are open. Previously it only skipped when *both* client and supplier orders were empty.

**Why it works:** The linkage agent's job is to create M:N links between client orders and supplier orders. Without client orders, it can only observe "no client orders exist" — a constant observation that doesn't need per-message LLM reasoning. In the SATA test case (standard_procurement type, no separate client_order tasks), this eliminated all 13 linkage calls.

**Files changed:** `src/linkage/linkage_worker.py` — tightened skip condition.

**Note:** This optimisation is specific to the current task type distribution. When client_order tasks are active, the linkage agent will run normally.

---

### Step 4: Model Tiering — Haiku for Trivial Messages (additional ~30%)

**Change:** Added `_select_model()` function that routes trivial messages to Claude Haiku ($0.80/$4.00 per M tokens) instead of Sonnet ($3.00/$15.00).

**Classification heuristic:**
- **Sonnet:** message has image, body >40 chars, contains digits, or matches business keywords (order, cancel, confirm, deliver, payment, rate, price, Hindi quantity words)
- **Haiku:** everything else (acknowledgements like "ok", "thanks sir", "..", "Increased")

**Files changed:** `src/agent/update_agent.py`, `src/config.py` (added `CLAUDE_MODEL_FAST`)

**Measured impact (100 messages):** 15 Haiku calls at $0.0047/call avg vs 19 Sonnet calls at $0.016/call avg. Total $0.380 (down from estimated $0.50+ without tiering).

---

### Step 5: Context Pruning (additional ~8%)

**Change:** Reduced token count in the user section of update_agent prompts:
1. **Omit skipped nodes** from node states — model doesn't act on them
2. **Compact JSON** — removed indent=2, uses single-line format
3. **Dropped `name` field** from node states — model has names from system prompt template
4. **Omitted null confidence** — redundant for pending nodes
5. **Trimmed routing signal** — from 3 lines to 1

**Files changed:** `src/agent/prompt.py`

**Measured impact:** User section reduced 45% (1,227→672 tokens for typical call). Total cost $0.351 for 100 messages (down from $0.380).

---

### Step 6: Message Batching — 60s Window (additional ~34%)

**Change:** Messages routed to the same task_id within a 60-second window are batched into a single LLM call instead of individual calls.

**Batch design:**
- **Window:** 60 seconds since last message for a task_id
- **Max batch size:** 10 messages
- **Flush triggers:** window timeout, max size reached, or no more messages
- **Model selection:** per-batch — if any message in batch is complex → Sonnet, all trivial → Haiku
- **Prompt change:** `build_user_section` accepts `new_messages: list[dict]`, formats as "New messages (N)" section

**Why 60 seconds:** Analysis of sata_jobs chat (359 messages, 23 days):
- 39% of inter-message gaps ≤5s (rapid-fire WhatsApp)
- 59% ≤60s (conversational burst boundary)
- 83% of messages fall into 60s bursts
- Average burst: 3.4 messages; max: 10

**Files changed:** `src/router/worker.py` (batch buffer + flush logic in `run()`, new `process_message_batch()`), `src/agent/update_agent.py` (`run_update_agent` accepts `messages: list`), `src/agent/prompt.py` (`build_user_section` accepts list)

**Measured impact (100 messages):**
- LLM calls: 34→19 (44% fewer)
- Cost: $0.351→$0.230 (34% cheaper)
- 8 Haiku + 11 Sonnet calls

---

## Cost Tracking Infrastructure

- **`usage_log` table:** every LLM call logged with `tokens_in`, `tokens_out`, `cost_usd`, `model`, `cache_creation_input_tokens`, `cache_read_input_tokens`
- **`compute_cost()`** in `src/store/db.py`: handles per-model pricing including cache rates
- **Integration replay:** `replay_result.db` contains full usage_log for cost analysis
- **Developer portal:** `/developer/runs/` shows per-run cost data; `/developer/integration/` shows detailed state

---

## Remaining Opportunities

| Opportunity | Estimated impact | Effort | Notes |
|---|---|---|---|
| Gemini Flash for simple tasks | 10-50× cheaper than Haiku | M | Need to benchmark quality on update_agent tasks |
| Indian LLMs (Sarvam, Krutrim) | Variable | L | Better Hindi, data residency, but less tested |
| Local models (Qwen, Gemma) | Zero marginal cost | L | Only for noise filtering / entity extraction |
| Reduce MAX_CONTEXT_MESSAGES | ~10-20% on heavy tasks | S | Currently 20; most tasks need <10 |
| Linkage prompt caching improvement | ~10% on linkage calls | S | Linkage system prompt is only 1K tokens — less benefit |

---

## Pricing Reference (as of 2026-03-30)

| Model | Input | Output | Cache write | Cache read |
|---|---|---|---|---|
| Claude Sonnet 4.6 | $3.00/M | $15.00/M | $3.75/M | $0.30/M |
| Claude Haiku 4.5 | $0.80/M | $4.00/M | $1.00/M | $0.08/M |
| Gemini Flash 8B | $0.0375/M | $0.15/M | — | — |

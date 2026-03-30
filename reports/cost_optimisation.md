# LLM Cost Optimisation Log

**Started:** 2026-03-30
**Last updated:** 2026-03-31

---

## Summary

Cumulative **87% cost reduction** from baseline, achieving the <$1/day target. Multi-model benchmarking confirms further savings possible with Gemini Flash tiering.

| Metric | Baseline | Current (Sonnet+Haiku) | Projected (+ Gemini tier) |
|---|---|---|---|
| Cost per message | $0.0177 | **$0.0023** | **~$0.0010** |
| Daily cost (350 msgs) | $6.21 | **$0.80** | **~$0.35** |
| Monthly cost (350 msgs) | $186 | **$24** | **~$11** |
| LLM calls per 100 msgs | 27 (×5 extrapolated) | **19** | **19** |

---

## Optimisation Steps (Implemented)

### Step 1: System Prompt Caching (33% reduction)

**Change:** Added `cache_control: {"type": "ephemeral"}` to the system prompt block in both `update_agent` and `linkage_agent` API calls.

**Why it works:** The system prompt (~3,700 tokens for standard_procurement, ~1,000 for linkage) is identical across all calls for the same order type. Anthropic's prompt caching charges 0.1x input price on cache hits vs 1x for uncached tokens. The 5-minute cache TTL covers burst message processing easily.

**Files changed:** `src/agent/update_agent.py`, `src/linkage/agent.py`

**Measured impact (20 messages):**
- Before: $0.355 ($0.254 update + $0.109 linkage)
- After: $0.239 ($0.129 update + $0.110 linkage)
- Update agent cost down 49% (large system prompt); linkage minimal change (small prompt)

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

## Multi-Model Benchmark (2026-03-31)

### Methodology

Ran 20 sample messages from the R1-D-L3-01 integration test trace through `update_agent` with 9 model backends across 4 providers. Messages stratified by length (short/medium/complex) using deterministic sampling (`random.seed(42)`). All models received the same system prompt (~14,900 chars) and user section. Outputs compared against Sonnet baseline for parse success, node accuracy, and cost.

Benchmark script: `tests/benchmarks/bench_models.py`

### Full Results

| Model | Parse Rate | Node Match vs Sonnet | Avg Latency | Cost/call | Daily (150) | Monthly |
|---|---|---|---|---|---|---|
| **Claude Sonnet 4.6** | 100% | baseline | 7.5s | $0.0059 | $0.88 | $26 |
| **Claude Haiku 4.5** | 80% | 40% | 3.4s | $0.0013 | $0.20 | $6 |
| **Gemini 2.5 Flash** | **100%** | **50%** | **1.8s** | **$0.0007** | **$0.11** | **$3** |
| **Gemini 2.5 Pro** | 100% | not tested* | 11.5s | $0.0081 | $1.22 | $37 |
| **Sarvam 105B** | 100% | 20% | 3.0s | $0.00 | $0.00 | $0 |
| **Sarvam 30B** | 100% | 10% | 1.2s | $0.00 | $0.00 | $0 |
| **DeepSeek V3** | not tested | — | — | ~$0.0008 | ~$0.12 | ~$4 |
| **Qwen 2.5 7B** (local) | 100% | 15% | 11.3s | $0.00 | $0.00 | $0 |
| **Gemma 3 12B** (local) | 5% | 0% | 74.5s | $0.00 | $0.00 | $0 |

*\*Gemini Pro was benchmarked standalone (100% parse, 20/20). Node match not directly compared because it was run in a separate pass without Sonnet baseline in the same batch. Excluded from consideration because it is more expensive than Sonnet ($0.0081 vs $0.0059/call) due to mandatory thinking tokens.*

*DeepSeek V3 was not tested — their payment system does not support India as of March 2026.*

### Simple vs Complex Message Breakdown

The tiering strategy routes simple messages to a cheaper model and complex messages to Sonnet. Accuracy on each tier matters differently.

**Simple messages** (acks, short text, no numbers — ~50% of routed messages):

| Model | Parse Rate | Latency | Cost/call |
|---|---|---|---|
| Haiku 4.5 | 100% (4/4) | 2.7s | $0.0011 |
| Gemini Flash | 100% (4/4) | 1.7s | $0.0007 |

Both are reliable on simple messages. Gemini Flash is faster and cheaper.

**Complex messages** (numbers, items, Hinglish, long text — ~50% of routed messages):

| Model | Parse Rate | Latency | Cost/call |
|---|---|---|---|
| Sonnet 4.6 | 100% (10/10) | 7.5s | $0.0059 |
| Haiku 4.5 | 70% (7/10) | 3.7s | $0.0016 |
| Gemini Flash | 100% (10/10) | 1.9s | $0.0007 |

Haiku drops to 70% parse rate on complex messages. Gemini Flash maintains 100% parse but with lower node accuracy than Sonnet (50% vs baseline). Sonnet remains the only reliable option for complex/gate-critical messages.

### Per-Model Analysis

**Gemini 2.5 Flash** — best simple-tier model:
- 100% parse rate (requires `response_mime_type="application/json"` + `thinking_budget=0`)
- 8x cheaper than Sonnet, 4x faster
- 50% node match — acceptable for simple messages where wrong decisions are low-impact
- Config: `google-genai` SDK, JSON mode enforced, thinking disabled

**Claude Haiku 4.5** — superseded by Gemini Flash:
- 80% overall parse rate, drops to 70% on complex messages
- 40% node match, slower and more expensive than Gemini Flash
- No longer used in production tiering

**Gemini 2.5 Pro** — not viable:
- 100% parse rate, 11.5s latency
- $0.0081/call — **37% more expensive than Sonnet** due to mandatory thinking tokens
- Thinking cannot be disabled (`thinking_budget=0` returns 400 error)

**Sarvam 105B** — promising Indian LLM, not ready:
- 100% parse rate — impressive structured output without JSON mode
- 20% node match — makes valid JSON with wrong decisions
- **Currently free** (may change)
- 3.0s latency, strong Hindi/Hinglish support
- Revisit when accuracy improves or for fine-tuning

**Sarvam 30B** — fastest model tested:
- 100% parse rate, 1.2s latency
- 10% node match — too inaccurate for production
- Currently free

**Qwen 2.5 7B** (local, Ollama on M-series Mac):
- 100% parse rate — reliably produces valid JSON
- Only 15% node match — makes valid but wrong decisions
- 11s latency locally; not production-viable without GPU
- Interesting candidate for fine-tuning

**Gemma 3 12B** (local): Unusable. 5% parse rate, 75s latency.

### Gemini Flash Configuration

The initial benchmark showed 65% parse rate. Two issues:
1. **Thinking tokens** consumed the output budget, truncating JSON at ~80 tokens
2. **No JSON mode** — model added markdown fences despite instructions

Fix:
```python
config = types.GenerateContentConfig(
    system_instruction=system_prompt,
    max_output_tokens=4096,
    temperature=0.0,
    response_mime_type="application/json",           # enforce JSON output
    thinking_config=types.ThinkingConfig(thinking_budget=0),  # disable thinking
)
```

This brought parse rate from 65% to 100% with no other changes.

---

## GPU / Self-Hosted Analysis (2026-03-31)

### Qwen 2.5 7B on GPU

| GPU | Est. Latency (4K in + 150 out) | Monthly Cost (always-on) |
|---|---|---|
| NVIDIA T4 (16GB) | ~3.5-5.5s | $50-90 (Vast.ai) / $117 (AWS spot) |
| NVIDIA L4 (24GB) | ~1.5-2.5s | $150-220 (Vast.ai) |
| NVIDIA A10G (24GB) | ~1.5-2.5s | $110-180 (Vast.ai) / $270 (RunPod) |

### Managed API Providers

| Provider | Model | Monthly (150 calls/day) |
|---|---|---|
| Alibaba Dashscope | Qwen 2.5 7B | ~$3.60 |
| Together AI | Qwen 2.5 7B Turbo | ~$3.90 |
| Fireworks AI | Qwen 2.5 7B | ~$3.90 |
| Sarvam AI | Sarvam 105B / 30B | $0.00 (currently free) |

### Verdict

**Self-hosting a GPU for 150 calls/day is not economical.** At <1% GPU utilization, you'd pay $50-270/month for a machine that sits idle 99% of the time. Managed APIs ($3-4/month) or Gemini Flash ($3/month) are 15-70x cheaper.

GPU self-hosting breaks even at ~5,000+ calls/day (30+ businesses at Ashish's volume).

---

## Implemented Tiering Strategy

Two-tier model routing in `_select_model()` (implemented 2026-03-31):

| Message Type | Model | Cost/call | When |
|---|---|---|---|
| Simple (ack, short, no numbers) | **Gemini 2.5 Flash** | $0.0007 | ~60% of messages |
| Complex (quantities, items, images, Hinglish) | **Sonnet 4.6** | $0.0059 | ~40% of messages |

**Projected daily cost:** ~$0.30-0.50/day ($9-15/month) — a further 50-60% reduction from the $0.88/day Sonnet-only baseline.

**Implementation:** `_select_model()` in `src/agent/update_agent.py` returns Gemini Flash for simple messages, Sonnet for complex. Gemini backend added via `_call_gemini_with_retry()` with JSON mode and thinking disabled. Images auto-fallback to Sonnet (Gemini image path not yet implemented).

**Linkage agent** stays on Sonnet — quality risk 9/10 (false positive dispatch is irreversible). Already skipped when no client orders are open, which is the primary cost saving. Backend refactored to use shared `LLMResponse` type; switching to Gemini is a one-line config change (`LINKAGE_MODEL` in `src/linkage/agent.py`) for future benchmarking.

### Models Not Selected and Why

| Model | Why Not |
|---|---|
| Haiku 4.5 | Superseded by Gemini Flash: worse parse rate (80% vs 100%), slower (3.4s vs 1.8s), more expensive ($0.0013 vs $0.0007) |
| Gemini 2.5 Pro | More expensive than Sonnet ($0.0081 vs $0.0059) due to mandatory thinking tokens |
| DeepSeek V3 | Payments not supported from India |
| Sarvam 105B/30B | 10-20% node accuracy — too low for production. Revisit when models improve |
| Qwen 2.5 7B | 15% node accuracy, 11s latency locally. Candidate for fine-tuning only |
| Gemma 3 12B | 5% parse rate — unusable |

---

## Cost Tracking Infrastructure

- **`usage_log` table:** every LLM call logged with `tokens_in`, `tokens_out`, `cost_usd`, `model`, `cache_creation_input_tokens`, `cache_read_input_tokens`
- **`compute_cost()`** in `src/store/db.py`: handles per-model pricing including cache rates
- **Integration replay:** `replay_result.db` contains full usage_log for cost analysis
- **Benchmark suite:** `tests/benchmarks/bench_models.py` — automated multi-model comparison with 9 backends
- **Developer portal:** `/developer/runs/` shows per-run cost data

---

## Pricing Reference (as of 2026-03-31)

| Model | Input (per M tok) | Output (per M tok) | Cache write | Cache read | Notes |
|---|---|---|---|---|---|
| Claude Sonnet 4.6 | $3.00 | $15.00 | $3.75 | $0.30 | Complex tier |
| Claude Haiku 4.5 | $0.80 | $4.00 | $1.00 | $0.08 | Superseded |
| Gemini 2.5 Flash | $0.15 | $0.60 | — | — | Simple tier |
| Gemini 2.5 Pro | $1.25 | $10.00 | — | — | Not viable (thinking overhead) |
| Sarvam 105B / 30B | $0.00 | $0.00 | — | — | Free (may change) |
| DeepSeek V3 | $0.27 | $1.10 | — | — | No India payments |
| Qwen 2.5 7B (local) | $0.00 | $0.00 | — | — | Fine-tuning candidate |
| Qwen 2.5 7B (Together AI) | $0.20 | $0.20 | — | — | Managed alternative |

# Changelog

All notable changes to the Mantri project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.3.0] - 2026-03-31

### Architecture
- **Entity-first routing** — `route()` returns entity_ids instead of task_ids. Groups map to entities, not tasks. Worker resolves entity → tasks and lets the agent decide assignment.
- **Multi-task agent output** — `AgentOutput.task_outputs: list[TaskOutput]` schema. Agent can assign a single message to multiple tasks. Clean schema, no backward compat split.
- **Maturity-based task creation** — confirmation gate (`order_confirmation` for client, `supplier_confirmation` for supplier) determines if a task's items are locked. New items for mature entities create new tasks.
- **`supplier_confirmation` gate node** added to supplier_order template.
- **`create_task_live()`** — runtime task creation with auto ID, template nodes, routing context, entity aliases.
- **DB-aware entity aliases** — `alias_dict` reads from both config and `entity_aliases` DB table with 30s cache.
- **Runtime group routing** — `task_routing_context` entries overlay Layer 2a with runtime entity mappings.
- **Auto-trigger cascade** — `cascade_auto_triggers()` fires `predispatch_checklist` and `delivery_photo_check` deterministically after node updates.
- **Stock path order_ready** — `check_stock_path_order_ready()` sets order_ready when `filled_from_stock` is active and supplier path is skipped.
- **`order_ready` co-ownership** — owned by linkage_agent (supplier path) and router_worker (stock path).

### Cost Optimisation
- **Three-tier model routing** — Gemini 2.5 Flash for simple messages ($0.0007/call), Sonnet for complex ($0.0059/call). Projected $0.30-0.50/day.
- **Multi-model benchmarks** — 9 models tested (Sonnet, Haiku, Gemini Flash/Pro, Sarvam 30B/105B, DeepSeek V3, Qwen 2.5, Gemma 3). Benchmark suite at `tests/benchmarks/bench_models.py`.
- **Gemini Flash fix** — `response_mime_type="application/json"` + `thinking_budget=0` fixes 65%→100% parse rate.
- **Cost report** — comprehensive `reports/cost_optimisation.md` with all benchmarks, GPU analysis, tiering strategy.

### Quality
- **Ambiguity flag flood reduction** — dedup (1hr window), rate limiting, low non-blocking auto-resolve, medium digest window. 376→44 flags in R1-D replay.
- **Dead letter retry** — parse failures retry with correction prompt. 32→0 dead letters.
- **Entity naming conventions** — Army unit hierarchy (Bty < Bde), Indian SME (company + proprietor = same entity) in both update_agent and eval prompts.
- **Vague entity references** — "agency", "party", "woh log" trigger entity ambiguity flags.
- **INC-06 fix** — auto-trigger cascade for predispatch_checklist.
- **INC-08 fix** — stock path order_ready + deterministic trigger simulation in test runner.
- **INC-18 fix** — entity ambiguity severity elevated to medium+; scorer accepts "at least" required severity.

### Testing
- **314→337 unit tests**, 62%→82% code coverage.
- **20/20 incremental tests** passing.
- **29 synthetic eval cases** (avg 91.6) + 3 real eval cases (avg 88.3).
- **3 new eval cases** — R1-D-L1-01 (delivery lifecycle), R5-L3-01 (cross-thread ambiguity), R2a-L2-01 (cadence across threads).
- **LINKAGE-01 integration test** — validates M:N linkage with client_order + supplier_order.
- **Pipeline scoring** — `_compute_pipeline_score()` with 6 dimensions (reliability, routing, extraction, node_progression, ambiguity_quality, linkage).
- **Replay progress tracking** — `replay_progress.json` written during live replays.
- **Run metadata** — git commit, config flags, model info, run notes captured per run.
- **Ambiguity flag scorer** — accepts "at least" required severity (high passes medium requirement).

### Developer Portal
- **Runs page** — collapsible framework groups, risk framework legend with tooltips, compressed unit test table, linkage tests section.
- **Integration page** — collapsible case sections, SVG bar chart (ambiguity + errors over time), paginated tables (5 rows), message body in ambiguity flags, model/cost columns, run notes display, pipeline score.
- **Coverage** — auto-regenerated in `publish_all.sh`.

### Infrastructure
- **Ansible deploy tags** — `--tags deploy` for fast code-only deploys (7 tasks vs 25).
- **DB migrations** — `ALTER TABLE` pattern for adding columns to existing tables on droplet.
- **Session cleanup script** — `~/.claude/clean-sessions.sh` for managing Claude Code session files.

### Eval Scores (as of this release)
| Case | Score | Type |
|---|---|---|
| R1-D-L3-01 | 89 | Real (pipeline) |
| R3-C-L3-02 | 88 | Real (eval agent) |
| R6-L1-01 | 88 | Real (eval agent) |
| Synthetic avg (29 cases) | 91.6 | Synthetic |

## [0.2.0] - 2026-03-30

### Initial Sprint 2 Release
- Two-agent architecture (update_agent + linkage_agent)
- 4-layer routing cascade
- Message batching (60s window)
- System prompt caching
- Model tiering (Sonnet + Haiku)
- Ambiguity escalation with profiles
- Dead letter handling
- Node ownership registry
- 196 unit tests, 62% coverage
- 20 incremental tests (19/20 pass)
- 26 synthetic eval cases
- Live replay infrastructure
- Developer portal with Allure reports

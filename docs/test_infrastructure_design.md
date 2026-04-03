# Test Infrastructure Design

## Current State

The test infrastructure uses **pytest with flags** for all test execution.
No orchestration layer (test_runner.py) — stages are run directly via pytest
and scripts. Portal publishing is manual (`publish_all.sh`).

### Test Stages

```
unit ─────────────┐
                  ├──→ live_replay ──→ eval ──→ publish
incremental ──────┤
                  │
dry_replay ───────┘
```

| Stage | What it tests | Runner | Cost | Outputs |
|---|---|---|---|---|
| unit | Code correctness | `pytest tests/unit_tests/` | Free | coverage.json |
| incremental | Agent behavior on single messages | `run_incremental_test.py` | ~$0.50 | incremental summary JSON |
| dry_replay | Routing correctness | `pytest test_dry_replay.py` | Free | dry run records |
| live_replay | Full pipeline (route → scrap batch → agent → apply) | `pytest --run-live --traced` | $5-10 | replay_result.json, .db, pipeline_score.json, Phoenix traces |
| eval | Judge panel scores replay output | Automated + Claude Code | Free | score.json, Phoenix annotations |
| publish | Portal regeneration | `publish_all.sh` | Free | static/developer/ HTML |

### Live Replay Flags

| Flag | Purpose | Default |
|---|---|---|
| `--run-live` | Gate for LLM API calls | Required for full runs |
| `--traced` | Enable Phoenix OTEL tracing | Off |
| `--dev-test` | Short replay (50 msgs) with LLM caching + pre-seeded tasks | Off |
| `--agents AO,AL` | Which agents to run (AO=update, AL=linkage) | AO,AL |
| `--max-messages N` | Limit message count (mutually exclusive with --dev-test) | None |
| `--run-note` | Attach note to run record | Empty |
| `--phoenix-endpoint` | Phoenix URL: `local`, `remote`, or full URL. Repeatable. | Remote |

### Message Processing Architecture

All message processing goes through `replay_messages()` in `worker.py` — the
shared function used by both production `run()` and the test harness.

```
Message → route()
  ├─ Entity group (sata_jobs → entity_sata):
  │   → MessageBuffer.add(entity_id, sender, msg)
  │   → on sender gap (60s) or size (10): flush SenderScrap
  │   → _process_scrap() → resolve entity→task → process_message_batch()
  │
  └─ Conv group (Tasks → __conv_pending__):
      → ConversationRouter.feed(msg)
      → on gap: flush → scraps → reply trees → date matching → entity assignment
      → _process_conversation_result() → process_message_batch()
```

The test harness (`instrumented_replay.py`) adds only:
- **Tracing patches** on route(), _call_with_retry(), _resolve_task_for_entity(),
  _apply_output() — no behavior change, just Phoenix span recording
- **Dev-test cache** on _call_with_retry() — checks/stores LLM responses in
  `dev_cache.json` when `--dev-test` is active

Mock boundary: only Redis (StreamCapture: xadd, xack, drain_events) and DB path.
All business logic runs through production code unchanged.

### Dev-Test Mode

For quick iteration without LLM costs:

```bash
# First run — builds cache ($1-5, needs --run-live):
PYTHONPATH=. pytest tests/integration_tests/test_live_replay.py -v -s --run-live --dev-test -k R12

# Subsequent runs — uses cache (free, instant):
PYTHONPATH=. pytest tests/integration_tests/test_live_replay.py -v -s --dev-test -k R12
```

- Fixed 50 messages
- Pre-seeds tasks from seed_tasks.json
- Caches LLM responses in `<case_dir>/dev_cache.json` (keyed by prompt hash)
- No Phoenix tracing
- No conv router LLM matching
- Results to `/tmp/mantri_dev_test/<case>/` (not permanent)
- `--dev-test` without cache requires `--run-live` to build it

### Test Cases (Auto-discovered)

Any directory under `tests/integration_tests/` with `replay_trace.json` +
`seed_tasks.json` is auto-discovered by `_discover_cases()`.

| Case | Messages | Groups | Focus |
|---|---|---|---|
| R1-D-L3-01 | 904 | 4 | SATA multi-item multi-supplier, full pipeline |
| R3-C-L3-02 | 690 | 2 | Est Div concurrent client orders |
| R12-L3-01 | 306 (236 warmup + 70 test) | 5 | Conversation routing for shared groups |
| LINKAGE-01 | 6 | 1 | Client-supplier linkage |

### Seeding

- **Full runs** (`--run-live`): config only (entities, aliases, monitored_groups).
  Tasks created through the pipeline via nil-task creation.
- **Dev-test** (`--dev-test`): full seed (config + task instances + node trees).

### Portal

Dynamic columns with angled headers for numeric stats. Tags:
- **E** — entity-first routing
- **I** — conversation routing (shared/internal groups)
- **T** — Phoenix traced
- **AO/AL** — agents (update/linkage)

Columns auto-appear when run records contain data (warmup, conversations,
entities discovered, tasks created live, etc.).

### Eval Stage: Judge Panel

| Judge | Type | Cost | What it evaluates |
|---|---|---|---|
| Deterministic scorers | Automated | Free | Routing accuracy, parse success, dead letters, task creation |
| rapidfuzz item matcher | Automated | Free | Item name matching against baselines |
| Gemini Flash LLM judge | Automated | Free | Fuzzy semantic matching (multilingual items, ambiguity quality) |
| Claude Code sub-agent | Automated (async) | Free | Authoritative holistic judgment |

Eval flow:
```
live_replay completes
  ├─→ eval_auto: deterministic + Gemini judges → scores in Phoenix
  ├─→ publish: portal with automated scores
  └─→ eval_review: Claude Code sub-agent (background, async)
       → authoritative score → re-publish
```

---

## TODO: Planned Features

### Unified Orchestration (test_runner.py)

A unified entry point for all test execution. Not yet implemented.

**Key features planned:**
- `--record` vs `--dev` modes (dev = default, no side effects; record = official results)
- SQLite state DB (`.test-state/pipeline.db`) tracking stage state and run history
- Pipeline command: `test_runner.py pipeline` runs all stages, skips fresh ones
- Automatic staleness detection via `git diff` against last-passed commit
- Cost gating: full replays blocked in dev mode, short replays require confirmation
- Failure classification: infra failures (restartable) vs test failures (recorded)

### /tmp Artifact Isolation

Scripts write to `/tmp` first, atomically promoted to canonical on success.
Prevents corrupt artifacts from killed processes.

Currently: `MANTRI_OUTPUT_DIR` env var partially supports this. Full atomic
promotion not yet implemented.

### Git Branching for Record Runs

Per-stage commits on a dedicated test branch (`test/record-YYYYMMDD-HHMM`),
rebased and merged on pipeline completion. Keeps test artifact commits
separate from code commits on main.

### State Machine

Each stage tracks: NEVER_RUN → PASSED | FAILED | INFRA_FAILED | SKIPPED | STALE.
Downstream stages marked STALE when dependencies update.

### Eval-synth → Incremental Migration

The 29 synthetic eval cases (R2a, R2b, R3-C-L1, R4-A, etc.) to be migrated
into incremental tests (INC-21+). They test isolated agent behavior on crafted
inputs, same as incremental.

### Crash Recovery

Detect crashed runs (stage in RUNNING state but no lock file) and clean up
partial artifacts automatically.

---

## Design Discussion Summary

### Problem statement (2026-04-01)

During development, several test infrastructure problems surfaced:
- Stale portal results not reflecting current code
- Forgotten eval runs and portal publishing
- Corrupt replay_result.json from killed processes
- No distinction between throwaway dev runs and official results
- No cost protection against accidental expensive replays

### Key design decisions

1. **Record vs Dev mode** — default dev (safe), record requires opt-in + clean git
2. **DIY over Prefect** — 41 deps, overkill for 7 stages. DIY ~400 lines, zero deps
3. **/tmp isolation** — atomic promotion prevents corrupt canonical artifacts
4. **Git branching** — test artifact commits on separate branch, per-stage commits
5. **Staleness via git diff** — deterministic, not timestamp-based
6. **Cost gating** — full replays blocked in dev, short replays require confirmation
7. **SKIPPED state** — pipeline profiles can skip stages without blocking downstream
8. **Judge panel** — automated judges + Claude Code sub-agent, async, non-blocking
9. **Eval depends on live_replay** — judges actual pipeline output, not separate runs
10. **Eval-synth → incremental** — eliminate duplicate test systems
11. **Sub-agent eval review** — async background, publishes immediately with auto scores

### Rejected alternatives

- **Prefect**: too heavyweight (41 deps, learning curve)
- **DVC**: wrong paradigm for tests
- **Phased /tmp migration**: defeats atomicity guarantee
- **JSON manifest over SQLite**: not queryable
- **Eval-real as separate agent run**: tested LLM capability, not pipeline quality

# Test Infrastructure Design

## Current State

The test infrastructure now uses a staged orchestration layer in
`scripts/test_runner.py` alongside direct pytest and script entrypoints.

Current execution modes:
- direct pytest for unit and replay tests
- script-driven incremental and publishing flows
- staged orchestration with run/state tracking in `.test-state/pipeline.db`

Portal publishing is a tracked stage, though shell wrappers such as
`publish_all.sh` are still used underneath.

### Test Stages

```
unit ─────────────┐
incremental ──────┼──→ live_replay ──→ eval_auto ──→ eval_review ──→ publish
dry_replay ───────┘
```

| Stage | What it tests | Runner | Cost | Outputs |
|---|---|---|---|---|
| unit | Code correctness, deterministic module behavior | `pytest` via `pytest.ini` | Free | unit summary JSON, coverage/allure artifacts |
| incremental | `update_agent` behavior on single-message and short multi-step cases | `scripts/run_incremental_test.py` | Low API cost when live | incremental summary JSON, per-case results |
| dry_replay | End-to-end replay without live LLM calls | `pytest tests/integration_tests/test_dry_replay.py` | Free | dry run records, replay artifacts |
| live_replay | Full pipeline replay through router, batching, update agent, linkage agent, DB state, optional Phoenix tracing | `pytest tests/integration_tests/test_live_replay.py` | Highest | `replay_result.json`, `.db`, `pipeline_score.json`, run records, Phoenix traces |
| eval_auto | Automated scoring and judge push after replay | tracing/eval scripts and `push_eval.py` | Low to free depending on judges | score artifacts, Phoenix annotations |
| eval_review | Secondary review stage tracked by `test_runner.py` | staged runner | Free | review outputs / stage record |
| publish | Portal regeneration | `scripts/publish_all.sh` via stage runner or direct shell | Free | `static/developer/` HTML |

### Test Layers Beyond Unit Tests

The active non-unit layers in the repo are:

- `tests/functional_tests/`
  - incremental cases `INC-*`
  - deterministic fixtures for `update_agent`

- `tests/integration_tests/`
  - replay-driven end-to-end cases
  - `test_dry_replay.py`
  - `test_live_replay.py`

- `tests/evals/`
  - eval scenario corpus
  - metadata, threads, saved agent outputs, and score files
  - used by replay scoring and judges, not by default pytest discovery

- `tests/benchmarks/`
  - model benchmark scripts and result snapshots

- `tests/runs/`
  - historical stage outputs and run records
  - artifacts, not test definitions

### Default Pytest Behavior

`pytest.ini` points default discovery at `tests/unit_tests` only. That means:

- plain `pytest` runs unit tests
- non-unit stages are normally run through explicit paths or `scripts/test_runner.py`

## Live Replay Flags

| Flag | Purpose | Default |
|---|---|---|
| `--run-live` | Gate for live LLM/API calls | Required for fresh live runs |
| `--traced` | Enable Phoenix OTEL tracing | Off |
| `--dev-test` | Short replay with response caching and pre-seeded tasks | Off |
| `--agents AO,AL` | Which agents to run (`AO` = orders/update, `AL` = linkage) | `AO,AL` |
| `--max-messages N` | Limit message count | None |
| `--run-note` | Attach note to run record | Empty |
| `--phoenix-endpoint` | Phoenix OTEL endpoint(s); repeatable | Remote default in tracing code |

## Message Processing Architecture

All replayed message processing goes through `replay_messages()` in
`src/router/worker.py`, which mirrors the production message path.

```
Message → route()
  ├─ Entity-routed group:
  │   → MessageBuffer.add(entity_id, sender, msg)
  │   → on sender gap or size limit: flush SenderScrap
  │   → _process_scrap() → resolve entity→task → process_message_batch()
  │
  └─ Shared/internal group:
      → ConversationRouter.feed(msg)
      → on gap: flush → scrap/reply-tree/date/item matching → entity assignment
      → _process_conversation_result() → process_message_batch()
```

The replay harness in `src/tracing/instrumented_replay.py` adds:

- tracing wrappers around routing, task resolution, LLM calls, and output application
- optional dev-test caching of LLM responses
- mocked infrastructure boundaries for Redis and DB path only

Business logic is otherwise the production code path.

## Dev-Test Mode

For quick iteration with cached LLM responses:

```bash
# First run — builds cache:
PYTHONPATH=. pytest tests/integration_tests/test_live_replay.py -v -s --run-live --dev-test -k R12

# Subsequent runs — reuses cache:
PYTHONPATH=. pytest tests/integration_tests/test_live_replay.py -v -s --dev-test -k R12
```

Current behavior:

- fixed short replay window in dev-test mode
- tasks are pre-seeded from `seed_tasks.json`
- Phoenix tracing is disabled
- conversation-router LLM matching is disabled
- cache now commonly exists as `dev_cache.db`, with some older case dirs also
  containing `dev_cache.json`
- results are intended for temporary/dev iteration rather than canonical run history

## Test Cases

Any directory under `tests/integration_tests/` with replay inputs is
treated as a replay case by the integration harness.

Examples currently present:

| Case | Focus |
|---|---|
| `R1-D-L3-01_sata_multi_item_multi_supplier` | Large full-pipeline multi-supplier case |
| `R3-C-L3-02_est_div_concurrent_client` | Concurrent client orders |
| `R12-L3-01_internal_staff_conversation_routing` | Shared-group conversation routing |
| `LINKAGE-01_client_supplier_linkage` | Focused client/supplier linkage behavior |

## Seeding

Two broad patterns exist:

- full live-style replay
  - config and monitored groups are loaded
  - runtime task creation can happen through the pipeline

- dev-test / seeded replay
  - tasks, entities, aliases, and node trees are preloaded from
    `seed_tasks.json`

Case generation and seeding tools live in:

- `scripts/build_integration_case.py`
- `scripts/build_seed_tasks.py`
- `scripts/build_expected_routing.py`
- `scripts/build_replay_trace.py`

## Portal

Developer portal publishing is part of the staged workflow now, not just an
ad hoc manual step. Outputs land under `static/developer/`, and stage metadata
is tracked by `scripts/test_runner.py`.

## Eval Stages

The replay/eval path is split into two stages in the current runner:

- `eval_auto`
  - deterministic scorers
  - replay judges
  - optional LLM judges
  - Phoenix annotation push

- `eval_review`
  - secondary review stage tracked separately from automated scoring

The most accurate implementation reference for this layer is the code in
`src/tracing/`, especially:

- `src/tracing/push_eval.py`
- `src/tracing/scorers.py`
- `src/tracing/judges.py`
- `src/tracing/llm_judges.py`
- `src/tracing/deepeval_dag.py`

## Orchestration Layer

### `scripts/test_runner.py`

The staged runner is already implemented.

Current capabilities include:

- stage registry and dependencies
- SQLite-backed run/state tracking in `.test-state/pipeline.db`
- status, run, pipeline, retry, publish, cleanup, and recommend commands
- cost hints and cost-gating metadata for expensive stages
- artifact tracking

This section was previously planned-only. It is now current state.

### SQLite State DB

The repo currently tracks stage and run state in `.test-state/pipeline.db`.
The exact schema is code-owned in `scripts/test_runner.py`, but conceptually
it stores:

- run records
- per-stage state
- timestamps, git context, config, errors, artifacts, and cost fields

## Still Planned / Incomplete

The following are still genuinely incomplete or only partially implemented:

### /tmp Artifact Isolation

The codebase has partial support for temp output dirs via `MANTRI_OUTPUT_DIR`,
but full atomic promote-on-success behavior is not consistently implemented
across every stage.

### Failure Classification Refinement

The runner already distinguishes failure modes, but infra/test/crash semantics
still depend on stage runner behavior and could be tightened further.

### Staleness Propagation

The general idea exists in the runner design, but the exact downstream
staleness rules should be treated as evolving implementation, not a finished
stable contract.

## Source Of Truth

This document is a high-level map. When it conflicts with code, prefer:

- `scripts/test_runner.py`
- `tests/integration_tests/README.md`
- `scripts/run_incremental_test.py`
- `src/tracing/instrumented_replay.py`
- `pytest.ini`

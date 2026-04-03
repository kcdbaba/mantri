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

#### Run Modes

**`--dev` (default):** For development iteration. Quick feedback, no side effects.
- Artifacts: written to `/tmp/mantri_dev/{stage}/` only
- Run records: NOT written to tests/runs/
- Portal: NOT regenerated
- Git state: any (uncommitted changes OK)
- **Cost gating**: full replays BLOCKED. Short replays require confirmation.

**`--record`:** For official results. Produces artifacts for posterity.
- Artifacts: scripts write to `/tmp`, atomically moved to canonical on success
- Run records: written to tests/runs/
- Portal: automatically regenerated
- Git state: MUST be clean (no uncommitted changes in src/)
- Committed: per-stage commits on a test branch

#### State Machine

| State | Meaning |
|---|---|
| NEVER_RUN | No record of this stage ever running |
| PASSED | Last record run passed |
| FAILED | Last record run failed (test failure) |
| INFRA_FAILED | Last record run failed due to infrastructure (restartable) |
| STALE | A dependency was updated since last pass |
| SKIPPED | Explicitly skipped by user or pipeline config |

State transitions:
```
NEVER_RUN ──→ PASSED | FAILED | INFRA_FAILED | SKIPPED
PASSED ──→ STALE (when dependency updates)
STALE ──→ PASSED | SKIPPED (on re-run)
FAILED ──→ PASSED (on re-run)
INFRA_FAILED ──→ PASSED (on re-run)
```

#### SQLite State DB

Location: `.test-state/pipeline.db` (gitignored)

```sql
CREATE TABLE stages (
    name        TEXT PRIMARY KEY,
    runner      TEXT NOT NULL,
    is_manual   INTEGER DEFAULT 0,
    cost_hint   TEXT,
    depends_on  TEXT                -- JSON array of stage names
);

CREATE TABLE runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stage       TEXT NOT NULL,
    mode        TEXT NOT NULL,       -- "record" or "dev"
    status      TEXT NOT NULL,       -- "passed", "failed", "infra_failed", "crashed"
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    duration_s  REAL,
    git_commit  TEXT,
    git_dirty   INTEGER,
    config      TEXT,               -- JSON: model, flags, max_messages, etc.
    error       TEXT,
    artifacts   TEXT,               -- JSON: {path: hash} for produced artifacts
    cost_usd    REAL,
    triggered_by TEXT               -- "cli", "pipeline", "retry"
);

CREATE TABLE stage_state (
    stage       TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    last_record_run_id INTEGER,
    last_passed_at TEXT,
    last_passed_commit TEXT,
    stale_reason TEXT
);
```

#### CLI Interface

```bash
# Status
python scripts/test_runner.py status
python scripts/test_runner.py status --verbose

# Single stage
python scripts/test_runner.py run unit
python scripts/test_runner.py run incremental INC-04
python scripts/test_runner.py run unit --record

# Pipeline
python scripts/test_runner.py pipeline
python scripts/test_runner.py pipeline --from eval_real
python scripts/test_runner.py pipeline --force

# Other
python scripts/test_runner.py retry
python scripts/test_runner.py publish
python scripts/test_runner.py cleanup
```

#### Taskfile Aliases

```yaml
version: '3'
tasks:
  status:   { cmds: ["python scripts/test_runner.py status"] }
  unit:     { cmds: ["python scripts/test_runner.py run unit {{.CLI_ARGS}}"] }
  inc:      { cmds: ["python scripts/test_runner.py run incremental {{.CLI_ARGS}}"] }
  replay:   { cmds: ["python scripts/test_runner.py run live_replay {{.CLI_ARGS}}"] }
  pipeline: { cmds: ["python scripts/test_runner.py pipeline {{.CLI_ARGS}}"] }
  publish:  { cmds: ["python scripts/test_runner.py publish"] }
```

### /tmp Artifact Isolation

Scripts write to `/tmp` first, atomically promoted to canonical on success.
Prevents corrupt artifacts from killed processes.

Currently: `MANTRI_OUTPUT_DIR` env var partially supports this. Full atomic
promotion not yet implemented.

```python
def run_record_stage(stage, config):
    temp_dir = f"/tmp/mantri_record_{stage}_{int(time.time())}"
    os.makedirs(temp_dir)
    run_id = record_run_start(stage)

    try:
        _execute_script(stage, config, output_dir=temp_dir)

        # Atomic promote
        for artifact_name, canonical_path in STAGE_CONFIG[stage]["artifacts"].items():
            temp_path = os.path.join(temp_dir, artifact_name)
            if os.path.exists(temp_path):
                os.rename(temp_path, canonical_path)

        hashes = compute_artifact_hashes(stage)
        record_run_success(run_id, artifact_hashes=hashes)
        commit_stage_artifacts(stage)

    except INFRA_EXCEPTIONS as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        record_run_infra_failed(run_id, str(e))
        raise
    except subprocess.CalledProcessError as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        record_run_failed(run_id, str(e))
        raise
    except (KeyboardInterrupt, SystemExit):
        shutil.rmtree(temp_dir, ignore_errors=True)
        record_run_crashed(run_id)
        raise
```

### Failure Classification

```python
INFRA_EXCEPTIONS = (
    ConnectionError, TimeoutError,
    requests.ConnectionError, requests.Timeout,
    redis.ConnectionError,
)

def classify_failure(exception):
    if isinstance(exception, subprocess.CalledProcessError):
        if exception.returncode == 1:
            return "failed"          # pytest test failure
        return "infra_failed"        # unexpected exit code
    if isinstance(exception, INFRA_EXCEPTIONS):
        return "infra_failed"
    if isinstance(exception, (KeyboardInterrupt, SystemExit)):
        return "crashed"
    return "infra_failed"            # unknown = assume infra (restartable)
```

### Staleness Detection

```python
def mark_downstream_stale(completed_stage):
    for stage in ALL_STAGES:
        if completed_stage in stage.depends_on:
            if stage_state[stage] in ("PASSED", "SKIPPED"):
                stage_state[stage] = "STALE"
                stage_state[stage].stale_reason = f"{completed_stage} updated"

def check_source_staleness():
    last_unit_commit = stage_state["unit"].last_passed_commit
    if not last_unit_commit:
        return
    changed = subprocess.run(
        ["git", "diff", "--name-only", last_unit_commit, "HEAD", "--", "src/"],
        capture_output=True, text=True
    ).stdout.strip()
    if changed:
        mark_stale("unit", f"src/ changed since {last_unit_commit[:8]}")
```

### Git Branching for Record Runs

```
main (your code commits)
  │
  ├── git checkout -b test/record-20260402-0930
  │     ├── commit: [test:record] unit PASSED (362/362)
  │     ├── commit: [test:record] incremental PASSED (20/20)
  │     ├── commit: [test:record] live_replay PASSED (R1-D: 77/100)
  │     └── commit: [test:record] publish portal regenerated
  │
  ├── git fetch origin main
  ├── git rebase origin/main
  ├── git checkout main && git merge --ff-only test/record-20260402-0930
  └── git branch -d test/record-20260402-0930
```

```python
def commit_stage_artifacts(stage, status, summary):
    artifacts = STAGE_CONFIG[stage]["artifacts"]["record"]
    subprocess.run(["git", "add"] + [a for a in artifacts if os.path.exists(a)])
    msg = f"[test:record] {stage} {status.upper()}"
    if summary:
        msg += f" ({summary})"
    subprocess.run(["git", "commit", "-m", msg])
```

### Upstream Artifact Integrity

```python
def verify_upstream_artifacts(stage):
    for dep in STAGE_CONFIG[stage]["depends_on"]:
        run = get_last_record_run(dep)
        if not run:
            continue
        for path, expected_hash in json.loads(run["artifacts"]).items():
            if not os.path.exists(path):
                raise IntegrityError(f"{path} missing")
            if hash_file(path) != expected_hash:
                raise IntegrityError(f"{path} modified since {dep} passed")
```

### Crash Recovery

```python
def detect_crashed_runs():
    running = get_stages_in_state("RUNNING")
    if running and not is_locked():
        for stage in running:
            cleanup_partial_artifacts(stage)
            record_run_crashed(stage)
```

### Cost Gating

| Stage | Dev mode behavior |
|---|---|
| unit, dry_replay | Ungated — free |
| incremental | Ungated — cheap (~$0.50) |
| live_replay (full) | **BLOCKED** |
| live_replay (--max-messages) | **CONFIRM** with cost estimate |

### Eval-synth → Incremental Migration

The 29 synthetic eval cases (R2a, R2b, R3-C-L1, R4-A, etc.) to be migrated
into incremental tests (INC-21+). They test isolated agent behavior on crafted
inputs, same as incremental.

### Portal Publishing Integration

| Section | Triggered by | Generator |
|---|---|---|
| unit + coverage | unit | publish_runs.py |
| integration (INC + linkage) | incremental | publish_runs.py |
| system (replay + eval) | live_replay, eval | publish_system.py |
| allure report | unit, incremental | publish_allure.py |

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

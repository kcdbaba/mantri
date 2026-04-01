# Test Infrastructure Design

## Design Goals

1. **Unified entry point** — every test execution (ad-hoc single case, full suite, full pipeline) goes through one tool (`test_runner.py`). No more running scripts directly and forgetting downstream steps.

2. **Record vs Dev distinction** — official results for posterity (`--record`) are separated from throwaway development iterations (`--dev`). Dev runs never pollute official artifacts, portal, or git history.

3. **Artifact integrity** — test artifacts are never partially written. Scripts write to `/tmp`, and only on success are artifacts atomically moved to canonical locations. A crash mid-write leaves canonical artifacts untouched. *Reason: we had corrupt replay_result.json files from killed processes overwriting good results.*

4. **Automatic downstream awareness** — completing a stage marks dependent stages as stale. The pipeline tells you what needs to run next. *Reason: we repeatedly forgot to run eval after replay, resulting in stale scores in the portal.*

5. **Failure classification** — infra failures (API timeout, Phoenix down) are restartable without re-running completed stages. Test failures (wrong agent output) are recorded as results. *Reason: replays cost $5-10; re-running a full pipeline because Phoenix timed out at the eval push step wastes money.*

6. **Cost protection** — expensive stages (live replay) are blocked in dev mode and require confirmation with cost estimates. *Reason: we accidentally spent $10 on replays that were abandoned due to tracer bugs.*

7. **Git traceability** — every official result is committed with a structured message at a known git commit. The pipeline can reconstruct test history from git log alone. *Reason: we couldn't tell whether portal results reflected the current code or an older version.*

8. **Clean git history** — test artifact commits are on a separate branch, rebased and merged on pipeline completion. Code commits and test commits don't interleave. *Reason: auto-committing test artifacts on main would create noisy history and conflict with active development.*

9. **Manual stage support** — eval-real runs in Claude Code, not as an automated script. The pipeline tracks it as PENDING_MANUAL and continues when marked complete. *Reason: eval-real uses Claude Code's reasoning (free under Max subscription) and cannot be automated.*

10. **Portal publishing is a pipeline stage** — not a separate "remember to publish" step. After any record run, affected portal sections regenerate and commit automatically. *Reason: the portal was frequently stale because publishing was a manual afterthought.*

11. **Existing scripts preserved** — the pipeline wraps existing test runners, it doesn't replace them. Scripts are modified only to support `/tmp` output directories. *Reason: the scripts work; we need orchestration, not rewrites.*

---

## Test Stages

```
unit ─────────────┐
                  ├──→ live_replay ──→ eval ──→ publish
incremental ──────┤
                  │
dry_replay ───────┘

All stages are technically independent (pipeline warns but doesn't block).
eval depends on live_replay (needs actual LLM output to judge).
```

| Stage | What it tests | Runner | Automated? | Cost | Outputs |
|---|---|---|---|---|---|
| unit | Code correctness | pytest via run_unit_tests.sh | Yes | Free | coverage.json |
| incremental | Agent behavior on single messages | run_incremental_test.py | Yes | ~$0.50 (LLM) | incremental summary JSON |
| dry_replay | Routing correctness | pytest test_dry_replay.py | Yes | Free | dry run records |
| live_replay | Full pipeline (route → agent → apply) | pytest --run-live --traced | Yes | $5-10 (LLM) | replay_result.json, .db, pipeline_score.json, Phoenix traces |
| eval | Judge panel scores replay output | Automated + Claude Code (manual) | Partially | Free | score.json, judge annotations in Phoenix |
| publish | Portal regeneration | publish scripts | Yes | Free | static/developer/ HTML |

### Eval-synth merged into incremental

The 29 synthetic eval cases (R2a, R2b, R3-C-L1, R4-A, R4-B, R5, R6, etc.)
are migrated into incremental tests (INC-21..INC-49 or similar). They test
the same thing — single message/thread through the agent, check output — just
with different input format. The separate eval-synth stage is eliminated.

*Reason: eval-synth cases don't go through the live pipeline. They're
isolated agent behavior tests, which is exactly what incremental tests are.
Maintaining two separate systems for the same purpose adds complexity.*

Migration: convert each `tests/evals/{SYNTH_CASE}/threads.txt` +
`metadata.json` into incremental test format (`new_message.json` +
`expected_updates.json`). The scoring logic already exists in
`run_incremental_test.py`.

### Eval stage: judge panel

The eval stage uses multiple judges on the same live replay output. Automated
judges run first (instant, free), then Claude Code provides the authoritative
judgment.

| Judge | Type | Cost | What it evaluates |
|---|---|---|---|
| Deterministic scorers | Automated | Free | Routing accuracy, parse success rate, dead letter rate, task creation sanity |
| rapidfuzz item matcher | Automated | Free | Item name matching against baselines |
| Gemini Flash LLM judge | Automated (LLM) | Free | Fuzzy semantic matching (multilingual items, ambiguity quality) |
| Claude Code | Manual | Free (Max sub) | Authoritative holistic judgment — reasoning about context, catching subtle issues |

Eval flow:
```
live_replay completes
  │
  ├──→ eval_auto (immediate, automated)
  │      → deterministic scorers + rapidfuzz + Gemini judges
  │      → scores stored in score.json + Phoenix annotations
  │      → eval_auto marked PASSED
  │
  ├──→ publish (with automated scores — immediate)
  │
  └──→ eval_review (background sub-agent, async)
         → Claude Code sub-agent reviews replay output + automated scores
         → writes authoritative score, overrides, annotations
         → eval_review marked PASSED
         → re-publish portal with updated scores
```

### eval_auto: fully automated

Runs immediately after live_replay with zero human involvement. Produces
the same deterministic + LLM judge scores we built today. This is the
baseline eval — always available, always runs.

### eval_review: automated via sub-agent

NOT a manual stage. Launched as a background Claude Code sub-agent that
reasons about the replay output and produces an authoritative judgment.

```python
def run_eval_review(case_dir):
    """Launch a background sub-agent to review replay output."""
    prompt = f"""
    Review the live replay output for {case_dir.name}.

    Read:
    - {case_dir}/replay_result.json (pipeline state: nodes, items, flags)
    - {case_dir}/pipeline_score.json (automated dimension scores)
    - {case_dir}/eval_baselines*.json (expected outcomes)

    For each dimension (task_recall, entity_accuracy, node_updates,
    item_extraction, ambiguity_quality), provide:
    - Score (0-100)
    - Whether you agree with the automated judge's score
    - Specific issues found
    - Overall verdict (PASS/PARTIAL/FAIL)

    Write your judgment to {case_dir}/score_review.json
    """
    # Launch as background agent in current Claude Code session
    # or as a separate claude process
    launch_background_agent(prompt)
```

The review runs asynchronously — the pipeline doesn't wait for it. Portal
publishes first with automated scores, then re-publishes when the review
completes.

*Reason for sub-agent instead of manual: the review is structured reasoning
against known inputs — exactly what a Claude Code sub-agent does well. No
human judgment required. Making it automated means eval never blocks the
pipeline and never gets forgotten.*

*Reason for async (not blocking): the review takes minutes (sub-agent
reasoning). The automated scores are good enough for immediate publishing.
The review adds depth, not urgency.*

*Fallback: if sub-agents aren't available (e.g., running outside Claude
Code), eval_review falls back to PENDING_MANUAL and the developer reviews
in their next Claude Code session.*

*Reason for panel approach: automated judges are fast and free but miss
subtle issues. Claude Code review is thorough. Running both lets us
cross-validate — disagreements between automated and review judges highlight
either judge bugs or genuine edge cases worth investigating.*

*Reason for eval depending on live_replay: eval judges the ACTUAL pipeline
output (replay_result.json), not a separate agent run. Without a replay,
there's nothing to judge.*

---

## Run Modes

### `--dev` (default)

For development iteration. Quick feedback, no side effects.

- Artifacts: written to `/tmp/mantri_dev/{stage}/` only. Never touch canonical locations.
- Run records: NOT written to tests/runs/
- SQLite state: NOT updated
- Portal: NOT regenerated
- Phoenix: traces to `mantri-dev` project or skipped
- Git state: any (uncommitted changes OK)
- **Cost gating**: full replays BLOCKED in dev mode. Short replays (`--max-messages`)
  require interactive confirmation with cost estimate before proceeding. Unit tests,
  incremental tests, dry replays, and evals are ungated (free or near-free).
- Use case: "I'm fixing INC-04, let me run it to check"

*Reason for default=dev: prevents accidental pollution of official results. You must explicitly opt into `--record`.*

### `--record`

For official results. Produces artifacts for posterity.

- Artifacts: scripts write to `/tmp/mantri_record_{stage}_{timestamp}/`, atomically moved to canonical locations on success only
- Run records: written to tests/runs/
- SQLite state: updated (stage marked as completed)
- Portal: automatically regenerated for affected sections
- Phoenix: traces to `mantri` project
- Git state: MUST be clean (no uncommitted changes in src/)
- Committed: per-stage commits on a test branch, rebased and merged on pipeline completion
- Use case: "I want to record the official score after merging the fix"

*Reason for clean git requirement: ensures every official result maps to a specific committed codebase version.*

---

## State Machine

Each stage has one of these states:

| State | Meaning |
|---|---|
| NEVER_RUN | No record of this stage ever running |
| PASSED | Last record run passed |
| FAILED | Last record run failed (test failure — recorded as result) |
| INFRA_FAILED | Last record run failed due to infrastructure (restartable) |
| STALE | A dependency was updated since last pass |
| PENDING_MANUAL | Upstream completed, this stage needs manual execution |
| SKIPPED | Explicitly skipped by user or pipeline config |

State transitions:
```
NEVER_RUN ──→ PASSED (on success)
NEVER_RUN ──→ FAILED (on test failure)
NEVER_RUN ──→ INFRA_FAILED (on infra failure)
NEVER_RUN ──→ SKIPPED (user/config skip)
PASSED ──→ STALE (when dependency updates)
STALE ──→ PASSED (on re-run success)
STALE ──→ SKIPPED (user/config skip)
FAILED ──→ PASSED (on re-run success)
INFRA_FAILED ──→ PASSED (on re-run success)
SKIPPED ──→ PASSED (on explicit run)
SKIPPED ──→ STALE (when dependency updates)
any ──→ PENDING_MANUAL (for manual stages when upstream completes)
```

SKIPPED is set when:
- User passes `--skip <stage>` to the pipeline command
- A pipeline profile excludes the stage (e.g., `--profile quick` skips live_replay and eval)
- Dev mode blocks a stage (e.g., full replay blocked in dev mode)

SKIPPED stages do NOT block downstream stages — the pipeline continues past
them. But `status` shows them clearly, and downstream stages that depend on
a SKIPPED stage inherit a warning: "eval_real depends on live_replay which
was SKIPPED — eval results may not reflect current code."

*Reason for SKIPPED not blocking: you should be able to run `--profile quick` (unit + incremental only) without the pipeline refusing because live_replay was skipped.*

---

## SQLite State DB

Location: `.test-state/pipeline.db` (gitignored — ephemeral state, reconstructible from git)

*Reason for SQLite over JSON manifest: queryable run history, atomic updates, no file-locking issues, stdlib support.*

### Tables

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

---

## CLI Interface

All through `scripts/test_runner.py`:

```bash
# Status
python scripts/test_runner.py status              # show all stages + state
python scripts/test_runner.py status --verbose     # show recent run history

# Single stage (dev mode, default)
python scripts/test_runner.py run unit             # dev run
python scripts/test_runner.py run incremental      # dev run, all 20
python scripts/test_runner.py run incremental INC-04  # dev run, single case

# Record mode
python scripts/test_runner.py run unit --record    # official run
python scripts/test_runner.py run incremental --record

# Pipeline (always record mode)
python scripts/test_runner.py pipeline             # full pipeline, skips fresh stages
python scripts/test_runner.py pipeline --from eval_real  # start from a stage
python scripts/test_runner.py pipeline --force     # re-run everything

# After manual stages
python scripts/test_runner.py complete eval_real   # mark manual stage as done

# Retry infra failure
python scripts/test_runner.py retry                # retry last infra-failed stage

# Publish only
python scripts/test_runner.py publish              # regenerate portal

# Cleanup
python scripts/test_runner.py cleanup              # delete stale test branches
```

### Taskfile as alias layer

```yaml
version: '3'
tasks:
  status:   { cmds: ["python scripts/test_runner.py status"] }
  unit:     { cmds: ["python scripts/test_runner.py run unit {{.CLI_ARGS}}"] }
  inc:      { cmds: ["python scripts/test_runner.py run incremental {{.CLI_ARGS}}"] }
  replay:   { cmds: ["python scripts/test_runner.py run live_replay {{.CLI_ARGS}}"] }
  eval:     { cmds: ["python scripts/test_runner.py run eval_real {{.CLI_ARGS}}"] }
  pipeline: { cmds: ["python scripts/test_runner.py pipeline {{.CLI_ARGS}}"] }
  publish:  { cmds: ["python scripts/test_runner.py publish"] }
  retry:    { cmds: ["python scripts/test_runner.py retry"] }
  cleanup:  { cmds: ["python scripts/test_runner.py cleanup"] }
```

*Reason for Taskfile: ergonomic CLI aliases (`task status` vs `python scripts/test_runner.py status`). Taskfile is optional — everything works through test_runner.py directly.*

---

## Script Integration and /tmp Artifact Isolation

### Why /tmp

Test scripts must write artifacts to `/tmp` first, never directly to canonical
tracked locations.

*Reason: a crash or kill signal during artifact write leaves a corrupt file in
the tracked location. The previous good result is lost, the portal shows garbage,
and git-tracked artifacts become invalid. We experienced this with killed replay
processes overwriting replay_result.json with partial data.*

With `/tmp` isolation:
- Scripts write to `/tmp/mantri_{mode}_{stage}_{timestamp}/`
- On success: atomic `os.rename()` to canonical location (same filesystem = instant)
- On failure: `/tmp` contents are cleaned up, canonical location untouched
- On crash: `/tmp` contents eventually cleaned up by OS, canonical untouched

### Script modifications required

Every test script needs an `--output-dir` parameter. The test_runner.py passes
a `/tmp` directory, the script writes there, the runner promotes on success.

| Script | Current output | Change needed |
|---|---|---|
| `run_unit_tests.sh` | `tests/runs/coverage.json` | Accept `$OUTPUT_DIR` env var |
| `run_incremental_test.py` | `tests/runs/incremental/` | Accept `--output-dir` arg |
| `test_live_replay.py` | `tests/integration_tests/{case}/` | Accept `--output-dir` arg |
| `publish_runs.py` | `static/developer/runs/index.html` | Accept `--output-dir` arg |
| `publish_integration.py` | `static/developer/integration/index.html` | Accept `--output-dir` arg |

This is NOT a phased approach. All scripts are modified before the pipeline
goes live. The modifications are small — adding one argument that defaults to
the current location for backward compatibility.

*Reason against phased approach: the whole point of /tmp isolation is atomic
safety. A "write to canonical then restore on failure" Phase 1 defeats this —
a crash during write leaves corrupt canonical files, which is exactly the
problem we're solving.*

### Dev mode output

In dev mode, artifacts stay in `/tmp/mantri_dev/{stage}/`. They are never
moved to canonical locations. The developer can inspect them at the temp path
if needed. They are cleaned up on the next dev run of the same stage.

### Record mode flow

```python
def run_record_stage(stage, config):
    temp_dir = f"/tmp/mantri_record_{stage}_{int(time.time())}"
    os.makedirs(temp_dir)

    run_id = record_run_start(stage)

    try:
        # Script writes to temp_dir
        _execute_script(stage, config, output_dir=temp_dir)

        # Atomic promote: move each artifact to canonical location
        for artifact_name, canonical_path in STAGE_CONFIG[stage]["artifacts"].items():
            temp_path = os.path.join(temp_dir, artifact_name)
            if os.path.exists(temp_path):
                os.rename(temp_path, canonical_path)

        # Record success with hashes
        hashes = compute_artifact_hashes(stage)
        record_run_success(run_id, artifact_hashes=hashes)

        # Git commit on test branch
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

---

## Failure Classification

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
        return "infra_failed"        # network/service failure
    if isinstance(exception, (KeyboardInterrupt, SystemExit)):
        return "crashed"             # user/system kill
    return "infra_failed"            # unknown = assume infra
```

*Reason for default=infra_failed: unknown failures should be restartable. If we default to "failed", the user has to re-run the entire pipeline instead of just retrying the broken stage.*

---

## Staleness Detection

When a record run completes for a stage, check all downstream stages:

```python
def mark_downstream_stale(completed_stage):
    for stage in ALL_STAGES:
        if completed_stage in stage.depends_on:
            if stage_state[stage] in ("PASSED", "SKIPPED"):
                stage_state[stage] = "STALE"
                stage_state[stage].stale_reason = f"{completed_stage} updated"
```

Source code changes trigger staleness:

```python
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

*Reason for git-based staleness (not timestamp-based): timestamps are unreliable across git checkouts and clones. Git diff against the last-passed commit is deterministic.*

---

## Git Branching Model for Record Runs

Record runs execute on a dedicated branch, keeping test artifact commits
separate from code commits on main.

*Reason: auto-committing test artifacts on main would interleave with code commits, creating noisy history and potential conflicts during active development.*

### Flow

```
main (your code commits)
  │
  ├── git checkout -b test/record-20260402-0930
  │     ├── commit: [test:record] unit PASSED (362/362)
  │     ├── commit: [test:record] incremental PASSED (20/20)
  │     ├── commit: [test:record] live_replay PASSED (R1-D: 77/100)
  │     ├── commit: [test:record] eval_real PASSED (3/3, avg 88)
  │     └── commit: [test:record] publish portal regenerated
  │
  ├── git fetch origin main
  ├── git rebase origin/main
  ├── git checkout main && git merge --ff-only test/record-20260402-0930
  └── git branch -d test/record-20260402-0930
```

*Reason for fetch before rebase: if someone pushed to remote while the pipeline was running, we need to pull first to avoid non-fast-forward merge.*

### Branch naming

`test/record-{YYYYMMDD}-{HHMM}` — e.g., `test/record-20260402-0930`

### Per-stage commits

```python
def commit_stage_artifacts(stage, status, summary):
    artifacts = STAGE_CONFIG[stage]["artifacts"]["record"]
    subprocess.run(["git", "add"] + [a for a in artifacts if os.path.exists(a)])
    msg = f"[test:record] {stage} {status.upper()}"
    if summary:
        msg += f" ({summary})"
    subprocess.run(["git", "commit", "-m", msg])
```

*Reason for per-stage commits (not batch): if the pipeline crashes at stage 4, stages 1-3 are safely committed. Batch commits lose everything on crash.*

### Interrupted pipeline

If the pipeline crashes or an infra failure stops it, the branch remains
with partial commits. The SQLite DB records the failure.

```bash
$ task status
Pipeline: test/record-20260402-0930 (IN PROGRESS, stopped at live_replay)
  unit            PASSED    committed
  incremental     PASSED    committed
  live_replay     INFRA_FAILED  (Phoenix timeout — restartable)
  eval_real       PENDING
  publish         PENDING

$ task retry
# Checks out test/record-20260402-0930
# Retries live_replay from where it failed
# On success: continues pipeline to completion, rebase+merge
```

### Stale branch cleanup

```python
def cleanup_stale_test_branches(max_age_days=7):
    branches = subprocess.check_output(
        ["git", "branch", "--list", "test/record-*",
         "--format=%(refname:short) %(creatordate:unix)"],
        text=True
    ).strip().splitlines()

    cutoff = time.time() - (max_age_days * 86400)
    for line in branches:
        branch, ts = line.rsplit(" ", 1)
        if int(ts) < cutoff:
            merged = subprocess.run(
                ["git", "merge-base", "--is-ancestor", branch, "main"],
                capture_output=True
            ).returncode == 0
            if merged:
                subprocess.run(["git", "branch", "-d", branch])
            else:
                log.warning("Stale unmerged: %s (keeping for review)", branch)
```

### Edge cases

**Multiple test branches:** `start_record_pipeline` refuses if one exists.
**Rebase conflict:** should be rare (test branch only adds artifact files).
**Code changes during test run:** status warns about stale branch.

---

## Run Preconditions and Cost Gating

### Record mode preconditions

1. **Clean git state**: `git diff --quiet src/` must pass
2. **Dependencies passed**: all upstream stages must be PASSED (not STALE or FAILED)
3. **Lock acquired**: no other pipeline running (`.test-state/lock`)

*Reason for dependency check: recording eval scores against stale replay results produces misleading official scores.*

### Dev mode cost gating

| Stage | Dev mode behavior |
|---|---|
| unit, dry_replay | Ungated — free |
| incremental | Ungated — cheap (~$0.50) |
| live_replay (full) | **BLOCKED** — not allowed in dev mode |
| live_replay (--max-messages) | **CONFIRM** — cost estimate, user must confirm |
| eval_real, eval_synth | Ungated — free (Claude Code) |

*Reason for blocking full replays in dev: we wasted $10 on replays with broken tracer code. Full replays should only run as record runs after code is committed and verified.*

---

## Portal Publishing Integration

Publishing is a pipeline stage triggered automatically after any record run.
Each stage maps to the portal sections it affects.

| Section | Triggered by | Generator |
|---|---|---|
| unit + coverage | unit | publish_runs.py (unit section) |
| integration (INC + linkage) | incremental | publish_runs.py (integration section) |
| system (replay + eval) | live_replay, eval_real, eval_synth | publish_system.py |
| allure report | unit, incremental | publish_allure.py |

*Reason for scoped regeneration: publishing the full portal after every stage is wasteful. Only regenerate sections whose inputs changed.*

---

## Upstream Artifact Integrity

Before a stage runs, verify that artifacts from its dependencies match
expected hashes:

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

*Reason: if someone manually edits replay_result.json, downstream eval scores would be computed against tampered data. Hash verification catches this.*

---

## Crash Recovery

If the DB shows a stage as RUNNING but no lock file exists, it crashed:

```python
def detect_crashed_runs():
    running = get_stages_in_state("RUNNING")
    if running and not is_locked():
        for stage in running:
            cleanup_partial_artifacts(stage)  # remove /tmp leftovers
            record_run_crashed(stage)
```

Runs at the start of every `test_runner.py` command.

*Reason: without crash recovery, a killed pipeline leaves the DB in RUNNING state permanently, blocking all future runs.*

---

## Run Metadata

Every run captures:

```python
run_metadata = {
    "git_commit": "abc1234",
    "git_dirty": False,
    "config": {
        "model": "claude-sonnet-4-6",
        "gemini_model": "gemini-2.5-flash",
        "agent_max_tokens": 2048,
        "traced": True,
        "phoenix_endpoints": ["local", "remote"],
        "max_messages": None,
    },
    "triggered_by": "cli",
    "case_filter": None,
    "duration_s": 147.3,
    "cost_usd": 6.90,
}
```

*Reason: "what model was used for the last replay?" and "how much did it cost?" are questions we ask regularly. Capturing config per-run makes them answerable.*

---

## Example Session

```bash
# Morning: check status
$ task status
Stage           State          Last Record Run     Commit    Notes
unit            STALE          Mar 31 13:28        abc1234   src/ changed
incremental     STALE          Mar 31 13:28        abc1234   upstream stale
dry_replay      PASSED         Apr 01 10:53        def5678
live_replay     PASSED         Apr 01 09:15        def5678   $6.90
eval_real       STALE          Mar 31 09:24        abc1234   replay updated
eval_synth      STALE          Mar 31 09:24        abc1234   replay updated
publish         STALE          Apr 01 19:55        def5678   eval stale

# Run cheap stages
$ task pipeline --from unit --to incremental --record
Creating branch: test/record-20260402-0930
Running unit... 362 passed ✓   [committed]
Running incremental... 20/20 ✓ [committed]
Publishing integration section... ✓ [committed]
Rebasing and merging to main... ✓
Branch test/record-20260402-0930 merged and deleted.

# Dev iteration on a fix
$ task inc INC-04
[dev mode] Output: /tmp/mantri_dev/incremental/
INC-04: PASS ✓

# Try a short replay in dev mode
$ task replay --max-messages 20
[dev mode] Short replay (~20 msgs, ~$0.26). Proceed? [y/N] y
Output: /tmp/mantri_dev/live_replay/
20 messages processed, 12 routed, 0 failures ✓
```

---

## Implementation Plan

1. Modify all test scripts to accept `--output-dir` parameter
2. Create `.test-state/` directory structure (gitignored)
3. Create `scripts/test_runner.py` (~400 lines, stdlib only)
4. Create `Taskfile.yml` (~40 lines)
5. Update `.gitignore` (track tests/runs/, gitignore .test-state/)
6. Rename /integration/ to /system/ in publish scripts + nginx
7. Restructure portal pages (publish_runs.py sections, publish_system.py)
8. End-to-end test: `task pipeline --record` on a clean branch

---

## Addendum: Design Discussion Summary

### Problem statement (2026-04-01)

During a long development session, several test infrastructure problems surfaced:
- INC-04 showed as PARTIAL in the portal from an old run, but passed when run directly
- Live replay results from the current session weren't showing in the portal
- Eval-real scores were from the previous day, not reflecting current code
- Phoenix trace push was forgotten after replays
- Portal publishing was a manual afterthought
- Short trace replays overwrote canonical results (fixed with `_short` suffix hack)
- No way to tell if portal results reflected current or old code

### Key design decisions

1. **Record vs Dev mode** — proposed to prevent accidental pollution of official results. Default is dev (safe). Record requires explicit opt-in and clean git state.

2. **Prefect vs DIY** — researched Prefect (Python workflow engine) for pipeline orchestration. Found: 41 dependencies, overkill for 7 stages, no open-source projects use it for test pipelines. Chose DIY Python script + Taskfile (zero dependencies, ~400 lines, full control).

3. **/tmp artifact isolation** — proposed after experiencing corrupt replay_result.json from killed processes. Scripts write to `/tmp`, runner promotes atomically on success. Phased approach was rejected: the whole point is preventing partial writes to canonical locations, which Phase 1 (write-then-restore) doesn't achieve.

4. **Git branching for test commits** — proposed to separate test artifact commits from code commits. Test branch created per pipeline run, per-stage commits, rebase+merge on completion. Adds fetch-before-rebase to handle remote changes.

5. **Staleness via git diff (not timestamps)** — timestamps are unreliable across git operations. Git diff against last-passed commit is deterministic.

6. **Cost gating in dev mode** — full replays blocked, short replays require confirmation with cost estimate. Motivated by $10 wasted on replays with broken tracer code.

7. **SKIPPED state** — added to state machine so pipeline profiles (e.g., `--profile quick`) can skip expensive stages without blocking downstream stages. Downstream stages inherit a warning.

8. **Manual stages** — eval-real and eval-synth run in Claude Code, not automated. Pipeline marks them as PENDING_MANUAL. Completed via `task eval complete`.

9. **Per-stage commits on test branch** — chosen over batch commits because crash at stage 4 preserves stages 1-3. Batch commits lose everything on crash.

10. **Dev runs must never run full replays** — even short replays require interactive user confirmation with cost estimate. Full replays are blocked entirely in dev mode. Motivated by accidental spend.

11. **Eval-real redesigned as judge panel** — the old eval-real skill had Claude Code act as the agent (producing its own output), then scoring itself. This tested "can an LLM do this?" not "did our pipeline do this well?" Redesigned: Claude Code judges the ACTUAL live replay output alongside automated judges. Eval now depends on live_replay.

12. **Eval-synth merged into incremental** — the 29 synthetic cases tested agent behavior on crafted single-message/thread inputs. This is exactly what incremental tests do. Two separate systems for the same purpose is unnecessary. Synthetic cases become INC-21..INC-49.

13. **Judge panel architecture** — automated judges (deterministic, rapidfuzz, Gemini) run first for instant free scoring. Claude Code provides authoritative manual judgment. Disagreements between judges highlight bugs or edge cases. All scores stored together in score.json + Phoenix.

14. **Pipeline warns but doesn't block on dependencies** — stages are technically independent (unit, incremental, dry_replay can all run in parallel). The pipeline warns if upstream stages are stale but doesn't refuse to run downstream. Only eval strictly depends on live_replay (needs output to judge).

15. **Test plan recommendation tool** — instead of enforcing dependency chains, a tool inspects code changes (`git diff`) and recommends which test stages to run. "You changed src/agent/prompt.py — recommend: incremental + live_replay + eval."

16. **Eval review automated via sub-agent** — initially designed as a manual stage (PENDING_MANUAL). Realized that the review is structured reasoning against known inputs — exactly what a Claude Code sub-agent does. Changed to async background agent that runs automatically after eval_auto. Pipeline publishes with automated scores immediately, re-publishes when review completes. Falls back to PENDING_MANUAL if sub-agents unavailable.

17. **Eval split into eval_auto + eval_review** — eval_auto (deterministic + Gemini) is immediate and automated. eval_review (Claude Code sub-agent) is async and deeper. Neither blocks the pipeline. Portal publishes with whatever scores are available.

### Rejected alternatives

- **Prefect**: too heavyweight (41 deps, learning curve, version instability)
- **DVC**: good staleness tracking but no run history, wrong paradigm for tests
- **Luigi/Airflow**: enterprise data pipeline tools, overkill
- **Phased /tmp migration**: defeats atomicity guarantee
- **JSON manifest instead of SQLite**: not queryable, no atomic updates
- **Publishing as separate tool**: leads to forgotten publishes
- **Eval-real as separate agent run**: tested LLM capability, not pipeline quality
- **Eval-synth as separate stage**: duplicated incremental test infrastructure

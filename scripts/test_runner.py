#!/usr/bin/env python3
"""
Unified test pipeline runner.

Every test execution — ad-hoc or pipeline — goes through this tool.
Manages record vs dev modes, artifact isolation via /tmp, state tracking
in SQLite, failure classification, cost gating, and git branching for
record runs.

Usage:
    python scripts/test_runner.py status                    # show pipeline state
    python scripts/test_runner.py run unit                  # dev run (default)
    python scripts/test_runner.py run unit --record         # official run
    python scripts/test_runner.py run incremental INC-04    # single case, dev
    python scripts/test_runner.py pipeline                  # full pipeline (record)
    python scripts/test_runner.py pipeline --from eval      # start from stage
    python scripts/test_runner.py retry                     # retry last infra failure
    python scripts/test_runner.py complete eval_review      # mark manual stage done
    python scripts/test_runner.py publish                   # regenerate portal
    python scripts/test_runner.py cleanup                   # delete stale test branches
    python scripts/test_runner.py recommend                 # suggest stages based on git diff
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".test-state"
DB_PATH = STATE_DIR / "pipeline.db"
LOCK_PATH = STATE_DIR / "lock"

# ---------------------------------------------------------------------------
# Stage configuration
# ---------------------------------------------------------------------------

STAGES = {
    "unit": {
        "runner": "scripts/run_unit_tests.sh",
        "runner_type": "shell",
        "cost": "free",
        "depends_on": [],
        "is_manual": False,
        "artifacts": [
            "tests/runs/coverage.json",
        ],
        "portal_sections": ["unit", "coverage", "allure"],
        "source_dirs": ["src/", "tests/unit_tests/"],
    },
    "incremental": {
        "runner": "scripts/run_incremental_test.py",
        "runner_type": "python",
        "cost": "~$0.50",
        "depends_on": [],
        "is_manual": False,
        "artifacts": [
            "tests/runs/incremental/{timestamp}_summary.json",
        ],
        "portal_sections": ["integration"],
        "source_dirs": ["src/", "tests/functional_tests/"],
    },
    "dry_replay": {
        "runner": "tests/integration_tests/test_dry_replay.py",
        "runner_type": "pytest",
        "cost": "free",
        "depends_on": [],
        "is_manual": False,
        "artifacts": [
            "tests/runs/integration/dry-*.json",
        ],
        "portal_sections": ["system"],
        "source_dirs": ["src/", "tests/integration_tests/"],
    },
    "live_replay": {
        "runner": "tests/integration_tests/test_live_replay.py",
        "runner_type": "pytest_live",
        "cost": "~$8",
        "depends_on": [],
        "is_manual": False,
        "artifacts": [
            "tests/integration_tests/{case}/replay_result.json",
            "tests/integration_tests/{case}/replay_result.db",
            "tests/integration_tests/{case}/pipeline_score.json",
            "tests/runs/integration/live-{case}-{timestamp}.json",
        ],
        "portal_sections": ["system"],
        "source_dirs": ["src/"],
        "cost_gate": {
            "full": "blocked",
            "short": "confirm",
            "estimate_per_msg": 0.013,
        },
    },
    "eval_auto": {
        "runner": "scripts/run_eval.sh",
        "runner_type": "shell",
        "cost": "free",
        "depends_on": ["live_replay"],
        "is_manual": False,
        "artifacts": [
            "tests/eval_issues.json",
        ],
        "portal_sections": ["system"],
        "source_dirs": ["src/tracing/"],
    },
    "eval_review": {
        "runner": "SUBAGENT",
        "runner_type": "subagent",
        "cost": "free",
        "depends_on": ["eval_auto"],
        "is_manual": False,  # automated via sub-agent, fallback to manual
        "artifacts": [
            "tests/integration_tests/{case}/score_review.json",
        ],
        "portal_sections": ["system"],
        "source_dirs": [],
    },
    "publish": {
        "runner": "scripts/publish_all.sh",
        "runner_type": "shell",
        "cost": "free",
        "depends_on": [],
        "is_manual": False,
        "artifacts": [
            "static/developer/runs/index.html",
            "static/developer/integration/index.html",
        ],
        "portal_sections": [],
        "source_dirs": ["scripts/publish_*.py"],
    },
}

STAGE_ORDER = ["unit", "incremental", "dry_replay", "live_replay",
               "eval_auto", "eval_review", "publish"]

# Infra exceptions that indicate restartable failures
INFRA_EXIT_CODES = {2, 3, 4, 5, 137, 139}  # signals, segfaults, etc.

# ---------------------------------------------------------------------------
# SQLite state management
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stage       TEXT NOT NULL,
    mode        TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    duration_s  REAL,
    git_commit  TEXT,
    git_dirty   INTEGER,
    config      TEXT,
    error       TEXT,
    artifacts   TEXT,
    cost_usd    REAL,
    triggered_by TEXT,
    case_filter TEXT
);

CREATE TABLE IF NOT EXISTS stage_state (
    stage       TEXT PRIMARY KEY,
    state       TEXT NOT NULL DEFAULT 'NEVER_RUN',
    last_record_run_id INTEGER,
    last_passed_at TEXT,
    last_passed_commit TEXT,
    stale_reason TEXT
);
"""


def _get_db() -> sqlite3.Connection:
    STATE_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Initialize stage_state for all stages
    for stage in STAGES:
        conn.execute(
            "INSERT OR IGNORE INTO stage_state (stage, state) VALUES (?, 'NEVER_RUN')",
            (stage,),
        )
    conn.commit()
    return conn


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, cwd=str(ROOT)
        ).strip()
    except Exception:
        return "(unknown)"


def _git_dirty() -> bool:
    try:
        return subprocess.run(
            ["git", "diff", "--quiet", "src/"], cwd=str(ROOT)
        ).returncode != 0
    except Exception:
        return True


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except FileNotFoundError:
        return "(missing)"


# ---------------------------------------------------------------------------
# Lock management
# ---------------------------------------------------------------------------

@contextmanager
def _acquire_lock():
    if LOCK_PATH.exists():
        pid = LOCK_PATH.read_text().strip()
        # Check if the locking process is still alive
        try:
            os.kill(int(pid), 0)
            print(f"ERROR: Pipeline locked by PID {pid}. Use --force to override.")
            sys.exit(1)
        except (OSError, ValueError):
            log.warning("Stale lock from PID %s — removing", pid)
            LOCK_PATH.unlink()

    LOCK_PATH.write_text(str(os.getpid()))
    try:
        yield
    finally:
        if LOCK_PATH.exists():
            LOCK_PATH.unlink()


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------

def _detect_crashed_runs():
    """If DB shows RUNNING but no lock exists, the run crashed."""
    db = _get_db()
    running = db.execute(
        "SELECT id, stage FROM runs WHERE status = 'running' AND finished_at IS NULL"
    ).fetchall()
    if running and not LOCK_PATH.exists():
        for row in running:
            log.warning("Detected crashed run: stage=%s run_id=%d", row["stage"], row["id"])
            db.execute(
                "UPDATE runs SET status='crashed', finished_at=?, error='Process crashed' WHERE id=?",
                (datetime.now().isoformat(), row["id"]),
            )
            db.execute(
                "UPDATE stage_state SET state='INFRA_FAILED', stale_reason='Process crashed' WHERE stage=?",
                (row["stage"],),
            )
        db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Cost gating
# ---------------------------------------------------------------------------

def _check_cost_gate(stage: str, mode: str, config: dict) -> bool:
    gate = STAGES[stage].get("cost_gate")
    if not gate or mode == "record":
        return True

    max_msgs = config.get("max_messages")
    if max_msgs is None:
        print(f"\nBLOCKED: Full {stage} not allowed in dev mode (cost: {STAGES[stage]['cost']}).")
        print("Use --record for official runs.")
        return False

    est_cost = max_msgs * gate.get("estimate_per_msg", 0.01)
    response = input(f"\nShort {stage} (~{max_msgs} msgs, ~${est_cost:.2f}). Proceed? [y/N] ")
    return response.strip().lower() == "y"


# ---------------------------------------------------------------------------
# Stage execution
# ---------------------------------------------------------------------------

def _run_stage(stage: str, mode: str, config: dict, case_filter: str = None):
    """Execute a single stage with artifact isolation."""
    stage_cfg = STAGES[stage]

    # Cost gate (dev mode only)
    if not _check_cost_gate(stage, mode, config):
        return "skipped"

    # Record mode preconditions
    if mode == "record":
        if _git_dirty():
            print(f"Cannot record: uncommitted changes in src/. Commit first or use dev mode.")
            return "blocked"

    # Create temp directory for output
    temp_dir = tempfile.mkdtemp(prefix=f"mantri_{mode}_{stage}_")

    db = _get_db()
    run_id = db.execute(
        """INSERT INTO runs (stage, mode, status, started_at, git_commit, git_dirty,
           config, triggered_by, case_filter)
           VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?)""",
        (stage, mode, datetime.now().isoformat(), _git_commit(), int(_git_dirty()),
         json.dumps(config), config.get("triggered_by", "cli"), case_filter),
    ).lastrowid
    db.commit()

    t0 = time.time()
    status = "passed"
    error = None

    try:
        _execute_stage(stage, stage_cfg, temp_dir, config, case_filter)

        if mode == "record":
            # Atomic promote: move artifacts from temp to canonical
            _promote_artifacts(stage, temp_dir)

        duration = time.time() - t0
        # Record success
        artifact_hashes = _compute_artifact_hashes(stage) if mode == "record" else {}
        db.execute(
            """UPDATE runs SET status='passed', finished_at=?, duration_s=?,
               artifacts=? WHERE id=?""",
            (datetime.now().isoformat(), duration, json.dumps(artifact_hashes), run_id),
        )
        if mode == "record":
            db.execute(
                """UPDATE stage_state SET state='PASSED', last_record_run_id=?,
                   last_passed_at=?, last_passed_commit=?, stale_reason=NULL
                   WHERE stage=?""",
                (run_id, datetime.now().isoformat(), _git_commit(), stage),
            )
            _mark_downstream_stale(db, stage)
        db.commit()
        status = "passed"

    except subprocess.CalledProcessError as e:
        duration = time.time() - t0
        if e.returncode == 1:
            status = "failed"
            error = f"Test failure (exit code 1)"
        else:
            status = "infra_failed"
            error = f"Exit code {e.returncode}"
        db.execute(
            "UPDATE runs SET status=?, finished_at=?, duration_s=?, error=? WHERE id=?",
            (status, datetime.now().isoformat(), duration, error, run_id),
        )
        if mode == "record":
            db.execute(
                "UPDATE stage_state SET state=?, stale_reason=? WHERE stage=?",
                (status.upper(), error, stage),
            )
        db.commit()

    except KeyboardInterrupt:
        duration = time.time() - t0
        status = "crashed"
        db.execute(
            "UPDATE runs SET status='crashed', finished_at=?, duration_s=?, error='KeyboardInterrupt' WHERE id=?",
            (datetime.now().isoformat(), duration, run_id),
        )
        db.commit()
        raise

    except Exception as e:
        duration = time.time() - t0
        status = "infra_failed"
        error = str(e)[:200]
        db.execute(
            "UPDATE runs SET status='infra_failed', finished_at=?, duration_s=?, error=? WHERE id=?",
            (datetime.now().isoformat(), duration, error, run_id),
        )
        if mode == "record":
            db.execute(
                "UPDATE stage_state SET state='INFRA_FAILED', stale_reason=? WHERE stage=?",
                (error, stage),
            )
        db.commit()

    finally:
        # Clean up temp dir (dev artifacts stay for inspection if needed)
        if mode == "record":
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            print(f"  Dev artifacts: {temp_dir}")
        db.close()

    return status


def _execute_stage(stage: str, cfg: dict, temp_dir: str, config: dict,
                   case_filter: str = None):
    """Run the actual test script with output directed to temp_dir."""
    runner_type = cfg["runner_type"]
    env = {**os.environ, "MANTRI_OUTPUT_DIR": temp_dir, "MANTRI_TEST_MODE": config.get("mode", "dev")}

    if runner_type == "shell":
        subprocess.run(
            ["bash", cfg["runner"]],
            cwd=str(ROOT), env=env, check=True,
        )
    elif runner_type == "python":
        cmd = ["python3", cfg["runner"]]
        if case_filter:
            cmd.append(case_filter)
        subprocess.run(cmd, cwd=str(ROOT), env=env, check=True)
    elif runner_type == "pytest":
        cmd = ["python3", "-m", "pytest", cfg["runner"], "-v", "--tb=short"]
        subprocess.run(cmd, cwd=str(ROOT), env=env, check=True)
    elif runner_type == "pytest_live":
        cmd = ["python3", "-m", "pytest", cfg["runner"],
               "-v", "-s", "--run-live", "--traced", "--skip-linkage",
               "--phoenix-endpoint", "local", "--phoenix-endpoint", "remote"]
        if case_filter:
            cmd.extend(["-k", case_filter])
        if config.get("max_messages"):
            cmd.extend(["--max-messages", str(config["max_messages"])])
        if config.get("run_note"):
            cmd.extend(["--run-note", config["run_note"]])
        subprocess.run(cmd, cwd=str(ROOT), env=env, check=True)
    elif runner_type == "subagent":
        # eval_review — automated via sub-agent or fallback to manual
        print(f"  {stage}: sub-agent execution (async)")
        # In Claude Code: would launch a background agent
        # Outside Claude Code: mark as PENDING_MANUAL
    else:
        raise ValueError(f"Unknown runner_type: {runner_type}")


def _promote_artifacts(stage: str, temp_dir: str):
    """Atomically move artifacts from temp to canonical locations."""
    temp_path = Path(temp_dir)
    for item in temp_path.iterdir():
        if item.is_file():
            # Determine canonical path based on stage config
            # For now, use filename matching
            canonical = _find_canonical_path(stage, item.name)
            if canonical:
                canonical.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item), str(canonical))
                log.debug("Promoted: %s → %s", item, canonical)


def _find_canonical_path(stage: str, filename: str) -> Path | None:
    """Map a temp artifact filename to its canonical location."""
    # Simple mapping based on known patterns
    if filename == "coverage.json":
        return ROOT / "tests/runs/coverage.json"
    if filename.endswith("_summary.json") and "incremental" in stage:
        return ROOT / "tests/runs/incremental" / filename
    if filename.endswith("_summary.json") and "eval" in stage:
        return ROOT / "tests/runs/eval" / filename
    return None


def _compute_artifact_hashes(stage: str) -> dict:
    """Compute hashes of canonical artifacts for integrity checking."""
    hashes = {}
    for pattern in STAGES[stage]["artifacts"]:
        # Expand globs
        for path in ROOT.glob(pattern):
            hashes[str(path.relative_to(ROOT))] = _hash_file(str(path))
    return hashes


def _mark_downstream_stale(db: sqlite3.Connection, completed_stage: str):
    """Mark stages that depend on completed_stage as STALE."""
    for stage_name, cfg in STAGES.items():
        if completed_stage in cfg.get("depends_on", []):
            current = db.execute(
                "SELECT state FROM stage_state WHERE stage=?", (stage_name,)
            ).fetchone()
            if current and current["state"] in ("PASSED", "SKIPPED"):
                db.execute(
                    "UPDATE stage_state SET state='STALE', stale_reason=? WHERE stage=?",
                    (f"{completed_stage} updated", stage_name),
                )


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def _show_status(verbose: bool = False):
    """Display pipeline status."""
    _detect_crashed_runs()
    _check_source_staleness()

    db = _get_db()

    print(f"\n{'Stage':<15s} {'State':<15s} {'Last Record Run':<20s} {'Commit':<10s} {'Notes'}")
    print("-" * 80)

    for stage in STAGE_ORDER:
        row = db.execute("SELECT * FROM stage_state WHERE stage=?", (stage,)).fetchone()
        state = row["state"] if row else "NEVER_RUN"
        last_run = ""
        commit = ""
        notes = ""

        if row and row["last_passed_at"]:
            last_run = row["last_passed_at"][:16]
            commit = row["last_passed_commit"] or ""
        if row and row["stale_reason"]:
            notes = row["stale_reason"]

        # Color coding
        state_str = state
        if state == "PASSED":
            state_str = f"\033[32m{state}\033[0m"
        elif state in ("FAILED", "INFRA_FAILED", "CRASHED"):
            state_str = f"\033[31m{state}\033[0m"
        elif state == "STALE":
            state_str = f"\033[33m{state}\033[0m"
        elif state == "SKIPPED":
            state_str = f"\033[36m{state}\033[0m"

        cost = STAGES[stage]["cost"]
        manual = " [MANUAL]" if STAGES[stage].get("is_manual") else ""
        print(f"  {stage:<13s} {state_str:<25s} {last_run:<20s} {commit:<10s} {notes}{manual}")

    # Stale branches
    branches = _list_test_branches()
    if branches:
        print(f"\nTest branches: {', '.join(branches)}")

    # Recommendations
    stale = db.execute("SELECT stage FROM stage_state WHERE state='STALE'").fetchall()
    if stale:
        stages = [r["stage"] for r in stale]
        print(f"\nSTALE: {', '.join(stages)}")
        print("  Run: python scripts/test_runner.py pipeline")

    if verbose:
        print(f"\nRecent runs:")
        runs = db.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 10"
        ).fetchall()
        for r in runs:
            print(f"  {r['started_at'][:16]} {r['stage']:<15s} {r['mode']:<8s} "
                  f"{r['status']:<15s} {r['duration_s'] or 0:.0f}s")

    db.close()


def _check_source_staleness():
    """Mark unit as STALE if src/ changed since last pass."""
    db = _get_db()
    row = db.execute(
        "SELECT last_passed_commit FROM stage_state WHERE stage='unit'"
    ).fetchone()
    if not row or not row["last_passed_commit"]:
        db.close()
        return

    try:
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", row["last_passed_commit"], "HEAD", "--", "src/"],
            text=True, cwd=str(ROOT), stderr=subprocess.DEVNULL,
        ).strip()
        if changed:
            db.execute(
                "UPDATE stage_state SET state='STALE', stale_reason=? WHERE stage='unit' AND state='PASSED'",
                (f"src/ changed since {row['last_passed_commit'][:8]}",),
            )
            db.commit()
    except subprocess.CalledProcessError:
        pass
    db.close()


# ---------------------------------------------------------------------------
# Git branching for record runs
# ---------------------------------------------------------------------------

def _list_test_branches() -> list[str]:
    try:
        output = subprocess.check_output(
            ["git", "branch", "--list", "test/record-*", "--format=%(refname:short)"],
            text=True, cwd=str(ROOT),
        ).strip()
        return output.splitlines() if output else []
    except Exception:
        return []


def _start_test_branch() -> str:
    branches = _list_test_branches()
    if branches:
        print(f"Cannot start: test branch {branches[0]} already exists.")
        print("  Use 'retry' to continue or 'cleanup --abandon' to discard.")
        sys.exit(1)

    branch = f"test/record-{time.strftime('%Y%m%d-%H%M')}"
    subprocess.run(["git", "checkout", "-b", branch], cwd=str(ROOT), check=True)
    return branch


def _commit_stage(stage: str, status: str, summary: str = ""):
    """Commit stage artifacts on the test branch."""
    # Add artifacts
    for pattern in STAGES[stage]["artifacts"]:
        for path in ROOT.glob(pattern):
            subprocess.run(["git", "add", str(path)], cwd=str(ROOT))

    msg = f"[test:record] {stage} {status.upper()}"
    if summary:
        msg += f" ({summary})"
    subprocess.run(["git", "commit", "-m", msg, "--allow-empty"], cwd=str(ROOT))


def _merge_test_branch(branch: str):
    """Rebase and merge test branch back to main."""
    subprocess.run(["git", "fetch", "origin", "main"], cwd=str(ROOT),
                    capture_output=True)
    subprocess.run(["git", "rebase", "origin/main"], cwd=str(ROOT), check=True)
    subprocess.run(["git", "checkout", "main"], cwd=str(ROOT), check=True)
    subprocess.run(["git", "merge", "--ff-only", branch], cwd=str(ROOT), check=True)
    subprocess.run(["git", "branch", "-d", branch], cwd=str(ROOT), check=True)
    print(f"Branch {branch} merged and deleted.")


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

def _run_pipeline(config: dict, from_stage: str = None, force: bool = False):
    """Run the full pipeline in record mode."""
    if _git_dirty():
        print("Cannot run pipeline: uncommitted changes in src/. Commit first.")
        sys.exit(1)

    branch = _start_test_branch()
    print(f"\nPipeline started on branch: {branch}\n")

    started = from_stage is None
    for stage in STAGE_ORDER:
        if not started:
            if stage == from_stage:
                started = True
            else:
                continue

        # Check if stage should be skipped
        if not force:
            db = _get_db()
            row = db.execute("SELECT state FROM stage_state WHERE stage=?", (stage,)).fetchone()
            db.close()
            if row and row["state"] == "PASSED":
                print(f"  {stage}: PASSED (skipping)")
                continue

        print(f"\n{'='*60}")
        print(f"  Running: {stage} (cost: {STAGES[stage]['cost']})")
        print(f"{'='*60}")

        status = _run_stage(stage, "record", config)

        if status == "passed":
            _commit_stage(stage, status)
            print(f"  {stage}: PASSED ✓ [committed]")
        elif status == "skipped":
            print(f"  {stage}: SKIPPED")
            continue
        elif status == "blocked":
            print(f"  {stage}: BLOCKED")
            continue
        else:
            print(f"  {stage}: {status.upper()} ✗")
            print(f"\nPipeline stopped at {stage}. Branch: {branch}")
            print(f"  Fix the issue, then: python scripts/test_runner.py retry")
            return

    # All stages passed — merge
    print(f"\n{'='*60}")
    print(f"  All stages complete. Merging to main...")
    print(f"{'='*60}")
    try:
        _merge_test_branch(branch)
    except subprocess.CalledProcessError as e:
        print(f"  Merge failed: {e}")
        print(f"  Resolve manually, then: python scripts/test_runner.py merge")


# ---------------------------------------------------------------------------
# Recommend stages based on git diff
# ---------------------------------------------------------------------------

def _recommend():
    """Suggest which stages to run based on code changes."""
    try:
        # Get changed files since last unit test pass
        db = _get_db()
        row = db.execute(
            "SELECT last_passed_commit FROM stage_state WHERE stage='unit'"
        ).fetchone()
        db.close()

        base = row["last_passed_commit"] if row and row["last_passed_commit"] else "HEAD~1"
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", base, "HEAD"],
            text=True, cwd=str(ROOT),
        ).strip().splitlines()
    except Exception:
        changed = []

    if not changed:
        print("No changes detected since last unit test pass.")
        return

    recommendations = set()
    for f in changed:
        if f.startswith("src/agent/"):
            recommendations.update(["unit", "incremental", "live_replay", "eval_auto"])
        elif f.startswith("src/router/"):
            recommendations.update(["unit", "dry_replay", "live_replay", "eval_auto"])
        elif f.startswith("src/store/"):
            recommendations.update(["unit", "incremental"])
        elif f.startswith("src/tracing/"):
            recommendations.update(["unit", "eval_auto"])
        elif f.startswith("src/conversation/"):
            recommendations.update(["unit"])
        elif f.startswith("tests/"):
            recommendations.add("unit")
        elif f.startswith("scripts/publish"):
            recommendations.add("publish")

    print(f"\nChanged files ({len(changed)}):")
    for f in changed[:10]:
        print(f"  {f}")
    if len(changed) > 10:
        print(f"  ... +{len(changed) - 10} more")

    ordered = [s for s in STAGE_ORDER if s in recommendations]
    print(f"\nRecommended stages: {' → '.join(ordered)}")
    print(f"  Run: python scripts/test_runner.py pipeline --from {ordered[0]}" if ordered else "")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Mantri test pipeline runner")
    sub = parser.add_subparsers(dest="command")

    # status
    p_status = sub.add_parser("status", help="Show pipeline state")
    p_status.add_argument("--verbose", "-v", action="store_true")

    # run
    p_run = sub.add_parser("run", help="Run a single stage")
    p_run.add_argument("stage", choices=list(STAGES.keys()))
    p_run.add_argument("case", nargs="?", help="Case filter (e.g., INC-04, R1-D)")
    p_run.add_argument("--record", action="store_true", help="Record mode (official)")
    p_run.add_argument("--max-messages", type=int)
    p_run.add_argument("--run-note", type=str, default="")

    # pipeline
    p_pipe = sub.add_parser("pipeline", help="Run full pipeline (record mode)")
    p_pipe.add_argument("--from", dest="from_stage", choices=list(STAGES.keys()))
    p_pipe.add_argument("--force", action="store_true")
    p_pipe.add_argument("--run-note", type=str, default="")

    # retry
    sub.add_parser("retry", help="Retry last infra-failed stage")

    # complete
    p_complete = sub.add_parser("complete", help="Mark a manual stage as done")
    p_complete.add_argument("stage", choices=list(STAGES.keys()))

    # publish
    sub.add_parser("publish", help="Regenerate portal pages")

    # cleanup
    sub.add_parser("cleanup", help="Delete stale test branches")

    # recommend
    sub.add_parser("recommend", help="Suggest stages based on git diff")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _detect_crashed_runs()

    if args.command == "status":
        _show_status(verbose=args.verbose)

    elif args.command == "run":
        mode = "record" if args.record else "dev"
        config = {
            "mode": mode,
            "max_messages": args.max_messages,
            "run_note": args.run_note,
            "triggered_by": "cli",
        }
        with _acquire_lock():
            status = _run_stage(args.stage, mode, config, case_filter=args.case)
            print(f"\n{args.stage}: {status.upper()}")

    elif args.command == "pipeline":
        config = {
            "mode": "record",
            "run_note": args.run_note,
            "triggered_by": "pipeline",
        }
        with _acquire_lock():
            _run_pipeline(config, from_stage=args.from_stage, force=args.force)

    elif args.command == "retry":
        db = _get_db()
        failed = db.execute(
            "SELECT stage FROM stage_state WHERE state IN ('INFRA_FAILED', 'CRASHED')"
        ).fetchall()
        db.close()
        if not failed:
            print("No infra-failed stages to retry.")
        else:
            stage = failed[0]["stage"]
            print(f"Retrying: {stage}")
            with _acquire_lock():
                status = _run_stage(stage, "record", {"triggered_by": "retry"})
                print(f"\n{stage}: {status.upper()}")

    elif args.command == "complete":
        db = _get_db()
        db.execute(
            "UPDATE stage_state SET state='PASSED', last_passed_at=?, last_passed_commit=? WHERE stage=?",
            (datetime.now().isoformat(), _git_commit(), args.stage),
        )
        db.commit()
        db.close()
        print(f"{args.stage}: marked PASSED")

    elif args.command == "publish":
        with _acquire_lock():
            _run_stage("publish", "record", {"triggered_by": "cli"})

    elif args.command == "cleanup":
        branches = _list_test_branches()
        if not branches:
            print("No test branches to clean up.")
        for branch in branches:
            merged = subprocess.run(
                ["git", "merge-base", "--is-ancestor", branch, "main"],
                capture_output=True, cwd=str(ROOT),
            ).returncode == 0
            if merged:
                subprocess.run(["git", "branch", "-d", branch], cwd=str(ROOT))
                print(f"Deleted merged branch: {branch}")
            else:
                print(f"Keeping unmerged: {branch}")

    elif args.command == "recommend":
        _recommend()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

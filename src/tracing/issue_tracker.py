"""
Eval issue tracker — detect, track, and resolve issues found during eval runs.

Issues have deterministic IDs based on case+message+dimension+target.
Each state change (detected, resolved, regressed) is recorded with full
evidence: expected/actual values, agent output, message body, git commit.

Issues can be linked to GitHub issues and PRs for TDD workflow.
"""

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.tracing.judges import EvalResult, MessageScore, AssertionResult

log = logging.getLogger(__name__)

ISSUES_PATH = Path("tests/eval_issues.json")


@dataclass
class IssueEvent:
    event: str              # "detected", "resolved", "regressed"
    run_id: str
    timestamp: str
    git_commit: str
    evidence: dict = field(default_factory=dict)


@dataclass
class EvalIssue:
    id: str
    status: str             # "open", "resolved", "regressed", "wontfix"
    severity: str           # "high", "medium", "low"
    dimension: str          # "routing", "node_update", "item", "ambiguity", "forbidden"
    case_id: str
    message_id: str
    target: str             # node_id, item description, etc.
    description: str
    github_issue: str       # GitHub issue URL or number
    github_pr: str          # GitHub PR URL or number that fixes/addresses this
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "severity": self.severity,
            "dimension": self.dimension,
            "case_id": self.case_id,
            "message_id": self.message_id,
            "target": self.target,
            "description": self.description,
            "github_issue": self.github_issue,
            "github_pr": self.github_pr,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvalIssue":
        return cls(
            id=d["id"],
            status=d["status"],
            severity=d.get("severity", "medium"),
            dimension=d.get("dimension", ""),
            case_id=d.get("case_id", ""),
            message_id=d.get("message_id", ""),
            target=d.get("target", ""),
            description=d.get("description", ""),
            github_issue=d.get("github_issue", ""),
            github_pr=d.get("github_pr", ""),
            history=d.get("history", []),
        )


def make_issue_id(case_id: str, message_id: str,
                  dimension: str, target: str) -> str:
    """Generate a deterministic issue ID."""
    return f"{case_id}:{message_id}:{dimension}:{target}"


def _get_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "(unknown)"


def _get_git_diff_summary(from_ref: str, to_ref: str) -> str:
    """Get a one-line summary of changes between two commits."""
    try:
        files = [
            "src/agent/prompt.py", "src/agent/templates.py",
            "src/agent/update_agent.py", "src/router/router.py",
        ]
        output = subprocess.check_output(
            ["git", "diff", "--stat", from_ref, to_ref, "--"] + files,
            text=True, stderr=subprocess.DEVNULL,
        )
        lines = output.strip().splitlines()
        return lines[-1] if lines else "(no changes)"
    except Exception:
        return "(diff unavailable)"


def load_issues(path: Path = ISSUES_PATH) -> dict[str, EvalIssue]:
    """Load existing issues from JSON file."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {
        issue_id: EvalIssue.from_dict(issue_data)
        for issue_id, issue_data in data.get("issues", {}).items()
    }


def save_issues(issues: dict[str, EvalIssue], path: Path = ISSUES_PATH):
    """Save issues to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "issues": {
            issue_id: issue.to_dict()
            for issue_id, issue in sorted(issues.items())
        },
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    log.info("Saved %d issues to %s", len(issues), path)


def _severity_from_dimension(dimension: str) -> str:
    """Assign default severity based on dimension."""
    if dimension == "forbidden":
        return "high"
    if dimension in ("routing", "node_update"):
        return "medium"
    return "low"


def _build_evidence(assertion: AssertionResult, message_score: MessageScore,
                    baselines: dict, replay_state: dict) -> dict:
    """Build evidence dict for an issue event, including task state."""
    # Find the message body and expected task from baselines
    body = ""
    expected_task_id = ""
    for msg_bl in baselines.get("messages", []):
        if msg_bl["message_id"] == message_score.message_id:
            body = msg_bl.get("body_summary", "")
            expected_task_id = msg_bl.get("expected_task_id", "")
            break

    # Capture task state at time of issue
    task_state = {}
    if expected_task_id and expected_task_id in replay_state.get("node_states", {}):
        nodes = replay_state["node_states"][expected_task_id]
        task_state = {
            "task_id": expected_task_id,
            "node_summary": {
                "completed": sum(1 for n in nodes if n["status"] == "completed"),
                "active": sum(1 for n in nodes if n["status"] in ("active", "in_progress")),
                "pending": sum(1 for n in nodes if n["status"] == "pending"),
                "blocked": sum(1 for n in nodes if n["status"] == "blocked"),
                "provisional": sum(1 for n in nodes if n["status"] == "provisional"),
            },
            "non_pending_nodes": [
                {"node_id": n["node_id"], "status": n["status"],
                 "confidence": n.get("confidence")}
                for n in nodes if n["status"] != "pending"
            ],
        }
        # Include items for this task
        task_items = replay_state.get("items", {}).get(expected_task_id, [])
        if task_items:
            task_state["items"] = [
                {"description": it.get("description"), "quantity": it.get("quantity")}
                for it in task_items
            ]
        # Include message count
        task_state["messages_stored"] = replay_state.get(
            "message_counts", {}).get(expected_task_id, 0)

        # Ambiguity flags for this task
        all_flags = replay_state.get("ambiguity_flags", [])
        task_flags = [f for f in all_flags if f.get("task_id") == expected_task_id]
        if task_flags:
            task_state["ambiguity_flags"] = [
                {"severity": f.get("severity"), "category": f.get("category"),
                 "description": f.get("description", "")[:100],
                 "status": f.get("status")}
                for f in task_flags
            ]

        # Fulfillment links involving this task
        all_links = replay_state.get("fulfillment_links", [])
        task_links = [
            lk for lk in all_links
            if lk.get("client_order_id") == expected_task_id
            or lk.get("supplier_order_id") == expected_task_id
        ]
        if task_links:
            task_state["fulfillment_links"] = [
                {"client_order_id": lk.get("client_order_id"),
                 "supplier_order_id": lk.get("supplier_order_id"),
                 "status": lk.get("status"),
                 "quantity_allocated": lk.get("quantity_allocated")}
                for lk in task_links
            ]

    # Dead letters at time of issue
    dead_letter_count = replay_state.get("dead_letter_count", 0)

    return {
        "message_body": body,
        "expected": assertion.expected,
        "actual": assertion.actual,
        "assertion_notes": assertion.notes,
        "task_state": task_state,
        "dead_letter_count": dead_letter_count,
        "total_tasks": len(replay_state.get("node_states", {})),
        "total_ambiguity_flags": len(replay_state.get("ambiguity_flags", [])),
        "total_fulfillment_links": len(replay_state.get("fulfillment_links", [])),
    }


def update_issues_from_eval(
    eval_result: EvalResult,
    baselines: dict,
    replay_state: dict,
    run_id: str = "",
) -> dict:
    """
    Diff eval results against known issues. Returns a change summary.

    - New failures → create issues with "detected" event
    - Known open issues still failing → update last_seen
    - Known open issues now passing → add "resolved" event
    - Known resolved issues failing again → add "regressed" event
    """
    existing = load_issues()
    git_commit = _get_git_commit()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    case_id = eval_result.case_id

    # Collect all current failures
    current_failures: dict[str, tuple[AssertionResult, MessageScore]] = {}
    for ms in eval_result.message_scores:
        for assertion in ms.assertions:
            if not assertion.passed:
                issue_id = make_issue_id(
                    case_id, ms.message_id,
                    assertion.assertion_type, assertion.target,
                )
                current_failures[issue_id] = (assertion, ms)

    changes = {
        "new_issues": [],
        "still_open": [],
        "resolved": [],
        "regressed": [],
    }

    # Process current failures
    for issue_id, (assertion, ms) in current_failures.items():
        evidence = _build_evidence(assertion, ms, baselines, replay_state)

        if issue_id not in existing:
            # New issue
            issue = EvalIssue(
                id=issue_id,
                status="open",
                severity=_severity_from_dimension(assertion.assertion_type),
                dimension=assertion.assertion_type,
                case_id=case_id,
                message_id=ms.message_id,
                target=assertion.target,
                description=f"{assertion.assertion_type}: {assertion.target} — "
                           f"expected {assertion.expected}, got {assertion.actual}",
                github_issue="",
                github_pr="",
                history=[{
                    "event": "detected",
                    "run_id": run_id,
                    "timestamp": now,
                    "git_commit": git_commit,
                    "evidence": evidence,
                }],
            )
            existing[issue_id] = issue
            changes["new_issues"].append(issue_id)

        elif existing[issue_id].status == "resolved":
            # Regression!
            issue = existing[issue_id]
            last_resolved_commit = ""
            for h in reversed(issue.history):
                if h["event"] == "resolved":
                    last_resolved_commit = h.get("git_commit", "")
                    break

            diff_summary = _get_git_diff_summary(last_resolved_commit, git_commit) if last_resolved_commit else ""

            issue.status = "regressed"
            issue.history.append({
                "event": "regressed",
                "run_id": run_id,
                "timestamp": now,
                "git_commit": git_commit,
                "evidence": {
                    **evidence,
                    "previous_resolution_commit": last_resolved_commit,
                    "diff_since_resolution": diff_summary,
                },
            })
            changes["regressed"].append(issue_id)

        else:
            # Still open — update evidence with latest run
            changes["still_open"].append(issue_id)

    # Check for resolved issues (open issues not in current failures for this case)
    for issue_id, issue in existing.items():
        if issue.case_id != case_id:
            continue
        if issue.status in ("open", "regressed") and issue_id not in current_failures:
            # Find the commit when it was last seen
            last_seen_commit = ""
            for h in reversed(issue.history):
                if h.get("git_commit"):
                    last_seen_commit = h["git_commit"]
                    break

            diff_summary = _get_git_diff_summary(last_seen_commit, git_commit) if last_seen_commit else ""

            issue.status = "resolved"
            issue.history.append({
                "event": "resolved",
                "run_id": run_id,
                "timestamp": now,
                "git_commit": git_commit,
                "evidence": {
                    "diff_since_last_seen": diff_summary,
                },
            })
            changes["resolved"].append(issue_id)

    save_issues(existing)
    return changes


def print_changes(changes: dict):
    """Print a summary of issue changes from an eval run."""
    if changes["new_issues"]:
        print(f"\n  NEW ISSUES ({len(changes['new_issues'])}):")
        for issue_id in changes["new_issues"]:
            print(f"    + {issue_id}")

    if changes["regressed"]:
        print(f"\n  REGRESSIONS ({len(changes['regressed'])}):")
        for issue_id in changes["regressed"]:
            print(f"    !! {issue_id}")

    if changes["resolved"]:
        print(f"\n  RESOLVED ({len(changes['resolved'])}):")
        for issue_id in changes["resolved"]:
            print(f"    ✓ {issue_id}")

    if changes["still_open"]:
        print(f"\n  STILL OPEN ({len(changes['still_open'])}):")
        for issue_id in changes["still_open"]:
            print(f"    - {issue_id}")

    if not any(changes.values()):
        print("\n  No issues detected.")

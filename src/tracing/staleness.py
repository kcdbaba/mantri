"""
Eval baseline staleness heuristic.

Diffs the prompt-relevant source files between the baseline's version_tag
(git hash) and current HEAD. Categorizes changes as cosmetic vs structural
and recommends whether baselines need regeneration.
"""

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Files that affect agent output — changes here may invalidate baselines
EVAL_RELEVANT_FILES = [
    "src/agent/prompt.py",
    "src/agent/templates.py",
    "src/agent/update_agent.py",
    "src/router/router.py",
    "src/router/worker.py",
]

# Patterns that indicate structural changes (not just cosmetic)
STRUCTURAL_PATTERNS = [
    "node_id", "new_status", "status", "node_updates",
    "item_extraction", "ambiguity", "severity", "category",
    "task_assignment", "confirmation_gate", "order_type",
    "confidence", "threshold", "MONITORED_GROUPS",
    "def route(", "def _select_model(", "def _is_complex_message(",
    "def build_system_prompt(", "def build_user_section(",
    "class NodeUpdate", "class AmbiguityFlag", "class TaskOutput",
    "class AgentOutput", "class ItemExtraction",
]


@dataclass
class FileChange:
    path: str
    lines_added: int
    lines_removed: int
    is_structural: bool
    structural_hits: list[str] = field(default_factory=list)


@dataclass
class StalenessReport:
    baseline_version: str
    current_version: str
    files_changed: list[FileChange] = field(default_factory=list)
    total_structural_changes: int = 0
    total_cosmetic_changes: int = 0
    recommendation: str = ""
    stale: bool = False

    def print_report(self):
        if not self.files_changed:
            print(f"  Baselines up-to-date (version={self.baseline_version})")
            return

        icon = "!!" if self.stale else "OK"
        print(f"\n  [{icon}] Baseline staleness check")
        print(f"  Baseline version: {self.baseline_version}")
        print(f"  Current version:  {self.current_version}")
        print(f"  Files changed:    {len(self.files_changed)}")
        print(f"  Structural:       {self.total_structural_changes} changes")
        print(f"  Cosmetic:         {self.total_cosmetic_changes} changes")

        for fc in self.files_changed:
            tag = "STRUCTURAL" if fc.is_structural else "cosmetic"
            print(f"    {fc.path}: +{fc.lines_added}/-{fc.lines_removed} [{tag}]")
            for hit in fc.structural_hits[:3]:
                print(f"      → {hit}")

        print(f"\n  Recommendation: {self.recommendation}")


def check_staleness(baselines_path: Path) -> StalenessReport:
    """
    Check if eval baselines are stale relative to current code.

    Reads the version_tag from baselines JSON, diffs against HEAD,
    and categorizes changes.
    """
    baselines = json.loads(baselines_path.read_text())
    version_tag = baselines.get("version_tag", "")

    if not version_tag:
        return StalenessReport(
            baseline_version="(none)",
            current_version=_get_head(),
            recommendation="No version_tag in baselines — cannot check staleness. "
                          "Consider regenerating.",
            stale=True,
        )

    current = _get_head()
    if version_tag == current:
        return StalenessReport(
            baseline_version=version_tag,
            current_version=current,
            recommendation="Baselines match current HEAD.",
        )

    report = StalenessReport(
        baseline_version=version_tag,
        current_version=current,
    )

    # Get diff for each relevant file
    for filepath in EVAL_RELEVANT_FILES:
        diff_lines = _get_diff(version_tag, current, filepath)
        if not diff_lines:
            continue

        added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

        # Check for structural patterns in the diff
        structural_hits = []
        for line in diff_lines:
            if not (line.startswith("+") or line.startswith("-")):
                continue
            for pattern in STRUCTURAL_PATTERNS:
                if pattern in line:
                    structural_hits.append(f"{pattern}: {line.strip()[:80]}")
                    break

        is_structural = len(structural_hits) > 0
        fc = FileChange(
            path=filepath,
            lines_added=added,
            lines_removed=removed,
            is_structural=is_structural,
            structural_hits=structural_hits,
        )
        report.files_changed.append(fc)

        if is_structural:
            report.total_structural_changes += len(structural_hits)
        else:
            report.total_cosmetic_changes += added + removed

    # Generate recommendation
    if not report.files_changed:
        report.recommendation = "No eval-relevant files changed. Baselines are valid."
    elif report.total_structural_changes == 0:
        report.recommendation = (
            "Only cosmetic changes detected (comments, formatting). "
            "Baselines likely still valid."
        )
    elif report.total_structural_changes <= 3:
        report.stale = True
        report.recommendation = (
            f"{report.total_structural_changes} structural changes detected. "
            "Review affected dimensions — partial baseline update may be needed."
        )
    else:
        report.stale = True
        total_lines = sum(fc.lines_added + fc.lines_removed for fc in report.files_changed)
        report.recommendation = (
            f"{report.total_structural_changes} structural changes across "
            f"{len(report.files_changed)} files ({total_lines} lines). "
            "Recommend full baseline regeneration."
        )

    return report


def build_drift_prompt_section(report: StalenessReport) -> str:
    """
    Generate a drift context section for injection into LLM judge prompts.
    Returns empty string if baselines are not stale.
    """
    if not report.stale or not report.files_changed:
        return ""

    lines = [
        "## Pipeline changes since baseline was authored",
        f"Baseline version: {report.baseline_version} → Current: {report.current_version}",
        "",
    ]

    for fc in report.files_changed:
        if not fc.is_structural:
            continue
        lines.append(f"**{fc.path}** (+{fc.lines_added}/-{fc.lines_removed}):")
        for hit in fc.structural_hits:
            lines.append(f"  - {hit}")
        lines.append("")

    lines.extend([
        "## How to handle drift",
        "When evaluating, account for the changes above:",
        "- If a baseline expectation is INVALIDATED by a pipeline change "
        "(e.g., a new node was added that the baseline doesn't know about, "
        "or a status rule was changed), mark it as 'drift_invalidated' rather "
        "than penalizing the agent.",
        "- If an expectation is still valid despite the changes, score normally.",
        "- In your response, include a 'stale_baselines' list of any expectations "
        "that should be updated due to drift.",
        "",
    ])

    return "\n".join(lines)


def _get_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "(unknown)"


def _get_diff(from_ref: str, to_ref: str, filepath: str) -> list[str]:
    """Get unified diff lines for a specific file between two git refs."""
    try:
        output = subprocess.check_output(
            ["git", "diff", from_ref, to_ref, "--", filepath],
            text=True, stderr=subprocess.DEVNULL,
        )
        return output.splitlines()
    except subprocess.CalledProcessError:
        return []
    except Exception:
        return []

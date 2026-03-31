"""
DeepEval DAG evaluation — structured evaluation as a decision tree.

Routing → Item Extraction → Node Updates → Ambiguity Detection

Each node produces pass/fail + score. Can be run via pytest.
"""

import json
import logging
from pathlib import Path
from dataclasses import dataclass, field

from src.tracing.judges import judge_replay, EvalResult
from src.tracing.scorers import score_replay, ScoreCard
from src.tracing.llm_judges import run_llm_judges, LLMJudgment

log = logging.getLogger(__name__)


@dataclass
class DAGNode:
    name: str
    score: float
    passed: bool
    details: str = ""


@dataclass
class DAGResult:
    """Result of running the full evaluation DAG."""
    nodes: list[DAGNode] = field(default_factory=list)
    overall_score: float = 0.0
    overall_pass: bool = False

    def summary(self) -> dict:
        return {
            "overall_score": round(self.overall_score, 3),
            "overall_pass": self.overall_pass,
            "nodes": [
                {"name": n.name, "score": round(n.score, 3),
                 "passed": n.passed, "details": n.details}
                for n in self.nodes
            ],
        }


def run_eval_dag(
    case_dir: Path,
    baselines_filename: str = "eval_baselines.json",
    run_llm: bool = True,
) -> DAGResult:
    """
    Run the full evaluation DAG on a completed replay.

    DAG structure:
      1. Routing check (deterministic)
      2. Item extraction (deterministic + LLM fuzzy)
      3. Node update correctness (deterministic + LLM semantic)
      4. Ambiguity quality (LLM)

    Args:
        case_dir: path to case directory with replay_result.json and baselines
        baselines_filename: name of the baselines JSON file
        run_llm: whether to run LLM judges (set False for fast/free eval)
    """
    from src.tracing.staleness import check_staleness

    result_path = case_dir / "replay_result.json"
    baselines_path = case_dir / baselines_filename

    if not result_path.exists():
        raise FileNotFoundError(f"No replay result at {result_path}")
    if not baselines_path.exists():
        raise FileNotFoundError(f"No baselines at {baselines_path}")

    # Check staleness — used to inject drift context into LLM judges
    staleness_report = check_staleness(baselines_path)

    replay = json.loads(result_path.read_text())
    stats = replay["stats"]
    state = replay["state"]

    dag = DAGResult()

    # ── Node 1: Routing ─────────────────────────────────────────────
    scorecard = score_replay(stats, state)
    routing_score = scorecard.routing_accuracy
    routing_pass = routing_score >= 0.6  # 60% threshold
    dag.nodes.append(DAGNode(
        name="routing",
        score=routing_score,
        passed=routing_pass,
        details=f"routing_accuracy={routing_score:.2f}, "
                f"parse_success={scorecard.parse_success_rate:.2f}, "
                f"dead_letters={scorecard.dead_letter_rate:.2f}",
    ))

    if not routing_pass:
        # Short-circuit: if routing is bad, downstream scores are meaningless
        dag.overall_score = routing_score * 0.4
        dag.overall_pass = False
        return dag

    # ── Node 2: Item extraction ──────────────────────────────────────
    eval_result = judge_replay(baselines_path, result_path)
    item_score = eval_result.summary()["avg_item_score"]

    # Enhance with LLM fuzzy matching if enabled
    llm_judgments = []
    llm_item_score = None
    if run_llm:
        llm_judgments = run_llm_judges(baselines_path, result_path,
                                       staleness_report=staleness_report)
        item_judgments = [j for j in llm_judgments if j.dimension == "item_match"]
        if item_judgments:
            llm_item_score = sum(j.score for j in item_judgments) / len(item_judgments)
            item_score = max(item_score, llm_item_score)

    item_pass = item_score >= 0.7
    llm_str = f"{llm_item_score:.2f}" if llm_item_score is not None else "n/a"
    dag.nodes.append(DAGNode(
        name="item_extraction",
        score=item_score,
        passed=item_pass,
        details=f"deterministic={eval_result.summary()['avg_item_score']:.2f}, llm_fuzzy={llm_str}",
    ))

    # ── Node 3: Node update correctness ──────────────────────────────
    node_score = eval_result.summary()["avg_node_update_score"]
    forbidden = eval_result.summary()["total_forbidden_violations"]
    node_pass = node_score >= 0.5 and forbidden == 0

    dag.nodes.append(DAGNode(
        name="node_updates",
        score=node_score,
        passed=node_pass,
        details=f"avg_score={node_score:.2f}, forbidden_violations={forbidden}",
    ))

    # ── Node 4: Ambiguity quality ────────────────────────────────────
    ambiguity_score = 1.0  # default: no flags expected = perfect
    if run_llm:
        amb_judgments = [j for j in llm_judgments if j.dimension == "ambiguity"]
        if amb_judgments:
            ambiguity_score = sum(j.score for j in amb_judgments) / len(amb_judgments)

    # Also factor in the deterministic ambiguity rate check
    if not scorecard.ambiguity_rate_ok:
        ambiguity_score *= 0.5

    ambiguity_pass = ambiguity_score >= 0.5
    dag.nodes.append(DAGNode(
        name="ambiguity_quality",
        score=ambiguity_score,
        passed=ambiguity_pass,
        details=f"score={ambiguity_score:.2f}, rate_ok={scorecard.ambiguity_rate_ok}",
    ))

    # ── Overall ──────────────────────────────────────────────────────
    weights = {"routing": 0.25, "item_extraction": 0.30,
               "node_updates": 0.30, "ambiguity_quality": 0.15}
    dag.overall_score = sum(
        n.score * weights.get(n.name, 0.25)
        for n in dag.nodes
    )
    dag.overall_pass = all(n.passed for n in dag.nodes)

    return dag

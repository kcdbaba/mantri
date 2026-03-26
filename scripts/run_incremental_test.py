#!/usr/bin/env python3
"""
Incremental update agent test runner.

Tests the update agent against structured incremental test cases in
data/incremental_cases/. Each case provides:
  - task_state.json     : current node states (input)
  - new_message.json    : single new message (input) — or messages.json for multi-step
  - expected_updates.json / expected_final_state.json : ground truth

Scoring is deterministic (no LLM judge needed) — checks structured JSON output
against expected node updates.

Usage:
    python scripts/run_incremental_test.py                    # all cases
    python scripts/run_incremental_test.py INC-02             # single case by ID
    python scripts/run_incremental_test.py --summary-only     # print summary table
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.update_agent import run_update_agent, AgentOutput
from src.router.router import route
from src.router.alias_dict import match_entities

CASES_DIR = Path("data/incremental_cases")
RESULTS_DIR = Path("data/incremental_results")


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def actual_updates_map(output: AgentOutput) -> dict[str, dict]:
    """node_id → {status, confidence, evidence}"""
    return {u.node_id: {"status": u.new_status, "confidence": u.confidence,
                        "evidence": u.evidence}
            for u in output.node_updates}


def score_single_message_case(case_dir: Path, output: AgentOutput | None) -> dict:
    expected = json.loads((case_dir / "expected_updates.json").read_text())
    result = {
        "case_id": json.loads((case_dir / "metadata.json").read_text())["id"],
        "verdict": "FAIL",
        "required_passed": 0,
        "required_total": 0,
        "forbidden_violations": 0,
        "routing_correct": None,
        "details": [],
    }

    if output is None:
        result["details"].append("UPDATE AGENT RETURNED NONE — likely API failure")
        return result

    actual = actual_updates_map(output)

    # Check required updates
    required = expected.get("required_updates", [])
    result["required_total"] = len(required)
    for req in required:
        node_id = req["node_id"]
        if node_id not in actual:
            result["details"].append(f"MISSING required update: {node_id}")
            continue

        actual_node = actual[node_id]

        # Status check (exact or one_of)
        if "new_status" in req:
            expected_status = req["new_status"]
            if actual_node["status"] != expected_status:
                result["details"].append(
                    f"WRONG STATUS for {node_id}: expected={expected_status} "
                    f"actual={actual_node['status']}"
                )
                continue
        elif "new_status_one_of" in req:
            if actual_node["status"] not in req["new_status_one_of"]:
                result["details"].append(
                    f"WRONG STATUS for {node_id}: expected one of {req['new_status_one_of']} "
                    f"actual={actual_node['status']}"
                )
                continue

        # Confidence check
        if actual_node["confidence"] < req.get("min_confidence", 0.0):
            result["details"].append(
                f"LOW CONFIDENCE for {node_id}: "
                f"min={req['min_confidence']:.2f} actual={actual_node['confidence']:.2f}"
            )
            continue

        # Evidence check
        evidence_lower = actual_node.get("evidence", "").lower()
        for term in req.get("evidence_must_contain", []):
            if term.lower() not in evidence_lower:
                result["details"].append(
                    f"EVIDENCE MISSING '{term}' for {node_id}: {actual_node['evidence'][:80]}"
                )
                break
        else:
            result["required_passed"] += 1
            result["details"].append(f"✓ {node_id} → {actual_node['status']} "
                                     f"(conf={actual_node['confidence']:.2f})")

    # Check forbidden updates
    for forbidden in expected.get("forbidden_updates", []):
        node_id = forbidden["node_id"]
        if node_id in actual:
            result["forbidden_violations"] += 1
            result["details"].append(
                f"FORBIDDEN update present: {node_id} → {actual[node_id]['status']} "
                f"({forbidden['reason']})"
            )

    # Routing check (INC-03 style)
    routing_exp = expected.get("routing")
    if routing_exp:
        message = json.loads((case_dir / "new_message.json").read_text())
        routes = route(message)
        if any(task_id == routing_exp["expected_task_id"] for task_id, _ in routes):
            result["routing_correct"] = True
            result["details"].append(f"✓ Routing: message → {routing_exp['expected_task_id']}")
        else:
            result["routing_correct"] = False
            result["details"].append(
                f"✗ Routing: expected {routing_exp['expected_task_id']}, got {routes}"
            )

    # Verdict
    all_required_passed = result["required_passed"] == result["required_total"]
    no_forbidden = result["forbidden_violations"] == 0
    routing_ok = result["routing_correct"] is not False  # None = not tested = OK

    if all_required_passed and no_forbidden and routing_ok:
        result["verdict"] = "PASS"
    elif result["required_passed"] > 0 and no_forbidden:
        result["verdict"] = "PARTIAL"

    return result


def score_multi_message_case(case_dir: Path) -> dict:
    """INC-05 style: run 3 sequential update agent calls, check final state."""
    meta = json.loads((case_dir / "metadata.json").read_text())
    initial_state = json.loads((case_dir / "task_state.json").read_text())
    messages = json.loads((case_dir / "messages.json").read_text())
    expected_final = json.loads((case_dir / "expected_final_state.json").read_text())

    result = {
        "case_id": meta["id"],
        "verdict": "FAIL",
        "details": [],
        "per_message": [],
    }

    # Simulate sequential state updates
    current_nodes = {n["node_id"]: n["status"] for n in initial_state["nodes"]}

    for msg in messages:
        output = run_update_agent(initial_state["task_id"], msg)
        if output is None:
            result["details"].append(f"AGENT FAILURE on message seq={msg['seq']}")
            return result

        msg_result = {"seq": msg["seq"], "updates": []}
        for upd in output.node_updates:
            current_nodes[upd.node_id] = upd.new_status
            msg_result["updates"].append(f"{upd.node_id} → {upd.new_status}")
        result["per_message"].append(msg_result)

    # Check final state
    expected_nodes = expected_final.get("final_state_must_have", {})
    all_correct = True
    for node_id, expected_status in expected_nodes.items():
        actual_status = current_nodes.get(node_id, "MISSING")
        if actual_status == expected_status:
            result["details"].append(f"✓ {node_id}: {actual_status}")
        else:
            result["details"].append(
                f"✗ {node_id}: expected={expected_status} actual={actual_status}"
            )
            all_correct = False

    result["verdict"] = "PASS" if all_correct else "FAIL"
    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_case(case_dir: Path) -> dict:
    meta = json.loads((case_dir / "metadata.json").read_text())
    print(f"\n{'='*60}")
    print(f"Running {meta['id']}: {meta['name']}")
    print(f"Quality risk dimension: {meta['quality_risk_dimension']}")

    # Multi-message case
    if (case_dir / "messages.json").exists():
        return score_multi_message_case(case_dir)

    # Single message case
    message = json.loads((case_dir / "new_message.json").read_text())
    task_state = json.loads((case_dir / "task_state.json").read_text())

    t0 = time.time()
    output = run_update_agent(task_state["task_id"], message)
    elapsed = time.time() - t0

    result = score_single_message_case(case_dir, output)
    result["elapsed_s"] = round(elapsed, 2)

    if output:
        result["raw_updates"] = [
            {"node_id": u.node_id, "status": u.new_status, "conf": u.confidence}
            for u in output.node_updates
        ]

    return result


def save_result(case_dir: Path, result: dict):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{result['case_id']}_result.json"
    out_path.write_text(json.dumps(result, indent=2))


def print_result(result: dict):
    verdict = result["verdict"]
    marker = "✅" if verdict == "PASS" else ("⚠️ " if verdict == "PARTIAL" else "❌")
    print(f"\n{marker} {result['case_id']} — {verdict}")
    for line in result.get("details", []):
        print(f"   {line}")
    if "per_message" in result:
        for pm in result["per_message"]:
            print(f"   msg {pm['seq']}: {', '.join(pm['updates'])}")


def run_all(case_filter: str | None = None) -> list[dict]:
    case_dirs = sorted(CASES_DIR.iterdir())
    results = []
    for d in case_dirs:
        if not d.is_dir():
            continue
        if case_filter and case_filter not in d.name:
            continue
        result = run_case(d)
        save_result(d, result)
        print_result(result)
        results.append(result)
    return results


def print_summary(results: list[dict]):
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    total = len(results)
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    partial = sum(1 for r in results if r["verdict"] == "PARTIAL")
    failed = sum(1 for r in results if r["verdict"] == "FAIL")
    print(f"Total: {total}  PASS: {passed}  PARTIAL: {partial}  FAIL: {failed}")
    print()
    for r in results:
        marker = "✅" if r["verdict"] == "PASS" else ("⚠️ " if r["verdict"] == "PARTIAL" else "❌")
        print(f"  {marker}  {r['case_id']}")

    # Highlight the primary risk case
    inc02 = next((r for r in results if r["case_id"] == "INC-02"), None)
    if inc02:
        print()
        status = inc02["verdict"]
        if status == "PASS":
            print("✅ INC-02 (cadence detection) PASSED — primary quality risk gap is closed")
        else:
            print("❌ INC-02 (cadence detection) FAILED — primary quality risk gap is NOT closed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run incremental update agent tests")
    parser.add_argument("case_id", nargs="?", help="Run a single case by ID prefix (e.g. INC-02)")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    # Initialise DB (needed for task store reads used by update agent)
    from src.store.db import init_schema, seed_task
    from src.config import SEED_TASK, ENTITY_ALIASES
    from src.agent.templates import STANDARD_PROCUREMENT_TEMPLATE

    init_schema()
    nodes = STANDARD_PROCUREMENT_TEMPLATE["nodes"]
    aliases = [
        {"alias": alias, "entity_id": entity_id,
         "entity_type": "client" if "sata" in entity_id else "supplier"}
        for alias, entity_id in ENTITY_ALIASES.items()
    ]
    seed_task(SEED_TASK, nodes, aliases)

    results = run_all(case_filter=args.case_id)
    if not args.summary_only:
        print_summary(results)

#!/usr/bin/env python3
"""
Incremental update agent test runner.

Tests the update agent against structured incremental test cases in
data/incremental_cases/. Each case provides:
  - metadata.json          : case ID, name, quality risk dimension
  - task_state.json        : current node states (input)
  - new_message.json       : single new message — OR messages.json for multi-step
  - expected_updates.json  : ground truth for single-message cases
  - expected_final_state.json : ground truth for multi-step cases

Scoring is deterministic (no LLM judge needed).

Usage:
    python scripts/run_incremental_test.py                 # all cases
    python scripts/run_incremental_test.py INC-02          # single case by ID prefix
    python scripts/run_incremental_test.py --summary-only  # summary table only
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.update_agent import run_update_agent, AgentOutput
from src.router.router import route

CASES_DIR = Path("tests/functional_tests")
RESULTS_DIR = Path("tests/functional_tests/results")
RUNS_DIR = Path("runs/incremental")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_node_states(task_state: dict) -> list[dict]:
    """Convert test case node format → DB row format expected by prompt builder."""
    task_id = task_state["task_id"]
    return [
        {
            "id": f"{task_id}_{n['node_id']}",
            "task_id": task_id,
            "name": n["node_id"].replace("_", " ").title(),
            "status": n["status"],
        }
        for n in task_state["nodes"]
    ]


def actual_updates_map(output: AgentOutput) -> dict[str, dict]:
    """node_id → {status, confidence, evidence}"""
    return {
        u.node_id: {"status": u.new_status, "confidence": u.confidence, "evidence": u.evidence}
        for u in output.node_updates
    }


# ---------------------------------------------------------------------------
# Scoring — single message
# ---------------------------------------------------------------------------

def score_single_message_case(case_dir: Path, output: AgentOutput | None) -> dict:
    expected = json.loads((case_dir / "expected_updates.json").read_text())
    meta = json.loads((case_dir / "metadata.json").read_text())

    result = {
        "case_id": meta["id"],
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

    # --- Required node updates ---
    required = expected.get("required_updates", [])
    result["required_total"] += len(required)

    for req in required:
        node_id = req["node_id"]
        if node_id not in actual:
            result["details"].append(f"MISSING required update: {node_id}")
            continue

        actual_node = actual[node_id]

        # Status check
        if "new_status" in req:
            if actual_node["status"] != req["new_status"]:
                result["details"].append(
                    f"WRONG STATUS for {node_id}: expected={req['new_status']} "
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
            result["details"].append(
                f"✓ {node_id} → {actual_node['status']} (conf={actual_node['confidence']:.2f})"
            )

    # --- Required task candidates ---
    required_candidates = expected.get("required_task_candidates", [])
    result["required_total"] += len(required_candidates)

    for cand in required_candidates:
        found = any(c.get("type") == cand["type"] for c in output.new_task_candidates)
        if found:
            result["required_passed"] += 1
            result["details"].append(f"✓ new_task_candidate: {cand['type']}")
        else:
            result["details"].append(f"MISSING task_candidate: {cand['type']}")

    # --- Required item extractions ---
    for req_item in expected.get("required_item_extractions", []):
        result["required_total"] += 1
        matched = [
            e for e in output.item_extractions
            if e.operation == req_item["operation"]
            and req_item.get("description_contains", "").lower() in e.description.lower()
        ]
        if matched:
            result["required_passed"] += 1
            result["details"].append(
                f"✓ item_extraction: {req_item['operation']} '{matched[0].description}'"
            )
        else:
            result["details"].append(
                f"MISSING item_extraction: op={req_item['operation']} "
                f"contains='{req_item.get('description_contains', '')}'"
            )

    # --- Required node_data extractions ---
    for req_nd in expected.get("required_node_data_extractions", []):
        result["required_total"] += 1
        node_id = req_nd["node_id"]
        required_keys = req_nd.get("required_keys", [])
        matched = [e for e in output.node_data_extractions if e.node_id == node_id]
        if not matched:
            result["details"].append(f"MISSING node_data_extraction for node={node_id}")
            continue
        nd = matched[0].data
        missing_keys = [k for k in required_keys if k not in nd]
        if missing_keys:
            result["details"].append(
                f"node_data for {node_id} missing keys: {missing_keys} (got: {list(nd.keys())})"
            )
        else:
            result["required_passed"] += 1
            result["details"].append(
                f"✓ node_data_extraction: {node_id} keys={list(nd.keys())}"
            )

    # --- Required ambiguity flags ---
    for req_flag in expected.get("required_ambiguity_flags", []):
        result["required_total"] += 1
        matched = [
            f for f in output.ambiguity_flags
            if f.severity == req_flag.get("severity", f.severity)
            and f.category == req_flag.get("category", f.category)
        ]
        blocking_ok = True
        if req_flag.get("blocking_node_id") and matched:
            blocking_ok = any(f.blocking_node_id == req_flag["blocking_node_id"] for f in matched)
        if matched and blocking_ok:
            result["required_passed"] += 1
            result["details"].append(
                f"✓ ambiguity_flag: {matched[0].severity}/{matched[0].category} "
                f"blocking={matched[0].blocking_node_id}"
            )
        else:
            result["details"].append(
                f"MISSING ambiguity_flag: severity={req_flag.get('severity')} "
                f"category={req_flag.get('category')} blocking={req_flag.get('blocking_node_id')}"
            )

    # --- Forbidden updates ---
    for forbidden in expected.get("forbidden_updates", []):
        node_id = forbidden["node_id"]
        if node_id not in actual:
            continue
        actual_status = actual[node_id]["status"]
        forbidden_statuses = forbidden.get("forbidden_statuses")
        if forbidden_statuses:
            if actual_status in forbidden_statuses:
                result["forbidden_violations"] += 1
                result["details"].append(
                    f"FORBIDDEN status for {node_id}: {actual_status} "
                    f"(not allowed: {forbidden_statuses}) — {forbidden['reason']}"
                )
        else:
            result["forbidden_violations"] += 1
            result["details"].append(
                f"FORBIDDEN update present: {node_id} → {actual_status} ({forbidden['reason']})"
            )

    # --- Routing check ---
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

    # --- Verdict ---
    all_required = result["required_passed"] == result["required_total"]
    no_forbidden = result["forbidden_violations"] == 0
    routing_ok = result["routing_correct"] is not False

    if all_required and no_forbidden and routing_ok:
        result["verdict"] = "PASS"
    elif result["required_passed"] > 0 and no_forbidden:
        result["verdict"] = "PARTIAL"

    return result


# ---------------------------------------------------------------------------
# Scoring — multi-message
# ---------------------------------------------------------------------------

def score_multi_message_case(case_dir: Path) -> dict:
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

    current_nodes = {n["node_id"]: n["status"] for n in initial_state["nodes"]}
    message_history = []

    for msg in messages:
        node_states = [
            {
                "id": f"{initial_state['task_id']}_{node_id}",
                "task_id": initial_state["task_id"],
                "name": node_id.replace("_", " ").title(),
                "status": status,
            }
            for node_id, status in current_nodes.items()
        ]

        output = run_update_agent(
            initial_state["task_id"], msg,
            node_states_override=node_states,
            recent_messages_override=message_history[-20:],
        )
        if output is None:
            result["details"].append(f"AGENT FAILURE on message seq={msg['seq']}")
            return result

        message_history.append(msg)
        msg_result = {"seq": msg["seq"], "updates": []}
        for upd in output.node_updates:
            current_nodes[upd.node_id] = upd.new_status
            msg_result["updates"].append(f"{upd.node_id} → {upd.new_status}")
        result["per_message"].append(msg_result)

    all_correct = True

    for node_id, expected_status in expected_final.get("final_state_must_have", {}).items():
        actual = current_nodes.get(node_id, "MISSING")
        if actual == expected_status:
            result["details"].append(f"✓ {node_id}: {actual}")
        else:
            result["details"].append(f"✗ {node_id}: expected={expected_status} actual={actual}")
            all_correct = False

    for node_id, acceptable in expected_final.get("final_state_acceptable", {}).items():
        actual = current_nodes.get(node_id, "MISSING")
        if actual in acceptable:
            result["details"].append(f"✓ {node_id}: {actual} (acceptable)")
        else:
            result["details"].append(
                f"✗ {node_id}: expected one of {acceptable} actual={actual}"
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

    if (case_dir / "messages.json").exists():
        return score_multi_message_case(case_dir)

    message = json.loads((case_dir / "new_message.json").read_text())
    task_state = json.loads((case_dir / "task_state.json").read_text())
    node_states = build_node_states(task_state)

    items_override = task_state.get("items")  # optional: pre-existing order items
    task_override = {
        "id": task_state["task_id"],
        "order_type": task_state.get("order_type", "standard_procurement"),
    }

    t0 = time.time()
    output = run_update_agent(
        task_state["task_id"], message,
        node_states_override=node_states,
        recent_messages_override=[],
        items_override=items_override,
        task_override=task_override,
    )
    elapsed = time.time() - t0

    result = score_single_message_case(case_dir, output)
    result["elapsed_s"] = round(elapsed, 2)

    if output:
        result["raw_updates"] = [
            {"node_id": u.node_id, "status": u.new_status, "conf": u.confidence}
            for u in output.node_updates
        ]
        result["raw_candidates"] = output.new_task_candidates
        result["raw_item_extractions"] = [
            {"op": e.operation, "desc": e.description, "qty": e.quantity, "unit": e.unit}
            for e in output.item_extractions
        ]
        result["raw_node_data"] = [
            {"node_id": e.node_id, "keys": list(e.data.keys())}
            for e in output.node_data_extractions
        ]
        result["raw_ambiguity_flags"] = [
            {"severity": f.severity, "category": f.category, "blocking": f.blocking_node_id}
            for f in output.ambiguity_flags
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
            print(f"   msg {pm['seq']}: {', '.join(pm['updates']) or '(no updates)'}")


def run_all(case_filter: str | None = None) -> list[dict]:
    case_dirs = sorted(CASES_DIR.iterdir())
    results = []
    for d in case_dirs:
        if not d.is_dir() or not (d / "metadata.json").exists():
            continue
        if case_filter and case_filter not in d.name:
            continue
        result = run_case(d)
        save_result(d, result)
        print_result(result)
        results.append(result)
    return results


def _publish_results():
    """Regenerate static/developer/runs/index.html after a full run."""
    try:
        import subprocess
        script = Path(__file__).parent / "publish_runs.py"
        subprocess.run([sys.executable, str(script)], check=True)
    except Exception as e:
        print(f"  publish_runs failed (non-fatal): {e}")


def _phoenix_client():
    import phoenix as px
    endpoint = os.environ.get("PHOENIX_ENDPOINT", "http://localhost:6006")
    headers = {}
    if auth := os.environ.get("PHOENIX_AUTH_HEADER"):
        headers["Authorization"] = auth
    return px.Client(endpoint=endpoint, headers=headers)


def push_to_phoenix(results: list[dict]) -> None:
    """Push INC test results to Phoenix as a named experiment."""
    try:
        import phoenix as px
        from phoenix.experiments import run_experiment
    except ImportError:
        print("Phoenix not installed — skipping experiment upload")
        return

    try:
        client = _phoenix_client()

        try:
            dataset = client.upload_dataset(
                dataset_name="mantri-incremental-tests",
                inputs=[{"case_id": r["case_id"]} for r in results],
                outputs=[{"expected_verdict": "PASS"} for r in results],
                metadata=[{"case_id": r["case_id"]} for r in results],
            )
        except Exception:
            dataset = client.get_dataset(name="mantri-incremental-tests")

        results_by_id = {r["case_id"]: r for r in results}

        def passthrough(input):  # noqa: A002
            return results_by_id.get(input["case_id"], {"verdict": "ERROR"})

        def verdict_label(output) -> str:
            return output.get("verdict", "FAIL")

        def pass_rate(output) -> float:
            total = output.get("required_total", 0)
            passed = output.get("required_passed", 0)
            if output.get("verdict") == "PASS":
                return 1.0
            return round(passed / total, 2) if total > 0 else 0.0

        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        exp_name = f"inc-{ts}"
        run_experiment(
            dataset,
            task=passthrough,
            evaluators={"verdict": verdict_label, "pass_rate": pass_rate},
            experiment_name=exp_name,
            print_summary=False,
        )
        endpoint = os.environ.get("PHOENIX_ENDPOINT", "http://localhost:6006")
        print(f"  Phoenix: {endpoint}  (experiment: {exp_name})")
    except Exception as e:
        print(f"  Phoenix upload failed (non-fatal): {e}")


def save_run_summary(results: list[dict]):
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    passed  = sum(1 for r in results if r["verdict"] == "PASS")
    partial = sum(1 for r in results if r["verdict"] == "PARTIAL")
    failed  = sum(1 for r in results if r["verdict"] == "FAIL")
    summary = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "total":   len(results),
        "passed":  passed,
        "partial": partial,
        "failed":  failed,
        "results": [
            {"case_id": r["case_id"], "verdict": r["verdict"],
             "required_passed": r.get("required_passed"), "required_total": r.get("required_total")}
            for r in results
        ],
    }
    path = RUNS_DIR / f"{ts}_summary.json"
    path.write_text(json.dumps(summary, indent=2))
    print(f"\nRun summary: {path}")


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

    checkpoints = {
        "INC-05": "supplier_QC → order_ready auto-trigger",
        "INC-08": "filled_from_stock → order_ready auto-trigger",
    }
    print()
    for case_id, label in checkpoints.items():
        r = next((r for r in results if r["case_id"] == case_id), None)
        if r:
            status = "✅ PASSED" if r["verdict"] == "PASS" else "❌ FAILED — quality risk open"
            print(f"{status}: {case_id} ({label})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run incremental update agent tests")
    parser.add_argument("case_id", nargs="?", help="Run a single case by ID prefix")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

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
    if not args.case_id:  # only save run history and push to Phoenix for full runs
        save_run_summary(results)
        push_to_phoenix(results)
        _publish_results()

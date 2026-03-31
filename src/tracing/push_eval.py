"""
Push eval results to Phoenix as span annotations.

Attaches deterministic scores, LLM judge results, and DAG eval
dimensions to the corresponding trace spans in Phoenix.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def push_eval_to_phoenix(
    case_dir: Path,
    baselines_filename: str = "eval_baselines.json",
    phoenix_endpoint: str = "http://localhost:6006",
    project_name: str = "mantri",
):
    """
    Run eval and push all results to Phoenix as annotations.

    1. Runs deterministic scorers → CODE annotations on root span
    2. Runs deterministic judges → CODE annotations on message spans
    3. Runs LLM judges → LLM annotations on message spans
    4. Runs DAG eval → CODE annotations on root span
    """
    import phoenix as px
    from src.tracing.scorers import score_replay
    from src.tracing.judges import judge_replay
    from src.tracing.llm_judges import run_llm_judges
    from src.tracing.deepeval_dag import run_eval_dag
    from src.tracing.staleness import check_staleness

    result_path = case_dir / "replay_result.json"
    baselines_path = case_dir / baselines_filename

    if not result_path.exists():
        log.error("No replay result at %s", result_path)
        return

    replay = json.loads(result_path.read_text())
    stats = replay["stats"]
    state = replay["state"]

    # Connect to Phoenix
    client = px.Client(endpoint=phoenix_endpoint)
    trace_df = client.get_spans_dataframe(project_name=project_name, limit=50000)

    if trace_df is None or len(trace_df) == 0:
        log.warning("No spans found in Phoenix project %s", project_name)
        return

    # Find the root span (replay:*) and message spans
    root_spans = trace_df[trace_df["name"].str.startswith("replay:")]
    msg_spans = trace_df[trace_df["name"].str.startswith("message:")]

    if len(root_spans) == 0:
        log.warning("No root replay span found")
        return

    # Use the most recent root span
    root_span_id = root_spans.sort_values("start_time", ascending=False).iloc[0]["context.span_id"]

    # Build message_id → span_id lookup
    msg_span_ids = {}
    for _, row in msg_spans.iterrows():
        attrs = row.get("attributes.message", {})
        if isinstance(attrs, dict):
            mid = attrs.get("id", "")
            if mid:
                msg_span_ids[mid] = row["context.span_id"]

    annotations = []

    # ── 1. Deterministic scorers → root span ────────────────────────
    card = score_replay(stats, state, trace_df=trace_df)
    summary = card.summary()
    for key, value in summary.items():
        if key == "detail_count":
            continue
        if isinstance(value, bool):
            score = 1.0 if value else 0.0
            label = "PASS" if value else "FAIL"
        elif isinstance(value, (int, float)):
            score = float(value)
            label = "PASS" if score >= 0.8 else "WARN" if score >= 0.5 else "FAIL"
        else:
            continue
        annotations.append({
            "span_id": root_span_id,
            "name": f"scorer:{key}",
            "annotator_kind": "CODE",
            "label": label,
            "score": score,
            "explanation": f"{key}={value}",
        })
    log.info("Prepared %d scorer annotations", len(annotations))

    # ── 2. Deterministic judges → message spans ─────────────────────
    if baselines_path.exists():
        eval_result = judge_replay(baselines_path, result_path, trace_df=trace_df)

        for ms in eval_result.message_scores:
            span_id = msg_span_ids.get(ms.message_id)
            if not span_id:
                continue

            annotations.append({
                "span_id": span_id,
                "name": "judge:node_update_score",
                "annotator_kind": "CODE",
                "label": "PASS" if ms.node_update_score >= 0.5 else "FAIL",
                "score": ms.node_update_score,
                "explanation": json.dumps([a.__dict__ for a in ms.assertions
                                           if a.assertion_type == "node_update"]),
            })
            annotations.append({
                "span_id": span_id,
                "name": "judge:item_score",
                "annotator_kind": "CODE",
                "label": "PASS" if ms.item_score >= 0.7 else "FAIL",
                "score": ms.item_score,
                "explanation": json.dumps([a.__dict__ for a in ms.assertions
                                           if a.assertion_type == "item"]),
            })
            if ms.forbidden_violations > 0:
                annotations.append({
                    "span_id": span_id,
                    "name": "judge:forbidden_violation",
                    "annotator_kind": "CODE",
                    "label": "FAIL",
                    "score": 0.0,
                    "explanation": json.dumps([a.__dict__ for a in ms.assertions
                                               if a.assertion_type == "forbidden" and not a.passed]),
                })
            annotations.append({
                "span_id": span_id,
                "name": "judge:overall",
                "annotator_kind": "CODE",
                "label": "PASS" if ms.overall_pass else "FAIL",
                "score": 1.0 if ms.overall_pass else 0.0,
            })

        log.info("Prepared %d judge annotations (total so far)", len(annotations))

        # ── 3. LLM judges → message spans ──────────────────────────
        staleness = check_staleness(baselines_path)
        llm_judgments = run_llm_judges(baselines_path, result_path,
                                       staleness_report=staleness)
        for j in llm_judgments:
            span_id = msg_span_ids.get(j.message_id)
            if not span_id:
                continue
            annotations.append({
                "span_id": span_id,
                "name": f"llm_judge:{j.dimension}",
                "annotator_kind": "LLM",
                "label": j.verdict,
                "score": j.score,
                "explanation": j.reasoning[:500],
            })

        log.info("Prepared %d annotations (including LLM judges)", len(annotations))

    # ── 4. DAG eval → root span ─────────────────────────────────────
    if baselines_path.exists():
        dag = run_eval_dag(case_dir, baselines_filename=baselines_filename,
                           run_llm=False)  # skip LLM to avoid double calls
        annotations.append({
            "span_id": root_span_id,
            "name": "dag:overall",
            "annotator_kind": "CODE",
            "label": "PASS" if dag.overall_pass else "FAIL",
            "score": dag.overall_score,
            "explanation": json.dumps(dag.summary()),
        })
        for node in dag.nodes:
            annotations.append({
                "span_id": root_span_id,
                "name": f"dag:{node.name}",
                "annotator_kind": "CODE",
                "label": "PASS" if node.passed else "FAIL",
                "score": node.score,
                "explanation": node.details,
            })

    # ── Push all annotations to Phoenix ─────────────────────────────
    if not annotations:
        log.info("No annotations to push")
        return

    import requests
    resp = requests.post(
        f"{phoenix_endpoint}/v1/span_annotations",
        json={"data": annotations},
    )
    if resp.status_code == 200:
        log.info("Pushed %d annotations to Phoenix (%s)", len(annotations), phoenix_endpoint)
        print(f"Pushed {len(annotations)} eval annotations to Phoenix")
    else:
        log.warning("Failed to push annotations: %s %s", resp.status_code, resp.text[:200])
        print(f"Failed to push annotations: {resp.status_code}")

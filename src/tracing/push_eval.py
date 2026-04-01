"""
Push eval results to Phoenix as SpanEvaluations.

Uses Phoenix's log_evaluations() API which populates the
annotations column in the UI, the Metrics tab, and the Traces table.
"""

import json
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def push_eval_to_phoenix(
    case_dir: Path,
    baselines_filename: str = "eval_baselines.json",
    phoenix_endpoint: str = "http://localhost:6006",
    project_name: str = "mantri",
    auth_headers: dict | None = None,
    session_id: str | None = None,
):
    """
    Run eval and push results to Phoenix as SpanEvaluations.

    1. Deterministic scorers → eval on message spans
    2. Deterministic judges → per-message evals
    3. LLM judges → per-message evals
    4. DAG eval → eval on message spans
    """
    import phoenix as px
    from phoenix.trace import SpanEvaluations
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

    client = px.Client(endpoint=phoenix_endpoint, headers=auth_headers or {})
    trace_df = client.get_spans_dataframe(project_name=project_name, limit=50000, timeout=60)

    if trace_df is None or len(trace_df) == 0:
        log.warning("No spans found in Phoenix project %s", project_name)
        return

    msg_spans = trace_df[trace_df["name"].str.startswith("message:")]

    # Filter by session if specified
    if session_id:
        msg_spans = msg_spans[msg_spans["attributes.session.id"] == session_id]
        log.info("Filtered to session %s: %d message spans", session_id, len(msg_spans))
    elif "attributes.session.id" in msg_spans.columns:
        # Auto-detect: if case_dir name matches a session's case_id attribute, use that
        case_id = case_dir.name.split("_")[0]
        for sid in msg_spans["attributes.session.id"].dropna().unique():
            session_spans = msg_spans[msg_spans["attributes.session.id"] == sid]
            if len(session_spans) > 0:
                run_attrs = session_spans.iloc[0].get("attributes.run", {})
                if isinstance(run_attrs, dict) and run_attrs.get("case_id") == case_id:
                    msg_spans = session_spans
                    session_id = sid
                    log.info("Auto-detected session %s for case %s: %d spans",
                             sid, case_id, len(msg_spans))
                    break

    if len(msg_spans) == 0:
        log.warning("No message spans found (session=%s)", session_id)
        return

    # Build message_id → span_id lookup
    msg_span_ids = {}
    for _, row in msg_spans.iterrows():
        attrs = row.get("attributes.message", {})
        if isinstance(attrs, dict):
            mid = attrs.get("id", "")
            if mid:
                msg_span_ids[mid] = row["context.span_id"]

    all_span_ids = list(msg_span_ids.values())
    log.info("Found %d message spans in Phoenix (session=%s)", len(all_span_ids), session_id)

    # ── 1. Deterministic scorers → one eval per message span ────────
    card = score_replay(stats, state, trace_df=trace_df)
    summary = card.summary()

    scorer_rows = []
    for span_id in all_span_ids:
        scorer_rows.append({
            "context.span_id": span_id,
            "label": "PASS" if summary["routing_accuracy"] >= 0.6 else "FAIL",
            "score": summary["routing_accuracy"],
            "explanation": json.dumps(summary),
        })
    if scorer_rows:
        scorer_df = pd.DataFrame(scorer_rows).set_index("context.span_id")
        client.log_evaluations(SpanEvaluations(
            eval_name="pipeline_scorecard",
            dataframe=scorer_df,
        ))
        log.info("Pushed pipeline_scorecard eval (%d spans)", len(scorer_rows))

    # ── 2. Deterministic judges → per-message evals ─────────────────
    if baselines_path.exists():
        eval_result = judge_replay(baselines_path, result_path, trace_df=trace_df)

        judge_rows = []
        for ms in eval_result.message_scores:
            span_id = msg_span_ids.get(ms.message_id)
            if not span_id:
                continue
            judge_rows.append({
                "context.span_id": span_id,
                "label": "PASS" if ms.overall_pass else "FAIL",
                "score": ms.node_update_score,
                "explanation": f"nodes={ms.node_update_score:.2f} items={ms.item_score:.2f} "
                              f"forbidden={ms.forbidden_violations}",
            })

        if judge_rows:
            judge_df = pd.DataFrame(judge_rows).set_index("context.span_id")
            client.log_evaluations(SpanEvaluations(
                eval_name="deterministic_judge",
                dataframe=judge_df,
            ))
            log.info("Pushed deterministic_judge eval (%d spans)", len(judge_rows))

        # ── 3. LLM judges → per-message evals ──────────────────────
        staleness = check_staleness(baselines_path)
        llm_judgments = run_llm_judges(baselines_path, result_path,
                                       staleness_report=staleness)

        if llm_judgments:
            llm_rows = []
            for j in llm_judgments:
                span_id = msg_span_ids.get(j.message_id)
                if not span_id:
                    continue
                llm_rows.append({
                    "context.span_id": span_id,
                    "label": j.verdict,
                    "score": j.score,
                    "explanation": f"{j.dimension}: {j.reasoning[:200]}",
                })
            if llm_rows:
                llm_df = pd.DataFrame(llm_rows).set_index("context.span_id")
                client.log_evaluations(SpanEvaluations(
                    eval_name="llm_judge",
                    dataframe=llm_df,
                ))
                log.info("Pushed llm_judge eval (%d spans)", len(llm_rows))

        # ── 4. DAG eval → per-message summary eval ─────────────────
        dag = run_eval_dag(case_dir, baselines_filename=baselines_filename,
                           run_llm=False)

        dag_rows = []
        for span_id in all_span_ids:
            dag_rows.append({
                "context.span_id": span_id,
                "label": "PASS" if dag.overall_pass else "FAIL",
                "score": dag.overall_score,
                "explanation": json.dumps(dag.summary()),
            })
        if dag_rows:
            dag_df = pd.DataFrame(dag_rows).set_index("context.span_id")
            client.log_evaluations(SpanEvaluations(
                eval_name="eval_dag",
                dataframe=dag_df,
            ))
            log.info("Pushed eval_dag eval (%d spans)", len(dag_rows))

    total = len(scorer_rows) + len(judge_rows if baselines_path.exists() else []) + \
            len(llm_rows if 'llm_rows' in dir() else []) + \
            len(dag_rows if 'dag_rows' in dir() else [])
    print(f"Pushed {total} eval annotations to Phoenix ({phoenix_endpoint})")

#!/usr/bin/env python3
"""
publish_system.py

Reads replay results, eval scores, and run records to generate the
System Tests detail page at static/developer/system/index.html.

Covers: live replay results, eval scores (real + synth), run history.

Usage:
    python scripts/publish_system.py
"""

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CASES_DIR = Path("tests/integration_tests")
RUNS_DIR = Path("tests/runs/integration")
EVAL_RUNS_DIR = Path("tests/runs/eval")
EVALS_DIR = Path("tests/evals")
OUT_PATH = Path("static/developer/system/index.html")

# Global counter for unique table IDs (used by pagination)
_table_counter = 0

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_case_results() -> list[dict]:
    """Find all case dirs with replay_result.json."""
    results = []
    for d in sorted(CASES_DIR.iterdir()):
        result_file = d / "replay_result.json"
        if result_file.exists():
            data = json.loads(result_file.read_text(encoding="utf-8"))
            data["_case_dir"] = d.name
            # Try to load metadata from eval case
            meta_candidates = list(Path("tests/evals").glob(f"{d.name}/metadata.json"))
            if meta_candidates:
                meta = json.loads(meta_candidates[0].read_text(encoding="utf-8"))
                data["_meta"] = meta

            # Load from replay_result.db if available
            db_path = d / "replay_result.db"
            if db_path.exists():
                try:
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row

                    # Fix 5: Load ambiguity bodies
                    rows = conn.execute(
                        "SELECT description, body FROM ambiguity_queue ORDER BY created_at"
                    ).fetchall()
                    body_map = {row["description"]: row["body"] for row in rows}
                    for flag in data.get("state", {}).get("ambiguity_flags", []):
                        flag["body"] = body_map.get(flag.get("description"), "")

                    # Model usage and cost
                    usage_rows = conn.execute(
                        "SELECT model, COUNT(*) as calls, SUM(cost_usd) as cost "
                        "FROM usage_log GROUP BY model"
                    ).fetchall()
                    data["_model_usage"] = [
                        {"model": r["model"], "calls": r["calls"], "cost": r["cost"] or 0}
                        for r in usage_rows
                    ]
                    total_cost = conn.execute(
                        "SELECT SUM(cost_usd) FROM usage_log"
                    ).fetchone()[0] or 0
                    data["_total_cost"] = total_cost

                    conn.close()
                except Exception:
                    pass

            results.append(data)
    return results


def _load_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    runs = []
    for f in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            runs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return runs


def _build_tags_html(run: dict) -> str:
    """Build concise tag badges with hover tooltips from a run record."""
    badges = []
    # Routing modes
    rm = run.get("routing_mode", "")
    if rm == "entity_first":
        badges.append("<span class='tag tag-mode' title='Entity-first routing'>E</span>")
    if run.get("conversations_created") or run.get("warmup_messages"):
        badges.append("<span class='tag tag-mode' title='Conversation routing for shared groups'>I</span>")
    # Tracing
    if run.get("traced"):
        badges.append("<span class='tag tag-traced' title='Phoenix OTEL tracing enabled'>T</span>")
    # Agents used
    run_agents = run.get("agents", [])
    if not run_agents:
        run_agents = ["AO"] if run.get("skip_linkage") else ["AO", "AL"]
    agent_tips = {"AO": "Update agent for order processing",
                  "AL": "Linkage agent for fulfillment matching"}
    for agent in run_agents:
        badges.append(f"<span class='tag tag-agent' title='{agent_tips.get(agent, agent)}'>{agent}</span>")
    # Dev test
    if run.get("dev_test"):
        badges.append("<span class='tag tag-dim' title='Dev test (cached LLM, pre-seeded tasks)'>\U0001f6a7</span>")
    # Partial replay
    max_msgs = run.get("max_messages")
    if max_msgs:
        badges.append(f"<span class='tag tag-dim' title='Partial replay (first {max_msgs} messages)'>{max_msgs}</span>")
    return " ".join(badges) if badges else "<span class='dim'>\u2014</span>"


def _fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def _fmt_dt_short(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%m/%d %H:%M")
    except Exception:
        return iso


def _fmt_dt_chart(iso: str) -> str:
    """Format date as 'Mar 30' for chart x-axis."""
    try:
        return datetime.fromisoformat(iso).strftime("%b %d")
    except Exception:
        return iso


# ---------------------------------------------------------------------------
# HTML sections
# ---------------------------------------------------------------------------

STATUS_CLASSES = {
    "completed": "pass",
    "in_progress": "active",
    "active": "active",
    "blocked": "fail",
    "provisional": "partial",
    "pending": "pending",
    "skipped": "skipped",
    "failed": "fail",
}

SEVERITY_CLASSES = {
    "high": "fail",
    "medium": "partial",
    "low": "pending",
}


def _paginated_table(headers: list[str], rows_html: list[str], table_id: str) -> str:
    """Wrap rows in a paginated table (10 rows per page)."""
    global _table_counter
    _table_counter += 1
    tid = table_id or f"tbl_{_table_counter}"
    page_size = 5
    needs_pagination = len(rows_html) > page_size

    tagged_rows = []
    for idx, row_html in enumerate(rows_html):
        page = idx // page_size
        style = "" if page == 0 else " style='display:none'"
        # Inject pagination attributes into the <tr> tag
        tagged_rows.append(
            row_html.replace("<tr>", f"<tr class='prow-{tid}' data-page='{page}'{style}>", 1)
        )

    total_pages = (len(rows_html) + page_size - 1) // page_size
    pagination_html = ""
    if needs_pagination:
        pagination_html = (
            f"<div class='pagination' id='pag-{tid}'>"
            f"<button onclick=\"paginate('{tid}', -1)\">Previous</button>"
            f"<span class='page-indicator' id='page-ind-{tid}'>Page 1 of {total_pages}</span>"
            f"<button onclick=\"paginate('{tid}', 1)\">Next</button>"
            f"</div>"
        )

    header_html = "".join(f"<th>{h}</th>" for h in headers)
    return (
        f"{pagination_html}"
        f"<table class='detail' id='tbl-{tid}'>"
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(tagged_rows)}</tbody></table>"
    )


def _node_table(task_id: str, nodes: list[dict]) -> str:
    global _table_counter
    _table_counter += 1
    tid = f"node_{task_id}_{_table_counter}"

    raw_rows = []
    for n in nodes:
        name = n["name"]
        status = n["status"]
        cls = STATUS_CLASSES.get(status, "")
        conf = f"{n['confidence']:.2f}" if n["confidence"] is not None else "\u2014"
        by = n.get("updated_by", "\u2014")
        raw_rows.append(
            f"<tr><td>{name}</td>"
            f"<td class='{cls}'>{status}</td>"
            f"<td>{conf}</td>"
            f"<td class='dim'>{by}</td></tr>"
        )
    return _paginated_table(
        ["Node", "Status", "Confidence", "Updated by"],
        raw_rows,
        tid,
    )


def _items_table(task_id: str, items: list[dict]) -> str:
    if not items:
        return "<p class='dim'>No items extracted</p>"

    global _table_counter
    _table_counter += 1
    tid = f"items_{task_id}_{_table_counter}"

    raw_rows = []
    for item in items:
        qty = item["quantity"] if item["quantity"] is not None else "<span class='fail'>null</span>"
        specs = (item.get("specs") or "\u2014")[:120]
        unit = item.get("unit", "\u2014")
        raw_rows.append(
            f"<tr><td>{item['description']}</td>"
            f"<td>{qty}</td>"
            f"<td>{unit}</td>"
            f"<td class='dim'>{specs}</td></tr>"
        )
    return _paginated_table(
        ["Description", "Qty", "Unit", "Specs"],
        raw_rows,
        tid,
    )


def _ambiguity_table(flags: list[dict], table_id: str = "") -> str:
    global _table_counter
    if not flags:
        return "<p class='dim'>No ambiguity flags raised</p>"

    _table_counter += 1
    tid = table_id or f"amb_{_table_counter}"
    page_size = 5
    needs_pagination = len(flags) > page_size

    # Check if any flag has a body (from Fix 5)
    has_body = any(f.get("body") for f in flags)

    rows = []
    for idx, f in enumerate(flags):
        sev_cls = SEVERITY_CLASSES.get(f["severity"], "")
        node = f.get("node_id") or "\u2014"
        desc = f["description"][:200]
        # Add data-page attribute for pagination
        page = idx // page_size
        style = "" if page == 0 else " style='display:none'"
        body_col = ""
        if has_body:
            body_text = (f.get("body") or "\u2014")[:80]
            body_col = f"<td class='dim'>{body_text}</td>"
        rows.append(
            f"<tr class='prow-{tid}' data-page='{page}'{style}>"
            f"<td>{f['task_id']}</td>"
            f"<td class='{sev_cls}'>{f['severity']}</td>"
            f"<td>{f['category']}</td>"
            f"<td>{node}</td>"
            f"<td class='desc'>{desc}</td>"
            f"{body_col}</tr>"
        )

    total_pages = (len(flags) + page_size - 1) // page_size

    pagination_html = ""
    if needs_pagination:
        pagination_html = (
            f"<div class='pagination' id='pag-{tid}'>"
            f"<button onclick=\"paginate('{tid}', -1)\">Previous</button>"
            f"<span class='page-indicator' id='page-ind-{tid}'>Page 1 of {total_pages}</span>"
            f"<button onclick=\"paginate('{tid}', 1)\">Next</button>"
            f"</div>"
        )

    msg_header = "<th>Message</th>" if has_body else ""
    return (
        f"{pagination_html}"
        f"<table class='detail' id='tbl-{tid}'>"
        f"<thead><tr><th>Task</th><th>Severity</th><th>Category</th>"
        f"<th>Node</th><th>Description</th>{msg_header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _links_table(links: list[dict]) -> str:
    if not links:
        return "<p class='dim'>No fulfillment links created</p>"
    rows = []
    for link in links:
        rows.append(
            f"<tr><td>{link['client_order_id']}</td>"
            f"<td>{link['client_item_description']}</td>"
            f"<td>{link['supplier_order_id']}</td>"
            f"<td>{link['supplier_item_description']}</td>"
            f"<td>{link['quantity_allocated']}</td>"
            f"<td>{link['match_confidence']:.2f}</td>"
            f"<td>{link['status']}</td></tr>"
        )
    return (
        f"<table class='detail'>"
        f"<thead><tr><th>Client order</th><th>Client item</th>"
        f"<th>Supplier order</th><th>Supplier item</th>"
        f"<th>Qty</th><th>Confidence</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _live_replay_chart(live_by_case: dict[str, list[dict]]) -> str:
    """Generate tabbed dual-axis bar charts for all cases.

    One SVG per case_id, with tab buttons to switch between them.
    Only includes 'full' runs (no skip_linkage, no max_messages).
    """
    case_ids = sorted(live_by_case.keys())
    if not case_ids:
        return ""

    # Filter each case to full runs only
    full_by_case = {}
    for cid in case_ids:
        full_runs = [
            r for r in live_by_case[cid]
            if not r.get("skip_linkage") and not r.get("max_messages")
        ]
        if full_runs:
            full_runs.sort(key=lambda r: r.get("run_at", ""))
            full_by_case[cid] = full_runs

    if not full_by_case:
        return ""

    tab_ids = list(full_by_case.keys())

    # Build tab buttons
    tab_buttons = []
    for i, cid in enumerate(tab_ids):
        active_cls = " chart-tab-active" if i == 0 else ""
        tab_buttons.append(
            f"<button class='chart-tab{active_cls}' "
            f"onclick=\"switchChartTab('{_safe_id(cid)}')\">{cid}</button>"
        )

    # Build one SVG per case
    svgs = []
    for i, cid in enumerate(tab_ids):
        display = "block" if i == 0 else "none"
        svgs.append(
            f"<div class='chart-tab-panel' id='chart-panel-{_safe_id(cid)}' "
            f"style='display:{display}'>"
            f"{_build_dual_axis_svg(cid, full_by_case[cid])}"
            f"</div>"
        )

    return (
        f"<div class='chart-container'>"
        f"<div class='chart-tabs'>{''.join(tab_buttons)}</div>"
        f"{''.join(svgs)}"
        f"</div>"
    )


def _safe_id(s: str) -> str:
    return s.replace("-", "_").replace(" ", "_")


def _build_dual_axis_svg(case_id: str, full_runs: list[dict]) -> str:
    """Build a dual-axis bar chart SVG for a single case."""
    w, h = 700, 220
    pad_l, pad_r, pad_t, pad_b = 55, 55, 30, 45

    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    amb_vals = [r.get("ambiguity_flag_count", 0) for r in full_runs]
    err_vals = [r.get("error_count", 0) for r in full_runs]
    max_amb = max(amb_vals) if amb_vals else 1
    max_err = max(err_vals) if err_vals else 1
    max_amb = max(max_amb, 1)
    max_err = max(max_err, 1)

    n = len(full_runs)
    group_w = plot_w / max(n, 1)
    bar_w_amb = min(group_w * 0.35, 30)
    bar_w_err = min(group_w * 0.25, 20)

    def x_center(i):
        return pad_l + (i + 0.5) * group_w

    def y_amb(v):
        return pad_t + plot_h - (v / max_amb) * plot_h

    def y_err(v):
        return pad_t + plot_h - (v / max_err) * plot_h

    baseline = pad_t + plot_h

    # Build ambiguity bars (blue/cyan) and error bars (red, narrower, overlaid)
    bars = []
    for i in range(n):
        cx = x_center(i)
        # Ambiguity bar
        av = amb_vals[i]
        if av > 0:
            aby = y_amb(av)
            abh = baseline - aby
            bars.append(
                f"<rect x='{cx - bar_w_amb / 2:.1f}' y='{aby:.1f}' "
                f"width='{bar_w_amb:.1f}' height='{abh:.1f}' "
                f"fill='#63b3ed' opacity='0.8'/>"
            )
            bars.append(
                f"<text x='{cx:.1f}' y='{aby - 3:.1f}' text-anchor='middle' "
                f"font-size='8' fill='#63b3ed'>{av}</text>"
            )
        # Error bar (narrower, overlaid)
        ev = err_vals[i]
        if ev > 0:
            eby = y_err(ev)
            ebh = baseline - eby
            bars.append(
                f"<rect x='{cx - bar_w_err / 2:.1f}' y='{eby:.1f}' "
                f"width='{bar_w_err:.1f}' height='{ebh:.1f}' "
                f"fill='#fc8181' opacity='0.7'/>"
            )
            bars.append(
                f"<text x='{cx:.1f}' y='{eby - 3:.1f}' text-anchor='middle' "
                f"font-size='8' fill='#fc8181'>{ev}</text>"
            )

    # X axis labels
    step = max(1, n // 8)
    x_labels = []
    for i in range(0, n, step):
        label = _fmt_dt_chart(full_runs[i].get("run_at", ""))
        x_labels.append(
            f"<text x='{x_center(i):.1f}' y='{h - 5}' text-anchor='middle' "
            f"font-size='9' fill='#4a5568'>{label}</text>"
        )

    # Y left axis labels (ambiguity)
    y_ticks = 4
    y_labels_left = []
    for i in range(y_ticks + 1):
        v = int(max_amb * i / y_ticks)
        yp = y_amb(v)
        y_labels_left.append(
            f"<text x='{pad_l - 8}' y='{yp + 3:.1f}' text-anchor='end' "
            f"font-size='9' fill='#63b3ed'>{v}</text>"
            f"<line x1='{pad_l}' y1='{yp:.1f}' x2='{w - pad_r}' y2='{yp:.1f}' "
            f"stroke='#2d3748' stroke-width='0.5'/>"
        )

    # Y right axis labels (errors)
    y_labels_right = []
    for i in range(y_ticks + 1):
        v = int(max_err * i / y_ticks)
        yp = y_err(v)
        y_labels_right.append(
            f"<text x='{w - pad_r + 8}' y='{yp + 3:.1f}' text-anchor='start' "
            f"font-size='9' fill='#fc8181'>{v}</text>"
        )

    return (
        f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}' "
        f"xmlns='http://www.w3.org/2000/svg'>"
        f"<text x='{w // 2}' y='16' text-anchor='middle' font-size='11' "
        f"fill='#a0aec0' font-weight='600'>{case_id}</text>"
        f"{''.join(y_labels_left)}"
        f"{''.join(y_labels_right)}"
        f"{''.join(bars)}"
        f"{''.join(x_labels)}"
        # Axes
        f"<line x1='{pad_l}' y1='{pad_t}' x2='{pad_l}' y2='{baseline}' "
        f"stroke='#63b3ed' stroke-width='1'/>"
        f"<line x1='{w - pad_r}' y1='{pad_t}' x2='{w - pad_r}' y2='{baseline}' "
        f"stroke='#fc8181' stroke-width='1'/>"
        f"<line x1='{pad_l}' y1='{baseline}' x2='{w - pad_r}' y2='{baseline}' "
        f"stroke='#2d3748' stroke-width='1'/>"
        # Axis labels
        f"<text x='{pad_l}' y='{pad_t - 6}' text-anchor='middle' font-size='9' "
        f"fill='#63b3ed'>Ambiguity</text>"
        f"<text x='{w - pad_r}' y='{pad_t - 6}' text-anchor='middle' font-size='9' "
        f"fill='#fc8181'>Errors</text>"
        f"</svg>"
    )


MODEL_ABBREV = {
    "claude-sonnet-4-6": "Sonnet",
    "gemini-2.5-flash": "Flash",
    "claude-haiku-4-5-20251001": "Haiku",
}


def _case_section(case: dict) -> str:
    stats = case["stats"]
    state = case["state"]
    meta = case.get("_meta", {})
    case_id = meta.get("id", case["_case_dir"])
    case_name = meta.get("name", "")
    description = meta.get("description", "")

    n_flags = len(state['ambiguity_flags'])
    n_links = len(state['fulfillment_links'])
    total_nodes = sum(len(nodes) for nodes in state["node_states"].values())

    # Summary line for the collapsible header
    summary_line = (
        f"{case_id} &mdash; "
        f"{stats['messages_total']} msgs, "
        f"{total_nodes} nodes, "
        f"{n_flags} flags"
    )

    # Stats summary — built programmatically from available data
    stat_items = [
        ("Messages", stats["messages_total"], ""),
        ("Routed", stats["messages_routed"], ""),
        ("Agent calls", stats["update_agent_calls"], ""),
        ("Linkage events", stats["linkage_events_processed"], ""),
        ("Agent failures", stats["update_agent_failures"],
         "fail" if stats["update_agent_failures"] else ""),
        ("Dead letters", state["dead_letter_count"],
         "fail" if state["dead_letter_count"] else ""),
        ("Ambiguity flags", n_flags, ""),
        ("Fulfillment links", n_links, ""),
        ("Tasks", len(state["node_states"]), ""),
    ]

    # Pipeline score from pipeline_score.json if available
    score_path = Path(CASES_DIR) / case["_case_dir"] / "pipeline_score.json"
    p_score = None
    if score_path.exists():
        p_score = json.loads(score_path.read_text())
        score_val = p_score.get("overall_score", "")
        score_cls = "pass" if score_val >= 70 else ("partial" if score_val >= 50 else "fail")
        stat_items.append(("Pipeline score", score_val, score_cls))

    # Model usage
    model_usage = case.get("_model_usage", [])
    if model_usage:
        model_str = ", ".join(
            f"{MODEL_ABBREV.get(u['model'], u['model'])} x{u['calls']}"
            for u in model_usage
        )
        stat_items.append(("Models", model_str, ""))

    # Cost
    total_cost = case.get("_total_cost", 0)
    if total_cost:
        stat_items.append(("Cost", f"${total_cost:.2f}", ""))

    # Run metadata
    run_meta = {}
    run_record_path = sorted(
        Path(RUNS_DIR).glob(f"live-{case_id}-*.json"), reverse=True
    )
    if run_record_path:
        run_record = json.loads(run_record_path[0].read_text())
        run_meta = run_record.get("run_metadata", {})
        if run_meta.get("git_commit"):
            stat_items.append(("Git", run_meta["git_commit"], "dim"))
        # live_task_creation always on since entity-first routing (v0.3.0)

    stat_cells = []
    for label, value, cls in stat_items:
        cls_attr = f" class='{cls}'" if cls else ""
        stat_cells.append(
            f"<div class='stat'><span class='stat-val'{cls_attr}>{value}</span>"
            f"<span class='stat-label'>{label}</span></div>"
        )

    # Run notes
    notes_html = ""
    if run_record_path:
        run_notes = json.loads(run_record_path[0].read_text()).get("run_notes", "")
        if run_notes:
            notes_html = f"<div class='run-note'><em>Note:</em> {run_notes}</div>"

    summary = f"<div class='stats-grid'>{''.join(stat_cells)}</div>{notes_html}"

    # Fix 4: Sort tasks — client before supplier
    task_ids = sorted(
        state["node_states"].keys(),
        key=lambda tid: (0 if "client" in tid else 1, tid),
    )

    # Node states per task — Fix 3: wrap each in <details>
    node_sections = []
    for task_id in task_ids:
        nodes = state["node_states"][task_id]
        completed = sum(1 for n in nodes if n["status"] == "completed")
        active = sum(1 for n in nodes if n["status"] in ("active", "in_progress"))
        blocked = sum(1 for n in nodes if n["status"] == "blocked")
        msgs = state.get("message_counts", {}).get(task_id, 0)
        items = state.get("items", {}).get(task_id, [])

        task_summary = (
            f"{task_id} "
            f"<span class='dim'>({completed} completed, {active} active, "
            f"{blocked} blocked, {msgs} messages)</span>"
        )
        node_sections.append(
            f"<details>"
            f"<summary class='task-summary'>{task_summary}</summary>"
            f"{_node_table(task_id, nodes)}"
            f"<h5>Items ({len(items)})</h5>"
            f"{_items_table(task_id, items)}"
            f"</details>"
        )

    amb_table_id = case_id.replace("-", "_").replace(" ", "_")

    # Eval Scores section from pipeline_score.json dimensions
    eval_scores_html = ""
    if p_score:
        dimensions = p_score.get("dimensions", {})
        # Show the DAG eval dimensions if available
        # Prioritised DAG eval dimensions — show these first, then any others
        # Covers both requested names and actual keys found in pipeline_score.json
        dag_dims = ["routing", "extraction", "item_extraction",
                     "node_progression", "node_updates", "ambiguity_quality"]
        dim_rows = []
        for dim_key in dag_dims:
            if dim_key in dimensions:
                dim = dimensions[dim_key]
                d_score = dim.get("score")
                d_notes = dim.get("notes", "")
                if d_score is None:
                    score_str = "<span class='dim'>n/a</span>"
                else:
                    d_cls = "pass" if d_score >= 70 else ("partial" if d_score >= 50 else "fail")
                    score_str = f"<span class='{d_cls}'>{d_score}</span>"
                dim_rows.append(
                    f"<tr><td>{dim_key.replace('_', ' ').title()}</td>"
                    f"<td>{score_str}</td>"
                    f"<td class='dim'>{d_notes}</td></tr>"
                )
        # Also show any other dimensions not in the DAG set
        for dim_key, dim in dimensions.items():
            if dim_key not in dag_dims:
                d_score = dim.get("score")
                d_notes = dim.get("notes", "")
                if d_score is None:
                    score_str = "<span class='dim'>n/a</span>"
                else:
                    d_cls = "pass" if d_score >= 70 else ("partial" if d_score >= 50 else "fail")
                    score_str = f"<span class='{d_cls}'>{d_score}</span>"
                dim_rows.append(
                    f"<tr><td>{dim_key.replace('_', ' ').title()}</td>"
                    f"<td>{score_str}</td>"
                    f"<td class='dim'>{d_notes}</td></tr>"
                )
        if dim_rows:
            eval_scores_html = (
                f"<details>"
                f"<summary class='sub-summary'>Eval Scores</summary>"
                f"<table class='detail'>"
                f"<thead><tr><th>Dimension</th><th>Score</th><th>Notes</th></tr></thead>"
                f"<tbody>{''.join(dim_rows)}</tbody></table>"
                f"</details>"
            )

    # Phoenix trace link from run record
    phoenix_link_html = ""
    if run_record_path:
        rr = json.loads(run_record_path[0].read_text())
        phoenix_ep = rr.get("phoenix_endpoint", "")
        if phoenix_ep:
            phoenix_link_html = (
                f"<p class='dim' style='margin-top:0.5rem'>"
                f"<a href='{phoenix_ep}' target='_blank'>View Phoenix traces</a></p>"
            )

    return (
        f"<div class='case'>"
        f"<details open>"
        f"<summary class='case-summary'>{summary_line}</summary>"
        f"<p class='case-desc'>{case_name}: {description}</p>"
        f"{summary}"
        f"{phoenix_link_html}"
        f"<details open>"
        f"<summary class='sub-summary'>Node States & Items</summary>"
        f"{''.join(node_sections)}"
        f"</details>"
        f"{eval_scores_html}"
        f"<details>"
        f"<summary class='sub-summary'>Ambiguity Flags ({n_flags})</summary>"
        f"{_ambiguity_table(state['ambiguity_flags'], amb_table_id)}"
        f"</details>"
        f"<details>"
        f"<summary class='sub-summary'>Fulfillment Links ({n_links})</summary>"
        f"{_links_table(state['fulfillment_links'])}"
        f"</details>"
        f"</details>"
        f"</div>"
    )


def _abbreviate_model(model: str) -> str:
    return MODEL_ABBREV.get(model, model)


def _fmt_model_usage(usage: list[dict]) -> str:
    if not usage:
        return "\u2014"
    parts = [f"{_abbreviate_model(u['model'])} \u00d7{u['calls']}" for u in usage]
    return ", ".join(parts)


def _fmt_per_group(per_group: dict) -> str:
    """Format per-group routing data with color-coded routed/unrouted counts."""
    if not per_group:
        return "\u2014"
    parts = []
    for group, counts in per_group.items():
        routed = counts.get("routed", 0)
        unrouted = counts.get("unrouted", 0)
        r_span = f'<span class="pg-routed">{routed}</span>'
        total = routed + unrouted
        if unrouted:
            u_span = f'<span class="pg-unrouted">{unrouted}</span>'
            parts.append(f"{group}: {r_span}/{total} ({u_span} unrouted)")
        else:
            parts.append(f"{group}: {r_span}/{total}")
    return ", ".join(parts)


def _fmt_per_group_plain(per_group: dict) -> str:
    """Plain text version for data-value attribute."""
    if not per_group:
        return "\u2014"
    parts = []
    for group, counts in per_group.items():
        routed = counts.get("routed", 0)
        unrouted = counts.get("unrouted", 0)
        parts.append(f"{group}: {routed}/{routed + unrouted}")
    return ", ".join(parts)


def _run_history(runs: list[dict], cases: list[dict]) -> str:
    import html as html_mod
    if not runs:
        return ""

    dry = [r for r in runs if r.get("test_type") == "dry"]
    live = [r for r in runs if r.get("test_type") == "live"]

    # Build case data lookup for model/cost info
    case_data_map = {}
    for c in cases:
        cid = c.get("_meta", {}).get("id", c.get("_case_dir", ""))
        case_data_map[cid] = c

    sections = []

    # --- Dry replay history, grouped by case_id ---
    if dry:
        dry_by_case = defaultdict(list)
        for r in dry:
            dry_by_case[r.get("case_id", "?")].append(r)

        rows = []
        for case_id in sorted(dry_by_case.keys()):
            case_runs = dry_by_case[case_id]
            # Find the latest full run for group row data (runs sorted newest-first)
            full_runs = [r for r in case_runs if not r.get("max_messages")]
            latest = full_runs[0] if full_runs else case_runs[0]
            group_key = f"dry_{case_id}"
            n_runs = len(case_runs)
            rate = f"{latest.get('routing_rate', 0):.0%}"
            per_group_html = _fmt_per_group(latest.get("per_group", {}))
            per_group_plain = _fmt_per_group_plain(latest.get("per_group", {}))

            rows.append(
                f"<tr class='group-row' data-group='{group_key}' onclick='toggleGroup(this)'>"
                f"<td><span class='toggle'>&#9654;</span> {case_id}</td>"
                f"<td data-value='{latest.get('total', 0)}' data-empty=''>{latest.get('total', 0)}</td>"
                f"<td data-value='{rate}' data-empty=''>{rate}</td>"
                f"<td data-value='{per_group_plain}' data-empty=''>{per_group_html}</td></tr>"
            )
            DRY_GP = 10
            for ci, r in enumerate(case_runs):
                gp = ci // DRY_GP
                r_rate = f"{r.get('routing_rate', 0):.0%}"
                r_pg = _fmt_per_group(r.get("per_group", {}))
                rows.append(
                    f"<tr class='group-child' data-group='{group_key}' data-gpage='{gp}'>"
                    f"<td class='dim'>{_fmt_dt(r.get('run_at',''))}</td>"
                    f"<td>{r.get('total',0)}</td>"
                    f"<td>{r_rate}</td>"
                    f"<td>{r_pg}</td></tr>"
                )

            # Add page count to group header if needed
            dry_pages = (len(case_runs) + DRY_GP - 1) // DRY_GP
            if dry_pages > 1:
                for ri in range(len(rows)):
                    if f"data-group='{group_key}'" in rows[ri] and "group-row" in rows[ri]:
                        rows[ri] = rows[ri].replace(
                            f"data-group='{group_key}'",
                            f"data-group='{group_key}' data-gpages='{dry_pages}'",
                        )
                        break

        per_group_tooltip = (
            'title="Layer 2a: Direct group JID → task mapping (confidence 0.90). '
            'Layer 2b: Entity keyword matching via rapidfuzz (confidence 0.75). '
            'Unrouted: no matching group or entity found."'
        )
        sections.append(
            "<h3>Dry Replay History</h3>"
            "<table class='detail'><thead><tr>"
            "<th>Case / Run</th><th>Messages</th><th>Route Rate</th>"
            f"<th {per_group_tooltip}>Per Group</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )

    # --- Live replay history, grouped by case_id, dynamic columns ---
    # Wide columns exempt from angled headers and centered alignment
    _WIDE = {"_tags", "_routed", "_models", "_config"}

    if live:
        live_by_case = defaultdict(list)
        for r in live:
            live_by_case[r.get("case_id", "?")].append(r)

        # Dynamic stat columns: (key, header, formatter)
        # Columns appear only if at least one run has a truthy value.
        _STAT_COLUMNS = [
            ("_tags",                 "Tags",         None),
            ("_routed",               "Routed",       None),
            ("_nodes",                "Nodes",        None),
            ("warmup_messages",       "Warmup",       lambda v: str(v)),
            ("conversations_created", "Convs",        lambda v: str(v)),
            ("entities_discovered",   "Entities",     lambda v: str(v)),
            ("tasks_created_live",    "Tasks (live)", lambda v: str(v)),
            ("fulfillment_link_count","Links",        lambda v: str(v)),
            ("ambiguity_flag_count",  "Ambiguity",    lambda v: str(v)),
            ("dead_letter_count",     "Dead Ltrs",    lambda v: str(v)),
            ("error_count",           "Errors",       lambda v: f"<span class='fail'>{v}</span>" if v else "0"),
            ("tasks_created",         "Tasks",        lambda v: str(v)),
            ("pipeline_score",        "Score",        lambda v: str(v) if v else "\u2014"),
            ("_cost",                 "Cost",         None),
            ("_models",               "Models",       None),
            ("_config",               "Config",       None),
        ]

        def _col_has_data(key):
            if key.startswith("_"):
                return True
            return any(r.get(key) for r in live)

        def _cell(r, key, cdata=None):
            if key == "_tags":
                return _build_tags_html(r)
            if key == "_routed":
                noise = r.get("messages_noise", 0)
                noise_html = f" <span class='dim'>({noise} noise)</span>" if noise else ""
                return f"{r.get('messages_routed',0)}/{r.get('messages_total',0)}{noise_html}"
            if key == "_nodes":
                ns = r.get("node_summary", {})
                return str(sum(v.get("total", 0) for v in ns.values()))
            if key == "_models":
                mu = r.get("model_usage", [])
                if mu:
                    return _fmt_model_usage(mu)
                if cdata:
                    return _fmt_model_usage(cdata.get("_model_usage", []))
                return "\u2014"
            if key == "_cost":
                tc = r.get("total_cost", 0)
                if tc:
                    return f"${tc:.2f}"
                if cdata:
                    c = cdata.get("_total_cost", 0)
                    return f"${c:.2f}" if c else "\u2014"
                return "\u2014"
            if key == "_config":
                meta = r.get("run_metadata", {})
                commit = meta.get("git_commit", "")
                note = r.get("run_notes", "")
                if note:
                    return f"<span title='{html_mod.escape(note, quote=True)}'>{commit} \u2139</span>"
                return commit
            val = r.get(key)
            if key == "tasks_created" and not val:
                val = len(r.get("node_summary", {}))
            col_def = next((c for c in _STAT_COLUMNS if c[0] == key), None)
            if col_def and col_def[2] and val:
                return col_def[2](val)
            return str(val) if val else ""

        active_cols = [(k, h) for k, h, _ in _STAT_COLUMNS if _col_has_data(k)]
        n_cols = len(active_cols) + 1  # +1 for Case/Run column

        rows = []
        for case_id in sorted(live_by_case.keys()):
            case_runs = live_by_case[case_id]
            full_runs = [r for r in case_runs if not r.get("max_messages")]
            latest = full_runs[0] if full_runs else case_runs[0]
            group_key = f"live_{case_id}"
            cdata = case_data_map.get(case_id, {})

            cells = "".join(
                    (f"<td>{_cell(latest, k, cdata)}</td>" if k in _WIDE else f"<td class='td-stat'>{_cell(latest, k, cdata)}</td>")
                    for k, _ in active_cols
                )
            rows.append(
                f"<tr class='group-row' data-group='{group_key}' onclick='toggleGroup(this)'>"
                f"<td><span class='toggle'>&#9654;</span> {case_id}</td>"
                f"{cells}</tr>"
            )
            GP_SIZE = 10
            for child_i, r in enumerate(case_runs):
                gp = child_i // GP_SIZE
                cells = "".join(
                        (f"<td>{_cell(r, k, cdata)}</td>" if k in _WIDE else f"<td class='td-stat'>{_cell(r, k, cdata)}</td>")
                        for k, _ in active_cols
                    )
                rows.append(
                    f"<tr class='group-child' data-group='{group_key}' data-gpage='{gp}'>"
                    f"<td class='dim'>{_fmt_dt(r.get('run_at',''))}</td>"
                    f"{cells}</tr>"
                )
                notes = r.get("run_notes", "")
                if notes:
                    escaped_notes = html_mod.escape(notes, quote=True)
                    prev_row = rows[-1]
                    rows[-1] = prev_row.replace(
                        f"data-group='{group_key}' data-gpage='{gp}'>",
                        f"data-group='{group_key}' data-gpage='{gp}' "
                        f"style='border-bottom:none'>",
                    )
                    rows.append(
                        f"<tr class='group-child note-row' data-group='{group_key}' "
                        f"data-gpage='{gp}' title='Run note for the row above'>"
                        f"<td colspan='{n_cols}' style='padding:0.2rem 0.75rem 0.4rem 2.5rem; "
                        f"border-top:none; border-left:3px solid #4a90d9; "
                        f"background:#0d1017; font-size:0.75rem; color:#718096'>"
                        f"\u21b3 <em>{escaped_notes}</em></td></tr>"
                    )
            # Add page count to group header for JS pagination
            total_gpages = (len(case_runs) + GP_SIZE - 1) // GP_SIZE
            if total_gpages > 1:
                for ri in range(len(rows)):
                    if f"data-group='{group_key}'" in rows[ri] and "group-row" in rows[ri]:
                        rows[ri] = rows[ri].replace(
                            f"data-group='{group_key}'",
                            f"data-group='{group_key}' data-gpages='{total_gpages}'",
                        )
                        break

        header_cells = "".join(
            f"<th>{h}</th>" if k in _WIDE else f"<th class='th-angled'><div>{h}</div></th>"
            for k, h in active_cols
        )
        sections.append(
            "<h3>Live Replay History</h3>"
            f"<table class='detail'><thead><tr>"
            f"<th>Case / Run</th>{header_cells}"
            f"</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            + _live_replay_chart(live_by_case)
        )

    return "".join(sections)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
  background: #0f1117; color: #e2e8f0;
  padding: 2rem; max-width: 1200px; margin: 0 auto;
}
a { color: #4a90d9; text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 1.4rem; font-weight: 600; color: #f7fafc; margin-bottom: 0.25rem; }
h2 { font-size: 1.1rem; font-weight: 600; color: #f7fafc; margin: 2.5rem 0 0.5rem;
     border-bottom: 1px solid #2d3748; padding-bottom: 0.4rem; }
h3 { font-size: 0.9rem; font-weight: 600; color: #a0aec0; margin: 1.5rem 0 0.5rem;
     text-transform: uppercase; letter-spacing: 0.06em; }
h4 { font-size: 0.88rem; color: #e2e8f0; margin: 1.25rem 0 0.4rem; }
h5 { font-size: 0.8rem; color: #4a5568; margin: 0.75rem 0 0.3rem; }
.meta { font-size: 0.78rem; color: #4a5568; margin-bottom: 2rem; }
.case-desc { font-size: 0.82rem; color: #4a5568; margin-bottom: 1rem; }
.dim { color: #4a5568; font-size: 0.8rem; }
.run-note { background: #1a1f2e; color: #a0aec0; font-size: 0.8rem; padding: 0.5rem 1rem; border-left: 3px solid #4a5568; }

.stats-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 0.75rem; margin: 1rem 0;
}
.stat {
  background: #1a1f2e; border: 1px solid #2d3748; border-radius: 6px;
  padding: 0.75rem; text-align: center;
}
.stat-val { display: block; font-size: 1.3rem; font-weight: 600; color: #f7fafc; }
.stat-label { display: block; font-size: 0.7rem; color: #4a5568; margin-top: 0.2rem;
              text-transform: uppercase; letter-spacing: 0.05em; }

table.detail { width: 100%; border-collapse: collapse; font-size: 0.8rem; margin-bottom: 1rem; }
table.detail th { text-align: left; padding: 0.35rem 0.6rem; color: #4a5568;
     font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
     border-bottom: 1px solid #2d3748; }
table.detail td { padding: 0.35rem 0.6rem; border-bottom: 1px solid #1a2030;
     vertical-align: top; }
table.detail tr:hover td { background: #1a2030; }
td.desc { max-width: 400px; font-size: 0.78rem; line-height: 1.4; }

.pg-routed  { color: #68d391; }
.pg-unrouted { color: #718096; }
.pass     { color: #48bb78; }
.active   { color: #4a90d9; }
.fail     { color: #fc8181; }
.partial  { color: #ed8936; }
.pending  { color: #4a5568; }
.skipped  { color: #2d3748; }

.case { margin-bottom: 3rem; }
nav { margin-bottom: 2rem; font-size: 0.82rem; }

/* Group row styles for collapsible history tables */
.group-row { cursor: pointer; background: #1a2030; border-top: 6px solid #0f1117; }
.group-row:hover td { background: #242c3a; }
.group-row td { font-weight: 600; color: #a0aec0; }
.group-row .toggle { display: inline-block; width: 1em; font-size: 0.75rem; transition: transform 0.2s; }
.group-row.open .toggle { transform: rotate(90deg); }
/* tags cell cleared via data-value/data-empty swap in toggleGroup */
.group-child { display: none; }
.group-child.visible { display: table-row; }
/* Angled stat column headers for compact width */
th.th-angled { white-space: nowrap; vertical-align: bottom; height: 5rem; width: 2.2rem;
               padding: 0 0.3rem 0.4rem; }
th.th-angled > div { transform: rotate(-55deg); transform-origin: bottom left;
                     width: 1.5em; display: block; margin-left: 0.7rem; }
td.td-stat { text-align: center; padding: 0.35rem 0.3rem; font-variant-numeric: tabular-nums; }

/* Collapsible case sections */
details { margin-bottom: 0.5rem; }
summary.case-summary {
  font-size: 1.1rem; font-weight: 600; color: #f7fafc;
  cursor: pointer; padding: 0.5rem 0;
  border-bottom: 1px solid #2d3748; margin-bottom: 0.5rem;
}
summary.case-summary:hover { color: #4a90d9; }
summary.sub-summary {
  font-size: 0.9rem; font-weight: 600; color: #a0aec0;
  cursor: pointer; padding: 0.3rem 0; margin: 1rem 0 0.5rem;
  text-transform: uppercase; letter-spacing: 0.06em;
}
summary.sub-summary:hover { color: #e2e8f0; }
summary.task-summary {
  font-size: 0.88rem; color: #e2e8f0;
  cursor: pointer; padding: 0.3rem 0; margin: 0.5rem 0 0.3rem;
}
summary.task-summary:hover { color: #4a90d9; }

/* Chart container and tabs */
.chart-container { margin: 1rem 0; }
.chart-tabs { display: flex; gap: 0; margin-bottom: 0; }
.chart-tab {
  background: #1a2030; border: 1px solid #2d3748; color: #4a5568;
  padding: 0.4rem 1rem; cursor: pointer; font-size: 0.8rem;
  border-bottom: none; border-radius: 4px 4px 0 0;
}
.chart-tab:hover { color: #e2e8f0; }
.chart-tab-active { background: #0f1117; color: #63b3ed; border-color: #63b3ed; border-bottom: 1px solid #0f1117; }
.chart-tab-panel { border: 1px solid #2d3748; border-radius: 0 4px 4px 4px; padding: 0.5rem; }

/* Pagination */
.pagination {
  display: flex; align-items: center; gap: 0.75rem;
  margin: 0.5rem 0 1.5rem; font-size: 0.8rem;
}
.gpag-cell .pagination { margin: 0; gap: 0.5rem; }
.pagination button {
  background: #1a2030; border: 1px solid #2d3748; color: #e2e8f0;
  padding: 0.3rem 0.75rem; border-radius: 4px; cursor: pointer;
  font-size: 0.78rem;
}
.pagination button:hover { background: #242c3a; }
.page-indicator { color: #4a5568; }
.tag { display: inline-block; padding: 0.1rem 0.35rem; border-radius: 3px;
       font-size: 0.68rem; font-weight: 600; margin: 0 1px; cursor: help;
       letter-spacing: 0.02em; vertical-align: middle; }
.tag-mode { background: #2d3748; color: #63b3ed; }
.tag-legacy { background: #2d3748; color: #a0aec0; }
.tag-traced { background: #2f855a; color: #c6f6d5; }
.tag-agent { background: #2a4365; color: #90cdf4; }
.tag-batch { background: #553c9a; color: #d6bcfa; }
.tag-dim { background: #1a2030; color: #718096; border: 1px solid #2d3748; }
.pagination { display: flex; align-items: center; gap: 0.5rem;
              justify-content: center; padding: 0.4rem 0; font-size: 0.78rem; }
.pagination button { background: #2d3748; color: #e2e8f0; border: 1px solid #4a5568;
                     border-radius: 3px; padding: 0.2rem 0.6rem; cursor: pointer;
                     font-size: 0.75rem; }
.pagination button:hover { background: #4a5568; }
.pagination span { color: #718096; }
"""


JS = """
var gpageState = {};

function toggleGroup(row) {
    var group = row.getAttribute('data-group');
    row.classList.toggle('open');
    var isOpen = row.classList.contains('open');
    var totalPages = parseInt(row.getAttribute('data-gpages') || '0');
    var hasPagination = totalPages > 1;
    var children = document.querySelectorAll('tr.group-child[data-group="' + group + '"]');

    if (isOpen) {
        var curPage = gpageState[group] || 0;
        for (var i = 0; i < children.length; i++) {
            var gp = children[i].getAttribute('data-gpage');
            if (hasPagination && gp !== null) {
                // Only show rows on current page
                if (parseInt(gp) === curPage) {
                    children[i].classList.add('visible');
                }
            } else {
                // No pagination — show all
                children[i].classList.add('visible');
            }
        }
        // Inject pagination controls as a spanning cell in header row
        if (hasPagination) {
            if (!row.querySelector('.gpag-cell')) {
                // Hide all cells except first, insert spanning pagination cell
                var allTds = row.querySelectorAll('td');
                var colsToSpan = allTds.length - 1;
                for (var c = 1; c < allTds.length; c++) {
                    allTds[c].style.display = 'none';
                    allTds[c].classList.add('gpag-hidden');
                }
                var pagTd = document.createElement('td');
                pagTd.className = 'gpag-cell';
                pagTd.setAttribute('colspan', colsToSpan);
                pagTd.style.cssText = 'text-align:center';
                pagTd.innerHTML = '<div class="pagination" style="justify-content:center">' +
                    '<button id="gprev-' + group + '">&#8249; Prev</button> ' +
                    '<span id="gpage-ind-' + group + '">Page ' + (curPage+1) + ' / ' + totalPages + '</span> ' +
                    '<button id="gnext-' + group + '">Next &#8250;</button></div>';
                row.appendChild(pagTd);
                // Attach click handlers (stopPropagation to prevent collapse)
                document.getElementById('gprev-' + group).onclick = function(e) {
                    e.stopPropagation(); paginateGroup(group, -1);
                };
                document.getElementById('gnext-' + group).onclick = function(e) {
                    e.stopPropagation(); paginateGroup(group, 1);
                };
            }
        }
    } else {
        // Collapse — hide all children, remove pagination cell, restore hidden cells
        for (var i = 0; i < children.length; i++) {
            children[i].classList.remove('visible');
        }
        var pagCell = row.querySelector('.gpag-cell');
        if (pagCell) pagCell.remove();
        var hiddenCells = row.querySelectorAll('.gpag-hidden');
        for (var h = 0; h < hiddenCells.length; h++) {
            hiddenCells[h].style.display = '';
            hiddenCells[h].classList.remove('gpag-hidden');
        }
    }

    // Swap data cells between showing data and empty
    var tds = row.querySelectorAll('td[data-value]');
    for (var j = 0; j < tds.length; j++) {
        if (!tds[j].hasAttribute('data-html')) {
            tds[j].setAttribute('data-html', tds[j].innerHTML);
        }
        if (isOpen) {
            tds[j].innerHTML = tds[j].getAttribute('data-empty');
        } else {
            tds[j].innerHTML = tds[j].getAttribute('data-html');
        }
    }
}

function paginateGroup(group, dir) {
    var headerRow = document.querySelector('tr.group-row[data-group="' + group + '"]');
    var totalPages = parseInt(headerRow.getAttribute('data-gpages') || '1');
    var curPage = gpageState[group] || 0;
    var newPage = curPage + dir;
    if (newPage < 0 || newPage >= totalPages) return;
    gpageState[group] = newPage;

    var children = document.querySelectorAll('tr.group-child[data-group="' + group + '"]');
    for (var i = 0; i < children.length; i++) {
        var gp = children[i].getAttribute('data-gpage');
        if (gp !== null) {
            if (parseInt(gp) === newPage) {
                children[i].classList.add('visible');
            } else {
                children[i].classList.remove('visible');
            }
        }
    }
    var ind = document.getElementById('gpage-ind-' + group);
    if (ind) ind.textContent = 'Page ' + (newPage+1) + ' / ' + totalPages;
}

var pageState = {};
function paginate(tid, dir) {
    if (!pageState[tid]) pageState[tid] = 0;
    var rows = document.querySelectorAll('tr.prow-' + tid);
    var maxPage = 0;
    for (var i = 0; i < rows.length; i++) {
        var p = parseInt(rows[i].getAttribute('data-page'));
        if (p > maxPage) maxPage = p;
    }
    var newPage = pageState[tid] + dir;
    if (newPage < 0 || newPage > maxPage) return;
    pageState[tid] = newPage;
    for (var i = 0; i < rows.length; i++) {
        var p = parseInt(rows[i].getAttribute('data-page'));
        rows[i].style.display = (p === newPage) ? '' : 'none';
    }
    document.getElementById('page-ind-' + tid).textContent =
        'Page ' + (newPage + 1) + ' of ' + (maxPage + 1);
}

function switchChartTab(caseId) {
    var panels = document.querySelectorAll('.chart-tab-panel');
    for (var i = 0; i < panels.length; i++) {
        panels[i].style.display = 'none';
    }
    var tabs = document.querySelectorAll('.chart-tab');
    for (var i = 0; i < tabs.length; i++) {
        tabs[i].classList.remove('chart-tab-active');
    }
    document.getElementById('chart-panel-' + caseId).style.display = 'block';
    // Find the clicked tab and activate it
    for (var i = 0; i < tabs.length; i++) {
        if (tabs[i].getAttribute('onclick').indexOf(caseId) !== -1) {
            tabs[i].classList.add('chart-tab-active');
        }
    }
}
"""


def generate() -> str:
    global _table_counter
    _table_counter = 0

    cases = _load_case_results()
    runs = _load_runs()
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    case_sections = "".join(_case_section(c) for c in cases)
    if not case_sections:
        case_sections = "<p class='dim'>No replay results found. Run a live replay first.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Mantri — Integration Tests</title>
  <style>{CSS}</style>
</head>
<body>
  <h1>System Test Results</h1>
  <p class="meta">
    Generated {generated} &nbsp;&middot;&nbsp;
    <a href="/developer/">Developer Portal</a> &nbsp;&middot;&nbsp;
    <a href="/developer/runs/">Test Results</a>
  </p>

  <nav>
    <a href="#history">Run History</a> &nbsp;&middot;&nbsp;
    <a href="#results">Latest Results</a>
  </nav>

  <div id="history">
  {_run_history(runs, cases)}
  </div>

  <details style="margin:1rem 0">
  <summary class="dim" style="cursor:pointer; font-size:0.78rem">Tag Legend</summary>
  <table class="detail" style="margin-top:0.3rem; font-size:0.78rem">
  <tr><td><span class="tag tag-mode">E</span></td><td>Entity-first routing (direct group → entity mapping)</td></tr>
  <tr><td><span class="tag tag-mode">I</span></td><td>Conversation routing for shared/internal groups</td></tr>
  <tr><td><span class="tag tag-traced">T</span></td><td>Phoenix OTEL tracing enabled</td></tr>
  <tr><td><span class="tag tag-agent">AO</span></td><td>Update agent — order processing</td></tr>
  <tr><td><span class="tag tag-agent">AL</span></td><td>Linkage agent — fulfillment matching</td></tr>
  <tr><td><span class="tag tag-dim">N</span></td><td>Partial replay — first N messages only</td></tr>
  <tr><td><span class="tag tag-dim">🚧</span></td><td>Dev test — cached LLM responses, pre-seeded tasks</td></tr>
  </table>
  </details>

  <h2 id="results">Latest Replay Results</h2>
  {case_sections}

  <script>{JS}</script>
</body>
</html>"""


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    html = generate()
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Published: {OUT_PATH}")


if __name__ == "__main__":
    main()

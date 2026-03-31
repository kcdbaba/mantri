#!/usr/bin/env python3
"""
publish_integration.py

Reads replay_result.json from each integration test case directory and generates
a rich detail page at static/developer/integration/index.html.

Usage:
    python scripts/publish_integration.py
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CASES_DIR = Path("tests/integration_tests")
RUNS_DIR = Path("tests/runs/integration")
OUT_PATH = Path("static/developer/integration/index.html")

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


def _node_table(task_id: str, nodes: list[dict]) -> str:
    rows = []
    for n in nodes:
        name = n["name"]
        status = n["status"]
        cls = STATUS_CLASSES.get(status, "")
        conf = f"{n['confidence']:.2f}" if n["confidence"] is not None else "\u2014"
        by = n.get("updated_by", "\u2014")
        rows.append(
            f"<tr><td>{name}</td>"
            f"<td class='{cls}'>{status}</td>"
            f"<td>{conf}</td>"
            f"<td class='dim'>{by}</td></tr>"
        )
    return (
        f"<table class='detail'>"
        f"<thead><tr><th>Node</th><th>Status</th><th>Confidence</th><th>Updated by</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _items_table(task_id: str, items: list[dict]) -> str:
    if not items:
        return "<p class='dim'>No items extracted</p>"
    rows = []
    for item in items:
        qty = item["quantity"] if item["quantity"] is not None else "<span class='fail'>null</span>"
        specs = (item.get("specs") or "\u2014")[:120]
        unit = item.get("unit", "\u2014")
        rows.append(
            f"<tr><td>{item['description']}</td>"
            f"<td>{qty}</td>"
            f"<td>{unit}</td>"
            f"<td class='dim'>{specs}</td></tr>"
        )
    return (
        f"<table class='detail'>"
        f"<thead><tr><th>Description</th><th>Qty</th><th>Unit</th><th>Specs</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _ambiguity_table(flags: list[dict], table_id: str = "") -> str:
    global _table_counter
    if not flags:
        return "<p class='dim'>No ambiguity flags raised</p>"

    _table_counter += 1
    tid = table_id or f"amb_{_table_counter}"
    page_size = 10
    needs_pagination = len(flags) > page_size

    rows = []
    for idx, f in enumerate(flags):
        sev_cls = SEVERITY_CLASSES.get(f["severity"], "")
        node = f.get("node_id") or "\u2014"
        desc = f["description"][:200]
        # Add data-page attribute for pagination
        page = idx // page_size
        style = "" if page == 0 else " style='display:none'"
        rows.append(
            f"<tr class='prow-{tid}' data-page='{page}'{style}>"
            f"<td>{f['task_id']}</td>"
            f"<td class='{sev_cls}'>{f['severity']}</td>"
            f"<td>{f['category']}</td>"
            f"<td>{node}</td>"
            f"<td class='desc'>{desc}</td></tr>"
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

    return (
        f"<table class='detail' id='tbl-{tid}'>"
        f"<thead><tr><th>Task</th><th>Severity</th><th>Category</th>"
        f"<th>Node</th><th>Description</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        f"{pagination_html}"
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


def _live_replay_chart(case_id: str, runs: list[dict]) -> str:
    """Generate an inline SVG line chart for a case_id's live replay runs.

    Only includes 'full' mode runs (no skip_linkage, no max_messages).
    X axis: run timestamp, Y axis: ambiguity_flag_count (line) + error_count (bars).
    """
    # Filter to full runs only
    full_runs = [
        r for r in runs
        if not r.get("skip_linkage") and not r.get("max_messages")
    ]
    if len(full_runs) < 2:
        return ""  # Need at least 2 points for a line

    # Sort by timestamp
    full_runs.sort(key=lambda r: r.get("run_at", ""))

    w, h = 700, 200
    pad_l, pad_r, pad_t, pad_b = 50, 20, 30, 40

    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    amb_vals = [r.get("ambiguity_flag_count", 0) for r in full_runs]
    err_vals = [r.get("error_count", 0) for r in full_runs]
    max_y = max(max(amb_vals), max(err_vals), 1)

    n = len(full_runs)
    bar_w = max(6, plot_w // (n * 3))

    def x_pos(i):
        if n == 1:
            return pad_l + plot_w / 2
        return pad_l + i * plot_w / (n - 1)

    def y_pos(v):
        return pad_t + plot_h - (v / max_y) * plot_h

    # Build line path
    points = " ".join(f"{x_pos(i):.1f},{y_pos(v):.1f}" for i, v in enumerate(amb_vals))

    # Build error bars
    bars = []
    for i, v in enumerate(err_vals):
        if v > 0:
            bx = x_pos(i) - bar_w / 2
            by = y_pos(v)
            bh = y_pos(0) - by
            bars.append(
                f"<rect x='{bx:.1f}' y='{by:.1f}' width='{bar_w}' height='{bh:.1f}' "
                f"fill='#fc8181' opacity='0.4'/>"
            )

    # X axis labels (every label or skip if too many)
    step = max(1, n // 6)
    x_labels = []
    for i in range(0, n, step):
        label = _fmt_dt_short(full_runs[i].get("run_at", ""))
        x_labels.append(
            f"<text x='{x_pos(i):.1f}' y='{h - 5}' text-anchor='middle' "
            f"font-size='9' fill='#4a5568'>{label}</text>"
        )

    # Y axis labels
    y_ticks = 4
    y_labels = []
    for i in range(y_ticks + 1):
        v = int(max_y * i / y_ticks)
        yp = y_pos(v)
        y_labels.append(
            f"<text x='{pad_l - 8}' y='{yp + 3:.1f}' text-anchor='end' "
            f"font-size='9' fill='#4a5568'>{v}</text>"
            f"<line x1='{pad_l}' y1='{yp:.1f}' x2='{w - pad_r}' y2='{yp:.1f}' "
            f"stroke='#2d3748' stroke-width='0.5'/>"
        )

    return (
        f"<div class='chart-container'>"
        f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}' "
        f"xmlns='http://www.w3.org/2000/svg'>"
        f"<text x='{w // 2}' y='16' text-anchor='middle' font-size='11' "
        f"fill='#a0aec0' font-weight='600'>{case_id} \u2014 Ambiguity Flags Over Time</text>"
        f"{''.join(y_labels)}"
        f"{''.join(bars)}"
        f"<polyline points='{points}' fill='none' stroke='#4a90d9' stroke-width='2'/>"
        + "".join(
            f"<circle cx='{x_pos(i):.1f}' cy='{y_pos(v):.1f}' r='3' fill='#4a90d9'/>"
            for i, v in enumerate(amb_vals)
        )
        + f"{''.join(x_labels)}"
        f"<line x1='{pad_l}' y1='{pad_t}' x2='{pad_l}' y2='{h - pad_b}' "
        f"stroke='#2d3748' stroke-width='1'/>"
        f"<line x1='{pad_l}' y1='{h - pad_b}' x2='{w - pad_r}' y2='{h - pad_b}' "
        f"stroke='#2d3748' stroke-width='1'/>"
        f"<text x='{w - pad_r}' y='{h - pad_b - 5}' text-anchor='end' font-size='9' "
        f"fill='#4a90d9'>ambiguity flags</text>"
        f"<text x='{w - pad_r}' y='{h - pad_b - 16}' text-anchor='end' font-size='9' "
        f"fill='#fc8181' opacity='0.7'>error count</text>"
        f"</svg></div>"
    )


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

    # Stats summary
    summary = (
        f"<div class='stats-grid'>"
        f"<div class='stat'><span class='stat-val'>{stats['messages_total']}</span><span class='stat-label'>Messages</span></div>"
        f"<div class='stat'><span class='stat-val'>{stats['messages_routed']}</span><span class='stat-label'>Routed</span></div>"
        f"<div class='stat'><span class='stat-val'>{stats['update_agent_calls']}</span><span class='stat-label'>Update agent calls</span></div>"
        f"<div class='stat'><span class='stat-val'>{stats['linkage_events_processed']}</span><span class='stat-label'>Linkage events</span></div>"
        f"<div class='stat'><span class='stat-val {('fail' if stats['update_agent_failures'] else '')}'>"
        f"{stats['update_agent_failures']}</span><span class='stat-label'>Agent failures</span></div>"
        f"<div class='stat'><span class='stat-val'>{state['dead_letter_count']}</span><span class='stat-label'>Dead letters</span></div>"
        f"<div class='stat'><span class='stat-val'>{n_flags}</span><span class='stat-label'>Ambiguity flags</span></div>"
        f"<div class='stat'><span class='stat-val'>{n_links}</span><span class='stat-label'>Fulfillment links</span></div>"
        f"</div>"
    )

    # Node states per task
    node_sections = []
    for task_id, nodes in state["node_states"].items():
        completed = sum(1 for n in nodes if n["status"] == "completed")
        active = sum(1 for n in nodes if n["status"] in ("active", "in_progress"))
        blocked = sum(1 for n in nodes if n["status"] == "blocked")
        msgs = state.get("message_counts", {}).get(task_id, 0)
        items = state.get("items", {}).get(task_id, [])

        task_header = (
            f"<h4>{task_id} "
            f"<span class='dim'>({completed} completed, {active} active, "
            f"{blocked} blocked, {msgs} messages)</span></h4>"
        )
        node_sections.append(
            f"{task_header}"
            f"{_node_table(task_id, nodes)}"
            f"<h5>Items ({len(items)})</h5>"
            f"{_items_table(task_id, items)}"
        )

    amb_table_id = case_id.replace("-", "_").replace(" ", "_")

    return (
        f"<div class='case'>"
        f"<details open>"
        f"<summary class='case-summary'>{summary_line}</summary>"
        f"<p class='case-desc'>{case_name}: {description}</p>"
        f"{summary}"
        f"<details open>"
        f"<summary class='sub-summary'>Node States & Items</summary>"
        f"{''.join(node_sections)}"
        f"</details>"
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


def _run_history(runs: list[dict]) -> str:
    if not runs:
        return ""

    dry = [r for r in runs if r.get("test_type") == "dry"]
    live = [r for r in runs if r.get("test_type") == "live"]

    sections = []

    # --- Dry replay history, grouped by case_id ---
    if dry:
        dry_by_case = defaultdict(list)
        for r in dry:
            dry_by_case[r.get("case_id", "?")].append(r)

        rows = []
        for case_id in sorted(dry_by_case.keys()):
            case_runs = dry_by_case[case_id]
            group_key = f"dry_{case_id}"
            rows.append(
                f"<tr class='group-row' data-group='{group_key}' onclick='toggleGroup(this)'>"
                f"<td><span class='toggle'>&#9654;</span> {case_id}</td>"
                f"<td colspan='3'>{len(case_runs)} run(s)</td></tr>"
            )
            for r in case_runs:
                rows.append(
                    f"<tr class='group-child' data-group='{group_key}'>"
                    f"<td class='dim'>{_fmt_dt(r.get('run_at',''))}</td>"
                    f"<td>{case_id}</td>"
                    f"<td>{r.get('total',0)}</td>"
                    f"<td>{r.get('routed',0)}/{r.get('total',0)}</td></tr>"
                )
        sections.append(
            "<h3>Dry Replay History</h3>"
            "<table class='detail'><thead><tr>"
            "<th>Run</th><th>Case</th><th>Messages</th><th>Routed</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )

    # --- Live replay history, grouped by case_id, with charts ---
    if live:
        live_by_case = defaultdict(list)
        for r in live:
            live_by_case[r.get("case_id", "?")].append(r)

        # Generate charts above the table
        charts = []
        for case_id in sorted(live_by_case.keys()):
            chart = _live_replay_chart(case_id, live_by_case[case_id])
            if chart:
                charts.append(chart)

        rows = []
        for case_id in sorted(live_by_case.keys()):
            case_runs = live_by_case[case_id]
            group_key = f"live_{case_id}"
            rows.append(
                f"<tr class='group-row' data-group='{group_key}' onclick='toggleGroup(this)'>"
                f"<td><span class='toggle'>&#9654;</span> {case_id}</td>"
                f"<td colspan='5'>{len(case_runs)} run(s)</td></tr>"
            )
            for r in case_runs:
                mode = "update only" if r.get("skip_linkage") else "full"
                if r.get("max_messages"):
                    mode += f" (first {r['max_messages']})"
                rows.append(
                    f"<tr class='group-child' data-group='{group_key}'>"
                    f"<td class='dim'>{_fmt_dt(r.get('run_at',''))}</td>"
                    f"<td>{case_id}</td>"
                    f"<td>{mode}</td>"
                    f"<td>{r.get('messages_routed',0)}/{r.get('messages_total',0)}</td>"
                    f"<td>{r.get('error_count',0)}</td>"
                    f"<td>{r.get('ambiguity_flag_count',0)}</td></tr>"
                )
        sections.append(
            "<h3>Live Replay History</h3>"
            + "".join(charts)
            + "<table class='detail'><thead><tr>"
            "<th>Run</th><th>Case</th><th>Mode</th><th>Routed</th>"
            "<th>Errors</th><th>Ambiguity</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
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

.pass     { color: #48bb78; }
.active   { color: #4a90d9; }
.fail     { color: #fc8181; }
.partial  { color: #ed8936; }
.pending  { color: #4a5568; }
.skipped  { color: #2d3748; }

.case { margin-bottom: 3rem; }
nav { margin-bottom: 2rem; font-size: 0.82rem; }

/* Group row styles for collapsible history tables */
.group-row { cursor: pointer; background: #1a2030; }
.group-row:hover td { background: #242c3a; }
.group-row td { font-weight: 600; color: #a0aec0; }
.group-row .toggle { display: inline-block; width: 1em; font-size: 0.75rem; transition: transform 0.2s; }
.group-row.open .toggle { transform: rotate(90deg); }
.group-child { display: none; }
.group-child.visible { display: table-row; }

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

/* Chart container */
.chart-container { margin: 1rem 0; }

/* Pagination */
.pagination {
  display: flex; align-items: center; gap: 0.75rem;
  margin: 0.5rem 0 1.5rem; font-size: 0.8rem;
}
.pagination button {
  background: #1a2030; border: 1px solid #2d3748; color: #e2e8f0;
  padding: 0.3rem 0.75rem; border-radius: 4px; cursor: pointer;
  font-size: 0.78rem;
}
.pagination button:hover { background: #242c3a; }
.page-indicator { color: #4a5568; }
"""


JS = """
function toggleGroup(row) {
    var group = row.getAttribute('data-group');
    row.classList.toggle('open');
    var children = document.querySelectorAll('tr.group-child[data-group="' + group + '"]');
    for (var i = 0; i < children.length; i++) {
        children[i].classList.toggle('visible');
    }
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
  <h1>Integration Test Results</h1>
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
  {_run_history(runs)}
  </div>

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

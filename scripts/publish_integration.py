#!/usr/bin/env python3
"""
publish_integration.py

Reads replay_result.json from each integration test case directory and generates
a rich detail page at static/developer/integration/index.html.

Usage:
    python scripts/publish_integration.py
"""

import json
from datetime import datetime
from pathlib import Path

CASES_DIR = Path("tests/integration_tests")
RUNS_DIR = Path("tests/runs/integration")
OUT_PATH = Path("static/developer/integration/index.html")

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
        conf = f"{n['confidence']:.2f}" if n["confidence"] is not None else "—"
        by = n.get("updated_by", "—")
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
        specs = (item.get("specs") or "—")[:120]
        rows.append(
            f"<tr><td>{item['description']}</td>"
            f"<td>{qty}</td>"
            f"<td>{item.get('unit', '—')}</td>"
            f"<td class='dim'>{specs}</td></tr>"
        )
    return (
        f"<table class='detail'>"
        f"<thead><tr><th>Description</th><th>Qty</th><th>Unit</th><th>Specs</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _ambiguity_table(flags: list[dict]) -> str:
    if not flags:
        return "<p class='dim'>No ambiguity flags raised</p>"
    rows = []
    for f in flags:
        sev_cls = SEVERITY_CLASSES.get(f["severity"], "")
        node = f.get("node_id") or "—"
        desc = f["description"][:200]
        rows.append(
            f"<tr><td>{f['task_id']}</td>"
            f"<td class='{sev_cls}'>{f['severity']}</td>"
            f"<td>{f['category']}</td>"
            f"<td>{node}</td>"
            f"<td class='desc'>{desc}</td></tr>"
        )
    return (
        f"<table class='detail'>"
        f"<thead><tr><th>Task</th><th>Severity</th><th>Category</th>"
        f"<th>Node</th><th>Description</th></tr></thead>"
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


def _case_section(case: dict) -> str:
    stats = case["stats"]
    state = case["state"]
    meta = case.get("_meta", {})
    case_id = meta.get("id", case["_case_dir"])
    case_name = meta.get("name", "")
    description = meta.get("description", "")

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
        f"<div class='stat'><span class='stat-val'>{len(state['ambiguity_flags'])}</span><span class='stat-label'>Ambiguity flags</span></div>"
        f"<div class='stat'><span class='stat-val'>{len(state['fulfillment_links'])}</span><span class='stat-label'>Fulfillment links</span></div>"
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

    return (
        f"<div class='case'>"
        f"<h2>{case_id}</h2>"
        f"<p class='case-desc'>{case_name}: {description}</p>"
        f"{summary}"
        f"<h3>Node States & Items</h3>"
        f"{''.join(node_sections)}"
        f"<h3>Ambiguity Flags ({len(state['ambiguity_flags'])})</h3>"
        f"{_ambiguity_table(state['ambiguity_flags'])}"
        f"<h3>Fulfillment Links ({len(state['fulfillment_links'])})</h3>"
        f"{_links_table(state['fulfillment_links'])}"
        f"</div>"
    )


def _run_history(runs: list[dict]) -> str:
    if not runs:
        return ""

    dry = [r for r in runs if r.get("test_type") == "dry"]
    live = [r for r in runs if r.get("test_type") == "live"]

    sections = []
    if dry:
        rows = []
        for r in dry:
            rows.append(
                f"<tr><td>{_fmt_dt(r.get('run_at',''))}</td>"
                f"<td>{r.get('case_id','?')}</td>"
                f"<td>{r.get('total',0)}</td>"
                f"<td>{r.get('routed',0)}/{r.get('total',0)}</td></tr>"
            )
        sections.append(
            "<h3>Dry Replay History</h3>"
            "<table class='detail'><thead><tr>"
            "<th>Run</th><th>Case</th><th>Messages</th><th>Routed</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )

    if live:
        rows = []
        for r in live:
            mode = "update only" if r.get("skip_linkage") else "full"
            if r.get("max_messages"):
                mode += f" (first {r['max_messages']})"
            rows.append(
                f"<tr><td>{_fmt_dt(r.get('run_at',''))}</td>"
                f"<td>{r.get('case_id','?')}</td>"
                f"<td>{mode}</td>"
                f"<td>{r.get('messages_routed',0)}/{r.get('messages_total',0)}</td>"
                f"<td>{r.get('error_count',0)}</td>"
                f"<td>{r.get('ambiguity_flag_count',0)}</td></tr>"
            )
        sections.append(
            "<h3>Live Replay History</h3>"
            "<table class='detail'><thead><tr>"
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
"""


def generate() -> str:
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

</body>
</html>"""


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    html = generate()
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Published: {OUT_PATH}")


if __name__ == "__main__":
    main()

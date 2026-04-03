#!/usr/bin/env python3
"""
publish_runs.py

Reads tests/runs/incremental/*.json and tests/runs/eval/*.json, generates a self-contained
static HTML page at static/developer/runs/index.html.

Run after any test suite to update the published results.
Also called automatically by run_incremental_test.py after full runs.

Usage:
    python scripts/publish_runs.py
"""

import json
from datetime import datetime
from pathlib import Path

RUNS_INC   = Path("tests/runs/incremental")
RUNS_EVAL  = Path("tests/runs/eval")
RUNS_UNIT  = Path("tests/runs/unit")
RUNS_INT   = Path("tests/runs/integration")
COVERAGE   = Path("tests/runs/coverage.json")
OUT_PATH   = Path("static/developer/runs/index.html")

# ── Data loading ───────────────────────────────────────────────────────────────

def _load_runs(directory: Path) -> list[dict]:
    if not directory.exists():
        return []
    runs = []
    for f in sorted(directory.glob("*.json"), reverse=True):
        if f.name == ".gitkeep":
            continue
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


# ── HTML helpers ───────────────────────────────────────────────────────────────

def _verdict_cell(verdict: str) -> str:
    if verdict == "PASS":
        return '<td class="pass">PASS</td>'
    if verdict == "PARTIAL":
        return '<td class="partial">PART</td>'
    if verdict == "FAIL":
        return '<td class="fail">FAIL</td>'
    return f'<td class="err">{verdict}</td>'


def _pct_bar(passed: int, total: int) -> str:
    if not total:
        return "—"
    pct = int(passed / total * 100)
    colour = "#48bb78" if pct == 100 else "#ed8936" if pct >= 80 else "#fc8181"
    return (
        f'<span class="bar-wrap">'
        f'<span class="bar" style="width:{pct}%;background:{colour}"></span>'
        f'</span> {passed}/{total}'
    )


# ── INC section ────────────────────────────────────────────────────────────────

def _inc_section(runs: list[dict]) -> str:
    if not runs:
        return "<p class='empty'>No incremental test runs recorded yet.</p>"

    # History table
    rows = []
    for r in runs:
        failed = [res["case_id"] for res in r.get("results", []) if res.get("verdict") != "PASS"]
        failed_str = ", ".join(failed) if failed else "—"
        rows.append(
            f"<tr>"
            f"<td>{_fmt_dt(r.get('run_at',''))}</td>"
            f"<td>{r.get('total', 0)}</td>"
            f"<td>{_pct_bar(r.get('passed', 0), r.get('total', 0))}</td>"
            f"<td class='{'fail' if r.get('failed', 0) else ''}'>{failed_str}</td>"
            f"</tr>"
        )

    from scripts._pagination import paginate_rows

    inc_ctrl, inc_body = paginate_rows(rows, "inc_runs")
    history = (
        inc_ctrl +
        "<table>"
        "<thead><tr><th>Run</th><th>Cases</th><th>Pass rate</th><th>Failed</th></tr></thead>"
        "<tbody>" + inc_body + "</tbody>"
        "</table>"
    )

    # Case matrix — split INC and LINKAGE with subheadings
    matrix = ""
    if len(runs) > 1:
        all_cases = sorted({res["case_id"] for r in runs for res in r.get("results", [])})
        inc_cases = [c for c in all_cases if c.startswith("INC")]
        linkage_cases = [c for c in all_cases if not c.startswith("INC")]
        run_labels = [_fmt_dt(r.get("run_at", "")) for r in runs]

        def _case_matrix(cases, table_id):
            header = "<tr><th>Case</th>" + "".join(f"<th>{l}</th>" for l in run_labels) + "</tr>"
            body_rows = []
            for case_id in cases:
                cells = []
                for r in runs:
                    result = next((res for res in r.get("results", []) if res["case_id"] == case_id), None)
                    if result is None:
                        cells.append("<td>—</td>")
                    elif result.get("verdict") == "PASS":
                        cells.append('<td class="pass">✓</td>')
                    elif result.get("verdict") == "PARTIAL":
                        cells.append('<td class="partial">~</td>')
                    else:
                        cells.append('<td class="fail">✗</td>')
                tooltip = _case_tooltip(case_id)
                body_rows.append(f"<tr><td class='case-id' title='{tooltip}'>{case_id}</td>{''.join(cells)}</tr>")
            mat_ctrl, mat_body = paginate_rows(body_rows, table_id)
            return (
                mat_ctrl +
                f"<div class='scroll'><table class='matrix'>"
                f"<thead>{header}</thead>"
                f"<tbody>" + mat_body + "</tbody>"
                f"</table></div>"
            )

        parts = []
        if inc_cases:
            parts.append(
                "<h3 style='color:#e2e8f0; margin-top:1.5rem'>Incremental Cases (INC-01..20)</h3>"
                "<p class='dim' style='margin-bottom:0.5rem'>Single-message agent behavior tests. "
                "Each case sends one message through the update agent and checks node updates, "
                "item extractions, and ambiguity flags against expected outcomes.</p>"
                + _case_matrix(inc_cases, "inc_matrix")
            )
        if linkage_cases:
            parts.append(
                "<h3 style='color:#e2e8f0; margin-top:1.5rem'>Linkage Cases</h3>"
                "<p class='dim' style='margin-bottom:0.5rem'>Multi-message pipeline tests "
                "exercising the linkage agent. Tests client order ↔ supplier order fulfillment "
                "link creation across coordinated tasks.</p>"
                + _case_matrix(linkage_cases, "linkage_matrix")
            )
        matrix = "".join(parts)

    latest = runs[0]
    summary = (
        f"Latest: {_fmt_dt(latest.get('run_at',''))} &nbsp;·&nbsp; "
        f"{latest.get('passed',0)}/{latest.get('total',0)} PASS"
        + (f" &nbsp;·&nbsp; <span class='fail'>{latest.get('failed',0)} failed</span>" if latest.get('failed') else "")
    )

    return f"<p class='summary'>{summary}</p>{history}{matrix}"


# ── Risk framework legend & tooltips ──────────────────────────────────────────

RISK_CATEGORIES = {
    "R1-D":  ("Delivery Completion",            "End-to-end delivery tracking across full order lifecycle"),
    "R2":    ("Cadence & Proactive Tasks",      "Detection of implicit/cadence tasks not mentioned in messages"),
    "R2a":   ("Delivery Subtasks",              "Invoice, challan, sign-off after delivery"),
    "R2b":   ("Periodic Cadence",               "End-of-month reconciliation, stock checks"),
    "R2c":   ("Supplier Dates",                 "Conservative milestone setting from optimistic supplier dates"),
    "R2d":   ("Deadline Reminders",             "Pre-delivery enquiry timing"),
    "R2e":   ("Large Order Flags",              "Invoice financing, working capital flags"),
    "R2f":   ("Proactive Outreach",             "Client follow-up, post-delivery feedback"),
    "R3-C":  ("Order Conflation",               "Separating distinct orders that share entities or threads"),
    "R4-A":  ("Supplier Entity Resolution",     "Resolving supplier name variants to single entity"),
    "R4-B":  ("Army Client Entity Resolution",  "Resolving Army unit/officer references to single client"),
    "R5":    ("Ambiguity Detection",            "Flagging uncertain information for human review"),
    "R6":    ("Post-Delivery QC",               "Handling delivery shortfalls and quality rejections"),
    "R7a":   ("Off-Platform Instruction Gap",   "Verbal instructions with no WhatsApp trail block downstream actions"),
    "R7b":   ("Execution Plan Dissonance",      "Staff act on 1:1 chats, groups not updated — agent state diverges"),
    "R8":    ("Image Content Blindness",        "Business-critical info in images — OCR/vision required (resolved S1)"),
    "R9":    ("Missing WhatsApp Entries",        "Task appears stalled vs genuinely stalled — no WhatsApp trail"),
    "R10":   ("Staff Quality Variance",         "Assignee reliability affecting escalation urgency"),
    "R11":   ("Warehouse Inventory Blindness",  "Stock-filled orders triggering false supplier tasks"),
    "R12":   ("Internal Group Conversation Routing", "Mapping staff operational chatter to entity-specific order conversations"),
}

DIFFICULTY_LEVELS = {
    "L1": ("Single Thread",   "All information in one WhatsApp thread"),
    "L2": ("Two Threads",     "Information split across two threads"),
    "L3": ("Three+ Threads",  "Information split across three or more threads"),
}

# Group order for collapsible rows
FRAMEWORK_GROUP_ORDER = ["R2", "R3-C", "R4-A", "R4-B", "R5", "R6", "R7", "R12"]


def _load_inc_descriptions() -> dict[str, str]:
    """Load INC test descriptions from metadata.json files."""
    descs = {}
    inc_dir = Path("tests/functional_tests")
    if inc_dir.exists():
        for meta_path in sorted(inc_dir.glob("INC-*/metadata.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                cid = meta.get("id", "")
                name = meta.get("name", "").replace("_", " ").title()
                qrd = meta.get("quality_risk_dimension", "")
                descs[cid] = f"{name} ({qrd})" if qrd else name
            except Exception:
                pass
    return descs


def _load_integration_descriptions() -> dict[str, str]:
    """Load integration test case descriptions from metadata.json files."""
    descs = {}
    for meta_path in sorted(Path("tests/evals").glob("*/metadata.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cid = meta.get("id", "")
            desc = meta.get("description", "")
            if desc:
                descs[cid] = desc[:80]
        except Exception:
            pass
    # Also check integration test dirs
    for meta_path in sorted(Path("tests/integration_tests").glob("*/seed_tasks.json")):
        case_name = meta_path.parent.name
        cid = case_name.split("_")[0]
        if cid not in descs:
            descs[cid] = case_name.replace("_", " ").title()[:80]
    return descs


# Loaded at generation time
INC_DESCRIPTIONS: dict[str, str] = {}
INTEGRATION_DESCRIPTIONS: dict[str, str] = {}


def _case_tooltip(case_id: str) -> str:
    """Build tooltip text from case_id. Handles eval (R-prefix), INC, and integration cases."""
    # INC tests
    if case_id in INC_DESCRIPTIONS:
        return INC_DESCRIPTIONS[case_id]

    # Integration test cases
    if case_id in INTEGRATION_DESCRIPTIONS:
        return INTEGRATION_DESCRIPTIONS[case_id]

    # Eval cases: risk category + difficulty level
    parts = []
    sub = case_id.split("-L")[0] if "-L" in case_id else case_id
    if sub in RISK_CATEGORIES:
        name, desc = RISK_CATEGORIES[sub]
        parts.append(f"{name}: {desc}")
    for lvl_code, (lvl_name, lvl_desc) in DIFFICULTY_LEVELS.items():
        if f"-{lvl_code}-" in case_id or case_id.endswith(f"-{lvl_code}"):
            parts.append(f"{lvl_code} = {lvl_name}: {lvl_desc}")
            break
    return " | ".join(parts) if parts else case_id


def _legend_section() -> str:
    """Risk Framework Legend with nomenclature, risk categories, and difficulty levels."""
    # Risk categories table
    cat_rows = []
    for code, (name, desc) in RISK_CATEGORIES.items():
        cat_rows.append(f"<tr><td class='case-id'>{code}</td><td>{name}</td><td>{desc}</td></tr>")

    # Difficulty levels table
    lvl_rows = []
    for code, (name, desc) in DIFFICULTY_LEVELS.items():
        lvl_rows.append(f"<tr><td class='case-id'>{code}</td><td>{name}</td><td>{desc}</td></tr>")

    return (
        "<div class='legend'>"
        "<details>"
        "<summary>Risk Framework Legend</summary>"
        "<p class='nomenclature'>"
        "Nomenclature: <code>R{category}-{subcategory}-L{level}-{sequence}</code><br>"
        "R = Risk category (R1&ndash;R6) &nbsp;&middot;&nbsp; "
        "L = Difficulty level (L1 = single thread, L2 = two threads, L3 = three+ threads)"
        "</p>"
        "<h3>Risk Categories</h3>"
        "<table>"
        "<thead><tr><th>Code</th><th>Name</th><th>Description</th></tr></thead>"
        "<tbody>" + "".join(cat_rows) + "</tbody>"
        "</table>"
        "<h3>Difficulty Levels</h3>"
        "<table>"
        "<thead><tr><th>Level</th><th>Name</th><th>Description</th></tr></thead>"
        "<tbody>" + "".join(lvl_rows) + "</tbody>"
        "</table>"
        "</details>"
        "</div>"
    )


# ── Eval section ───────────────────────────────────────────────────────────────

def _eval_section(runs: list[dict], suite: str) -> str:
    if not runs:
        return "<p class='empty'>No eval runs recorded yet.</p>"

    latest = runs[0]
    passed = latest.get("passed", 0)
    total = latest.get("total", 0)
    avg = latest.get("avg_score", "—")

    summary = (
        f"<p class='summary'>Latest: {_fmt_dt(latest.get('run_at',''))} &nbsp;·&nbsp; "
        f"{passed}/{total} PASS &nbsp;·&nbsp; avg score: {avg}</p>"
    )

    results = latest.get("results", [])
    if not results:
        return summary

    if suite == "synth":
        return summary + _eval_grouped_table(results, suite)
    else:
        return summary + _eval_flat_table(results)


def _eval_flat_table(results: list[dict]) -> str:
    """Original flat eval table (used for real cases)."""
    case_rows = []
    for c in results:
        v = c.get("verdict", "?")
        cls = "pass" if v == "PASS" else ("partial" if v == "PARTIAL" else "fail")
        score = c.get("overall_score", "—")
        fw = c.get("framework", "")
        lvl = c.get("level", "")
        tooltip = _case_tooltip(c.get("case_id", ""))
        case_rows.append(
            f"<tr>"
            f"<td class='case-id' title='{tooltip}'>{c.get('case_id', '?')}</td>"
            f"<td>{fw}</td><td>{lvl}</td>"
            f"<td class='{cls}'>{v}</td>"
            f"<td>{score}</td>"
            f"</tr>"
        )
    return (
        "<table>"
        "<thead><tr><th>Case</th><th>Framework</th><th>Level</th>"
        "<th>Verdict</th><th>Score</th></tr></thead>"
        "<tbody>" + "".join(case_rows) + "</tbody>"
        "</table>"
    )


def _eval_grouped_table(results: list[dict], suite: str) -> str:
    """Grouped eval table with collapsible framework groups (for synthetic)."""
    from collections import OrderedDict
    import html as html_mod

    # Group results by framework
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for fw in FRAMEWORK_GROUP_ORDER:
        members = [c for c in results if c.get("framework") == fw]
        if members:
            groups[fw] = members
    # Catch any frameworks not in the predefined order
    seen = set(FRAMEWORK_GROUP_ORDER)
    for c in results:
        fw = c.get("framework", "other")
        if fw not in seen:
            seen.add(fw)
            groups.setdefault(fw, [])
            groups[fw].append(c)

    table_id = f"eval-{suite}"
    rows = []
    for fw, members in groups.items():
        scores = [c.get("overall_score", 0) for c in members if isinstance(c.get("overall_score"), (int, float))]
        avg_score = f"{sum(scores) / len(scores):.1f}" if scores else "—"
        pass_count = sum(1 for c in members if c.get("verdict") == "PASS")
        fw_name = RISK_CATEGORIES.get(fw, (fw, ""))[0]

        rows.append(
            f"<tr class='group-row' data-group='{table_id}-{fw}' onclick='toggleGroup(this)'>"
            f"<td><span class='toggle'>&#9654;</span>{fw}</td>"
            f"<td>{fw_name}</td><td></td>"
            f"<td>{pass_count}/{len(members)}</td>"
            f"<td>{avg_score}</td>"
            f"</tr>"
        )
        for c in members:
            v = c.get("verdict", "?")
            cls = "pass" if v == "PASS" else ("partial" if v == "PARTIAL" else "fail")
            score = c.get("overall_score", "—")
            lvl = c.get("level", "")
            tooltip = html_mod.escape(_case_tooltip(c.get("case_id", "")), quote=True)
            rows.append(
                f"<tr class='group-child' data-group='{table_id}-{fw}'>"
                f"<td class='case-id' title='{tooltip}'>&nbsp;&nbsp;{c.get('case_id', '?')}</td>"
                f"<td></td><td>{lvl}</td>"
                f"<td class='{cls}'>{v}</td>"
                f"<td>{score}</td>"
                f"</tr>"
            )

    return (
        "<table>"
        "<thead><tr><th>Case</th><th>Framework</th><th>Level</th>"
        "<th>Verdict</th><th>Score</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody>"
        "</table>"
    )


# ── Coverage section ──────────────────────────────────────────────────────────

def _load_coverage() -> dict | None:
    if not COVERAGE.exists():
        return None
    try:
        return json.loads(COVERAGE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _coverage_section(cov: dict | None) -> str:
    if not cov:
        return "<p class='dim'>No coverage data. Run: pytest --cov=src --cov-report=json:tests/runs/coverage.json</p>"

    totals = cov.get("totals", {})
    pct = totals.get("percent_covered", 0)
    stmts = totals.get("num_statements", 0)
    miss = totals.get("missing_lines", 0)

    # Per-file breakdown
    files = cov.get("files", {})
    rows = []
    for fpath in sorted(files.keys()):
        f = files[fpath]
        s = f.get("summary", {})
        fpct = s.get("percent_covered", 0)
        fstmts = s.get("num_statements", 0)
        fmiss = s.get("missing_lines", 0)
        if fstmts == 0:
            continue
        # Color by coverage level
        cls = "pass" if fpct >= 90 else ("partial" if fpct >= 70 else ("fail" if fpct < 50 else ""))
        short = fpath.replace("src/", "")
        rows.append(
            f"<tr><td class='case-id'>{short}</td>"
            f"<td>{fstmts}</td><td>{fmiss}</td>"
            f"<td class='{cls}'>{fpct:.0f}%</td></tr>"
        )

    colour = "#48bb78" if pct >= 80 else "#ed8936" if pct >= 60 else "#fc8181"

    return (
        f"<p class='summary'>Overall: <strong style='color:{colour}'>{pct:.0f}%</strong> "
        f"({stmts - miss}/{stmts} statements covered)</p>"
        "<table>"
        "<thead><tr><th>Module</th><th>Stmts</th><th>Miss</th><th>Cover</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody>"
        "</table>"
    )


# ── Unit tests section ─────────────────────────────────────────────────────────

def _unit_run_key(r: dict) -> tuple:
    """Key for dedup: (total, passed, failed, skipped)."""
    return (r.get("total", 0), r.get("passed", 0), r.get("failed", 0), r.get("skipped", 0))


def _unit_section(runs: list[dict]) -> str:
    if not runs:
        return "<p class='empty'>No unit test runs recorded yet.</p>"

    # Filter out runs with very small totals (early dev noise)
    MIN_TOTAL = 20
    runs = [r for r in runs if r.get("total", 0) >= MIN_TOTAL]

    from scripts._pagination import paginate_rows

    # Compress: drop runs where data is same as the next (later) contiguous row.
    # We keep the LATEST of each contiguous group and show how many were skipped.
    compressed: list[tuple[dict, int]] = []
    i = 0
    while i < len(runs):
        key = _unit_run_key(runs[i])
        skipped = 0
        j = i + 1
        while j < len(runs) and _unit_run_key(runs[j]) == key:
            skipped += 1
            j += 1
        compressed.append((runs[i], skipped))
        i = j

    rows = []
    for r, skipped in compressed:
        ts = _fmt_dt(r.get('run_at', ''))
        if skipped:
            ts += f" <span class='skip-note'>({skipped} identical run{'s' if skipped > 1 else ''} hidden)</span>"
        rows.append(
            f"<tr>"
            f"<td>{ts}</td>"
            f"<td>{r.get('total', 0)}</td>"
            f"<td>{_pct_bar(r.get('passed', 0), r.get('total', 0))}</td>"
            f"<td class='{'fail' if r.get('failed', 0) else ''}'>{r.get('failed', 0)}</td>"
            f"<td>{r.get('skipped', 0)}</td>"
            f"</tr>"
        )
    latest = runs[0]
    summary = (
        f"Latest: {_fmt_dt(latest.get('run_at', ''))} &nbsp;·&nbsp; "
        f"{latest.get('passed', 0)}/{latest.get('total', 0)} passed"
        + (f" &nbsp;·&nbsp; <span class='fail'>{latest.get('failed', 0)} failed</span>"
           if latest.get('failed') else "")
    )

    unit_ctrl, unit_body = paginate_rows(rows, "unit_runs", page_size=20)
    return (
        f"<p class='summary'>{summary} &nbsp;·&nbsp; "
        f"<a href='/developer/tests/'>Full Allure report →</a></p>"
        + unit_ctrl +
        "<table>"
        "<thead><tr><th>Run</th><th>Total</th><th>Pass rate</th><th>Failed</th><th>Skipped</th></tr></thead>"
        "<tbody>" + unit_body + "</tbody>"
        "</table>"
    )


# ── Linkage section ──────────────────────────────────────────────────────────

def _linkage_section(runs: list[dict]) -> str:
    import html as html_mod

    linkage_runs = [r for r in runs if r.get("case_id", "").startswith("LINKAGE")]
    if not linkage_runs:
        return "<p class='empty'>No linkage test runs recorded yet.</p>"

    from collections import OrderedDict
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for r in linkage_runs:
        groups.setdefault(r.get("case_id", "?"), []).append(r)

    rows = []
    for case_id, members in groups.items():
        tooltip = html_mod.escape(_case_tooltip(case_id), quote=True)
        for r in members:
            routed = r.get("messages_routed", 0)
            total = r.get("messages_total", 0)
            ua_calls = r.get("update_agent_calls", 0)
            linkage_evts = r.get("linkage_events_processed", 0)
            links = r.get("fulfillment_link_count", 0)
            flags = r.get("ambiguity_flag_count", 0)
            dead = r.get("dead_letter_count", 0)

            # Model / cost info
            model_info = ""
            model_usage = r.get("model_usage", [])
            if model_usage:
                parts = [f"{m.get('model', '?')} ({m.get('calls', 0)} calls)" for m in model_usage]
                model_info = ", ".join(parts)
            cost = r.get("total_cost")
            cost_str = f"${cost:.4f}" if cost is not None else ""
            extra = " · ".join(filter(None, [model_info, cost_str]))

            rows.append(
                f"<tr>"
                f"<td class='case-id' title='{tooltip}'>{case_id}</td>"
                f"<td>{_fmt_dt(r.get('run_at', ''))}</td>"
                f"<td>{total}</td>"
                f"<td>{routed}/{total}</td>"
                f"<td>{ua_calls}</td>"
                f"<td>{linkage_evts}</td>"
                f"<td>{links}</td>"
                f"<td>{flags}</td>"
                f"<td class='{'fail' if dead else ''}'>{dead}</td>"
                f"<td class='case-id'>{extra}</td>"
                f"</tr>"
            )

    from scripts._pagination import paginate_rows

    link_ctrl, link_body = paginate_rows(rows, "linkage_runs")
    return (
        "<h3 style='color:#e2e8f0; margin-top:1.5rem'>Linkage Tests</h3>"
        "<p class='dim' style='margin-bottom:0.5rem'>Multi-message pipeline tests "
        "exercising the linkage agent. Tests client order ↔ supplier order fulfillment "
        "link creation across coordinated tasks.</p>"
        + link_ctrl +
        "<table>"
        "<thead><tr><th>Case</th><th>Run</th><th>Messages</th>"
        "<th>Routed</th><th>Update Agent</th><th>Linkage Events</th>"
        "<th>Links</th><th>Ambiguity</th><th>Dead Letters</th>"
        "<th>Model / Cost</th></tr></thead>"
        "<tbody>" + link_body + "</tbody>"
        "</table>"
    )


# ── Load eval runs by suite ────────────────────────────────────────────────────

def _load_eval_runs() -> tuple[list[dict], list[dict]]:
    """Return (synth_runs, real_runs) sorted newest-first."""
    synth, real = [], []
    if not RUNS_EVAL.exists():
        return synth, real
    for f in sorted(RUNS_EVAL.glob("*.json"), reverse=True):
        if f.name == ".gitkeep":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = f.stem  # e.g. eval-synth-20260330T010709_summary or 20260330T010709_summary
        if "synth" in name:
            synth.append(data)
        else:
            real.append(data)
    return synth, real


# ── System tests compact summary ───────────────────────────────────────────────

def _tag_legend() -> str:
    return (
        "<details style='margin-top:0.75rem'>"
        "<summary class='dim' style='cursor:pointer; font-size:0.78rem'>Tag Legend</summary>"
        "<table class='detail' style='margin-top:0.3rem; font-size:0.78rem'>"
        "<tr><td><span class='tag tag-mode'>E</span></td><td>Entity-first routing (v0.3.0+)</td></tr>"
        "<tr><td><span class='tag tag-legacy'>L</span></td><td>Legacy batch routing (pre v0.3.0)</td></tr>"
        "<tr><td><span class='tag tag-traced'>T</span></td><td>Phoenix OTEL tracing enabled</td></tr>"
        "<tr><td><span class='tag tag-agent'>AO</span></td><td>Update agent — order processing</td></tr>"
        "<tr><td><span class='tag tag-agent'>AL</span></td><td>Linkage agent — fulfillment matching</td></tr>"
        "<tr><td><span class='tag tag-dim'>AS</span></td><td>Shared groups conversation routing (planned)</td></tr>"
        "<tr><td><span class='tag tag-batch'>B</span></td><td>Production batching (60s window, max 10 msgs)</td></tr>"
        "<tr><td><span class='tag tag-dim'>🔃</span></td><td>Asynchronous linkage processing (planned)</td></tr>"
        "<tr><td><span class='tag tag-dim'>N</span></td><td>Partial replay — first N messages only</td></tr>"
        "</table></details>"
    )


def _system_summary(int_runs: list[dict], real_runs: list[dict],
                     synth_runs: list[dict]) -> str:
    """Compact summary of system tests for /runs/ page."""
    sections = []

    # Latest replay results
    live_runs = [r for r in int_runs if r.get("test_type") == "live"]
    if live_runs:
        from collections import OrderedDict
        groups: OrderedDict[str, list[dict]] = OrderedDict()
        for r in live_runs:
            groups.setdefault(r.get("case_id", "?"), []).append(r)

        rows = []
        for case_id, members in groups.items():
            full_runs = [r for r in members if not r.get("max_messages")]
            latest = full_runs[0] if full_runs else members[0]
            score = latest.get("pipeline_score", "")
            routed = latest.get("messages_routed", 0)
            total = latest.get("messages_total", 0)
            tasks = latest.get("tasks_created", len(latest.get("node_summary", {})))
            cost = latest.get("total_cost", 0)
            dead = latest.get("dead_letter_count", 0)

            # Concise tag badges with hover tooltips
            tag_badges = []
            rm = latest.get("routing_mode", "")
            if rm == "entity_first":
                tag_badges.append("<span class='tag tag-mode' title='Entity-first routing (v0.3.0+)'>E</span>")
            elif rm == "legacy_batch":
                tag_badges.append("<span class='tag tag-legacy' title='Legacy batch routing (pre v0.3.0)'>L</span>")
            if latest.get("traced"):
                tag_badges.append("<span class='tag tag-traced' title='Phoenix OTEL tracing enabled'>T</span>")
            # Agents
            run_agents = latest.get("agents", [])
            if not run_agents:
                run_agents = ["AO"] if latest.get("skip_linkage") else ["AO", "AL"]
            tips = {"AO": "Update agent for order processing",
                    "AL": "Linkage agent for fulfillment matching",
                    "AS": "Conversation routing for shared groups"}
            for agent in run_agents:
                tag_badges.append(f"<span class='tag tag-agent' title='{tips.get(agent, agent)}'>{agent}</span>")
            # Batching
            if latest.get("batch_mode"):
                tag_badges.append("<span class='tag tag-batch' title='Production batching (60s window)'>B</span>")
            # Partial replay
            if latest.get("max_messages"):
                n = latest["max_messages"]
                tag_badges.append(f"<span class='tag tag-dim' title='Partial replay (first {n} messages)'>{n}</span>")
            tags_html = " ".join(tag_badges) if tag_badges else "<span class='dim'>—</span>"

            score_html = f"<span class='pass'>{score}</span>" if score and score >= 70 else (
                f"<span class='partial'>{score}</span>" if score and score >= 50 else
                f"<span class='fail'>{score}</span>" if score else "—"
            )

            rows.append(
                f"<tr>"
                f"<td>{case_id}</td>"
                f"<td>{routed}/{total}</td>"
                f"<td>{tasks}</td>"
                f"<td>{dead}</td>"
                f"<td>{score_html}</td>"
                f"<td>{tags_html}</td>"
                f"<td class='dim'>${cost:.2f}</td>"
                f"<td class='dim'>{_fmt_dt(latest.get('run_at', ''))}</td>"
                f"</tr>"
            )

        sections.append(
            "<h3>Latest Replay Results</h3>"
            "<table><thead><tr>"
            "<th>Case</th><th>Routed</th><th>Tasks</th><th>Dead Letters</th>"
            "<th>Score</th><th>Tags</th><th>Cost</th><th>Run</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )

    # Latest eval results
    for label, runs in [("Eval — Real", real_runs), ("Eval — Synthetic", synth_runs)]:
        if runs:
            latest = runs[0]
            passed = latest.get("passed", 0)
            total = latest.get("total", 0)
            avg = latest.get("avg_score", 0)
            run_at = _fmt_dt(latest.get("run_at", ""))

            sections.append(
                f"<h3>{label}</h3>"
                f"<p class='summary'>{_pct_bar(passed, total)} "
                f"&nbsp;·&nbsp; avg score: {avg:.0f} "
                f"&nbsp;·&nbsp; {run_at}</p>"
            )

    # Tags legend
    sections.append(
        _tag_legend()
    )

    return "\n".join(sections) if sections else "<p class='empty'>No system test results yet.</p>"


# ── Main ───────────────────────────────────────────────────────────────────────

CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
  background: #0f1117; color: #e2e8f0;
  padding: 2rem; max-width: 1100px; margin: 0 auto;
}
a { color: #4a90d9; text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 1.4rem; font-weight: 600; color: #f7fafc; margin-bottom: 0.25rem; }
h2 { font-size: 1rem; font-weight: 600; color: #a0aec0; margin: 2rem 0 0.75rem;
     text-transform: uppercase; letter-spacing: 0.08em; border-bottom: 1px solid #2d3748;
     padding-bottom: 0.4rem; }
h3 { font-size: 0.85rem; color: #4a5568; margin: 1.25rem 0 0.5rem; }
.meta { font-size: 0.78rem; color: #4a5568; margin-bottom: 2rem; }
.summary { font-size: 0.85rem; color: #a0aec0; margin-bottom: 0.75rem; }
.empty { font-size: 0.85rem; color: #4a5568; font-style: italic; }
table { width: 100%; border-collapse: collapse; font-size: 0.83rem; margin-bottom: 1rem; }
th { text-align: left; padding: 0.4rem 0.75rem; color: #4a5568;
     font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
     border-bottom: 1px solid #2d3748; }
td { padding: 0.4rem 0.75rem; border-bottom: 1px solid #1a2030; }
tr:hover td { background: #1a2030; }
.case-id { font-family: monospace; font-size: 0.8rem; color: #a0aec0; }
.pass  { color: #48bb78; }
.partial { color: #ed8936; }
.fail  { color: #fc8181; }
.err   { color: #4a5568; }
.bar-wrap { display: inline-block; width: 80px; height: 6px;
            background: #2d3748; border-radius: 3px; vertical-align: middle;
            margin-right: 0.4rem; }
.bar { display: inline-block; height: 100%; border-radius: 3px; }
.scroll { overflow-x: auto; }
table.matrix td, table.matrix th { padding: 0.3rem 0.6rem; text-align: center; }
table.matrix td.case-id { text-align: left; }
nav { margin-bottom: 2rem; font-size: 0.82rem; }
.tag { display: inline-block; padding: 0.1rem 0.35rem; border-radius: 3px;
       font-size: 0.68rem; font-weight: 600; margin: 0 1px; cursor: help;
       letter-spacing: 0.02em; vertical-align: middle; }
.tag-mode { background: #2d3748; color: #63b3ed; }
.tag-legacy { background: #2d3748; color: #a0aec0; }
.tag-traced { background: #2f855a; color: #c6f6d5; }
.tag-agent { background: #2a4365; color: #90cdf4; }
.tag-batch { background: #553c9a; color: #d6bcfa; }
.tag-dim { background: #1a2030; color: #718096; border: 1px solid #2d3748; }
.dim { font-size: 0.85rem; color: #4a5568; }
/* Collapsible group rows */
.group-row { cursor: pointer; background: #1a2030; }
.group-row:hover td { background: #242c3a; }
.group-row td { font-weight: 600; color: #a0aec0; }
.group-row .toggle { display: inline-block; width: 1em; font-size: 0.75rem;
                     margin-right: 0.3rem; transition: transform 0.15s; }
.group-row.open .toggle { transform: rotate(90deg); }
.group-child { display: none; }
.group-child.visible { display: table-row; }
.skip-note { font-size: 0.75rem; color: #718096; font-style: italic; }
.badge { display: inline-block; font-size: 0.65rem; padding: 0.1rem 0.35rem;
         border-radius: 3px; vertical-align: middle; margin-left: 0.3rem;
         font-weight: 600; letter-spacing: 0.03em; }
.badge-mode { background: #2d3748; color: #63b3ed; }
.badge-traced { background: #2f855a; color: #c6f6d5; }
/* Angled stat column headers for compact width */
th.th-angled { white-space: nowrap; vertical-align: bottom; height: 5rem; width: 2.2rem;
               padding: 0 0.3rem 0.4rem; }
th.th-angled > div { transform: rotate(-55deg); transform-origin: bottom left;
                     width: 1.5em; display: block; margin-left: 0.7rem; }
td.td-stat { text-align: center; padding: 0.4rem 0.3rem; font-variant-numeric: tabular-nums; }
/* Legend section */
.legend { margin-bottom: 1.5rem; }
.legend details { margin-bottom: 0.75rem; }
.legend summary { cursor: pointer; font-size: 0.85rem; color: #a0aec0;
                  font-weight: 600; margin-bottom: 0.4rem; }
.legend .nomenclature { font-size: 0.82rem; color: #718096;
                        margin: 0.5rem 0 1rem 0; line-height: 1.5; }
.legend code { background: #2d3748; padding: 0.1rem 0.35rem; border-radius: 3px;
               font-size: 0.8rem; color: #e2e8f0; }
.pagination { display: flex; align-items: center; gap: 0.5rem;
              justify-content: center; padding: 0.4rem 0; font-size: 0.78rem; }
.pagination button { background: #2d3748; color: #e2e8f0; border: 1px solid #4a5568;
                     border-radius: 3px; padding: 0.2rem 0.6rem; cursor: pointer;
                     font-size: 0.75rem; }
.pagination button:hover { background: #4a5568; }
.pagination span { color: #718096; }
"""


def generate() -> str:
    global INC_DESCRIPTIONS, INTEGRATION_DESCRIPTIONS
    INC_DESCRIPTIONS = _load_inc_descriptions()
    INTEGRATION_DESCRIPTIONS = _load_integration_descriptions()

    inc_runs   = _load_runs(RUNS_INC)
    unit_runs  = _load_runs(RUNS_UNIT)
    int_runs   = _load_runs(RUNS_INT)
    synth_runs, real_runs = _load_eval_runs()
    cov        = _load_coverage()

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Mantri — Test Results</title>
  <style>{CSS}</style>
</head>
<body>
  <h1>Test Results</h1>
  <p class="meta">
    Generated {generated} &nbsp;·&nbsp;
    <a href="/developer/">← Developer Portal</a>
  </p>

  <nav>
    <a href="#unit">Unit</a> &nbsp;·&nbsp;
    <a href="#coverage">Coverage</a> &nbsp;·&nbsp;
    <a href="#integration">Integration</a> &nbsp;·&nbsp;
    <a href="#system">System</a>
  </nav>

  <h2 id="unit">Unit Tests</h2>
  {_unit_section(unit_runs)}

  <h2 id="coverage">Code Coverage</h2>
  {_coverage_section(cov)}

  <h2 id="integration">Integration Tests</h2>
  <p class="dim" style="margin-bottom:0.5rem">Agent behavior tests: incremental single-message cases (INC-01..20) and linkage pipeline tests.</p>
  {_inc_section(inc_runs)}
  {_linkage_section(int_runs)}

  <h2 id="system">System Tests</h2>
  <p class="dim" style="margin-bottom:0.5rem">
    Full pipeline replay + eval scoring.
    <a href="/developer/system/">Full detail view →</a> &nbsp;·&nbsp;
    <a href="/developer/phoenix/">Phoenix traces →</a>
  </p>
  {_system_summary(int_runs, real_runs, synth_runs)}

  {_legend_section()}

  <script>
  function toggleGroup(row) {{
    var group = row.getAttribute('data-group');
    var open = row.classList.toggle('open');
    var children = document.querySelectorAll('tr.group-child[data-group="' + group + '"]');
    for (var i = 0; i < children.length; i++) {{
      if (open) {{ children[i].classList.add('visible'); }}
      else {{ children[i].classList.remove('visible'); }}
    }}
  }}
  var pageState = {{}};
  function paginate(tid, dir) {{
    if (!pageState[tid]) pageState[tid] = 0;
    var rows = document.querySelectorAll('tr[data-page].prow-' + tid);
    var maxPage = 0;
    for (var i = 0; i < rows.length; i++) {{
      var p = parseInt(rows[i].getAttribute('data-page'));
      if (p > maxPage) maxPage = p;
    }}
    var newPage = pageState[tid] + dir;
    if (newPage < 0 || newPage > maxPage) return;
    pageState[tid] = newPage;
    for (var i = 0; i < rows.length; i++) {{
      var p = parseInt(rows[i].getAttribute('data-page'));
      rows[i].style.display = (p === newPage) ? '' : 'none';
    }}
    var ind = document.getElementById('page-ind-' + tid);
    if (ind) ind.textContent = 'Page ' + (newPage + 1) + ' of ' + (maxPage + 1);
  }}
  </script>
</body>
</html>"""


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    html = generate()
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Published: {OUT_PATH}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
publish_runs.py

Reads runs/incremental/*.json and runs/eval/*.json, generates a self-contained
static HTML page at static/developer/runs/index.html.

Run after any test suite to update the published results.
Also called automatically by run_incremental_test.py after full runs.

Usage:
    python scripts/publish_runs.py
"""

import json
from datetime import datetime
from pathlib import Path

RUNS_INC  = Path("runs/incremental")
RUNS_EVAL = Path("runs/eval")
OUT_PATH  = Path("static/developer/runs/index.html")

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

    history = (
        "<table>"
        "<thead><tr><th>Run</th><th>Cases</th><th>Pass rate</th><th>Failed</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody>"
        "</table>"
    )

    # Case matrix (only if >1 run)
    matrix = ""
    if len(runs) > 1:
        all_cases = sorted({res["case_id"] for r in runs for res in r.get("results", [])})
        run_labels = [_fmt_dt(r.get("run_at", "")) for r in runs]

        header = "<tr><th>Case</th>" + "".join(f"<th>{l}</th>" for l in run_labels) + "</tr>"
        body_rows = []
        for case_id in all_cases:
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
            body_rows.append(f"<tr><td class='case-id'>{case_id}</td>{''.join(cells)}</tr>")

        matrix = (
            "<h3>Per-case history</h3>"
            "<div class='scroll'><table class='matrix'>"
            f"<thead>{header}</thead>"
            "<tbody>" + "".join(body_rows) + "</tbody>"
            "</table></div>"
        )

    latest = runs[0]
    summary = (
        f"Latest: {_fmt_dt(latest.get('run_at',''))} &nbsp;·&nbsp; "
        f"{latest.get('passed',0)}/{latest.get('total',0)} PASS"
        + (f" &nbsp;·&nbsp; <span class='fail'>{latest.get('failed',0)} failed</span>" if latest.get('failed') else "")
    )

    return f"<p class='summary'>{summary}</p>{history}{matrix}"


# ── Eval section ───────────────────────────────────────────────────────────────

def _eval_section(runs: list[dict], suite: str) -> str:
    suite_runs = [r for r in runs if suite in r.get("_suite", "") or
                  any(suite in str(r) for _ in [1])]
    # Eval runs don't store suite name — infer from filename handled in load
    if not runs:
        return "<p class='empty'>No runs recorded yet.</p>"

    rows = []
    for r in runs:
        rows.append(
            f"<tr>"
            f"<td>{_fmt_dt(r.get('run_at',''))}</td>"
            f"<td>{r.get('total', 0)}</td>"
            f"<td>{_pct_bar(r.get('passed', 0), r.get('total', 0))}</td>"
            f"<td>{r.get('avg_score', '—')}</td>"
            f"</tr>"
        )

    return (
        "<table>"
        "<thead><tr><th>Run</th><th>Cases</th><th>Pass rate</th><th>Avg score</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody>"
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
"""


def generate() -> str:
    inc_runs   = _load_runs(RUNS_INC)
    synth_runs, real_runs = _load_eval_runs()

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
    <a href="#inc">Incremental Tests</a> &nbsp;·&nbsp;
    <a href="#synth">Eval Synth</a> &nbsp;·&nbsp;
    <a href="#real">Eval Real</a>
  </nav>

  <h2 id="inc">Incremental Tests (INC)</h2>
  {_inc_section(inc_runs)}

  <h2 id="synth">Synthetic Evals</h2>
  {_eval_section(synth_runs, "synth")}

  <h2 id="real">Real Case Evals</h2>
  {_eval_section(real_runs, "real")}

</body>
</html>"""


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    html = generate()
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Published: {OUT_PATH}")


if __name__ == "__main__":
    main()

# synthetic-batch

Run the eval agent and judge on all (or a subset of) synthetic test cases from `data/evaluations_data.csv`.

Usage:
  /synthetic-batch
  /synthetic-batch --ids R4-A-L1-01 R4-B-L1-01
  /synthetic-batch --skip-existing

Arguments: $ARGUMENTS

---

## Instructions

Parse `$ARGUMENTS` for:
- `--ids <id> [<id> ...]` (optional): run only these case IDs
- `--skip-existing` (flag): skip cases that already have a `score.json`

### Step 1 — Read the CSV

Read `data/evaluations_data.csv`. Expected columns:
`id`, `scenario`, `sprint`, `data_source`, `input_threads`, `expected_output`, `pass_criteria`, `challenge`, `notes`

Filter rows by `--ids` if provided. If no rows match, report and stop.

### Step 2 — For each row

**Derive names:**
- `framework`: first two dash-separated parts of `id` (e.g. `R4-A-L1-01` → `R4-A`)
- `level`: third dash-separated part (e.g. `L1`)
- `slug`: lowercase `scenario`, spaces → underscores, strip special chars, max 50 chars
- `case_dir_name`: `<id>_<slug>` (e.g. `R4-A-L1-01_single_item_single_supplier`)
- `case_dir`: `data/cases/<case_dir_name>/`

**Skip check:** if `--skip-existing` and `<case_dir>/score.json` exists, skip this case and record its existing score.

**Set up case directory:**
1. Create `<case_dir>/` if needed.
2. Write `<case_dir>/metadata.json`:
```json
{
  "id": "<id>",
  "name": "<slug>",
  "framework": "<framework>",
  "level": "<level>",
  "sprint": "<sprint>",
  "data_source": "<data_source>",
  "description": "<scenario>",
  "chat_inputs": null,
  "completeness": {"complete": true, "missing": []},
  "expected_output": "<expected_output>",
  "pass_criteria": "<pass_criteria>",
  "challenge": "<challenge>",
  "notes": "<notes>"
}
```
3. Write `<case_dir>/threads.txt` with the content from the `input_threads` column.

**Run test:** Read `prompts/testing_prompt.txt` and `<case_dir>/threads.txt`. Act as the eval agent following the testing prompt. Write output to `<case_dir>/agent_output.txt`.

**Evaluate:** Read `<case_dir>/metadata.json` and `<case_dir>/agent_output.txt`. Determine active dimensions from framework prefix (same rules as `/run-test`). Judge the output. Write `<case_dir>/score.json`.

Print per-case: verdict, overall score, pass/fail list.

### Step 3 — Batch summary

After all cases, print and write `data/cases/synthetic_batch_summary.json`:

```json
{
  "run_at": "<ISO timestamp>",
  "total": 0,
  "passed": 0,
  "partial": 0,
  "failed": 0,
  "errored": 0,
  "avg_score": 0.0,
  "by_framework": {
    "<framework>": {"count": 0, "avg_score": 0.0, "verdicts": []}
  },
  "results": []
}
```

Print table:
```
BATCH SUMMARY
═════════════════════════
Total   : N
PASS    : N
PARTIAL : N
FAIL    : N
Avg     : XX.X/100

By framework:
  R4-A    avg= 85.0  [P P F P]
  R1-D    avg= 72.5  [P P]
```

# eval-real

Run the eval agent on a real case, then optionally judge the output and push scores to Phoenix.

Usage:
  /eval-real --case tests/evals/<case_dir>
  /eval-real --case tests/evals/<case_dir> --evaluate
  /eval-real --case tests/evals/<case_dir> --evaluate --skip-run

Arguments: $ARGUMENTS

---

## Instructions

Parse `$ARGUMENTS` for:
- `--case <path>` (required): path to the case directory
- `--evaluate` (flag): run LLM-as-judge after the test
- `--skip-run` (flag): skip the test run, use existing `agent_output.txt`

### Step 1 — Run the test (unless --skip-run)

1. Read `prompts/testing_prompt.txt` — this is the system prompt / instructions for the eval agent.
2. Read `<case_dir>/threads.txt` — this is the input to the eval agent.
3. Act as the eval agent: follow the instructions in `testing_prompt.txt` exactly, using `threads.txt` as your input. Produce the full agent output.
4. Write the output to `<case_dir>/agent_output.txt`.

### Step 2 — Evaluate (if --evaluate)

Read `<case_dir>/metadata.json`. Extract:
- `id`, `framework`, `level`, `description`, `expected_output`, `pass_criteria`, `completeness`, `notes`

Determine active dimensions based on the framework prefix:
- `R4-A` or `R4-B`: entity_accuracy, cross_thread_correlation, ambiguity_flagging
- `R3-C`: task_recall, entity_accuracy, cross_thread_correlation, ambiguity_flagging
- `R1-D`: task_recall, implicit_task_detection, next_step_quality, cross_thread_correlation, ambiguity_flagging
- `R5`: next_step_quality, ambiguity_flagging
- `R6`: task_recall, implicit_task_detection, ambiguity_flagging
- `R2`: task_recall, implicit_task_detection, next_step_quality
- (default / unrecognised): all dimensions

Dimension descriptions:
- task_recall: Are all tasks — including implicit ones — identified? Missing a task is the most costly failure.
- entity_accuracy: Are the right customers, suppliers, items, and orders linked to each task?
- cross_thread_correlation: Are messages about the same order correctly unified across multiple threads?
- next_step_quality: Are suggested next steps correct and actionable?
- implicit_task_detection: Does the agent recognise situations that imply a required action even when not explicitly stated?
- ambiguity_flagging: Does the agent flag uncertainty for human review rather than silently guessing wrong?

Read `<case_dir>/agent_output.txt`.

Scoring guidance:
- 90–100: Excellent. Fully meets criteria with no meaningful gaps.
- 70–89: Good. Meets most criteria, minor gaps that wouldn't cause operational failures.
- 50–69: Partial. Meets some criteria, meaningful gaps that could cause issues.
- 20–49: Poor. Major failures against core criteria.
- 0–19: Fail. Does not meet the criteria at all.

Produce a judgment in the following JSON schema (inactive dimensions → null score):

```json
{
  "verdict": "PASS | PARTIAL | FAIL",
  "overall_score": 0,
  "dimensions": {
    "task_recall":              {"score": null, "notes": ""},
    "entity_accuracy":          {"score": null, "notes": ""},
    "cross_thread_correlation": {"score": null, "notes": ""},
    "next_step_quality":        {"score": null, "notes": ""},
    "implicit_task_detection":  {"score": null, "notes": ""},
    "ambiguity_flagging":       {"score": null, "notes": ""}
  },
  "pass_criteria_met": true,
  "passes": [],
  "failures": [],
  "notes": ""
}
```

Write the JSON to `<case_dir>/score.json`, then print a summary:
- Verdict and overall score
- Bar chart for each active dimension (e.g. `████████░░ 80`)
- Whether pass criteria were met
- List of passes and failures

### Step 3 — Push to Phoenix (if --evaluate)

After writing `score.json`, run:

```
python scripts/log_eval_scores.py --case <case_dir> --suite eval-real
```

This pushes the score to the Phoenix experiments UI at http://localhost:6006.

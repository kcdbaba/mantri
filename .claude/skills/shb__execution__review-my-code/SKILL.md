---
name: shb__execution__review-my-code
description: Self-review your own code changes with quality checks before requesting peer review
disable-model-invocation: true
---

Run the **Review My Code** workflow from the Sherpa-B MCP server.

Call `activity/get-workflow` with `activity_id: "review-my-code"` to get the workflow structure, then follow `workflow_execution_instructions` to execute it state by state.

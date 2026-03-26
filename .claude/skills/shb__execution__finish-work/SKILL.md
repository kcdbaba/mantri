---
name: shb__execution__finish-work
description: Finalize PR with spec updates, follow-ups, code cleanup review, then mark ready for review
disable-model-invocation: true
---

Run the **Finish Work** workflow from the Sherpa-B MCP server.

Call `activity/get-workflow` with `activity_id: "finish-work"` to get the workflow structure, then follow `workflow_execution_instructions` to execute it state by state.

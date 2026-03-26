---
name: shb__execution__begin-work
description: Select an issue, create branch, write tests, and implement solution
disable-model-invocation: true
---

Run the **Begin Work** workflow from the Sherpa-B MCP server.

Call `activity/get-workflow` with `activity_id: "begin-work"` to get the workflow structure, then follow `workflow_execution_instructions` to execute it state by state.

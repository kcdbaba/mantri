---
name: shb__building__context_management
description: design how information flows through your LLM calls, ensuring each reasoning step gets exactly what it needs while avoiding context bloat and quality risks
disable-model-invocation: true
---

Run the **Context Management Workflow** workflow from the Sherpa-B MCP server.

Call `activity/get-workflow` with `activity_id: "context_management"` to get the workflow structure, then follow `workflow_execution_instructions` to execute it state by state.

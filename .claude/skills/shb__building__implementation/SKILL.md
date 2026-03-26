---
name: shb__building__implementation
description: design delivery mechanism informed by user context and translate workflow to executable implementation with minimal scope
disable-model-invocation: true
---

Run the **Implementation Workflow** workflow from the Sherpa-B MCP server.

Call `activity/get-workflow` with `activity_id: "implementation"` to get the workflow structure, then follow `workflow_execution_instructions` to execute it state by state.

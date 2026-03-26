---
name: shb__building__first_deployment
description: deploy agent to small group of testers to validate quality risk assumptions through real-world usage
disable-model-invocation: true
---

Run the **First Deployment Workflow** workflow from the Sherpa-B MCP server.

Call `activity/get-workflow` with `activity_id: "first_deployment"` to get the workflow structure, then follow `workflow_execution_instructions` to execute it state by state.

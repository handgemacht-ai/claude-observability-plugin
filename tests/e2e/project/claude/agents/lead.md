---
name: lead
description: Lead agent of the tracing e2e test. Starts the planner.
model: haiku
---
You are the lead agent of a tracing test.

When the user asks you to run the chain, call the Agent tool exactly once with
subagent_type "planner", description "Plan the chain" and prompt
"Run the worker chain and return the code word." Do not run it in the
background. Wait for the result, then answer with one sentence that contains
the code word the planner returned.

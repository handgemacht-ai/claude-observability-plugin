from __future__ import annotations

from typing import Any

from tests.session_files import (
    FORKED,
    SESSION,
    SessionDir,
    assistant,
    fork_rows,
    parent_with_skill_calls,
    tool_result,
    tool_use,
    user,
)


def emit(hook_module: Any, fake_langfuse: Any, session: SessionDir) -> None:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, SESSION, session.transcript, flush_deferred_agent_turns=True
    )


def parent_of(fake_langfuse: Any, observation: Any) -> Any:
    return next(o for o in fake_langfuse.observations if o._otel_span is observation._otel_span.parent)


def chain(fake_langfuse: Any, observation: Any) -> list[str]:
    """Observation names from the root down to the observation."""
    names = [observation.name]
    while observation._otel_span.parent is not None:
        observation = parent_of(fake_langfuse, observation)
        names.append(observation.name)
    return list(reversed(names))


def named(fake_langfuse: Any, prefix: str) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.name.startswith(prefix)]


def test_forked_skill_agent_of_a_helper_nests_under_the_skill_call_of_that_helper(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = SessionDir(tmp_path)
    session.write_main([
        user("u1", 0, "Review it."),
        assistant("a1", 1, [tool_use("toolu_reviewer", "Agent", description="Reviewer", subagent_type="reviewer")]),
        tool_result("r1", 50, "toolu_reviewer", "Reviewed.", agentId="reviewer"),
        assistant("a2", 51, [{"type": "text", "text": "Done."}]),
    ])
    session.add_agent("reviewer", parent_with_skill_calls([("toolu_skill", 10, 40)]),
                      {"agentType": "reviewer", "description": "Reviewer", "toolUseId": "toolu_reviewer",
                       "spawnDepth": 1})
    session.add_agent("forked", fork_rows(10.016), {**FORKED, "parentAgentId": "reviewer"})

    emit(hook_module, fake_langfuse, session)

    [forked] = named(fake_langfuse, "Subagent: general-purpose")
    assert chain(fake_langfuse, forked) == [
        "Conversational Turn",
        "Tool: Agent",
        "Subagent: reviewer · Reviewer",
        "Tool: Skill",
        "Subagent: general-purpose · /code-review round 0",
    ]
    assert forked.kwargs["metadata"]["agent_depth"] == 2
    assert forked.kwargs["metadata"]["parent_agent_id"] == "reviewer"
    assert forked.kwargs["metadata"]["attributed_by"] == "skill_call_time"
    skill_call = parent_of(fake_langfuse, forked)
    assert skill_call.kwargs["metadata"]["tool_id"] == "toolu_skill"
    assert skill_call.kwargs["metadata"]["subagent_id"] == "forked"
    [call_inside_fork] = [o for o in fake_langfuse.observations
                          if o.as_type == "generation" and o._otel_span.parent is forked._otel_span]
    assert call_inside_fork.kwargs["metadata"]["agent_id"] == "forked"


def test_forked_skill_agent_of_a_workflow_agent_nests_under_the_workflow_agent(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = SessionDir(tmp_path)
    launch = tool_result("r1", 3, "toolu_workflow", "Workflow done.", status="async_launched",
                         taskType="local_workflow", runId="wf_run", workflowName="check")
    session.write_main([
        user("u1", 0, "Run the check."),
        assistant("a1", 1, [tool_use("toolu_workflow", "Workflow", workflow="check")]),
        launch,
        assistant("a2", 60, [{"type": "text", "text": "Done."}]),
    ])
    session.add_agent("wf-agent", parent_with_skill_calls([("toolu_skill", 10, 40)]),
                      {"agentType": "workflow-subagent", "spawnDepth": 1}, run="wf_run")
    session.add_agent("forked", fork_rows(10.012), {**FORKED, "parentAgentId": "wf-agent"})

    emit(hook_module, fake_langfuse, session)

    [forked] = named(fake_langfuse, "Subagent: general-purpose")
    assert chain(fake_langfuse, forked) == [
        "Conversational Turn",
        "Tool: Workflow",
        "Workflow agent: check/wf-agent",
        "Tool: Skill",
        "Subagent: general-purpose · /code-review round 0",
    ]


def test_helpers_of_helpers_nest_at_every_depth(hook_module, fake_langfuse, isolated_hook_state, tmp_path):
    session = SessionDir(tmp_path)
    session.write_main([
        user("u1", 0, "Review it."),
        assistant("a1", 1, [tool_use("toolu_reviewer", "Agent", description="Reviewer", subagent_type="reviewer")]),
        tool_result("r1", 90, "toolu_reviewer", "Reviewed.", agentId="reviewer"),
        assistant("a2", 91, [{"type": "text", "text": "Done."}]),
    ])
    session.add_agent("reviewer", parent_with_skill_calls([("toolu_skill_1", 10, 80)]),
                      {"agentType": "reviewer", "description": "Reviewer", "toolUseId": "toolu_reviewer",
                       "spawnDepth": 1})
    second_level = [
        user("f1-prompt", 10.016, "Skill text.", isMeta=True),
        assistant("f1-call", 20, [tool_use("toolu_skill_2", "Skill", skill="check", args="deep")]),
        tool_result("f1-result", 60, "toolu_skill_2", 'Skill "check" completed (forked execution).'),
        assistant("f1-answer", 61, [{"type": "text", "text": "Second level done."}]),
    ]
    session.add_agent("fork-1", second_level, {**FORKED, "parentAgentId": "reviewer"})
    session.add_agent("fork-2", fork_rows(20.011), {**FORKED, "spawnDepth": 3, "parentAgentId": "fork-1"})

    emit(hook_module, fake_langfuse, session)

    deepest = next(o for o in fake_langfuse.observations if o.name == "Subagent: general-purpose · /check deep")
    assert chain(fake_langfuse, deepest) == [
        "Conversational Turn",
        "Tool: Agent",
        "Subagent: reviewer · Reviewer",
        "Tool: Skill",
        "Subagent: general-purpose · /code-review round 0",
        "Tool: Skill",
        "Subagent: general-purpose · /check deep",
    ]
    assert deepest.kwargs["metadata"]["agent_depth"] == 3


def test_helper_without_a_launching_call_adds_no_observation(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = SessionDir(tmp_path)
    session.write_main([
        user("u1", 0, "Review it."),
        assistant("a1", 1, [tool_use("toolu_reviewer", "Agent", description="Reviewer", subagent_type="reviewer")]),
        tool_result("r1", 50, "toolu_reviewer", "Reviewed.", agentId="reviewer"),
        assistant("a2", 51, [{"type": "text", "text": "Done."}]),
    ])
    session.add_agent("reviewer", parent_with_skill_calls([("toolu_skill", 10, 20)]),
                      {"agentType": "reviewer", "description": "Reviewer", "toolUseId": "toolu_reviewer",
                       "spawnDepth": 1})
    session.add_agent("orphan", fork_rows(30), {**FORKED, "parentAgentId": "reviewer"})

    emit(hook_module, fake_langfuse, session)

    assert named(fake_langfuse, "Subagent: general-purpose") == []
    assert len(named(fake_langfuse, "Subagent: reviewer")) == 1

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.session_files import (
    FORKED,
    SessionDir,
    assistant,
    fork_rows,
    parent_with_skill_calls,
    tool_result,
    tool_use,
    user,
)


def attribute(hook_module: Any, session: SessionDir) -> dict[str, dict[str, Any]]:
    return hook_module.get_subagent_transcripts_by_tool_use_id(session.transcript)


def test_forked_skill_agent_of_a_workflow_agent_nests_under_the_skill_call(hook_module, tmp_path):
    session = SessionDir(tmp_path)
    session.add_agent("wf-agent", parent_with_skill_calls([("toolu_skill", 10, 40)]),
                      {"agentType": "workflow-subagent", "spawnDepth": 1}, run="wf_run")
    session.add_agent("forked", fork_rows(10.016),
                      {**FORKED, "parentAgentId": "wf-agent"})

    entries = attribute(hook_module, session)

    assert set(entries) == {"toolu_skill"}
    entry = entries["toolu_skill"]
    assert entry["agent_id"] == "forked"
    assert entry["parent_agent_id"] == "wf-agent"
    assert entry["spawn_depth"] == 2
    assert entry["description"] == "/code-review round 0"
    assert entry["attributed_by"] == "skill_call_time"


def test_forked_skill_agents_one_after_the_other_each_take_their_own_call(hook_module, tmp_path):
    session = SessionDir(tmp_path)
    session.add_agent("parent", parent_with_skill_calls([("toolu_first", 10, 40), ("toolu_second", 42, 70)]),
                      {"agentType": "reviewer", "toolUseId": "toolu_launch", "spawnDepth": 1})
    session.add_agent("fork-1", fork_rows(10.012), {**FORKED, "parentAgentId": "parent"})
    session.add_agent("fork-2", fork_rows(42.015), {**FORKED, "parentAgentId": "parent"})

    entries = attribute(hook_module, session)

    assert entries["toolu_first"]["agent_id"] == "fork-1"
    assert entries["toolu_second"]["agent_id"] == "fork-2"
    assert entries["toolu_launch"]["agent_id"] == "parent"


def test_forked_skill_agents_started_together_take_one_call_each(hook_module, tmp_path):
    session = SessionDir(tmp_path)
    parent_rows = [
        user("p-prompt", 0, "Parent task."),
        assistant("p-calls", 10, [tool_use("toolu_a", "Skill", skill="code-review", args="a"),
                                  tool_use("toolu_b", "Skill", skill="code-review", args="b")]),
        tool_result("p-result-a", 30, "toolu_a"),
        tool_result("p-result-b", 31, "toolu_b"),
    ]
    session.add_agent("parent", parent_rows, {"agentType": "reviewer", "toolUseId": "toolu_launch"})
    session.add_agent("fork-1", fork_rows(10.010), {**FORKED, "parentAgentId": "parent"})
    session.add_agent("fork-2", fork_rows(10.020), {**FORKED, "parentAgentId": "parent"})

    entries = attribute(hook_module, session)

    assert {call: entries[call]["agent_id"] for call in ("toolu_a", "toolu_b")} == {
        "toolu_a": "fork-1", "toolu_b": "fork-2",
    }


def test_a_skill_call_that_was_over_before_the_agent_started_does_not_claim_it(hook_module, tmp_path):
    session = SessionDir(tmp_path)
    session.add_agent("parent", parent_with_skill_calls([("toolu_early", 10, 20)]),
                      {"agentType": "reviewer", "toolUseId": "toolu_launch"})
    session.add_agent("typed", fork_rows(50), {**FORKED, "parentAgentId": "parent"})

    entries = attribute(hook_module, session)

    assert set(entries) == {"toolu_launch"}


def test_launch_result_in_the_parent_transcript_names_the_agent(hook_module, tmp_path):
    session = SessionDir(tmp_path)
    parent_rows = [
        user("p-prompt", 0, "Parent task."),
        assistant("p-call", 10, [tool_use("toolu_bg", "Skill", skill="code-review", args="low")]),
        tool_result("p-result", 10.2, "toolu_bg", "Skill launched.", status="forked", background=True,
                    agentId="forked"),
    ]
    session.add_agent("parent", parent_rows, {"agentType": "reviewer", "toolUseId": "toolu_launch"})
    session.add_agent("forked", fork_rows(300), {**FORKED, "parentAgentId": "parent"})

    entries = attribute(hook_module, session)

    assert entries["toolu_bg"]["agent_id"] == "forked"
    assert entries["toolu_bg"]["attributed_by"] == "launch_result"
    assert entries["toolu_bg"]["description"] == "/code-review low"


def test_a_skill_call_named_by_a_launch_result_is_not_matched_by_time_again(hook_module, tmp_path):
    session = SessionDir(tmp_path)
    parent_rows = [
        user("p-prompt", 0, "Parent task."),
        assistant("p-call", 10, [tool_use("toolu_bg", "Skill", skill="code-review", args="low")]),
        tool_result("p-result", 10.2, "toolu_bg", "Skill launched.", status="forked", background=True,
                    agentId="named"),
    ]
    session.add_agent("parent", parent_rows, {"agentType": "reviewer", "toolUseId": "toolu_launch"})
    session.add_agent("named", fork_rows(10.1), {**FORKED, "parentAgentId": "parent"})
    session.add_agent("other", fork_rows(10.15), {**FORKED, "parentAgentId": "parent"})

    entries = attribute(hook_module, session)

    assert entries["toolu_bg"]["agent_id"] == "named"
    assert "other" not in {entry["agent_id"] for entry in entries.values()}


def test_agent_call_inside_an_agent_names_its_helper_by_agent_id(hook_module, tmp_path):
    session = SessionDir(tmp_path)
    parent_rows = [
        user("p-prompt", 0, "Parent task."),
        assistant("p-call", 10, [tool_use("toolu_inner", "Agent", description="Inner", subagent_type="worker")]),
        tool_result("p-result", 20, "toolu_inner", "Inner done.", agentId="inner"),
    ]
    session.add_agent("parent", parent_rows,
                      {"agentType": "workflow-subagent", "spawnDepth": 1}, run="wf_run")
    session.add_agent("inner", fork_rows(10.05),
                      {"agentType": "worker", "parentAgentId": "parent", "spawnDepth": 2})

    entries = attribute(hook_module, session)

    assert entries["toolu_inner"]["agent_id"] == "inner"
    assert entries["toolu_inner"]["agent_type"] == "worker"
    assert entries["toolu_inner"]["description"] == "Inner"


def test_a_skill_launch_in_the_main_transcript_names_its_agent(hook_module, tmp_path):
    session = SessionDir(tmp_path)
    session.write_main([
        user("u1", 0),
        assistant("a1", 1, [tool_use("toolu_main_skill", "Skill", skill="code-review")]),
        tool_result("r1", 1.2, "toolu_main_skill", "Skill launched.", status="forked", agentId="forked"),
    ])
    session.add_agent("forked", fork_rows(1.1), {"agentType": "general-purpose", "spawnDepth": 1})

    entries = attribute(hook_module, session)

    assert entries["toolu_main_skill"]["agent_id"] == "forked"
    assert "attributed_by" not in entries["toolu_main_skill"]


def test_helper_without_any_launching_call_stays_out_and_is_logged(hook_module, tmp_path, isolated_hook_state):
    session = SessionDir(tmp_path)
    session.add_agent("parent", parent_with_skill_calls([]),
                      {"agentType": "reviewer", "toolUseId": "toolu_launch"})
    session.add_agent("orphan", fork_rows(5), {**FORKED, "parentAgentId": "parent"})

    entries = attribute(hook_module, session)

    assert set(entries) == {"toolu_launch"}
    assert "not attributable to a launching tool_use" in (isolated_hook_state / "langfuse_hook.log").read_text()


@pytest.mark.parametrize("parent_id", ["../parent", "workflows/parent", "..", "", 7, None])
def test_a_parent_id_that_leaves_the_agent_directory_is_not_followed(hook_module, tmp_path, parent_id):
    session = SessionDir(tmp_path)
    outside = tmp_path / "agent-parent.jsonl"
    outside.write_text("".join(json.dumps(r) + "\n" for r in parent_with_skill_calls([("toolu_x", 10, 40)])),
                       encoding="utf-8")
    session.add_agent("child", fork_rows(10.01), {**FORKED, "parentAgentId": parent_id})

    assert attribute(hook_module, session) == {}


def test_agent_with_its_own_tool_use_id_is_untouched(hook_module, tmp_path):
    session = SessionDir(tmp_path)
    session.add_agent("plain", fork_rows(1), {"agentType": "worker", "toolUseId": "toolu_plain",
                                              "parentAgentId": "someone", "spawnDepth": 2})

    entries = attribute(hook_module, session)

    assert set(entries) == {"toolu_plain"}
    assert "attributed_by" not in entries["toolu_plain"]


def test_launcher_rows_are_read_once_per_launcher(hook_module, tmp_path, monkeypatch):
    session = SessionDir(tmp_path)
    session.add_agent("parent", parent_with_skill_calls([("toolu_one", 10, 20), ("toolu_two", 30, 40)]),
                      {"agentType": "reviewer", "toolUseId": "toolu_launch"})
    session.add_agent("fork-1", fork_rows(10.01), {**FORKED, "parentAgentId": "parent"})
    session.add_agent("fork-2", fork_rows(30.01), {**FORKED, "parentAgentId": "parent"})
    reads: list[str] = []
    real = hook_module.get_agent_launches_by_agent_id
    monkeypatch.setattr(hook_module, "get_agent_launches_by_agent_id",
                        lambda path: reads.append(path.name) or real(path))

    attribute(hook_module, session)

    assert reads.count("agent-parent.jsonl") == 1

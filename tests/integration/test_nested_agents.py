from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest


def copy_fixture(fixture_transcript_path, tmp_path: Path, name: str) -> Path:
    source = fixture_transcript_path(name)
    target_dir = tmp_path / name
    shutil.copytree(source.parent, target_dir)
    return target_dir / "transcript.jsonl"


def record_propagation(hook_module, monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def recording_propagate(**kwargs: Any):
        calls.append(kwargs)
        yield

    monkeypatch.setattr(hook_module, "propagate_attributes", recording_propagate)
    return calls


def emit_three_levels(hook_module, fake_langfuse, fixture_transcript_path, tmp_path, **kwargs):
    transcript = copy_fixture(fixture_transcript_path, tmp_path, "nested_agents_three_levels")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-deep", transcript, flush_deferred_agent_turns=True, **kwargs
    )
    return {observation.name: observation for observation in fake_langfuse.observations}


def children_of(fake_langfuse, observation) -> list[str]:
    return [o.name for o in fake_langfuse.observations if o._otel_span.parent is observation._otel_span]


def test_three_agent_levels_nest_under_their_launching_tool_spans(
    hook_module, fake_langfuse, fixture_transcript_path, tmp_path
):
    by_name = emit_three_levels(hook_module, fake_langfuse, fixture_transcript_path, tmp_path)

    root = by_name["lead · Conversational Turn"]
    level_one = by_name["Subagent: planner · Level one"]
    level_two = by_name["Subagent: worker · Level two"]
    level_three = by_name["Subagent: Explore · Level three"]
    assert root.as_type == "agent"
    assert {o.as_type for o in (level_one, level_two, level_three)} == {"agent"}

    assert children_of(fake_langfuse, root) == ["Instructions", "LLM Call", "Tool: Agent", "LLM Call"]
    for parent, child in ((root, level_one), (level_one, level_two), (level_two, level_three)):
        launch = next(
            o for o in fake_langfuse.observations
            if o.name == "Tool: Agent" and o._otel_span.parent is parent._otel_span
        )
        assert launch.as_type == "tool"
        assert child._otel_span.parent is launch._otel_span
        # An agent launch spans its agent's whole run.
        assert launch.end_time >= child.end_time

    assert [level.kwargs["metadata"]["agent_depth"] for level in (level_one, level_two, level_three)] == [1, 2, 3]
    assert level_three.kwargs["metadata"]["agent_id"] == "agent-c"
    assert level_three.kwargs["metadata"]["parent_agent_id"] == "agent-b"
    assert level_three.kwargs["metadata"]["spawn_depth"] == 3
    assert level_three.kwargs["metadata"]["agent_definition"] == {
        "name": "Explore", "source": "builtin", "builtin": True,
    }
    assert level_two.output == {"role": "assistant", "content": "B result."}

    generations_in_c = [
        o for o in fake_langfuse.observations
        if o.as_type == "generation" and o._otel_span.parent is level_three._otel_span
    ]
    assert [g.kwargs["metadata"]["agent_id"] for g in generations_in_c] == ["agent-c"]


def test_background_child_result_inside_an_agent_feeds_its_next_generation(
    hook_module, fake_langfuse, fixture_transcript_path, tmp_path
):
    by_name = emit_three_levels(hook_module, fake_langfuse, fixture_transcript_path, tmp_path)
    level_one = by_name["Subagent: planner · Level one"]
    generations = [
        o for o in fake_langfuse.observations
        if o.as_type == "generation" and o._otel_span.parent is level_one._otel_span
    ]

    assert len(generations) == 2
    final_input = generations[1].kwargs["input"]
    assert final_input[-1] == {
        "role": "tool",
        "tool_results": [{"tool_use_id": "toolu_level_two", "tool_name": "Agent", "output": "B result."}],
    }


def test_trace_name_tags_and_metadata_carry_the_agent_names(
    hook_module, fake_langfuse, fixture_transcript_path, tmp_path, monkeypatch
):
    calls = record_propagation(hook_module, monkeypatch)

    by_name = emit_three_levels(hook_module, fake_langfuse, fixture_transcript_path, tmp_path)

    assert calls[0]["trace_name"] == "lead · Claude Code Turn"
    assert calls[0]["tags"] == [
        "claude-code", "agent:lead", "subagent:planner", "subagent:worker", "subagent:Explore",
    ]
    metadata = by_name["lead · Conversational Turn"].kwargs["metadata"]
    assert metadata["main_agent"] == "lead"
    assert metadata["main_agent_definition"] == {"name": "lead", "source": "unknown", "builtin": False}
    assert [agent["agent_id"] for agent in metadata["subagents"]] == ["agent-a", "agent-b", "agent-c"]
    assert [f["path"] for f in metadata["instruction_files"]] == [
        "/repo/CLAUDE.md", "/repo/.claude/rules/style.md",
    ]


def test_default_main_agent_keeps_the_upstream_names(
    hook_module, fake_langfuse, fixture_transcript_path, tmp_path, monkeypatch
):
    calls = record_propagation(hook_module, monkeypatch)
    transcript = copy_fixture(fixture_transcript_path, tmp_path, "nested_agents_three_levels")
    rows = transcript.read_text(encoding="utf-8").splitlines()
    rows[0] = json.dumps({"type": "agent-setting", "agentSetting": "claude", "sessionId": "session-deep"})
    transcript.write_text("\n".join(rows) + "\n", encoding="utf-8")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-deep", transcript, flush_deferred_agent_turns=True
    )

    assert calls[0]["trace_name"] == "Claude Code Turn"
    assert "agent:claude" in calls[0]["tags"]
    assert "Conversational Turn" in [o.name for o in fake_langfuse.observations]


def test_main_agent_falls_back_to_payload_then_state(
    hook_module, fake_langfuse, fixture_transcript_path, tmp_path, monkeypatch
):
    calls = record_propagation(hook_module, monkeypatch)
    transcript = copy_fixture(fixture_transcript_path, tmp_path, "nested_agents_three_levels")
    rows = transcript.read_text(encoding="utf-8").splitlines()
    transcript.write_text("\n".join(rows[1:3]) + "\n", encoding="utf-8")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-deep", transcript, payload_agent_type="reviewer"
    )
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(rows[3:]) + "\n")
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-deep", transcript, flush_deferred_agent_turns=True
    )

    assert [call["trace_name"] for call in calls] == ["reviewer · Claude Code Turn"]
    state = json.loads(hook_module.STATE_FILE.read_text(encoding="utf-8"))
    assert [entry["main_agent"] for entry in state.values()] == ["reviewer"]


def test_instructions_observations_carry_system_prompt_and_files(
    hook_module, fake_langfuse, fixture_transcript_path, tmp_path
):
    by_name = emit_three_levels(hook_module, fake_langfuse, fixture_transcript_path, tmp_path)
    instructions = [o for o in fake_langfuse.observations if o.name == "Instructions"]
    by_parent = {
        next(p.name for p in fake_langfuse.observations if p._otel_span is o._otel_span.parent): o
        for o in instructions
    }

    root_messages = by_parent["lead · Conversational Turn"].kwargs["input"]
    assert root_messages[0] == {"role": "system", "content": "You are lead.\n\nBe precise."}
    assert root_messages[1]["content"].startswith("Contents of /repo/CLAUDE.md (Project):")
    assert root_messages[2]["content"].endswith("Use short names.")
    root_metadata = by_parent["lead · Conversational Turn"].kwargs["metadata"]
    assert root_metadata["system_prompt"]["cli_prefix"].startswith("You are Claude Code")
    assert root_metadata["tools"] == ["Agent", "Read"]

    assert by_parent["Subagent: planner · Level one"].kwargs["input"] == [
        {"role": "system", "content": "You are the planner."}
    ]
    # Explore wrote no snapshot in this fixture: only its instruction file shows.
    explore_messages = by_parent["Subagent: Explore · Level three"].kwargs["input"]
    assert [m["role"] for m in explore_messages] == ["user"]
    assert "Subagent: worker · Level two" not in by_parent
    assert by_name["Subagent: Explore · Level three"].kwargs["metadata"]["instruction_files"][0]["path"] == "/repo/CLAUDE.md"


def test_capture_flags_turn_instruction_and_system_prompt_capture_off(
    hook_module, fake_langfuse, fixture_transcript_path, tmp_path, monkeypatch
):
    monkeypatch.setattr(hook_module, "CAPTURE_INSTRUCTIONS", False)
    monkeypatch.setattr(hook_module, "CAPTURE_SYSTEM_PROMPT", False)

    by_name = emit_three_levels(hook_module, fake_langfuse, fixture_transcript_path, tmp_path)

    assert "Instructions" not in by_name
    assert "instruction_files" not in by_name["lead · Conversational Turn"].kwargs["metadata"]


def test_hook_records_supplement_the_transcript(
    hook_module, fake_langfuse, fixture_transcript_path, tmp_path, isolated_hook_state
):
    context_file = isolated_hook_state / "langfuse_context" / "session-deep.jsonl"
    context_file.parent.mkdir(parents=True)
    records = [
        {"event": "InstructionsLoaded", "ts": "2025-12-31T23:59:59+00:00", "file_path": "/repo/CLAUDE.md",
         "memory_type": "Project", "load_reason": "session_start", "sha256": "x"},
        {"event": "InstructionsLoaded", "ts": "2025-12-31T23:59:59+00:00", "file_path": "/home/u/.claude/CLAUDE.md",
         "memory_type": "User", "load_reason": "session_start", "content": "Hook-only rule."},
        {"event": "InstructionsLoaded", "ts": "2026-01-01T00:00:03.600+00:00", "agent_id": "agent-c",
         "file_path": "/repo/src/CLAUDE.md", "memory_type": "Project", "load_reason": "nested_traversal",
         "trigger_file_path": "/repo/src/app.py", "content": "Nested rule."},
        {"event": "InstructionsLoaded", "ts": "2027-01-01T00:00:00+00:00", "file_path": "/repo/later.md",
         "memory_type": "Project", "load_reason": "path_glob_match", "content": "Too late."},
    ]
    context_file.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    by_name = emit_three_levels(hook_module, fake_langfuse, fixture_transcript_path, tmp_path)

    root_files = {f["path"]: f for f in by_name["lead · Conversational Turn"].kwargs["metadata"]["instruction_files"]}
    assert root_files["/repo/CLAUDE.md"]["source"] == "transcript+hook"
    assert root_files["/repo/CLAUDE.md"]["load_reason"] == "session_start"
    assert root_files["/home/u/.claude/CLAUDE.md"]["source"] == "hook"
    assert "/repo/later.md" not in root_files
    assert "/repo/src/CLAUDE.md" not in root_files

    explore_files = {
        f["path"]: f for f in by_name["Subagent: Explore · Level three"].kwargs["metadata"]["instruction_files"]
    }
    assert explore_files["/repo/src/CLAUDE.md"]["trigger_file_path"] == "/repo/src/app.py"


def write_agent(subagents_dir: Path, agent_id: str, tool_use_id: str, rows: list[dict[str, Any]]) -> None:
    subagents_dir.mkdir(parents=True, exist_ok=True)
    (subagents_dir / f"agent-{agent_id}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    (subagents_dir / f"agent-{agent_id}.meta.json").write_text(
        json.dumps({"agentType": "general-purpose", "description": agent_id, "toolUseId": tool_use_id}),
        encoding="utf-8",
    )


def agent_rows(agent_id: str, launches: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"type": "user", "timestamp": "2026-01-01T00:00:01.000Z", "agentId": agent_id, "uuid": f"{agent_id}-u",
         "message": {"role": "user", "content": "go"}},
    ]
    if launches:
        rows += [
            {"type": "assistant", "timestamp": "2026-01-01T00:00:02.000Z", "agentId": agent_id,
             "uuid": f"{agent_id}-a1", "message": {"id": f"{agent_id}-m1", "role": "assistant", "model": "m",
             "content": [{"type": "tool_use", "id": launches, "name": "Agent", "input": {"prompt": "go"}}]}},
            {"type": "user", "timestamp": "2026-01-01T00:00:03.000Z", "agentId": agent_id, "uuid": f"{agent_id}-r1",
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": launches,
                                                      "content": "done"}]}},
        ]
    rows.append(
        {"type": "assistant", "timestamp": "2026-01-01T00:00:04.000Z", "agentId": agent_id, "uuid": f"{agent_id}-a2",
         "message": {"id": f"{agent_id}-m2", "role": "assistant", "model": "m",
                     "content": [{"type": "text", "text": f"{agent_id} done"}]}}
    )
    return rows


def emit_launch(hook_module, fake_langfuse, subagents: dict[str, Any], tool_use_id: str) -> None:
    turn = hook_module.build_turns([
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "uuid": "u", "message": {"role": "user", "content": "x"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:00.500Z", "uuid": "a", "message": {
            "id": "m", "role": "assistant", "model": "m",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": "Agent", "input": {}}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:05.000Z", "uuid": "r", "message": {
            "role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}]}},
    ])[0]
    hook_module.emit_turn_observations(
        fake_langfuse, None, turn, hook_module.parse_timestamp(turn.user_msg),
        subagent_transcripts_by_tool_use_id=subagents,
    )


def test_an_agent_cycle_expands_each_agent_once(hook_module, fake_langfuse, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")
    subagents_dir = tmp_path / "session" / "subagents"
    # Corrupt data: x launches y, y's launch id points back at x.
    write_agent(subagents_dir, "x", "toolu_x", agent_rows("x", "toolu_y"))
    write_agent(subagents_dir, "y", "toolu_y", agent_rows("y", "toolu_x"))
    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)

    emit_launch(hook_module, fake_langfuse, subagents, "toolu_x")

    names = [o.name for o in fake_langfuse.observations]
    assert names.count("Subagent: general-purpose · x") == 1
    assert names.count("Subagent: general-purpose · y") == 1
    assert names.count("Tool: Agent") == 3


def test_missing_agent_transcript_keeps_the_tool_span(hook_module, fake_langfuse, tmp_path):
    subagents = {"toolu_gone": {"path": tmp_path / "agent-gone.jsonl", "agent_id": "gone", "agent_type": "Explore"}}

    emit_launch(hook_module, fake_langfuse, subagents, "toolu_gone")

    assert [o.name for o in fake_langfuse.observations] == ["LLM Call", "Tool: Agent"]


def test_depth_limit_stops_expansion(hook_module, fake_langfuse, tmp_path, monkeypatch):
    monkeypatch.setattr(hook_module, "MAX_AGENT_DEPTH", 2)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")
    subagents_dir = tmp_path / "session" / "subagents"
    write_agent(subagents_dir, "one", "toolu_1", agent_rows("one", "toolu_2"))
    write_agent(subagents_dir, "two", "toolu_2", agent_rows("two", "toolu_3"))
    write_agent(subagents_dir, "three", "toolu_3", agent_rows("three", None))
    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)

    emit_launch(hook_module, fake_langfuse, subagents, "toolu_1")

    names = [o.name for o in fake_langfuse.observations]
    assert "Subagent: general-purpose · two" in names
    assert "Subagent: general-purpose · three" not in names


@pytest.mark.parametrize("close_first", [False, True])
def test_an_open_turn_ships_its_agent_tree_once(
    hook_module, fake_langfuse, fixture_transcript_path, tmp_path, close_first
):
    transcript = copy_fixture(fixture_transcript_path, tmp_path, "nested_agents_three_levels")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-deep", transcript)
    if close_first:
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "user", "timestamp": "2026-01-01T00:01:00.000Z", "sessionId": "session-deep",
                "uuid": "user-deep-2", "message": {"role": "user", "content": "Next."},
            }) + "\n")
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-deep", transcript)

    names = [o.name for o in fake_langfuse.observations]
    for name in ("Subagent: planner · Level one", "Subagent: worker · Level two", "Subagent: Explore · Level three"):
        assert names.count(name) == 1
    assert names.count("lead · Conversational Turn") == 1
    assert names.count("Instructions") == 3


def main_row(kind: str, uuid: str, timestamp: str, content: Any, **extra: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"role": kind, "content": content}
    if kind == "assistant":
        message.update({"id": f"msg-{uuid}", "model": "claude-test"})
    return {"type": kind, "uuid": uuid, "timestamp": timestamp, "sessionId": "session-resume",
            "message": message, **extra}


def tool_use(tool_use_id: str, name: str, tool_input: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}]


def tool_result(tool_use_id: str, text: str) -> list[dict[str, Any]]:
    return [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]


def test_a_resumed_agent_run_nests_under_its_send_message_call(hook_module, fake_langfuse, tmp_path):
    transcript = tmp_path / "session-resume.jsonl"
    main_rows = [
        main_row("user", "u1", "2026-01-01T00:00:00.000Z", "Start X"),
        main_row("assistant", "a1", "2026-01-01T00:00:01.000Z", tool_use("toolu_x", "Agent", {"description": "x"})),
        main_row("user", "r1", "2026-01-01T00:00:05.000Z", tool_result("toolu_x", "X first."),
                 toolUseResult={"status": "completed", "agentId": "x"}),
        main_row("assistant", "a2", "2026-01-01T00:00:06.000Z", [{"type": "text", "text": "ok"}]),
        main_row("user", "u2", "2026-01-01T00:01:00.000Z", "Ask X again"),
        main_row("assistant", "a3", "2026-01-01T00:01:01.000Z",
                 tool_use("toolu_send", "SendMessage", {"to": "x", "message": "again"})),
        main_row("user", "r3", "2026-01-01T00:01:01.500Z", tool_result("toolu_send", "Agent resumed."),
                 toolUseResult={"success": True, "resumedAgentId": "x"}),
        main_row("assistant", "a4", "2026-01-01T00:01:02.000Z", [{"type": "text", "text": "waiting"}]),
        main_row("user", "n1", "2026-01-01T00:01:10.000Z",
                 "<task-notification><task-id>x</task-id><tool-use-id>toolu_send</tool-use-id>"
                 "<result>X second.</result></task-notification>",
                 origin={"kind": "task-notification"}),
        main_row("assistant", "a5", "2026-01-01T00:01:11.000Z", [{"type": "text", "text": "X said second"}]),
    ]
    transcript.write_text("\n".join(json.dumps(r) for r in main_rows) + "\n", encoding="utf-8")
    write_agent(tmp_path / "session-resume" / "subagents", "x", "toolu_x", [
        main_row("user", "xu1", "2026-01-01T00:00:01.500Z", "go"),
        main_row("assistant", "xa1", "2026-01-01T00:00:04.000Z", [{"type": "text", "text": "X first."}]),
        main_row("user", "xc", "2026-01-01T00:01:02.000Z", "again", isMeta=True, origin={"kind": "coordinator"}),
        main_row("assistant", "xa2", "2026-01-01T00:01:09.000Z", [{"type": "text", "text": "X second."}]),
    ])
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-resume", transcript, flush_deferred_agent_turns=True
    )

    by_name = {o.name: o for o in fake_langfuse.observations}
    launch_run = by_name["Subagent: general-purpose · x"]
    resumed_run = by_name["Subagent: general-purpose · x (resumed #1)"]
    assert launch_run.output == {"role": "assistant", "content": "X first."}
    assert resumed_run.output == {"role": "assistant", "content": "X second."}
    assert resumed_run.kwargs["input"] == {"role": "user", "content": "again"}
    assert resumed_run.kwargs["metadata"]["resumed_run"] == 1
    assert resumed_run.kwargs["metadata"]["launch_tool_use_id"] == "toolu_x"
    assert resumed_run._otel_span.parent is by_name["Tool: SendMessage"]._otel_span


def write_session(transcript: Path, rows: list[dict[str, Any]]) -> None:
    transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_a_run_that_starts_after_the_firing_read_ships_once_later(
    hook_module, fake_langfuse, tmp_path, monkeypatch
):
    transcript = tmp_path / "session-resume.jsonl"
    first_turn = [
        main_row("user", "u1", "2026-01-01T00:00:00.000Z", "Start X"),
        main_row("assistant", "a1", "2026-01-01T00:00:01.000Z", tool_use("toolu_x", "Agent", {"description": "x"})),
        main_row("user", "r1", "2026-01-01T00:00:05.000Z", tool_result("toolu_x", "X first."),
                 toolUseResult={"status": "completed", "agentId": "x"}),
        main_row("assistant", "a2", "2026-01-01T00:00:06.000Z", [{"type": "text", "text": "ok"}]),
    ]
    second_turn = [
        main_row("user", "u2", "2026-01-01T00:01:00.000Z", "Ask X again"),
        main_row("assistant", "a3", "2026-01-01T00:01:01.000Z",
                 tool_use("toolu_send", "SendMessage", {"to": "x", "message": "again"})),
        main_row("user", "r3", "2026-01-01T00:01:01.500Z", tool_result("toolu_send", "Agent resumed."),
                 toolUseResult={"success": True, "resumedAgentId": "x"}),
        main_row("assistant", "a4", "2026-01-01T00:01:02.000Z", [{"type": "text", "text": "waiting"}]),
        main_row("user", "n1", "2026-01-01T00:01:10.000Z",
                 "<task-notification><task-id>x</task-id><tool-use-id>toolu_send</tool-use-id>"
                 "<result>X second.</result></task-notification>",
                 origin={"kind": "task-notification"}),
        main_row("assistant", "a5", "2026-01-01T00:01:11.000Z", [{"type": "text", "text": "X said second"}]),
    ]
    # The agent transcript already holds the resumed run, but the main
    # transcript the firing read does not show the SendMessage call yet.
    write_agent(tmp_path / "session-resume" / "subagents", "x", "toolu_x", [
        main_row("user", "xu1", "2026-01-01T00:00:01.500Z", "go"),
        main_row("assistant", "xa1", "2026-01-01T00:00:04.000Z", [{"type": "text", "text": "X first."}]),
        main_row("user", "xc", "2026-01-01T00:01:02.000Z", "again", isMeta=True, origin={"kind": "coordinator"}),
        main_row("assistant", "xa2", "2026-01-01T00:01:09.000Z", [{"type": "text", "text": "X second."}]),
    ])
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    snapshot = hook_module.parse_timestamp("2026-01-01T00:00:30.000Z")
    monkeypatch.setattr(hook_module, "firing_snapshot_time", lambda: snapshot)

    write_session(transcript, first_turn)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-resume", transcript)

    launch_run = next(o for o in fake_langfuse.observations if o.name == "Subagent: general-purpose · x")
    assert launch_run.output == {"role": "assistant", "content": "X first."}

    snapshot = hook_module.parse_timestamp("2026-01-01T00:02:00.000Z")
    write_session(transcript, first_turn + second_turn)
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-resume", transcript, flush_deferred_agent_turns=True
    )

    names = [o.name for o in fake_langfuse.observations]
    assert names.count("Subagent: general-purpose · x") == 1
    assert names.count("Subagent: general-purpose · x (resumed #1)") == 1
    x_generations = [
        o for o in fake_langfuse.observations
        if o.as_type == "generation" and o.kwargs["metadata"].get("agent_id") == "x"
    ]
    assert len(x_generations) == 2


def test_an_agent_resumed_by_a_later_turns_agent_ships_each_run_once(hook_module, fake_langfuse, tmp_path):
    transcript = tmp_path / "session-resume.jsonl"
    write_session(transcript, [
        main_row("user", "u1", "2026-01-01T00:00:00.000Z", "Start B"),
        main_row("assistant", "a1", "2026-01-01T00:00:01.000Z", tool_use("toolu_b", "Agent", {"description": "b"})),
        main_row("user", "r1", "2026-01-01T00:00:05.000Z", tool_result("toolu_b", "B first."),
                 toolUseResult={"status": "completed", "agentId": "b"}),
        main_row("assistant", "a2", "2026-01-01T00:00:06.000Z", [{"type": "text", "text": "ok"}]),
        main_row("user", "u2", "2026-01-01T00:01:00.000Z", "Start A"),
        main_row("assistant", "a3", "2026-01-01T00:01:01.000Z", tool_use("toolu_a", "Agent", {"description": "a"})),
        main_row("user", "r3", "2026-01-01T00:01:20.000Z", tool_result("toolu_a", "A done."),
                 toolUseResult={"status": "completed", "agentId": "a"}),
        main_row("assistant", "a4", "2026-01-01T00:01:21.000Z", [{"type": "text", "text": "done"}]),
    ])
    subagents_dir = tmp_path / "session-resume" / "subagents"
    write_agent(subagents_dir, "b", "toolu_b", [
        main_row("user", "bu1", "2026-01-01T00:00:01.500Z", "go"),
        main_row("assistant", "ba1", "2026-01-01T00:00:04.000Z", [{"type": "text", "text": "B first."}]),
        main_row("user", "bc", "2026-01-01T00:01:03.000Z", "again", isMeta=True, origin={"kind": "coordinator"}),
        main_row("assistant", "ba2", "2026-01-01T00:01:09.000Z", [{"type": "text", "text": "B second."}]),
    ])
    write_agent(subagents_dir, "a", "toolu_a", [
        main_row("user", "au1", "2026-01-01T00:01:01.500Z", "go"),
        main_row("assistant", "aa1", "2026-01-01T00:01:02.000Z",
                 tool_use("toolu_a_send", "SendMessage", {"to": "b", "message": "again"})),
        main_row("user", "ar1", "2026-01-01T00:01:02.500Z", tool_result("toolu_a_send", "Agent resumed."),
                 toolUseResult={"success": True, "resumedAgentId": "b"}),
        main_row("user", "an1", "2026-01-01T00:01:10.000Z",
                 "<task-notification><task-id>b</task-id><tool-use-id>toolu_a_send</tool-use-id>"
                 "<result>B second.</result></task-notification>",
                 origin={"kind": "task-notification"}),
        main_row("assistant", "aa2", "2026-01-01T00:01:19.000Z", [{"type": "text", "text": "A done."}]),
    ])
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-resume", transcript, flush_deferred_agent_turns=True
    )

    names = [o.name for o in fake_langfuse.observations]
    assert names.count("Subagent: general-purpose · b") == 1
    assert names.count("Subagent: general-purpose · b (resumed #1)") == 1
    launch_run = next(o for o in fake_langfuse.observations if o.name == "Subagent: general-purpose · b")
    assert launch_run.output == {"role": "assistant", "content": "B first."}
    b_generations = [
        o for o in fake_langfuse.observations
        if o.as_type == "generation" and o.kwargs["metadata"].get("agent_id") == "b"
    ]
    assert len(b_generations) == 2

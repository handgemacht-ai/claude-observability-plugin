from __future__ import annotations

import json

import pytest

# tests/fixtures/transcripts/api_request_rows/transcript.jsonl has the row
# shapes that Claude Code 2.1.310 and 2.1.314 write: next to the chat rows,
# api-request-shape, api-request-blob (message copies in role user, assistant
# and system, without uuid or timestamp) and api-request rows. The text is made up.
CHAT_TYPES = ("user", "assistant")
LOG_TYPES = ("api-request", "api-request-blob", "api-request-shape")


@pytest.fixture
def rows(fixture_transcript_path, read_fixture_jsonl):
    return read_fixture_jsonl(fixture_transcript_path("api_request_rows"))


def without_log_rows(rows):
    return [row for row in rows if row["type"] in CHAT_TYPES]


def test_the_fixture_holds_every_log_row_type_in_every_blob_role(rows):
    assert {row["type"] for row in rows} == set(CHAT_TYPES + LOG_TYPES)
    blob_roles = {row["message"]["role"] for row in rows if row["type"] == "api-request-blob"}
    assert blob_roles == {"user", "assistant", "system"}
    assert not any("uuid" in row or "timestamp" in row for row in rows if row["type"] == "api-request-blob")


@pytest.mark.parametrize("row_type", ["api-request-blob", "api-request", "api-request-shape", "api-request-future-kind"])
@pytest.mark.parametrize("role", ["user", "assistant", "system"])
def test_a_log_row_is_no_chat_row_whatever_its_message_role(hook_module, row_type, role):
    row = {"type": row_type, "message": {"role": role, "content": [{"type": "text", "text": "Copy."}]}}

    assert hook_module.get_user_or_assistant_role_from_row(row) is None
    assert not hook_module.is_tool_result(
        {"type": row_type, "message": {"role": role, "content": [{"type": "tool_result", "tool_use_id": "t"}]}}
    )


@pytest.mark.parametrize("row_type", ["user", "assistant"])
def test_a_user_or_assistant_row_has_the_role_of_its_type(hook_module, row_type):
    assert hook_module.get_user_or_assistant_role_from_row({"type": row_type}) == row_type


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_a_row_without_a_type_takes_the_role_of_its_message(hook_module, role):
    assert hook_module.get_user_or_assistant_role_from_row({"message": {"role": role, "content": "x"}}) == role
    assert hook_module.get_user_or_assistant_role_from_row({"type": None, "message": {"role": role}}) == role
    assert hook_module.get_user_or_assistant_role_from_row({"type": "", "message": {"role": role}}) == role
    assert hook_module.get_user_or_assistant_role_from_row({"message": {"role": "system"}}) is None


def test_turns_start_only_at_the_real_user_rows(hook_module, rows):
    turns = hook_module.build_turns(rows)

    assert [turn.user_msg["uuid"] for turn in turns] == ["user-1", "user-2"]
    assert [hook_module.extract_text_from_content(hook_module.get_content_from_row(t.user_msg)) for t in turns] == [
        "First question.",
        "Second question.",
    ]


def test_assistant_copies_in_blob_rows_add_no_step(hook_module, rows):
    turns = hook_module.build_turns(rows)

    steps = [[hook_module.get_message_id(row) for row in turn.assistant_msgs] for turn in turns]
    assert steps == [["msg_a", "msg_b"], ["msg_c", "msg_d"]]
    assert [list(turn.tool_use_timestamps_by_id) for turn in turns] == [["toolu_a1", "toolu_a2"], ["toolu_c1"]]


def test_tool_results_keep_the_time_of_their_own_row(hook_module, rows):
    turns = hook_module.build_turns(rows)

    results = {tool_use_id: result for turn in turns for tool_use_id, result in turn.tool_results_by_id.items()}
    assert set(results) == {"toolu_a1", "toolu_a2", "toolu_c1"}
    assert all(result["timestamp"] is not None for result in results.values())
    own_rows = {
        block["tool_use_id"]: row["timestamp"]
        for row in rows
        if row["type"] == "user"
        for block in (row["message"]["content"] if isinstance(row["message"]["content"], list) else [])
        if block["type"] == "tool_result"
    }
    assert {tool_use_id: result["timestamp"] for tool_use_id, result in results.items()} == own_rows


def test_the_turns_are_those_of_the_log_without_its_log_rows(hook_module, rows):
    assert hook_module.build_turns(rows) == hook_module.build_turns(without_log_rows(rows))


def test_no_part_of_the_log_gives_a_turn_without_a_user_row_uuid(hook_module, rows):
    for cut in range(1, len(rows) + 1):
        closed, trailing, _ = hook_module.assemble_turns(rows[:cut])

        turns = closed + ([trailing] if trailing else [])
        assert all(isinstance(turn.user_msg.get("uuid"), str) for turn in turns), f"first {cut} rows"


def test_a_log_of_only_log_rows_has_no_turn(hook_module, rows):
    log_rows = [row for row in rows if row["type"] in LOG_TYPES]

    assert hook_module.assemble_turns(log_rows) == ([], None, [])


def test_the_log_rows_stay_in_the_rows_that_are_read(hook_module, rows, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    state = hook_module.SessionState()

    read_rows, _ = hook_module.read_new_jsonl(transcript, state)

    assert [row["type"] for row in read_rows] == [row["type"] for row in rows]


# An agent that a forked skill starts has no typed prompt: its transcript opens
# with the skill text as an isMeta user row, then attachments, and the model's
# first request goes out with api-request-blob copies of that text. Before the
# blob rows were ignored, the first user copy gave such an agent its turn.
def forked_skill_agent_rows():
    prompt = "Review target: origin/main..HEAD. Report findings."
    common = {"isSidechain": True, "agentId": "a0", "sessionId": "5e55a1b2-0000-4000-8000-0000000000c0"}
    return [
        {"type": "user", "isMeta": True, "uuid": "skill-prompt", "promptId": "p", "timestamp": "2026-11-02T09:00:00.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}, **common},
        {"type": "attachment", "uuid": "attachment-1", "timestamp": "2026-11-02T09:00:00.100Z",
         "attachment": {"type": "date_change", "newDate": "2026-11-02"}, **common},
        {"type": "api-request-blob", "hash": "h1", "sessionId": common["sessionId"],
         "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}},
        {"type": "api-request", "id": "req-1", "timestamp": "2026-11-02T09:00:00.200Z", "tail": ["h1"], "keep": 0,
         "sessionId": common["sessionId"]},
        {"type": "assistant", "uuid": "reply-1", "timestamp": "2026-11-02T09:00:01.000Z", "requestRef": "req-1",
         "message": {"id": "msg_agent_1", "role": "assistant", "model": "claude-test", "content": [
             {"type": "tool_use", "id": "toolu_agent_1", "name": "Read", "input": {"file_path": "a.txt"}}]}, **common},
        {"type": "user", "uuid": "result-1", "timestamp": "2026-11-02T09:00:02.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_agent_1", "content": "Contents."}]}, **common},
        {"type": "api-request-blob", "hash": "h2", "sessionId": common["sessionId"],
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_agent_1", "name": "Read", "input": {"file_path": "a.txt"}}]}},
        {"type": "api-request", "id": "req-2", "timestamp": "2026-11-02T09:00:02.100Z", "tail": ["h2"], "keep": 1,
         "base": "req-1", "sessionId": common["sessionId"]},
        {"type": "assistant", "uuid": "reply-2", "timestamp": "2026-11-02T09:00:03.000Z", "requestRef": "req-2",
         "message": {"id": "msg_agent_2", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "text", "text": "No findings."}]}, **common},
    ]


def test_an_agent_that_opens_with_an_injected_prompt_starts_its_turn_there(hook_module):
    rows = forked_skill_agent_rows()

    run_rows, run_index, run_end = hook_module.select_agent_run_rows({}, rows)

    assert (run_index, run_end) == (0, len(rows))
    assert "isMeta" not in run_rows[0] and rows[0]["isMeta"] is True
    [turn] = hook_module.build_turns(run_rows)
    assert turn.user_msg["uuid"] == "skill-prompt"
    assert [hook_module.get_message_id(row) for row in turn.assistant_msgs] == ["msg_agent_1", "msg_agent_2"]


def test_an_agent_that_opens_with_a_typed_prompt_is_cut_as_it_was(hook_module):
    rows = forked_skill_agent_rows()
    del rows[0]["isMeta"]

    run_rows, _, _ = hook_module.select_agent_run_rows({}, rows)

    assert run_rows == rows


def test_an_agent_that_opens_with_an_injected_prompt_is_emitted_with_its_steps(hook_module, fake_langfuse, tmp_path):
    path = tmp_path / "agent-a0.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in forked_skill_agent_rows()) + "\n", encoding="utf-8")

    hook_module.emit_subagent_observations(
        fake_langfuse, None, {"path": path, "description": "Review", "agent_type": "general-purpose"}, None
    )

    assert [o.name for o in fake_langfuse.observations] == [
        "Subagent: general-purpose · Review",
        "Subagent LLM Call",
        "Tool: Read",
        "Subagent LLM Call",
    ]
    agent = fake_langfuse.observations[0]
    assert agent.kwargs["input"] == {"role": "user", "content": "Review target: origin/main..HEAD. Report findings."}

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SESSION = "session-boundary"


def user_row(uuid: str, second: int, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-01-01T00:00:{second:02d}.000Z",
        "sessionId": SESSION,
        "uuid": uuid,
        "message": {"role": "user", "content": text},
    }


def assistant_row(uuid: str, second: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": f"2026-01-01T00:00:{second:02d}.000Z",
        "sessionId": SESSION,
        "uuid": uuid,
        "message": {"id": f"msg-{uuid}", "role": "assistant", "model": "claude-test", "content": blocks},
    }


def tool_result_row(uuid: str, second: int, tool_use_id: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-01-01T00:00:{second:02d}.000Z",
        "sessionId": SESSION,
        "uuid": uuid,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}],
        },
    }


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def tool_use(tool_use_id: str, name: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}


def turn_user_texts(hook_module: Any, rows: list[dict[str, Any]]) -> list[str]:
    return [
        hook_module.extract_text_from_content(hook_module.get_content_from_row(turn.user_msg))
        for turn in hook_module.build_turns(rows)
    ]


def test_yes_after_a_go_question_continues_the_turn(hook_module):
    rows = [
        user_row("u1", 0, "Build the feature."),
        assistant_row("a1", 1, [text("The plan is ready. Go?")]),
        user_row("u2", 2, "yes"),
        assistant_row("a2", 3, [text("Built.")]),
    ]

    turns = hook_module.build_turns(rows)

    assert turn_user_texts(hook_module, rows) == ["Build the feature."]
    assert [row["uuid"] for _, row in turns[0].follow_ups] == ["u2"]
    assert len(turns[0].assistant_msgs) == 2


def test_reply_after_an_ask_user_question_continues_the_turn(hook_module):
    rows = [
        user_row("u1", 0, "Pick a colour."),
        assistant_row("a1", 1, [tool_use("toolu_ask", "AskUserQuestion")]),
        user_row("u2", 2, "blue"),
        assistant_row("a2", 3, [text("Blue it is.")]),
    ]

    assert turn_user_texts(hook_module, rows) == ["Pick a colour."]


def test_new_message_after_a_plain_stop_starts_a_new_turn(hook_module):
    rows = [
        user_row("u1", 0, "Build the feature."),
        assistant_row("a1", 1, [text("Built.")]),
        user_row("u2", 2, "Now fix the docs."),
        assistant_row("a2", 3, [text("Fixed.")]),
    ]

    assert turn_user_texts(hook_module, rows) == ["Build the feature.", "Now fix the docs."]


def test_message_typed_while_the_agent_works_starts_a_new_turn(hook_module):
    rows = [
        user_row("u1", 0, "Build the feature."),
        assistant_row("a1", 1, [text("Checking the file?"), tool_use("toolu_bash", "Bash")]),
        user_row("u2", 2, "Also add a test."),
        assistant_row("a2", 3, [text("Added.")]),
    ]

    assert turn_user_texts(hook_module, rows) == ["Build the feature.", "Also add a test."]


def test_question_in_the_middle_of_a_message_is_not_a_question_stop(hook_module):
    rows = [
        user_row("u1", 0, "Build the feature."),
        assistant_row("a1", 1, [text("Should I use a table? I used a table. Done.")]),
        user_row("u2", 2, "Thanks."),
        assistant_row("a2", 3, [text("Welcome.")]),
    ]

    assert turn_user_texts(hook_module, rows) == ["Build the feature.", "Thanks."]


def test_question_closed_by_markdown_still_counts(hook_module):
    rows = [
        user_row("u1", 0, "Build the feature."),
        assistant_row("a1", 1, [text("Ready to go on? **Go?**")]),
        user_row("u2", 2, "yes"),
        assistant_row("a2", 3, [text("Built.")]),
    ]

    assert turn_user_texts(hook_module, rows) == ["Build the feature."]


def test_question_stop_after_tool_work_then_plain_stop_then_message(hook_module):
    rows = [
        user_row("u1", 0, "Build the feature."),
        assistant_row("a1", 1, [tool_use("toolu_read", "Read")]),
        tool_result_row("r1", 2, "toolu_read"),
        assistant_row("a2", 3, [text("Go?")]),
        user_row("u2", 4, "yes"),
        assistant_row("a3", 5, [text("Built.")]),
        user_row("u3", 6, "Next topic."),
        assistant_row("a4", 7, [text("Sure.")]),
    ]

    assert turn_user_texts(hook_module, rows) == ["Build the feature.", "Next topic."]


def test_reply_shows_in_the_history_before_the_message_that_answers_it(hook_module):
    rows = [
        user_row("u1", 0, "Build the feature."),
        assistant_row("a1", 1, [text("Go?")]),
        user_row("u2", 2, "yes"),
        assistant_row("a2", 3, [text("Built.")]),
    ]

    history = hook_module.build_session_history(Path("unused"), rows=rows)

    assert [message["role"] for message in history.messages] == ["user", "assistant", "user", "assistant"]
    assert [message["content"] for message in history.messages if message["role"] == "user"] == [
        "Build the feature.",
        "yes",
    ]


def write_transcript(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def named(fake_langfuse: Any, name: str) -> list[Any]:
    return [observation for observation in fake_langfuse.observations if observation.name == name]


def test_one_root_and_one_trace_for_a_question_stop_and_its_reply(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    first_stop = [
        user_row("u1", 0, "Build the feature."),
        assistant_row("a1", 1, [text("Go?")]),
    ]
    write_transcript(transcript, first_stop)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)

    write_transcript(transcript, first_stop + [user_row("u2", 2, "yes"), assistant_row("a2", 3, [text("Built.")])])
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript, flush_deferred_agent_turns=True)

    assert len(named(fake_langfuse, "Conversational Turn")) == 1
    assert len(named(fake_langfuse, "LLM Call")) == 2

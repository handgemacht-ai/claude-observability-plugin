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


def sent_roots(fake_langfuse: Any) -> list[Any]:
    return [root for root in named(fake_langfuse, "Conversational Turn") if root.end_time is not None]


def hook_firing(hook_module: Any, fake_langfuse: Any, transcript: Path, rows: list[dict[str, Any]], **options: Any) -> None:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    write_transcript(transcript, rows)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript, **options)


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

    assert len(sent_roots(fake_langfuse)) == 1
    assert len(named(fake_langfuse, "LLM Call")) == 2


def test_root_is_sent_with_the_final_answer_and_the_last_stop_after_a_question_stop(
    hook_module, fake_langfuse, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    question_stop = [user_row("u1", 0, "Build the feature."), assistant_row("a1", 1, [text("Go?")])]
    final_answer = assistant_row("a2", 3, [text("Built.")])

    hook_firing(hook_module, fake_langfuse, transcript, question_stop)
    sent_after_question = sent_roots(fake_langfuse)
    hook_firing(hook_module, fake_langfuse, transcript, question_stop + [user_row("u2", 2, "yes"), final_answer])

    [root] = sent_roots(fake_langfuse)
    assert sent_after_question == []
    assert root.output == {"role": "assistant", "content": "Built."}
    assert root.end_time == hook_module.to_otel_nanoseconds(hook_module.parse_timestamp(final_answer))


def test_sent_root_keeps_the_id_and_trace_the_children_point_at(hook_module, fake_langfuse, tmp_path):
    transcript = tmp_path / f"{SESSION}.jsonl"
    question_stop = [user_row("u1", 0, "Build the feature."), assistant_row("a1", 1, [text("Go?")])]

    hook_firing(hook_module, fake_langfuse, transcript, question_stop)
    [held_root] = named(fake_langfuse, "Conversational Turn")
    hook_firing(
        hook_module, fake_langfuse, transcript,
        question_stop + [user_row("u2", 2, "yes"), assistant_row("a2", 3, [text("Built.")])],
    )

    [root] = sent_roots(fake_langfuse)
    assert (root.id, root.trace_id) == (held_root.id, held_root.trace_id)


def test_unanswered_question_sends_the_root_when_the_session_ends(hook_module, fake_langfuse, tmp_path):
    transcript = tmp_path / f"{SESSION}.jsonl"
    question_stop = [user_row("u1", 0, "Build the feature."), assistant_row("a1", 1, [text("Go?")])]

    hook_firing(hook_module, fake_langfuse, transcript, question_stop)
    hook_firing(hook_module, fake_langfuse, transcript, question_stop, flush_deferred_agent_turns=True)

    [root] = sent_roots(fake_langfuse)
    assert root.output == {"role": "assistant", "content": "Go?"}


def test_second_process_without_the_first_one_s_state_does_not_send_a_second_root(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    rows = [user_row("u1", 0, "Build the feature."), assistant_row("a1", 1, [text("Built.")])]

    hook_firing(hook_module, fake_langfuse, transcript, rows)
    (isolated_hook_state / "langfuse_state.json").unlink()
    hook_firing(hook_module, fake_langfuse, transcript, rows)

    assert len(sent_roots(fake_langfuse)) == 1


def test_root_goes_out_when_the_claim_cannot_be_written(hook_module, fake_langfuse, tmp_path, monkeypatch):
    claim_dir_that_is_a_file = tmp_path / "not-a-directory"
    claim_dir_that_is_a_file.write_text("x", encoding="utf-8")
    monkeypatch.setattr(hook_module, "ROOT_CLAIM_DIR", claim_dir_that_is_a_file)
    transcript = tmp_path / f"{SESSION}.jsonl"

    hook_firing(hook_module, fake_langfuse, transcript, [user_row("u1", 0, "Hi."), assistant_row("a1", 1, [text("Hello.")])])

    assert len(sent_roots(fake_langfuse)) == 1


def background_launch_rows() -> list[dict[str, Any]]:
    launch = tool_result_row("r1", 3, "toolu_helper")
    launch["toolUseResult"] = {"isAsync": True, "status": "async_launched", "agentId": "helper-1"}
    return [
        user_row("u1", 0, "Build the feature."),
        assistant_row("a1", 1, [text("Plan: a helper builds it. Go?")]),
        user_row("u2", 2, "yes"),
        assistant_row("a2", 3, [tool_use("toolu_helper", "Agent")]),
        launch,
        assistant_row("a3", 4, [text("I have started the helper.")]),
    ]


def helper_finished_rows() -> list[dict[str, Any]]:
    notification = user_row(
        "n1", 10,
        "<task-notification>\n<task-id>helper-1</task-id>\n<tool-use-id>toolu_helper</tool-use-id>\n"
        "<status>completed</status>\n<result>done</result>\n</task-notification>",
    )
    return [notification, assistant_row("a4", 12, [text("The feature is built.")])]


def test_root_waits_for_the_background_helper_and_gets_the_final_answer(hook_module, fake_langfuse, tmp_path):
    transcript = tmp_path / f"{SESSION}.jsonl"
    started = background_launch_rows()
    finished = started + helper_finished_rows()

    hook_firing(hook_module, fake_langfuse, transcript, started[:2])
    hook_firing(hook_module, fake_langfuse, transcript, started)
    sent_while_helper_runs = sent_roots(fake_langfuse)
    hook_firing(hook_module, fake_langfuse, transcript, finished)

    [root] = sent_roots(fake_langfuse)
    assert sent_while_helper_runs == []
    assert root.output == {"role": "assistant", "content": "The feature is built."}
    assert root.end_time == hook_module.to_otel_nanoseconds(hook_module.parse_timestamp(finished[-1]))


def real_run_on_disk_at_a_stop(fixture: Path, project: Path, session: str, main_rows: int, until: str) -> Path:
    rows = (fixture / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    transcript = project / f"{session}.jsonl"
    transcript.write_text("\n".join(rows[:main_rows]) + "\n", encoding="utf-8")
    subagents = project / session / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    for source in (fixture / "transcript" / "subagents").iterdir():
        lines = source.read_text(encoding="utf-8").splitlines()
        if source.suffix == ".jsonl":
            lines = [line for line in lines if json.loads(line).get("timestamp", "") <= until]
        (subagents / source.name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript


def test_root_waits_for_the_answer_to_a_helper_notification_not_yet_on_disk(
    hook_module, fake_langfuse, tmp_path, fixture_transcript_path
):
    fixture = fixture_transcript_path("background_helper_answer_late_on_disk").parent
    session = "c4dda0fb-0b51-4948-8978-29eb1c492323"
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    rows_on_disk_at_each_stop = [
        (3, "2026-10-09T13:47:18.6"),
        (10, "2026-10-09T13:47:34.0"),
        (15, "2026-10-09T13:47:52.0"),
        (21, "2026-10-09T13:48:04.7"),
    ]

    for main_rows, until in rows_on_disk_at_each_stop:
        transcript = real_run_on_disk_at_a_stop(fixture, tmp_path, session, main_rows, until)
        hook_module.emit_new_turns_from_transcript(fake_langfuse, config, session, transcript)

    [root] = sent_roots(fake_langfuse)
    last_answer = json.loads((fixture / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[20])
    assert root.output["content"].startswith("Done: `shout` stays as one line")
    assert root.end_time == hook_module.to_otel_nanoseconds(hook_module.parse_timestamp(last_answer))

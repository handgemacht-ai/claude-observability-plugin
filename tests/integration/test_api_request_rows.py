from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

# The transcript has the row shapes of Claude Code 2.1.310 and 2.1.314, which
# writes api-request-shape, api-request-blob and api-request rows next to the
# chat rows (see tests/unit/test_non_chat_rows.py). Two requests: the first has
# two parallel tool calls and an answer, the second one tool call and an answer.
SESSION = "5e55a1b2-0000-4000-8000-0000000000c0"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "transcripts" / "api_request_rows" / "transcript.jsonl"
USER_ROW_UUIDS = ["user-1", "user-2"]
MESSAGE_IDS = {"msg_a": 0, "msg_b": 0, "msg_c": 1, "msg_d": 1}
TOOL_USE_IDS = {"toolu_a1": 0, "toolu_a2": 0, "toolu_c1": 1}


def trace_id_of(user_row_uuid: str) -> str:
    return hashlib.sha256(f"{SESSION}:{user_row_uuid}".encode("utf-8")).hexdigest()[:32]


def span_id_of(trace_id: str, step_id: str) -> str:
    return hashlib.sha256(f"{trace_id}:{step_id}".encode("utf-8")).hexdigest()[:16]


def write_transcript(tmp_path: Path, keep_log_rows: bool = True) -> Path:
    transcript = tmp_path / f"{SESSION}.jsonl"
    if keep_log_rows:
        shutil.copy(FIXTURE, transcript)
        return transcript
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    kept = [row for row in rows if row["type"] in ("user", "assistant")]
    transcript.write_text("\n".join(json.dumps(row) for row in kept) + "\n", encoding="utf-8")
    return transcript


def emit_session(hook_module: Any, fake_langfuse: Any, transcript: Path) -> None:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, SESSION, transcript, flush_deferred_agent_turns=True
    )


def roots_of(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]


def generations_of(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.as_type == "generation"]


def tools_of(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.as_type == "tool"]


def test_one_trace_per_real_user_row_with_the_trace_id_of_its_uuid(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    emit_session(hook_module, fake_langfuse, write_transcript(tmp_path))

    assert [root.trace_id for root in roots_of(fake_langfuse)] == [trace_id_of(u) for u in USER_ROW_UUIDS]
    assert {o.trace_id for o in fake_langfuse.observations} == {trace_id_of(u) for u in USER_ROW_UUIDS}


def test_steps_carry_the_span_ids_of_their_trace_and_their_ids(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    emit_session(hook_module, fake_langfuse, write_transcript(tmp_path))

    traces = [trace_id_of(u) for u in USER_ROW_UUIDS]
    assert sorted(o.id for o in generations_of(fake_langfuse)) == sorted(
        span_id_of(traces[turn], message_id) for message_id, turn in MESSAGE_IDS.items()
    )
    assert sorted(o.id for o in tools_of(fake_langfuse)) == sorted(
        span_id_of(traces[turn], tool_use_id) for tool_use_id, turn in TOOL_USE_IDS.items()
    )
    assert sorted(o.id for o in roots_of(fake_langfuse)) == sorted(span_id_of(trace, "root") for trace in traces)


def test_no_step_is_counted_twice_for_its_copy_in_a_blob_row(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    emit_session(hook_module, fake_langfuse, write_transcript(tmp_path))

    assert len(roots_of(fake_langfuse)) == 2
    assert len(generations_of(fake_langfuse)) == len(MESSAGE_IDS)
    assert len(tools_of(fake_langfuse)) == len(TOOL_USE_IDS)
    ids = [o.id for o in fake_langfuse.observations]
    assert len(ids) == len(set(ids))


def test_a_session_is_sent_the_same_in_two_firings(hook_module, fake_langfuse, isolated_hook_state, tmp_path):
    transcript = tmp_path / f"{SESSION}.jsonl"
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    first_prompt_row_count = next(i for i, line in enumerate(lines) if '"uuid":"user-2"' in line)
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    transcript.write_text("\n".join(lines[: first_prompt_row_count + 2]) + "\n", encoding="utf-8")
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, SESSION, transcript, flush_deferred_agent_turns=True
    )

    traces = [trace_id_of(u) for u in USER_ROW_UUIDS]
    assert [root.trace_id for root in roots_of(fake_langfuse)] == traces
    assert len(generations_of(fake_langfuse)) == len(MESSAGE_IDS)
    assert len(tools_of(fake_langfuse)) == len(TOOL_USE_IDS)


def test_an_input_rebuilt_from_the_transcript_holds_no_copy_from_a_blob_row(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    emit_session(hook_module, fake_langfuse, write_transcript(tmp_path))

    calls = generations_of(fake_langfuse)
    assert [call.kwargs["metadata"]["history"] for call in calls] == [
        {"messages": 1},
        {"messages": 3},
        {"messages": 5},
        {"messages": 7},
    ]
    assert calls[0].kwargs["input"] == [{"role": "user", "content": "First question."}]
    assert "Hook note." not in json.dumps([call.kwargs["input"] for call in calls])


QUESTION_1 = {"role": "user", "content": [
    {"type": "text", "text": "First question."},
    {"type": "text", "text": "<system-reminder>Reminder text.</system-reminder>"},
]}
RESULTS_1 = {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "toolu_a1", "content": "Contents of a."},
    {"type": "tool_result", "tool_use_id": "toolu_a2", "content": "Contents of b."},
]}
HOOK_NOTE = {"role": "system", "content": [{"type": "text", "text": "Hook note."}]}
QUESTION_2 = {"role": "user", "content": [
    {"type": "text", "text": "Second question."},
    {"type": "text", "text": "<system-reminder>Reminder text.</system-reminder>"},
]}
RESULT_2 = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_c1", "content": "Match line."}]}
CALLS = [
    ("msg_a", None, [QUESTION_1]),
    ("msg_b", "msg_a", [RESULTS_1, HOOK_NOTE]),
    ("msg_c", "msg_b", [QUESTION_2]),
    ("msg_d", "msg_c", [RESULT_2]),
]


def save_calls(directory: Path) -> None:
    """Save the four calls the way Claude Code does with OTEL_LOG_RAW_API_BODIES=file:<dir>."""
    directory.mkdir(parents=True, exist_ok=True)
    for message_id, previous_message_id, messages in CALLS:
        thread = ({"type": "continue", "previous_message_id": previous_message_id}
                  if previous_message_id else {"type": "create"})
        request_file = f"{message_id}.request.json"
        response_file = f"{message_id}.response.json"
        (directory / request_file).write_text(json.dumps({"thread": thread, "messages": messages}), encoding="utf-8")
        (directory / response_file).write_text(json.dumps({"id": message_id, "content": []}), encoding="utf-8")
        entry = {"session_id": SESSION, "message_id": message_id,
                 "request_file": request_file, "response_file": response_file}
        with (directory / "index.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


@pytest.mark.parametrize("keep_log_rows", [True, False], ids=["with_log_rows", "without_log_rows"])
def test_inputs_from_saved_api_requests_do_not_depend_on_the_log_rows(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch, keep_log_rows
):
    saved = tmp_path / "saved"
    monkeypatch.setenv("CC_LANGFUSE_API_REQUESTS_DIR", str(saved))
    save_calls(saved)
    transcript = write_transcript(tmp_path, keep_log_rows)

    emit_session(hook_module, fake_langfuse, transcript)

    calls = generations_of(fake_langfuse)
    assert [call.kwargs["input"] for call in calls] == [messages for _, _, messages in CALLS]
    assert [call.kwargs["metadata"]["history"] for call in calls] == [
        {"messages": 1, "source": "saved_api_request", "total_messages": 1},
        {"messages": 2, "source": "saved_api_request", "previous_message_id": "msg_a", "total_messages": 4,
         "previous_observation_id": calls[0].id},
        {"messages": 1, "source": "saved_api_request", "previous_message_id": "msg_b", "total_messages": 6},
        {"messages": 1, "source": "saved_api_request", "previous_message_id": "msg_c", "total_messages": 8,
         "previous_observation_id": calls[2].id},
    ]

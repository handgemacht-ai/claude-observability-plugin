from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SESSION = "dddddddd-4444-4444-4444-444444444444"
HOOK_NOTE = {
    "role": "system",
    "content": [{"type": "text", "text": "PostToolUse:Read hook additional context: explain the read"}],
}


def user_row(index: int, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-10-18T14:{index:02d}:00.000Z",
        "sessionId": SESSION,
        "uuid": f"user-row-{index}",
        "message": {"role": "user", "content": text},
    }


def assistant_row(index: int, part: int, content: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": f"2026-10-18T14:{index:02d}:{10 + part}.000Z",
        "sessionId": SESSION,
        "uuid": f"assistant-row-{index}-{part}",
        "message": {
            "id": f"msg-{index}-{part}",
            "role": "assistant",
            "model": "claude-test",
            "content": content,
            "usage": {"input_tokens": 2, "output_tokens": 5},
        },
    }


def tool_result_row(index: int, tool_use_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-10-18T14:{index:02d}:20.000Z",
        "sessionId": SESSION,
        "uuid": f"tool-result-{tool_use_id}",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": text},
        ]},
    }


TOOL_USE = [{"type": "tool_use", "id": "toolu-1", "name": "Read", "input": {"file_path": "README.md"}}]
ANSWER = [{"type": "text", "text": "Answer 1."}]


def write_transcript(path: Path) -> None:
    rows = [
        user_row(1, "Question 1?"),
        assistant_row(1, 0, TOOL_USE),
        tool_result_row(1, "toolu-1", "file content"),
        assistant_row(1, 1, ANSWER),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def save_call(directory: Path, message_id: str, messages: list[Any], content: Any,
              previous_message_id: str | None = None, timestamp: str = "2026-10-18T14:01:10.000Z") -> None:
    """Save one call the way Claude Code does with OTEL_LOG_RAW_API_BODIES=file:<dir>."""
    directory.mkdir(parents=True, exist_ok=True)
    thread = ({"type": "continue", "previous_message_id": previous_message_id}
              if previous_message_id else {"type": "create"})
    request_file = f"request-of-{message_id}.request.json"
    response_file = f"req-of-{message_id}.response.json"
    (directory / request_file).write_text(json.dumps({"thread": thread, "messages": messages}), encoding="utf-8")
    (directory / response_file).write_text(json.dumps({"id": message_id, "content": content}), encoding="utf-8")
    entry = {"timestamp": timestamp, "session_id": SESSION, "message_id": message_id,
             "request_file": request_file, "response_file": response_file}
    with (directory / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def emit(hook_module: Any, fake_langfuse: Any, transcript: Path) -> list[Any]:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)
    return [o.kwargs for o in fake_langfuse.observations if o.as_type == "generation"]


def test_generation_input_is_the_saved_conversation_with_hook_notes(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    saved = tmp_path / "saved"
    monkeypatch.setenv("CC_LANGFUSE_API_REQUESTS_DIR", str(saved))
    question = {"role": "user", "content": [{"type": "text", "text": "Question 1?"}]}
    tool_result = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu-1", "content": "file content"}]}
    save_call(saved, "msg-1-0", [question], TOOL_USE)
    save_call(saved, "msg-1-1", [tool_result, HOOK_NOTE], ANSWER, previous_message_id="msg-1-0")
    transcript = tmp_path / f"{SESSION}.jsonl"
    write_transcript(transcript)

    first, second = emit(hook_module, fake_langfuse, transcript)

    assert first["input"] == [question]
    assert second["input"] == [question, {"role": "assistant", "content": TOOL_USE}, tool_result, HOOK_NOTE]
    assert second["metadata"]["history"] == {"messages": 4, "source": "saved_api_request"}


def test_generation_input_is_rebuilt_when_a_saved_call_is_missing(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    saved = tmp_path / "saved"
    monkeypatch.setenv("CC_LANGFUSE_API_REQUESTS_DIR", str(saved))
    save_call(saved, "msg-1-1", [HOOK_NOTE], ANSWER, previous_message_id="msg-1-0")
    transcript = tmp_path / f"{SESSION}.jsonl"
    write_transcript(transcript)

    _, second = emit(hook_module, fake_langfuse, transcript)

    assert [message["role"] for message in second["input"]] == ["user", "assistant", "tool"]
    assert second["metadata"]["history"] == {"messages": 3}


def test_sweep_starts_a_new_index_daily_and_drops_week_old_files(hook_module, tmp_path, monkeypatch):
    saved = tmp_path / "saved"
    monkeypatch.setenv("CC_LANGFUSE_API_REQUESTS_DIR", str(saved))
    save_call(saved, "msg-old", [HOOK_NOTE], ANSWER, timestamp="2026-10-10T08:00:00.000Z")
    old_request = saved / "request-of-msg-old.request.json"
    week_ago = datetime(2026, 10, 10, tzinfo=timezone.utc).timestamp()
    os.utime(old_request, (week_ago, week_ago))
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)

    hook_module.sweep_saved_api_requests(now=now)

    assert (saved / "index.20261010T080000.jsonl").exists()
    assert not (saved / "index.jsonl").exists()
    assert not old_request.exists()
    assert (saved / "req-of-msg-old.response.json").exists()

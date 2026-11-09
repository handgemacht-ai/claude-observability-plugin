"""Builders for what the OTLP exporter sends and what Langfuse answers, with synthetic bytes only."""
from __future__ import annotations

import io
import json
import logging
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest

from tests.session_files import SESSION, SessionDir, assistant, tool_result, tool_use, user

SPAN_BATCH_URL = "https://langfuse.test/api/public/otel/v1/traces"


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _length_delimited(field: int, payload: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def protobuf_answer(rejected_spans: int = 0, error_message: str = "") -> bytes:
    """An ExportTraceServiceResponse that carries a partial_success."""
    partial_success = b""
    if rejected_spans:
        partial_success += _varint(1 << 3) + _varint(rejected_spans)
    if error_message:
        partial_success += _length_delimited(2, error_message.encode())
    return _length_delimited(1, partial_success)


def json_answer(rejected_spans: Any = 0, error_message: str = "") -> bytes:
    """The OTLP/JSON form of the same answer, where int64 values are strings."""
    return json.dumps({"partialSuccess": {"rejectedSpans": str(rejected_spans), "errorMessage": error_message}}).encode()


class Answer:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content
        self.headers: dict[str, str] = {}


def langfuse_answers(monkeypatch: pytest.MonkeyPatch, answers: list[Answer]) -> None:
    """Puts in place a requests whose Session.send gives the answers one after the other."""
    module = types.ModuleType("requests")
    to_give = list(answers)

    class Session:
        def send(self, request: Any, **_kwargs: Any) -> Any:
            return to_give.pop(0)

    module.Session = Session
    monkeypatch.setitem(sys.modules, "requests", module)


def a_span_batch(body: bytes = b"spans") -> Any:
    return types.SimpleNamespace(url=SPAN_BATCH_URL, body=body)


def send_to_langfuse(batch: Any, **kwargs: Any) -> None:
    sys.modules["requests"].Session().send(batch, **kwargs)


def log_text(hook_module: Any) -> str:
    return hook_module.LOG_FILE.read_text(encoding="utf-8") if hook_module.LOG_FILE.exists() else ""


def expect_logged(hook_module: Any, *fragments: str) -> None:
    text = log_text(hook_module)
    for fragment in fragments:
        assert fragment in text, f"{fragment!r} is not in the plugin log:\n{text}"


def expect_not_logged(hook_module: Any, fragment: str) -> None:
    text = log_text(hook_module)
    assert fragment not in text, f"{fragment!r} is in the plugin log:\n{text}"


def expect_verdict(watch: Any, verdict: str) -> None:
    assert watch.verdict() == verdict


def a_watch_of_the_exports(hook_module: Any) -> Any:
    watch = hook_module.ExportWatch()
    assert hook_module.install_export_watch(watch) is True
    return watch


def the_sdk_says(logger_name: str, level: int, message: str) -> None:
    logging.getLogger(logger_name).log(level, message)


def a_session_with_one_question(tmp_path: Path) -> SessionDir:
    session = SessionDir(tmp_path)
    session.write_main([user("u1", 0, "Question?"), assistant("a1", 1, [{"type": "text", "text": "Answer."}])])
    return session


def a_session_with_two_helpers(tmp_path: Path) -> SessionDir:
    session = SessionDir(tmp_path)
    session.write_main([
        user("u1", 0, "Check both."),
        assistant("a1", 1, [tool_use("toolu_first", "Agent", description="First", subagent_type="checker"),
                            tool_use("toolu_second", "Agent", description="Second", subagent_type="checker")]),
        tool_result("r1", 50, "toolu_first", "First done.", agentId="first"),
        tool_result("r2", 51, "toolu_second", "Second done.", agentId="second"),
        assistant("a2", 52, [{"type": "text", "text": "Done."}]),
    ])
    for agent_id, description in (("first", "First"), ("second", "Second")):
        session.add_agent(agent_id, [user(f"{agent_id}-prompt", 2, f"{description} task."),
                                     assistant(f"{agent_id}-answer", 3, [{"type": "text", "text": "Done."}])],
                          {"agentType": "checker", "description": description,
                           "toolUseId": f"toolu_{agent_id}", "spawnDepth": 1})
    return session


def a_session_with_three_helpers(tmp_path: Path) -> SessionDir:
    session = a_session_with_two_helpers(tmp_path)
    session.write_main([
        user("u1", 0, "Check all three."),
        assistant("a1", 1, [tool_use(f"toolu_{agent_id}", "Agent", description=agent_id.title(), subagent_type="checker")
                            for agent_id in ("first", "second", "third")]),
        tool_result("r1", 50, "toolu_first", "First done.", agentId="first"),
        tool_result("r2", 51, "toolu_second", "Second done.", agentId="second"),
        tool_result("r3", 52, "toolu_third", "Third done.", agentId="third"),
        assistant("a2", 53, [{"type": "text", "text": "Done."}]),
    ])
    session.add_agent("third", [user("third-prompt", 2, "Third task."),
                                assistant("third-answer", 3, [{"type": "text", "text": "Done."}])],
                      {"agentType": "checker", "description": "Third", "toolUseId": "toolu_third", "spawnDepth": 1})
    return session


def run_a_firing_in_a_new_process(hook_module: Any, fake_langfuse: Any, session: SessionDir,
                                  monkeypatch: pytest.MonkeyPatch, event: str = "Stop") -> None:
    """Runs main() as Claude Code does for the hook event, with the exports of an earlier process forgotten."""
    hook_module.EXPORT_WATCH.last_try.clear()
    monkeypatch.setattr(hook_module, "get_langfuse_config", lambda: hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", None))
    monkeypatch.setattr(hook_module, "create_langfuse_client", lambda _config: fake_langfuse)
    payload = {"session_id": SESSION, "transcript_path": str(session.transcript), "hook_event_name": event}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert hook_module.main() == 0


def langfuse_gets_a_span_batch_when_flushed(fake_langfuse: Any) -> None:
    fake_langfuse.on_flush = lambda: send_to_langfuse(a_span_batch())


def each_flush_takes(fake_langfuse: Any, seconds: float) -> None:
    fake_langfuse.on_flush = lambda: time.sleep(seconds)


def turns_sent(fake_langfuse: Any) -> int:
    return len([o for o in fake_langfuse.observations if o.name == "Conversational Turn"])


def flushes_before_the_end(fake_langfuse: Any) -> list[list[tuple[str, bool]]]:
    """What was started and ended at each flush that came before the flush at the end of the firing."""
    return fake_langfuse.flushes[:-1]


def helpers_ended(flush: list[tuple[str, bool]]) -> list[str]:
    return [name for name, ended in flush if name.startswith("Subagent:") and ended]


def helpers_started(flush: list[tuple[str, bool]]) -> list[str]:
    return [name for name, _ended in flush if name.startswith("Subagent:")]

"""Run the hook on a turn whose final answer Claude Code writes to the log only after the Stop hook started."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import types
from pathlib import Path
from typing import Any, Optional

import pytest

from tests.session_files import SESSION, assistant, tool_result, tool_use, user

ANSWER = "The tests pass."


def append(transcript: Path, rows: list[dict[str, Any]]) -> None:
    transcript.parent.mkdir(parents=True, exist_ok=True)
    with transcript.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def a_turn_without_its_final_answer(transcript: Path) -> None:
    append(transcript, [
        user("u1", 0, "Run the tests."),
        assistant("a1", 1, [tool_use("toolu_1", "Bash", command="pytest")]),
        tool_result("r1", 2, "toolu_1", "4 passed"),
    ])


def claude_code_writes_the_final_answer(transcript: Path) -> None:
    append(transcript, [assistant("a2", 3, [{"type": "text", "text": ANSWER}])])


def claude_code_writes_the_final_answer_after(transcript: Path, seconds: float) -> threading.Timer:
    writer = threading.Timer(seconds, claude_code_writes_the_final_answer, [transcript])
    writer.start()
    return writer


class _Pipe(io.BytesIO):
    def close(self) -> None:
        self.sent = self.getvalue()
        super().close()


class Hook:
    """Runs the hook the way Claude Code does, and runs the processes it starts on its own in this process."""

    def __init__(self, hook_module: Any, fake_langfuse: Any, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.hook_module = hook_module
        self.transcript = transcript
        self.monkeypatch = monkeypatch
        self.started: list[tuple[list[str], _Pipe]] = []
        monkeypatch.setattr(hook_module, "get_langfuse_config", lambda: hook_module.LangfuseConfig(
            "public", "secret", "https://example.test", None))
        monkeypatch.setattr(hook_module, "create_langfuse_client", lambda _config: fake_langfuse)
        monkeypatch.setattr(hook_module, "flush_and_shutdown_langfuse_client", lambda _client: hook_module.DELIVERED)
        monkeypatch.setattr(subprocess, "Popen", self._start)

    def _start(self, args: list[str], **_: Any) -> Any:
        pipe = _Pipe()
        self.started.append((list(args), pipe))
        return types.SimpleNamespace(stdin=pipe)

    def _run(self, args: list[str], stdin: str) -> None:
        self.monkeypatch.setattr(sys, "argv", ["langfuse_hook.py", *args])
        self.monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        assert self.hook_module.main() == 0

    def stop(self, answer: Optional[str]) -> None:
        payload = {"session_id": SESSION, "transcript_path": str(self.transcript), "hook_event_name": "Stop",
                   "stop_hook_active": False}
        if answer is not None:
            payload["last_assistant_message"] = answer
        self._run([], json.dumps(payload))

    def session_end(self) -> None:
        self._run([], json.dumps({
            "session_id": SESSION, "transcript_path": str(self.transcript), "hook_event_name": "SessionEnd",
            "reason": "prompt_input_exit",
        }))

    def run_the_processes_it_started(self) -> None:
        started, self.started = self.started, []
        for args, pipe in started:
            self._run(args[2:], pipe.sent.decode("utf-8"))


def no_process_can_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def _cannot_start(*_: Any, **__: Any) -> Any:
        raise OSError("no process can start")
    monkeypatch.setattr(subprocess, "Popen", _cannot_start)


def the_wait_for_the_final_answer_ends_after(hook_module: Any, monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    monkeypatch.setattr(hook_module, "FINAL_ANSWER_WAIT_SECONDS", seconds)


def turn_roots(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]


def expect_final_answer_sent(fake_langfuse: Any, answer: str) -> None:
    [root] = turn_roots(fake_langfuse)
    assert root.output == {"role": "assistant", "content": answer}, "the root of the turn went out without its final answer"
    generation_texts = [o.kwargs["output"].get("content") for o in fake_langfuse.observations if o.as_type == "generation"]
    assert answer in generation_texts, "the LLM call of the final answer was not sent"


def expect_turn_sent_without_final_answer(fake_langfuse: Any) -> None:
    [root] = turn_roots(fake_langfuse)
    assert root.output == {"role": "assistant", "content": ""}


def expect_each_step_sent_once(fake_langfuse: Any) -> None:
    assert len(turn_roots(fake_langfuse)) == 1, "the root of the turn was sent more than once"
    ids = [o.id for o in fake_langfuse.observations]
    assert len(ids) == len(set(ids)), "a step of the turn was sent twice"


def expect_nothing_started(hook: Hook) -> None:
    assert hook.started == [], "a Stop that has nothing to wait for must not start a process"


def expect_one_process_started(hook: Hook) -> None:
    assert len(hook.started) == 1, "a Stop whose final answer is not in the log must hand its firing to one process"

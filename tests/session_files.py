"""Builders for the files Claude Code writes for a session, with synthetic rows only."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SESSION = "session-launches"
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def a_pid_that_is_gone() -> int:
    pid = 4_000_000
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except OSError:
            pass
        pid += 1


def stamp(seconds: float) -> str:
    return (BASE + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def user(uuid: str, at: float, text: str = "Go.", session: str = SESSION, **extra: Any) -> dict[str, Any]:
    return {"type": "user", "timestamp": stamp(at), "sessionId": session, "uuid": uuid,
            "message": {"role": "user", "content": text}, **extra}


def assistant(uuid: str, at: float, blocks: list[dict[str, Any]], session: str = SESSION) -> dict[str, Any]:
    return {"type": "assistant", "timestamp": stamp(at), "sessionId": session, "uuid": uuid,
            "message": {"id": f"msg-{uuid}", "role": "assistant", "model": "claude-test", "content": blocks}}


def tool_use(tool_use_id: str, name: str, **tool_input: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}


def tool_result(uuid: str, at: float, tool_use_id: str, text: str = "done",
                **tool_use_result: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "type": "user", "timestamp": stamp(at), "sessionId": SESSION, "uuid": uuid,
        "message": {"role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]},
    }
    if tool_use_result:
        row["toolUseResult"] = tool_use_result
    return row


class SessionDir:
    """The files Claude Code 2.1.31x writes for a session: a main transcript,
    agents flat in subagents/ and Workflow agents in subagents/workflows/<run>/."""

    def __init__(self, tmp_path: Path) -> None:
        self.transcript = tmp_path / f"{SESSION}.jsonl"
        self.subagents = tmp_path / SESSION / "subagents"
        self.subagents.mkdir(parents=True)
        self.write_main([])

    def write_main(self, rows: list[dict[str, Any]]) -> None:
        self.transcript.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def add_agent(self, agent_id: str, rows: list[dict[str, Any]], meta: dict[str, Any],
                  run: str | None = None) -> Path:
        directory = self.subagents / "workflows" / run if run else self.subagents
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        path = directory / f"agent-{agent_id}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path


FORKED = {"agentType": "general-purpose", "isBuiltIn": True, "spawnDepth": 2}


def fork_rows(at: float) -> list[dict[str, Any]]:
    return [user("fork-prompt", at, "Skill text.", isMeta=True),
            assistant("fork-answer", at + 1, [{"type": "text", "text": "Review done."}])]


def parent_with_skill_calls(calls: list[tuple[str, float, float]]) -> list[dict[str, Any]]:
    """A parent agent that calls Skill (toolu id, start, end) one after the other."""
    rows = [user("p-prompt", 0, "Parent task.")]
    for index, (call_id, started, ended) in enumerate(calls):
        rows.append(assistant(f"p-call-{index}", started,
                              [tool_use(call_id, "Skill", skill="code-review", args=f"round {index}")]))
        rows.append(tool_result(f"p-result-{index}", ended, call_id,
                                'Skill "code-review" completed (forked execution).'))
    return rows

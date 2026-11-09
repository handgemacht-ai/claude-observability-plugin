"""Builders for the files Claude Code writes for a session, with synthetic rows only."""
from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

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

    def add_agent(self, agent_id: str, rows: list[dict[str, Any]], meta: dict[str, Any] | None,
                  run: str | None = None, folder: str | None = None) -> Path:
        """An agent's transcript and, unless meta is None, its meta. A folder names
        another project folder next to the one of the main transcript: a session in
        a worktree writes its agents under the worktree's project folder."""
        subagents = self.transcript.parent.parent / folder / SESSION / "subagents" if folder else self.subagents
        directory = subagents / "workflows" / run if run else subagents
        directory.mkdir(parents=True, exist_ok=True)
        if meta is not None:
            (directory / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        path = directory / f"agent-{agent_id}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path


def a_session_with(tmp_path: Path, rows: list[dict[str, Any]]) -> SessionDir:
    session = SessionDir(tmp_path)
    session.write_main(rows)
    return session


def answer(uuid: str, at: float, text: str) -> dict[str, Any]:
    return assistant(uuid, at, [{"type": "text", "text": text}])


HELPER_TASK = "helper-1"
HELPER_CALL = "toolu_helper"


def notification_text(task_id: str = HELPER_TASK, tool_use_id: str = HELPER_CALL) -> str:
    return (f"<task-notification>\n<task-id>{task_id}</task-id>\n<tool-use-id>{tool_use_id}</tool-use-id>\n"
            "<status>completed</status>\n<summary>Done.</summary>\n</task-notification>")


def queued(at: float, text: str) -> dict[str, Any]:
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": stamp(at), "sessionId": SESSION,
            "content": text}


def taken_from_queue(at: float, text: str) -> dict[str, Any]:
    return {"type": "queue-operation", "operation": "remove", "timestamp": stamp(at), "sessionId": SESSION,
            "content": text, "reason": "absorbed_mid_turn"}


def attached_to_running_turn(uuid: str, at: float, text: str) -> dict[str, Any]:
    return {"type": "attachment", "timestamp": stamp(at), "sessionId": SESSION, "uuid": uuid,
            "attachment": {"type": "queued_command", "prompt": text, "commandMode": "task-notification",
                           "origin": {"kind": "task-notification"}}}


def delivered(uuid: str, at: float, text: str) -> dict[str, Any]:
    return user(uuid, at, text, origin={"kind": "task-notification"})


def hand_back(uuid: str, at: float, task_id: str = HELPER_TASK) -> dict[str, Any]:
    """The helper's final report, sent to the main session as a peer message."""
    body = "[Subagent hand-back] Report."
    return user(uuid, at, f'Another Claude session sent a message:\n<agent-message from="{task_id}">\n{body}\n'
                "</agent-message>", isMeta=True,
                origin={"kind": "peer", "from": task_id, "senderTaskId": task_id, "body": body})


def a_turn_that_launched_a_helper() -> list[dict[str, Any]]:
    return [user("u1", 0, "Ask a helper."),
            assistant("a1", 1, [tool_use(HELPER_CALL, "Agent", description="Helper", prompt="Look.")]),
            tool_result("r1", 2, HELPER_CALL, "Async agent launched successfully.",
                        status="async_launched", isAsync=True),
            answer("a2", 3, "The helper runs.")]


def a_helper_result_attached_to_the_running_turn() -> list[dict[str, Any]]:
    note = notification_text()
    return a_turn_that_launched_a_helper() + [
        assistant("a3", 4, [tool_use("toolu_read", "Read", file_path="notes.md")]),
        queued(5, note),
        taken_from_queue(6, note),
        tool_result("r2", 6, "toolu_read", "Notes."),
        attached_to_running_turn("n1", 5, note),
    ]


def a_helper_hand_back() -> list[dict[str, Any]]:
    return a_turn_that_launched_a_helper() + [queued(10, notification_text()), hand_back("h1", 10)]


def answered(rows: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    return rows + [answer("final", 30, text)]


def followed_by_the_helper_notification(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows + [delivered("n-late", 31, notification_text())]


def a_finished_turn_while_a_helper_takes_its_own_background_task_result() -> list[dict[str, Any]]:
    note = notification_text("bash-1", "toolu_helper_bash")
    return [user("u1", 0, "Go."), answer("a1", 1, "Done."), queued(5, note), taken_from_queue(6, note)]


def a_resumed_helper_that_did_not_hand_back_again() -> list[dict[str, Any]]:
    note = notification_text(tool_use_id="toolu_resume")
    return answered(a_helper_hand_back(), "Resuming the helper.") + [
        assistant("a4", 32, [tool_use("toolu_resume", "SendMessage", to=HELPER_TASK, message="Again.")]),
        tool_result("r3", 33, "toolu_resume", "Resumed.", resumedAgentId=HELPER_TASK),
        answer("a5", 34, "The helper runs again."),
        queued(40, note),
        delivered("n2", 41, note),
    ]


HELPER_BACKGROUND_CALL = "toolu_helper_bash"
HELPER_LATER_CALL = "toolu_helper_read"


def notification_of_a_later_stop(task_id: str = HELPER_TASK) -> str:
    """A helper that stops again is reported without the call that launched it."""
    return (f"<task-notification>\n<task-id>{task_id}</task-id>\n<status>completed</status>\n"
            "<summary>Waiting on its own background task.</summary>\n</task-notification>")


def a_helper_that_waits_on_its_own_background_task() -> list[dict[str, Any]]:
    return [user("h-prompt", 2.5, "Look."),
            assistant("h-a1", 4, [tool_use(HELPER_BACKGROUND_CALL, "Bash", command="make test",
                                           run_in_background=True)]),
            tool_result("h-r1", 5, HELPER_BACKGROUND_CALL, "Running in the background."),
            answer("h-a2", 9, "Waiting for the test run.")]


def woken_by_its_background_task(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    note = "Background task event.\n" + notification_text("bash-1", HELPER_BACKGROUND_CALL)
    return rows + [user("h-wake", 20, note, isMeta=True, origin={"kind": "task-notification"}),
                   assistant("h-a3", 21, [tool_use(HELPER_LATER_CALL, "Read", file_path="report.txt")]),
                   tool_result("h-r2", 22, HELPER_LATER_CALL, "All passed.")]


def stopped_again(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows + [answer("h-a4", 25, "The test run passed.")]


def a_main_turn_answered_at_the_helper_first_stop() -> list[dict[str, Any]]:
    return a_turn_that_launched_a_helper() + [queued(10, notification_text()),
                                              delivered("n1", 10.1, notification_text()),
                                              answer("a3", 11, "The helper waits on its test run.")]


def answered_at_the_helper_later_stop(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows + [queued(30, notification_of_a_later_stop()),
                   delivered("n2", 30.1, notification_of_a_later_stop()),
                   answer("a4", 31, "The helper is done.")]


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


WORKTREE_FOLDER = "start-folder--claude-worktrees-feature"
HELPER_META = {"agentType": "explorer", "description": "Look up", "toolUseId": "toolu_helper", "spawnDepth": 1}


def helper_rows(at: float, tool_use_id: str = "toolu_helper_read") -> list[dict[str, Any]]:
    """A helper that reads one file and answers."""
    return [user("h-prompt", at, "Look it up."),
            assistant("h-call", at + 1, [tool_use(tool_use_id, "Read", file_path="notes.md")]),
            tool_result("h-result", at + 2, tool_use_id, "Notes."),
            assistant("h-answer", at + 3, [{"type": "text", "text": "Found it."}])]


def a_session_that_launched_a_helper(tmp_path: Path) -> SessionDir:
    session = SessionDir(tmp_path)
    session.write_main([
        user("u1", 0, "Look it up."),
        assistant("a1", 1, [tool_use("toolu_helper", "Agent", description="Look up", subagent_type="explorer")]),
        tool_result("r1", 30, "toolu_helper", "Found it.", agentId="helper"),
        assistant("a2", 31, [{"type": "text", "text": "Done."}]),
    ])
    return session


def a_session_whose_helper_wrote_under_a_worktree_folder(tmp_path: Path) -> SessionDir:
    """The main transcript stays in the folder the session started in, the helper's
    files sit under the project folder of the worktree the session entered."""
    session = a_session_that_launched_a_helper(tmp_path)
    session.add_agent("helper", helper_rows(10), HELPER_META, folder=WORKTREE_FOLDER)
    return session


def a_session_whose_helper_has_meta_and_transcript_in_both_folders(tmp_path: Path) -> SessionDir:
    session = a_session_that_launched_a_helper(tmp_path)
    session.add_agent("helper", helper_rows(10), HELPER_META)
    session.add_agent("helper", helper_rows(20), HELPER_META, folder=WORKTREE_FOLDER)
    return session


def a_session_whose_helper_has_its_meta_only_under_the_worktree_folder(tmp_path: Path) -> SessionDir:
    session = a_session_that_launched_a_helper(tmp_path)
    session.add_agent("helper", helper_rows(20), None)
    session.add_agent("helper", helper_rows(10), HELPER_META, folder=WORKTREE_FOLDER)
    return session


def a_session_whose_nested_helper_wrote_under_a_worktree_folder(tmp_path: Path) -> SessionDir:
    """A helper launched by another helper: its meta names its parent, not a launch."""
    session = a_session_that_launched_a_helper(tmp_path)
    outer = [user("o-prompt", 2, "Look it up."),
             assistant("o-call", 3, [tool_use("toolu_inner", "Agent", description="Dig", subagent_type="explorer")]),
             tool_result("o-result", 20, "toolu_inner", "Dug.", agentId="inner"),
             assistant("o-answer", 21, [{"type": "text", "text": "Found it."}])]
    session.add_agent("helper", outer, HELPER_META, folder=WORKTREE_FOLDER)
    session.add_agent("inner", helper_rows(4), {"agentType": "explorer", "spawnDepth": 2, "parentAgentId": "helper"},
                      folder=WORKTREE_FOLDER)
    return session


def a_session_whose_workflow_run_wrote_under_a_worktree_folder(tmp_path: Path, run: str) -> SessionDir:
    session = SessionDir(tmp_path)
    session.add_agent("step", helper_rows(10), {"agentType": "workflow-subagent", "spawnDepth": 1},
                      run=run, folder=WORKTREE_FOLDER)
    return session


def a_session_whose_old_helper_folder_was_superseded(tmp_path: Path) -> SessionDir:
    """Claude Code renames a session directory it replaces to <session>.superseded-<ms>."""
    session = a_session_that_launched_a_helper(tmp_path)
    superseded = tmp_path.parent / WORKTREE_FOLDER / f"{SESSION}.superseded-1700000000000" / "subagents"
    superseded.mkdir(parents=True)
    (superseded / "agent-helper.meta.json").write_text(json.dumps(HELPER_META), encoding="utf-8")
    (superseded / "agent-helper.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in helper_rows(10)), encoding="utf-8")
    return session


@contextlib.contextmanager
def a_project_folder_the_user_cannot_search(tmp_path: Path) -> Iterator[None]:
    """A folder next to the session's, such as one a sudo or container run left, owned by root with mode 0700."""
    folder = tmp_path.parent / "folder-of-another-user"
    (folder / SESSION / "subagents").mkdir(parents=True)
    folder.chmod(0)
    try:
        yield
    finally:
        folder.chmod(0o700)


def expect_helper_read_from(subagents: dict[str, dict[str, Any]], tool_use_id: str, folder: str) -> None:
    """The helper launched by the call was read from the subagents/ directory under the project folder."""
    assert tool_use_id in subagents, sorted(subagents)
    assert subagents[tool_use_id]["path"].parent.parent.parent.name == folder


def expect_tool_step_sent(fake_langfuse: Any, name: str, tool_use_id: str) -> None:
    assert [o.name for o in fake_langfuse.observations
            if o.kwargs.get("metadata", {}).get("tool_id") == tool_use_id] == [f"Tool: {name}"]


def expect_llm_call_of_agent_sent(fake_langfuse: Any, agent_id: str) -> None:
    assert [o for o in fake_langfuse.observations
            if o.as_type == "generation" and o.kwargs.get("metadata", {}).get("agent_id") == agent_id]


def expect_one_log_line(state_dir: Path, text: str) -> None:
    log = (state_dir / "langfuse_hook.log").read_text(encoding="utf-8")
    assert log.count(text) == 1, log

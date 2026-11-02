from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from tests.session_files import assistant, user

SESSION = "session-moves"


def turn(index: int, session: str = SESSION) -> list[dict[str, Any]]:
    return [
        user(f"u{index}", index * 10, f"Question {index}?", session),
        assistant(f"a{index}", index * 10 + 1, [{"type": "text", "text": f"Answer {index}."}], session),
    ]


def write(path: Path, rows: list[dict[str, Any]], mode: str = "w") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def move(source: Path, target: Path) -> Path:
    """What Claude Code does when a session enters or leaves a worktree: the log and its folder move."""
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    folder = source.with_suffix("")
    if folder.exists():
        folder.rename(target.with_suffix(""))
    return target


def fire(hook_module: Any, fake_langfuse: Any, transcript: Path, session_id: str = SESSION) -> int:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    return hook_module.emit_new_turns_from_transcript(fake_langfuse, config, session_id, transcript)


def sent_questions(fake_langfuse: Any) -> list[str]:
    return [o.kwargs["input"]["content"] for o in fake_langfuse.observations if o.name == "Conversational Turn"]


def saved_state(isolated_hook_state: Path) -> dict[str, Any]:
    return json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))


def test_a_log_that_moves_continues_where_it_stopped(hook_module, fake_langfuse, isolated_hook_state, tmp_path):
    first = tmp_path / "project" / f"{SESSION}.jsonl"
    write(first, turn(1) + turn(2))
    fire(hook_module, fake_langfuse, first)

    moved = move(first, tmp_path / "project--worktree" / f"{SESSION}.jsonl")
    write(moved, turn(3), mode="a")
    emitted = fire(hook_module, fake_langfuse, moved)

    assert emitted == 1
    assert sent_questions(fake_langfuse) == ["Question 1?", "Question 2?", "Question 3?"]
    assert len(saved_state(isolated_hook_state)) == 1


def test_a_log_that_moves_there_and_back_sends_nothing_twice(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    home = tmp_path / "project" / f"{SESSION}.jsonl"
    away = tmp_path / "project--worktree" / f"{SESSION}.jsonl"
    write(home, turn(1))
    fire(hook_module, fake_langfuse, home)

    move(home, away)
    write(away, turn(2), mode="a")
    fire(hook_module, fake_langfuse, away)
    move(away, home)
    write(home, turn(3), mode="a")
    fire(hook_module, fake_langfuse, home)
    write(home, turn(4), mode="a")
    fire(hook_module, fake_langfuse, home)

    assert sent_questions(fake_langfuse) == [f"Question {n}?" for n in (1, 2, 3, 4)]
    assert len(saved_state(isolated_hook_state)) == 1


def test_the_state_records_where_the_log_is(hook_module, fake_langfuse, isolated_hook_state, tmp_path):
    transcript = tmp_path / "project" / f"{SESSION}.jsonl"
    write(transcript, turn(1))
    fire(hook_module, fake_langfuse, transcript)

    [entry] = saved_state(isolated_hook_state).values()

    assert entry["transcript_path"] == str(transcript)
    assert entry["head_len"] == transcript.stat().st_size
    assert entry["head_hash"]


def test_state_saved_under_the_key_of_the_path_is_taken_over(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / "project" / f"{SESSION}.jsonl"
    write(transcript, turn(1))
    fire(hook_module, fake_langfuse, transcript)
    [current] = saved_state(isolated_hook_state).values()
    legacy_entry = {k: v for k, v in current.items() if k not in ("transcript_path", "head_len", "head_hash")}
    legacy_key = hook_module.get_legacy_session_state_key(SESSION, str(transcript))
    (isolated_hook_state / "langfuse_state.json").write_text(json.dumps({legacy_key: legacy_entry}), encoding="utf-8")
    fake_langfuse.observations.clear()

    write(transcript, turn(2), mode="a")
    emitted = fire(hook_module, fake_langfuse, transcript)

    assert emitted == 1
    assert sent_questions(fake_langfuse) == ["Question 2?"]
    state = saved_state(isolated_hook_state)
    assert list(state) == [hook_module.get_session_state_key(SESSION)]
    assert state[hook_module.get_session_state_key(SESSION)]["offset"] == transcript.stat().st_size


def test_old_state_of_the_same_path_is_removed_once_the_session_has_its_own(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / "project" / f"{SESSION}.jsonl"
    write(transcript, turn(1))
    fire(hook_module, fake_langfuse, transcript)
    state = saved_state(isolated_hook_state)
    stale_key = hook_module.get_legacy_session_state_key(SESSION, str(transcript))
    state[stale_key] = {"offset": 1, "updated": "2999-01-01T00:00:00+00:00"}
    (isolated_hook_state / "langfuse_state.json").write_text(json.dumps(state), encoding="utf-8")

    write(transcript, turn(2), mode="a")
    fire(hook_module, fake_langfuse, transcript)

    assert list(saved_state(isolated_hook_state)) == [hook_module.get_session_state_key(SESSION)]
    assert sent_questions(fake_langfuse) == ["Question 1?", "Question 2?"]


def test_another_log_with_the_same_session_id_keeps_its_own_state(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    # Claude Code gives some runs a session id worked out from a name, so two
    # projects can hold different logs under one session id.
    first = tmp_path / "project-a" / f"{SESSION}.jsonl"
    second = tmp_path / "project-b" / f"{SESSION}.jsonl"
    def rows(prefix: str, step: int, text: str) -> list[dict[str, Any]]:
        return [user(f"{prefix}-u{step}", step * 2, text, SESSION),
                assistant(f"{prefix}-a{step}", step * 2 + 1, [{"type": "text", "text": "Ok."}], SESSION)]

    write(first, rows("a", 1, "A starts."))
    write(second, rows("b", 1, "B starts."))

    fire(hook_module, fake_langfuse, first)
    fire(hook_module, fake_langfuse, second)
    write(first, rows("a", 2, "A goes on."), mode="a")
    write(second, rows("b", 2, "B goes on."), mode="a")
    fire(hook_module, fake_langfuse, first)
    fire(hook_module, fake_langfuse, second)

    assert sent_questions(fake_langfuse) == ["A starts.", "B starts.", "A goes on.", "B goes on."]
    assert len(saved_state(isolated_hook_state)) == 2


def test_a_log_shorter_than_what_was_read_is_another_log(hook_module, fake_langfuse, isolated_hook_state, tmp_path):
    first = tmp_path / "project" / f"{SESSION}.jsonl"
    write(first, turn(1) + turn(2))
    fire(hook_module, fake_langfuse, first)
    stub = tmp_path / "project--worktree" / f"{SESSION}.jsonl"
    stub.parent.mkdir()
    shutil.copyfile(first, stub)
    stub.write_bytes(stub.read_bytes()[: first.stat().st_size // 2])

    key, superseded = hook_module.resolve_session_state_key(
        hook_module.load_hook_state(), SESSION, stub
    )

    assert key == hook_module.get_legacy_session_state_key(SESSION, str(stub))
    assert superseded == []


def test_two_sessions_do_not_share_state(hook_module, fake_langfuse, isolated_hook_state, tmp_path):
    first = tmp_path / "project" / "session-one.jsonl"
    second = tmp_path / "project" / "session-two.jsonl"
    write(first, turn(1, "session-one"))
    write(second, turn(2, "session-two"))

    fire(hook_module, fake_langfuse, first, "session-one")
    fire(hook_module, fake_langfuse, second, "session-two")

    assert sent_questions(fake_langfuse) == ["Question 1?", "Question 2?"]
    assert len(saved_state(isolated_hook_state)) == 2


def test_helpers_are_found_after_the_log_moved(hook_module, fake_langfuse, isolated_hook_state, tmp_path):
    from tests.session_files import SessionDir, tool_result, tool_use

    home = SessionDir(tmp_path / "project")
    session = "session-launches"
    home.write_main([
        user("u1", 0, "Review it."),
        assistant("a1", 1, [tool_use("toolu_reviewer", "Agent", description="Reviewer", subagent_type="reviewer")]),
        tool_result("r1", 50, "toolu_reviewer", "Reviewed.", agentId="reviewer"),
        assistant("a2", 51, [{"type": "text", "text": "Done."}]),
    ])
    home.add_agent("reviewer", [user("p", 2, "Go."), assistant("p2", 3, [{"type": "text", "text": "Ok."}])],
                   {"agentType": "reviewer", "description": "Reviewer", "toolUseId": "toolu_reviewer"})

    moved = move(home.transcript, tmp_path / "project--worktree" / f"{session}.jsonl")
    fire(hook_module, fake_langfuse, moved, session)

    assert [o.name for o in fake_langfuse.observations if o.name.startswith("Subagent:")] == [
        "Subagent: reviewer · Reviewer"
    ]

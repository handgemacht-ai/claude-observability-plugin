from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def context_hook() -> Any:
    path = REPO_ROOT / "hooks" / "langfuse_context_hook.py"
    spec = importlib.util.spec_from_file_location("langfuse_context_hook_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def state_dir(tmp_path, monkeypatch) -> Path:
    state = tmp_path / "state"
    monkeypatch.setenv("CC_LANGFUSE_STATE_DIR", str(state))
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.delenv("CC_LANGFUSE_CAPTURE_INSTRUCTIONS", raising=False)
    monkeypatch.delenv("CC_LANGFUSE_MAX_CHARS", raising=False)
    return state


def run_hook(context_hook, monkeypatch, capsys, payload: Any) -> int:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    exit_code = context_hook.main()
    assert capsys.readouterr().out == ""
    return exit_code


def records(state_dir: Path, session_id: str = "s-1") -> list[dict[str, Any]]:
    path = state_dir / "langfuse_context" / f"{session_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def instructions_payload(file_path: Path, **extra: Any) -> dict[str, Any]:
    return {
        "session_id": "s-1",
        "hook_event_name": "InstructionsLoaded",
        "file_path": str(file_path),
        "memory_type": "Project",
        "load_reason": "session_start",
        **extra,
    }


def test_instructions_loaded_records_file_metadata_and_content(context_hook, state_dir, tmp_path, monkeypatch, capsys):
    rule = tmp_path / "rules" / "style.md"
    rule.parent.mkdir()
    rule.write_text("Use short names.", encoding="utf-8")

    assert run_hook(context_hook, monkeypatch, capsys, instructions_payload(
        rule, load_reason="path_glob_match", globs=["src/**/*.py"], trigger_file_path="/p/src/a.py",
        agent_id="agent-7", agent_type="worker",
    )) == 0

    (record,) = records(state_dir)
    assert record["event"] == "InstructionsLoaded"
    assert record["file_path"] == str(rule)
    assert record["content"] == "Use short names."
    assert record["truncated"] is False
    assert record["globs"] == ["src/**/*.py"]
    assert record["trigger_file_path"] == "/p/src/a.py"
    assert (record["agent_id"], record["agent_type"]) == ("agent-7", "worker")
    assert len(record["sha256"]) == 64


def test_repeated_loads_are_recorded_once(context_hook, state_dir, tmp_path, monkeypatch, capsys):
    rule = tmp_path / "CLAUDE.md"
    rule.write_text("Be brief.", encoding="utf-8")

    for _ in range(3):
        run_hook(context_hook, monkeypatch, capsys, instructions_payload(rule))
    run_hook(context_hook, monkeypatch, capsys, instructions_payload(rule, load_reason="compact"))

    assert [r["load_reason"] for r in records(state_dir)] == ["session_start", "compact"]


def test_content_is_capped_and_can_be_turned_off(context_hook, state_dir, tmp_path, monkeypatch, capsys):
    rule = tmp_path / "CLAUDE.md"
    rule.write_text("abcdefghij", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_MAX_CHARS", "4")

    run_hook(context_hook, monkeypatch, capsys, instructions_payload(rule))
    monkeypatch.setenv("CC_LANGFUSE_CAPTURE_INSTRUCTIONS", "false")
    run_hook(context_hook, monkeypatch, capsys, instructions_payload(rule, load_reason="compact"))

    capped, without_content = records(state_dir)
    assert (capped["content"], capped["truncated"], capped["chars"]) == ("abcd", True, 10)
    assert "content" not in without_content
    assert without_content["sha256"] == capped["sha256"]


def test_session_start_records_the_main_agent(context_hook, state_dir, monkeypatch, capsys):
    run_hook(context_hook, monkeypatch, capsys, {
        "session_id": "s-1", "hook_event_name": "SessionStart", "source": "startup",
        "model": "claude-haiku", "agent_type": "lead",
    })

    (record,) = records(state_dir)
    assert (record["event"], record["agent_type"], record["source"]) == ("SessionStart", "lead", "startup")


@pytest.mark.parametrize("payload", [
    "", "not json", "[]", {"hook_event_name": "InstructionsLoaded"},
    {"session_id": "s-1", "hook_event_name": "Stop"},
    {"session_id": "s-1", "hook_event_name": "InstructionsLoaded"},
])
def test_bad_or_foreign_payloads_exit_zero_without_records(context_hook, state_dir, monkeypatch, capsys, payload):
    assert run_hook(context_hook, monkeypatch, capsys, payload) == 0
    assert not (state_dir / "langfuse_context").exists()


def test_unreadable_file_is_recorded_without_content(context_hook, state_dir, tmp_path, monkeypatch, capsys):
    run_hook(context_hook, monkeypatch, capsys, instructions_payload(tmp_path / "missing.md"))

    (record,) = records(state_dir)
    assert record["read_error"] == "FileNotFoundError"
    assert "content" not in record


def test_nothing_is_recorded_without_langfuse_keys(context_hook, state_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY")
    rule = tmp_path / "CLAUDE.md"
    rule.write_text("x", encoding="utf-8")

    run_hook(context_hook, monkeypatch, capsys, instructions_payload(rule))

    assert not (state_dir / "langfuse_context").exists()


def test_session_ids_cannot_escape_the_context_dir(context_hook, tmp_path):
    path = context_hook.get_context_file_path(tmp_path, "../../etc/passwd")

    assert path.parent == tmp_path / "langfuse_context"


def test_records_round_trip_into_the_stop_hook(context_hook, hook_module, state_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hook_module, "STATE_DIR", state_dir)
    rule = tmp_path / "CLAUDE.md"
    rule.write_text("Be brief.", encoding="utf-8")
    run_hook(context_hook, monkeypatch, capsys, instructions_payload(rule))

    context = hook_module.AgentContext()
    hook_module.merge_hook_instruction_records(context, hook_module.load_context_records("s-1"))

    assert context.instruction_files[str(rule)]["content"] == "Be brief."
    assert context.instruction_files[str(rule)]["load_reason"] == "session_start"


def test_old_context_files_are_swept(hook_module, isolated_hook_state):
    import os
    import time

    context_dir = isolated_hook_state / "langfuse_context"
    context_dir.mkdir(parents=True)
    old = context_dir / "old.jsonl"
    new = context_dir / "new.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    new.write_text("{}\n", encoding="utf-8")
    past = time.time() - 40 * 86400
    os.utime(old, (past, past))

    hook_module.sweep_stale_context_files()

    assert not old.exists()
    assert new.exists()

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tests.session_files import a_pid_that_is_gone

TRACE = "a" * 32


def claim_path(hook_module: Any) -> Path:
    return hook_module.ROOT_CLAIM_DIR / f"{TRACE}.claim"


def write_claim(hook_module: Any, **content: Any) -> None:
    hook_module.ROOT_CLAIM_DIR.mkdir(parents=True, exist_ok=True)
    claim_path(hook_module).write_text(json.dumps(content), encoding="utf-8")


def claimed_at(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def test_the_first_claim_wins_and_a_second_one_is_refused(hook_module, isolated_hook_state):
    first = hook_module.claim_turn_root(TRACE)
    second = hook_module.claim_turn_root(TRACE)

    assert first is not None
    assert second is None
    assert json.loads(claim_path(hook_module).read_text())["state"] == "pending"


def test_a_confirmed_claim_blocks_for_good(hook_module, isolated_hook_state):
    claim = hook_module.claim_turn_root(TRACE)
    hook_module.confirm_root_claim(claim)
    stamp = (datetime.now(timezone.utc) - timedelta(days=20)).timestamp()
    os.utime(claim_path(hook_module), (stamp, stamp))

    assert hook_module.claim_turn_root(TRACE) is None
    assert json.loads(claim_path(hook_module).read_text())["state"] == "sent"


def test_a_claim_file_of_an_older_release_counts_as_sent(hook_module, isolated_hook_state):
    hook_module.ROOT_CLAIM_DIR.mkdir(parents=True)
    claim_path(hook_module).write_text("", encoding="utf-8")

    assert hook_module.claim_turn_root(TRACE) is None


def test_a_pending_claim_of_a_process_that_is_gone_is_taken_over(hook_module, isolated_hook_state):
    write_claim(hook_module, state="pending", token="old", pid=a_pid_that_is_gone(),
                host=socket.gethostname(), claimed_at=claimed_at(1))

    claim = hook_module.claim_turn_root(TRACE)

    assert claim is not None
    assert json.loads(claim_path(hook_module).read_text())["token"] == claim.token
    assert [p.name for p in hook_module.ROOT_CLAIM_DIR.iterdir()] == [claim_path(hook_module).name]


def test_a_pending_claim_older_than_a_hook_can_run_is_taken_over(hook_module, isolated_hook_state):
    write_claim(hook_module, state="pending", token="old", pid=os.getpid(),
                host=socket.gethostname(), claimed_at=claimed_at(60))

    assert hook_module.claim_turn_root(TRACE) is not None


def test_a_fresh_pending_claim_of_a_live_process_blocks(hook_module, isolated_hook_state):
    write_claim(hook_module, state="pending", token="other", pid=os.getpid(),
                host=socket.gethostname(), claimed_at=claimed_at(1))

    assert hook_module.claim_turn_root(TRACE) is None


def test_a_pending_claim_of_another_machine_blocks_until_it_is_old(hook_module, isolated_hook_state):
    write_claim(hook_module, state="pending", token="other", pid=a_pid_that_is_gone(),
                host="another-machine", claimed_at=claimed_at(1))

    assert hook_module.claim_turn_root(TRACE) is None


def test_a_pending_claim_without_a_date_is_taken_over(hook_module, isolated_hook_state):
    write_claim(hook_module, state="pending", token="other")

    assert hook_module.claim_turn_root(TRACE) is not None


def test_only_one_of_two_processes_takes_over_a_stale_claim(hook_module, isolated_hook_state, monkeypatch):
    write_claim(hook_module, state="pending", token="old", pid=a_pid_that_is_gone(),
                host=socket.gethostname(), claimed_at=claimed_at(1))
    real_rename = os.rename

    def lose_the_race(source: Any, target: Any) -> None:
        real_rename(source, target)
        raise FileNotFoundError(source)

    monkeypatch.setattr(os, "rename", lose_the_race)

    assert hook_module.claim_turn_root(TRACE) is None


def test_a_released_claim_can_be_taken_again(hook_module, isolated_hook_state):
    claim = hook_module.claim_turn_root(TRACE)

    hook_module.release_root_claim(claim)

    assert not claim_path(hook_module).exists()
    assert hook_module.claim_turn_root(TRACE) is not None


def test_a_process_releases_only_its_own_claim(hook_module, isolated_hook_state):
    mine = hook_module.claim_turn_root(TRACE)
    claim_path(hook_module).unlink()
    write_claim(hook_module, state="pending", token="someone-else", pid=os.getpid(),
                host=socket.gethostname(), claimed_at=claimed_at(0))

    hook_module.release_root_claim(mine)
    hook_module.confirm_root_claim(mine)

    assert json.loads(claim_path(hook_module).read_text())["token"] == "someone-else"
    assert json.loads(claim_path(hook_module).read_text())["state"] == "pending"


def test_settling_confirms_or_releases_every_root_this_process_sent(hook_module, isolated_hook_state):
    first, second = hook_module.claim_turn_root("1" * 32), hook_module.claim_turn_root("2" * 32)
    hook_module.SENT_ROOT_CLAIMS[:] = [first, second]

    hook_module.settle_root_claims(True)

    assert hook_module.SENT_ROOT_CLAIMS == []
    assert {json.loads(p.read_text())["state"] for p in hook_module.ROOT_CLAIM_DIR.iterdir()} == {"sent"}

    third = hook_module.claim_turn_root("3" * 32)
    hook_module.SENT_ROOT_CLAIMS[:] = [third]

    hook_module.settle_root_claims(False)

    assert not (hook_module.ROOT_CLAIM_DIR / f"{'3' * 32}.claim").exists()


def test_a_trace_without_an_id_is_always_sent(hook_module, isolated_hook_state):
    assert hook_module.claim_turn_root(None) is not None
    assert hook_module.claim_turn_root(None) is not None


def test_the_sweep_removes_old_claims_and_what_a_crash_left_beside_them(hook_module, isolated_hook_state):
    hook_module.ROOT_CLAIM_DIR.mkdir(parents=True)
    old = [hook_module.ROOT_CLAIM_DIR / name for name in
           (f"{TRACE}.claim", f"{TRACE}.claim.tmp-12", f"{TRACE}.claim.stale-12")]
    for path in old:
        path.write_text("x")
        stamp = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
        os.utime(path, (stamp, stamp))
    fresh = hook_module.ROOT_CLAIM_DIR / f"{'b' * 32}.claim"
    fresh.write_text("x")

    hook_module.sweep_stale_context_files()

    assert [p.name for p in hook_module.ROOT_CLAIM_DIR.iterdir()] == [fresh.name]


def test_the_sweep_removes_old_notes_of_stopped_firings(hook_module, isolated_hook_state):
    isolated_hook_state.mkdir(parents=True)
    old = isolated_hook_state / "langfuse_firing.0123456789abcdef.99.json"
    fresh = isolated_hook_state / "langfuse_firing.0123456789abcdef.100.json"
    for path in (old, fresh):
        path.write_text("{}")
    stamp = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    os.utime(old, (stamp, stamp))

    hook_module.sweep_stale_context_files()

    assert fresh.exists() and not old.exists()

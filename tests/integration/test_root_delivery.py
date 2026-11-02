from __future__ import annotations

import io
import json
import os
import sys
import threading
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.session_files import a_pid_that_is_gone, assistant, user

SESSION = "session-delivery"


def question(index: int) -> list[dict[str, Any]]:
    return [
        user(f"u{index}", index * 10, f"Question {index}?", SESSION),
        assistant(f"a{index}", index * 10 + 1, [{"type": "text", "text": f"Answer {index}."}], SESSION),
    ]


def append(transcript: Path, rows: list[dict[str, Any]]) -> None:
    transcript.parent.mkdir(parents=True, exist_ok=True)
    with transcript.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


class Hook:
    """Runs the hook the way Claude Code does, with the delivery that each run is given."""

    def __init__(self, hook_module: Any, fake_langfuse: Any, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.hook_module = hook_module
        self.transcript = transcript
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(hook_module, "get_langfuse_config", lambda: hook_module.LangfuseConfig(
            "public", "secret", "https://example.test", None))
        monkeypatch.setattr(hook_module, "create_langfuse_client", lambda _config: fake_langfuse)

    def stop(self, delivery: str) -> None:
        self.monkeypatch.setattr(self.hook_module, "flush_and_shutdown_langfuse_client", lambda _client: delivery)
        payload = {"session_id": SESSION, "transcript_path": str(self.transcript), "hook_event_name": "Stop"}
        self.monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        assert self.hook_module.main() == 0


@pytest.fixture
def hook(hook_module, fake_langfuse, tmp_path, monkeypatch, isolated_hook_state) -> Hook:
    return Hook(hook_module, fake_langfuse, tmp_path / "project" / f"{SESSION}.jsonl", monkeypatch)


def roots(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]


def claims(hook_module: Any) -> list[dict[str, Any]]:
    if not hook_module.ROOT_CLAIM_DIR.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(hook_module.ROOT_CLAIM_DIR.iterdir())]


def state_entries(hook_module: Any) -> dict[str, Any]:
    return json.loads(hook_module.STATE_FILE.read_text()) if hook_module.STATE_FILE.exists() else {}


def firing_notes(hook_module: Any) -> list[Path]:
    return sorted(hook_module.STATE_DIR.glob("langfuse_firing.*"))


def stopped_firing(hook_module: Any, fake_langfuse: Any, transcript: Path, *, process_is_gone: bool = True) -> None:
    """A firing that saved its progress and was stopped before its flush finished, as Claude Code does
    to a SessionEnd hook after 1.5 seconds: no verdict, no claim settled. With process_is_gone False the
    process that did it is another one that is still running (the parent of the test)."""
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", None)
    firing = hook_module.FiringRecord()
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript, firing=firing)
    hook_module.SENT_ROOT_CLAIMS.clear()
    pid = a_pid_that_is_gone() if process_is_gone else os.getppid()
    # Every firing of a real process has a note of its own; in this test they all share one pid.
    note = firing.note_path.rename(firing.note_path.with_name(f"{firing.note_path.name}.{len(firing_notes(hook_module))}.json"))
    claim_files = list(hook_module.ROOT_CLAIM_DIR.iterdir()) if hook_module.ROOT_CLAIM_DIR.exists() else []
    for path in [note, *claim_files]:
        content = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**content, "pid": pid}), encoding="utf-8")


def sent_since(fake_langfuse: Any, count: int) -> list[str]:
    return [o.kwargs["input"]["content"] for o in fake_langfuse.observations[count:] if o.name == "Conversational Turn"]



def test_a_root_that_arrived_stays_claimed_and_is_not_sent_again(hook, hook_module, fake_langfuse):
    append(hook.transcript, question(1))

    hook.stop(hook_module.DELIVERED)
    hook.stop(hook_module.DELIVERED)

    assert len(roots(fake_langfuse)) == 1
    assert [claim["state"] for claim in claims(hook_module)] == ["sent"]


def test_a_root_that_did_not_arrive_is_sent_again_by_the_next_firing(hook, hook_module, fake_langfuse):
    append(hook.transcript, question(1))

    hook.stop(hook_module.LOST)

    assert claims(hook_module) == [], "a root that did not arrive must not stay claimed"
    [undone] = state_entries(hook_module).values()
    assert undone.get("offset", 0) == 0, "the progress of the firing that did not arrive must be undone"

    hook.stop(hook_module.DELIVERED)

    assert len(roots(fake_langfuse)) == 2, "the root of the lost firing was not sent again"
    assert [claim["state"] for claim in claims(hook_module)] == ["sent"]
    [entry] = state_entries(hook_module).values()
    assert entry["offset"] == hook.transcript.stat().st_size


def test_the_turns_of_a_firing_that_did_not_arrive_are_sent_with_the_next_ones(hook, hook_module, fake_langfuse):
    append(hook.transcript, question(1))
    hook.stop(hook_module.DELIVERED)
    append(hook.transcript, question(2))

    hook.stop(hook_module.LOST)
    append(hook.transcript, question(3))
    hook.stop(hook_module.DELIVERED)

    asked = [o.kwargs["input"]["content"] for o in roots(fake_langfuse)]
    assert asked.count("Question 2?") == 2, "the second question was lost once and sent again"
    assert asked.count("Question 3?") == 1
    assert asked.count("Question 1?") == 1


def test_a_firing_that_was_stopped_before_it_knew_whether_its_spans_arrived_is_sent_again(
    hook, hook_module, fake_langfuse
):
    append(hook.transcript, question(1))
    stopped_firing(hook_module, fake_langfuse, hook.transcript)
    sent = len(fake_langfuse.observations)

    hook.stop(hook_module.DELIVERED)

    assert sent_since(fake_langfuse, sent) == ["Question 1?"], "the stopped firing's turn was not sent again"
    assert [claim["state"] for claim in claims(hook_module)] == ["sent"]
    assert firing_notes(hook_module) == []
    [entry] = state_entries(hook_module).values()
    assert entry["offset"] == hook.transcript.stat().st_size


def test_an_outage_that_stops_the_session_end_firing_does_not_lose_the_first_request(
    hook, hook_module, fake_langfuse
):
    # The Stop firing is lost and put back; the SessionEnd firing sends the request again
    # and is stopped by Claude Code before it knows whether it arrived; the session is resumed.
    append(hook.transcript, question(1))
    hook.stop(hook_module.LOST)
    stopped_firing(hook_module, fake_langfuse, hook.transcript)
    append(hook.transcript, question(2))
    sent = len(fake_langfuse.observations)

    hook.stop(hook_module.DELIVERED)

    assert sent_since(fake_langfuse, sent) == ["Question 1?", "Question 2?"]


def test_the_note_of_a_firing_that_is_still_running_is_left_alone(hook, hook_module, fake_langfuse):
    append(hook.transcript, question(1))
    stopped_firing(hook_module, fake_langfuse, hook.transcript, process_is_gone=False)
    [note] = firing_notes(hook_module)
    append(hook.transcript, question(2))
    sent = len(fake_langfuse.observations)

    hook.stop(hook_module.DELIVERED)

    assert sent_since(fake_langfuse, sent) == ["Question 2?"]
    assert firing_notes(hook_module) == [note], "the running firing still needs its note"


def test_the_note_of_a_firing_that_has_had_longer_than_a_hook_can_run_counts_as_stopped(
    hook, hook_module, fake_langfuse
):
    append(hook.transcript, question(1))
    stopped_firing(hook_module, fake_langfuse, hook.transcript, process_is_gone=False)
    [note] = firing_notes(hook_module)
    content = json.loads(note.read_text(encoding="utf-8"))
    long_ago = datetime.now(timezone.utc) - hook_module.FIRING_NOTE_MAX_AGE - timedelta(minutes=1)
    note.write_text(json.dumps({**content, "at": long_ago.isoformat()}), encoding="utf-8")
    sent = len(fake_langfuse.observations)

    hook.stop(hook_module.DELIVERED)

    assert sent_since(fake_langfuse, sent) == ["Question 1?"]
    assert firing_notes(hook_module) == []


def test_two_firings_that_were_stopped_put_back_the_oldest_state(hook, hook_module, fake_langfuse):
    # The second firing started while the first was still running, so it went on from the first one's progress.
    append(hook.transcript, question(1) + question(2))
    stopped_firing(hook_module, fake_langfuse, hook.transcript, process_is_gone=False)
    append(hook.transcript, question(3))
    stopped_firing(hook_module, fake_langfuse, hook.transcript)
    for note in firing_notes(hook_module):
        content = json.loads(note.read_text(encoding="utf-8"))
        note.write_text(json.dumps({**content, "pid": a_pid_that_is_gone()}), encoding="utf-8")
    sent = len(fake_langfuse.observations)

    hook.stop(hook_module.DELIVERED)

    assert sent_since(fake_langfuse, sent) == ["Question 1?", "Question 2?", "Question 3?"]


def test_firings_that_were_stopped_again_and_again_are_given_up_on(hook, hook_module, fake_langfuse):
    append(hook.transcript, question(1))
    for _ in range(hook_module.MAX_UNCONFIRMED_FIRINGS + 1):
        stopped_firing(hook_module, fake_langfuse, hook.transcript)
    sent = len(fake_langfuse.observations)

    hook.stop(hook_module.DELIVERED)

    assert sent_since(fake_langfuse, sent) == [], "the hook must go on after a few tries in a row"
    assert firing_notes(hook_module) == []


@pytest.mark.parametrize("delivery", ["delivered", "lost", "refused"])
def test_a_firing_that_knows_what_became_of_its_spans_leaves_no_note(hook, hook_module, fake_langfuse, delivery):
    append(hook.transcript, question(1))

    hook.stop(delivery)

    assert firing_notes(hook_module) == []


def test_a_note_that_cannot_be_written_does_not_stop_the_firing(hook, hook_module, fake_langfuse, monkeypatch):
    append(hook.transcript, question(1))
    monkeypatch.setattr(hook_module, "FIRING_NOTE_PATTERN", "no-such-folder/{session}.{pid}")

    hook.stop(hook_module.DELIVERED)

    assert len(roots(fake_langfuse)) == 1
    [entry] = state_entries(hook_module).values()
    assert entry["offset"] == hook.transcript.stat().st_size


def test_a_session_that_still_has_its_state_under_the_old_key_is_not_sent_again_after_a_lost_firing(
    hook, hook_module, fake_langfuse
):
    # The firing that moves the state to the key of the session is lost: the old entry
    # must come back whole, not be replaced by an empty one under the new key.
    append(hook.transcript, question(1) + question(2))
    hook.stop(hook_module.DELIVERED)
    state = state_entries(hook_module)
    session_key = hook_module.get_session_state_key(SESSION)
    legacy_key = hook_module.get_legacy_session_state_key(SESSION, str(hook.transcript))
    legacy_entry = {k: v for k, v in state[session_key].items() if k not in ("transcript_path", "head_len", "head_hash")}
    hook_module.STATE_FILE.write_text(json.dumps({legacy_key: legacy_entry}), encoding="utf-8")
    append(hook.transcript, question(3))

    hook.stop(hook_module.LOST)
    hook.stop(hook_module.DELIVERED)

    asked = [o.kwargs["input"]["content"] for o in roots(fake_langfuse)]
    assert asked.count("Question 1?") == 1
    assert asked.count("Question 2?") == 1
    assert asked.count("Question 3?") == 2, "the third question was lost once and sent again"
    assert list(state_entries(hook_module)) == [session_key]


def test_a_batch_that_langfuse_refused_is_not_sent_again_but_does_not_block_the_root(
    hook, hook_module, fake_langfuse
):
    append(hook.transcript, question(1))

    hook.stop(hook_module.REFUSED)
    hook.stop(hook_module.DELIVERED)

    assert len(roots(fake_langfuse)) == 1, "a refused batch is the same refusal when it is sent again"
    assert claims(hook_module) == [], "the claim of a refused root must not block a later repair"


def test_the_hook_gives_up_after_a_few_tries_in_a_row(hook, hook_module, fake_langfuse):
    append(hook.transcript, question(1))

    for _ in range(hook_module.MAX_UNCONFIRMED_FIRINGS + 1):
        hook.stop(hook_module.LOST)

    assert len(roots(fake_langfuse)) == hook_module.MAX_UNCONFIRMED_FIRINGS + 1
    [entry] = state_entries(hook_module).values()
    assert entry["offset"] == hook.transcript.stat().st_size, "the last firing's progress must be kept"
    hook.stop(hook_module.DELIVERED)
    assert len(roots(fake_langfuse)) == hook_module.MAX_UNCONFIRMED_FIRINGS + 1


def test_a_delivery_in_between_resets_the_count_of_tries(hook, hook_module, fake_langfuse):
    append(hook.transcript, question(1))
    for _ in range(hook_module.MAX_UNCONFIRMED_FIRINGS):
        hook.stop(hook_module.LOST)
    hook.stop(hook_module.DELIVERED)
    append(hook.transcript, question(2))

    hook.stop(hook_module.LOST)
    hook.stop(hook_module.DELIVERED)

    asked = [o.kwargs["input"]["content"] for o in roots(fake_langfuse)]
    assert asked.count("Question 2?") == 2


def test_a_root_that_fails_to_go_out_gives_its_claim_back(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    append(transcript, question(1))
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", None)

    def refuse(self: Any, *, end_time: Any = None) -> None:
        raise RuntimeError("the SDK refused")

    monkeypatch.setattr(type(fake_langfuse), "_create_observation_from_otel_span",
                        _observation_that_cannot_end(fake_langfuse, refuse), raising=True)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)

    assert claims(hook_module) == []
    assert hook_module.SENT_ROOT_CLAIMS == []


def _observation_that_cannot_end(fake_langfuse: Any, refuse: Any) -> Any:
    original = type(fake_langfuse)._create_observation_from_otel_span

    def create(self: Any, *, otel_span: Any, as_type: str, **kwargs: Any) -> Any:
        observation = original(self, otel_span=otel_span, as_type=as_type, **kwargs)
        if kwargs.get("input", {}).get("role") == "user" and as_type == "agent" and "Conversational" in otel_span.name:
            observation.end = types.MethodType(refuse, observation)
        return observation

    return create


def fake_requests(monkeypatch: pytest.MonkeyPatch, answers: list[Any]) -> Any:
    """A requests whose Session.send answers with the given status codes, or raises the given error."""
    module = types.ModuleType("requests")

    class Session:
        def send(self, request: Any, **_kwargs: Any) -> Any:
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return types.SimpleNamespace(status_code=answer)

    module.Session = Session
    monkeypatch.setitem(sys.modules, "requests", module)
    return Session


def span_batch(body: bytes) -> Any:
    return types.SimpleNamespace(url="https://langfuse.test/api/public/otel/v1/traces", body=body)


@pytest.mark.parametrize("sends, expected", [
    ([(b"a", 200)], "delivered"),
    ([(b"a", 200), (b"b", 202)], "delivered"),
    ([(b"a", 500)], "lost"),
    ([(b"a", 503), (b"a", 200)], "delivered"),
    ([(b"a", 503), (b"b", 200)], "lost"),
    ([(b"a", 200), (b"b", 429)], "lost"),
    ([(b"a", ConnectionError("down"))], "lost"),
    ([(b"a", 401)], "refused"),
    ([(b"a", 413)], "refused"),
    ([(b"a", 200), (b"b", 401)], "refused"),
    ([(b"a", 401), (b"b", 503)], "lost"),
])
def test_the_export_watch_tells_what_became_of_the_batches(hook_module, monkeypatch, sends, expected):
    # A batch that is sent again with the same bytes counts by its last try.
    session = fake_requests(monkeypatch, [answer for _body, answer in sends])
    watch = hook_module.ExportWatch()
    assert hook_module.install_export_watch(watch) is True

    for body, _answer in sends:
        try:
            session().send(span_batch(body))
        except ConnectionError:
            pass

    assert watch.verdict() == expected


def test_the_export_watch_ignores_what_is_not_a_span_batch(hook_module, monkeypatch):
    session = fake_requests(monkeypatch, [500, 500])
    watch = hook_module.ExportWatch()
    hook_module.install_export_watch(watch)

    session().send(types.SimpleNamespace(url="https://langfuse.test/api/public/media", body=b"x"))
    session().send(types.SimpleNamespace(url="https://langfuse.test/api/public/ingestion", body=b"y"))

    assert watch.verdict() == "delivered"


def test_the_export_watch_is_installed_once_and_hands_the_error_on(hook_module, monkeypatch):
    session = fake_requests(monkeypatch, [ConnectionError("down")])
    watch = hook_module.ExportWatch()
    hook_module.install_export_watch(watch)
    hook_module.install_export_watch(watch)

    with pytest.raises(ConnectionError):
        session().send(span_batch(b"x"))

    assert list(watch.last_try.values()) == ["lost"]


def test_the_export_watch_is_off_without_requests(hook_module, monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", None)

    assert hook_module.install_export_watch(hook_module.ExportWatch()) is False


def test_a_flush_that_does_not_finish_in_time_is_lost(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "FLUSH_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()

    class Slow:
        def flush(self) -> None:
            release.wait(2)

        def shutdown(self) -> None:
            pass

    try:
        assert hook_module.flush_and_shutdown_langfuse_client(Slow()) == "lost"
    finally:
        release.set()


def test_a_flush_that_finishes_gives_the_verdict_of_the_watch(hook_module):
    class Refused:
        def flush(self) -> None:
            hook_module.EXPORT_WATCH.record(b"one", 401)

        def shutdown(self) -> None:
            pass

    class Quiet:
        def flush(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

    assert hook_module.flush_and_shutdown_langfuse_client(Quiet()) == "delivered"
    assert hook_module.flush_and_shutdown_langfuse_client(Refused()) == "refused"
    assert hook_module.flush_and_shutdown_langfuse_client(None) == "delivered"

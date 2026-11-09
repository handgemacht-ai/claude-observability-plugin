from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

import pytest

from tests.export_helpers import (
    Answer,
    a_session_with_one_question,
    a_session_with_two_helpers,
    a_span_batch,
    a_watch_of_the_exports,
    expect_logged,
    expect_not_logged,
    expect_verdict,
    flushes_before_the_end,
    helpers_ended,
    helpers_started,
    json_answer,
    langfuse_answers,
    langfuse_gets_a_span_batch_when_flushed,
    log_text,
    protobuf_answer,
    run_a_firing_in_a_new_process,
    send_to_langfuse,
    the_sdk_says,
    turns_sent,
)

OTLP_EXPORTER = "opentelemetry.exporter.otlp.proto.http.trace_exporter"
DROPPED_BATCH = "Dropping span batch: serialized size 44040192 bytes exceeds max_request_size 41943040 bytes."


@pytest.mark.parametrize("answer", [
    pytest.param(protobuf_answer(3, "three spans were too big"), id="protobuf"),
    pytest.param(json_answer(2, "two spans were too big"), id="json"),
])
def test_a_batch_that_langfuse_took_only_in_part_counts_as_lost(hook_module, monkeypatch, answer):
    langfuse_answers(monkeypatch, [Answer(200, answer)])
    watch = a_watch_of_the_exports(hook_module)

    send_to_langfuse(a_span_batch())

    expect_verdict(watch, "lost")


@pytest.mark.parametrize("answer", [
    pytest.param(b"", id="empty"),
    pytest.param(b"{}", id="json without partial success"),
    pytest.param(json_answer(0, ""), id="json with none rejected"),
    pytest.param(protobuf_answer(0, ""), id="protobuf with an empty partial success"),
    pytest.param(protobuf_answer(0, "a span was changed"), id="a warning and no rejected span"),
    pytest.param(b"\x0a\x09\x08", id="protobuf cut off"),
    pytest.param(b"<html>ok</html>", id="not an OTLP answer"),
])
def test_a_batch_that_langfuse_took_whole_counts_as_delivered(hook_module, monkeypatch, answer):
    langfuse_answers(monkeypatch, [Answer(200, answer)])
    watch = a_watch_of_the_exports(hook_module)

    send_to_langfuse(a_span_batch())

    expect_verdict(watch, "delivered")


def test_an_answer_that_is_streamed_is_left_unread(hook_module, monkeypatch):
    langfuse_answers(monkeypatch, [Answer(200, protobuf_answer(3, "too big"))])
    watch = a_watch_of_the_exports(hook_module)

    send_to_langfuse(a_span_batch(), stream=True)

    expect_verdict(watch, "delivered")


def test_a_rejected_batch_that_is_sent_again_and_taken_counts_by_its_last_try(hook_module, monkeypatch):
    langfuse_answers(monkeypatch, [Answer(200, protobuf_answer(3, "too big")), Answer(200)])
    watch = a_watch_of_the_exports(hook_module)

    send_to_langfuse(a_span_batch(b"the same batch"))
    send_to_langfuse(a_span_batch(b"the same batch"))

    expect_verdict(watch, "delivered")


def test_the_log_tells_how_many_spans_langfuse_rejected_and_why(hook_module, monkeypatch):
    langfuse_answers(monkeypatch, [Answer(200, protobuf_answer(352, "span exceeds the size limit"))])
    a_watch_of_the_exports(hook_module)

    send_to_langfuse(a_span_batch())

    expect_logged(hook_module, "rejected_spans=352", "span exceeds the size limit")


def test_the_log_carries_the_warning_of_an_answer_that_rejected_nothing(hook_module, monkeypatch):
    langfuse_answers(monkeypatch, [Answer(200, protobuf_answer(0, "a span was changed"))])
    a_watch_of_the_exports(hook_module)

    send_to_langfuse(a_span_batch())

    expect_logged(hook_module, "rejected_spans=0", "a span was changed")


def test_a_firing_whose_spans_langfuse_rejected_in_part_is_sent_again_by_the_next_firing(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    session = a_session_with_one_question(tmp_path)
    langfuse_answers(monkeypatch, [Answer(200, protobuf_answer(1, "too big")), Answer(200)])
    langfuse_gets_a_span_batch_when_flushed(fake_langfuse)

    run_a_firing_in_a_new_process(hook_module, fake_langfuse, session, monkeypatch)
    run_a_firing_in_a_new_process(hook_module, fake_langfuse, session, monkeypatch)

    assert turns_sent(fake_langfuse) == 2


def test_warnings_of_the_opentelemetry_exporter_reach_the_plugin_log(hook_module):
    hook_module.route_sdk_warnings_to_log()

    the_sdk_says(OTLP_EXPORTER, logging.WARNING, DROPPED_BATCH)

    expect_logged(hook_module, "[WARNING]", OTLP_EXPORTER, DROPPED_BATCH)


def test_errors_of_the_langfuse_sdk_reach_the_plugin_log(hook_module):
    hook_module.route_sdk_warnings_to_log()

    the_sdk_says("langfuse", logging.ERROR, "Failed to export span batch")

    expect_logged(hook_module, "[ERROR]", "langfuse: Failed to export span batch")


@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
def test_what_the_sdk_loggers_say_below_a_warning_stays_out_of_the_plugin_log(hook_module, level):
    hook_module.route_sdk_warnings_to_log()

    the_sdk_says("langfuse", level, "Batch exported")

    expect_not_logged(hook_module, "Batch exported")


def test_routing_the_sdk_loggers_twice_logs_a_warning_once(hook_module):
    hook_module.route_sdk_warnings_to_log()
    hook_module.route_sdk_warnings_to_log()

    the_sdk_says("opentelemetry", logging.WARNING, DROPPED_BATCH)

    assert log_text(hook_module).count(DROPPED_BATCH) == 1


def test_a_logger_set_above_warning_still_gives_its_warnings_to_the_plugin_log(hook_module):
    logging.getLogger("langfuse").setLevel(logging.ERROR)
    hook_module.route_sdk_warnings_to_log()

    the_sdk_says("langfuse", logging.WARNING, "Span batch dropped")

    expect_logged(hook_module, "Span batch dropped")


def test_a_firing_logs_what_the_exporter_warns_while_it_flushes(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    session = a_session_with_one_question(tmp_path)
    fake_langfuse.on_flush = lambda: the_sdk_says(OTLP_EXPORTER, logging.WARNING, DROPPED_BATCH)

    run_a_firing_in_a_new_process(hook_module, fake_langfuse, session, monkeypatch)

    expect_logged(hook_module, DROPPED_BATCH)


def test_the_client_is_made_to_send_smaller_batches_than_the_sdk_default(hook_module):
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", None)

    client = hook_module.create_langfuse_client(config)

    assert client.kwargs["flush_at"] == hook_module.EXPORT_BATCH_SPANS
    assert 0 < hook_module.EXPORT_BATCH_SPANS < 512


def test_a_batch_size_that_the_operator_set_is_left_to_the_sdk(hook_module, monkeypatch):
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", None)
    monkeypatch.setenv("LANGFUSE_FLUSH_AT", "100")

    client = hook_module.create_langfuse_client(config)

    assert client.kwargs["flush_at"] is None


def test_a_stop_firing_flushes_after_each_helper_before_the_next_one_is_read(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    session = a_session_with_two_helpers(tmp_path)

    run_a_firing_in_a_new_process(hook_module, fake_langfuse, session, monkeypatch, event="Stop")

    after_first, after_second = flushes_before_the_end(fake_langfuse)
    assert helpers_started(after_first) == helpers_ended(after_first) == ["Subagent: checker · First"]
    assert helpers_ended(after_second) == ["Subagent: checker · First", "Subagent: checker · Second"]


def test_a_session_end_firing_does_not_flush_between_helpers(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    session = a_session_with_two_helpers(tmp_path)

    run_a_firing_in_a_new_process(hook_module, fake_langfuse, session, monkeypatch, event="SessionEnd")

    assert flushes_before_the_end(fake_langfuse) == []
    assert len(fake_langfuse.flushes) == 1


def test_helpers_inside_a_helper_go_out_with_it(
    hook_module, fake_langfuse, isolated_hook_state, fixture_transcript_path, tmp_path, monkeypatch
):
    transcript = a_copy_of_the_three_level_session(fixture_transcript_path, tmp_path)
    hook_module.PIECE_FLUSH.enabled = True

    an_emission_of(hook_module, fake_langfuse, transcript)

    [after_the_helper_and_its_helpers] = fake_langfuse.flushes
    assert len(helpers_ended(after_the_helper_and_its_helpers)) == 3


def test_flushing_between_helpers_stops_after_a_flush_that_does_not_finish_in_time(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    session = a_session_with_two_helpers(tmp_path)
    monkeypatch.setattr(hook_module, "FLUSH_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()
    fake_langfuse.on_flush = lambda: release.wait(2)

    try:
        run_a_firing_in_a_new_process(hook_module, fake_langfuse, session, monkeypatch, event="Stop")
    finally:
        release.set()

    assert len(flushes_before_the_end(fake_langfuse)) == 1
    expect_logged(hook_module, "did not finish")


def test_a_flush_between_helpers_that_fails_does_not_stop_the_firing(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    session = a_session_with_two_helpers(tmp_path)

    def fail() -> None:
        raise RuntimeError("the SDK refused")

    fake_langfuse.on_flush = fail

    run_a_firing_in_a_new_process(hook_module, fake_langfuse, session, monkeypatch, event="Stop")

    assert turns_sent(fake_langfuse) == 1
    assert len(flushes_before_the_end(fake_langfuse)) == 2


def a_copy_of_the_three_level_session(fixture_transcript_path, tmp_path: Path) -> Path:
    source = fixture_transcript_path("nested_agents_three_levels")
    shutil.copytree(source.parent, tmp_path / "three_levels")
    return tmp_path / "three_levels" / "transcript.jsonl"


def an_emission_of(hook_module, fake_langfuse, transcript: Path) -> None:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-deep", transcript, flush_deferred_agent_turns=True
    )

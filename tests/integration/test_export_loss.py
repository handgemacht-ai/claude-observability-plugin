from __future__ import annotations

import logging

import pytest

from tests.export_helpers import (
    Answer,
    a_langfuse_config,
    a_session_with_one_question,
    a_session_with_two_helpers,
    a_span_batch,
    a_watch_of_the_exports,
    expect_logged,
    expect_not_logged,
    expect_one_flush_with_the_helpers,
    expect_the_batch_size_of_the_sdk,
    expect_verdict,
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
def test_a_batch_that_langfuse_took_only_in_part_counts_as_refused(hook_module, monkeypatch, answer):
    langfuse_answers(monkeypatch, [Answer(200, answer)])
    watch = a_watch_of_the_exports(hook_module)

    send_to_langfuse(a_span_batch())

    expect_verdict(watch, "refused")


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


def test_the_log_warns_how_many_spans_langfuse_rejected_and_why(hook_module, monkeypatch):
    langfuse_answers(monkeypatch, [Answer(200, protobuf_answer(352, "span exceeds the size limit"))])
    a_watch_of_the_exports(hook_module)

    send_to_langfuse(a_span_batch())

    expect_logged(hook_module, "[WARNING]", "rejected_spans=352", "span exceeds the size limit", "not sent again")


def test_the_log_carries_the_warning_of_an_answer_that_rejected_nothing(hook_module, monkeypatch):
    langfuse_answers(monkeypatch, [Answer(200, protobuf_answer(0, "a span was changed"))])
    a_watch_of_the_exports(hook_module)

    send_to_langfuse(a_span_batch())

    expect_logged(hook_module, "rejected_spans=0", "a span was changed")


def test_a_firing_whose_spans_langfuse_rejected_in_part_is_not_sent_again_by_the_next_firing(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    session = a_session_with_one_question(tmp_path)
    langfuse_answers(monkeypatch, [Answer(200, protobuf_answer(1, "too big")), Answer(200)])
    langfuse_gets_a_span_batch_when_flushed(fake_langfuse)

    run_a_firing_in_a_new_process(hook_module, fake_langfuse, session, monkeypatch)
    run_a_firing_in_a_new_process(hook_module, fake_langfuse, session, monkeypatch)

    assert turns_sent(fake_langfuse) == 1


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


def test_the_client_sends_at_the_batch_size_of_the_sdk(hook_module):
    config = a_langfuse_config(hook_module)

    client = hook_module.create_langfuse_client(config)

    expect_the_batch_size_of_the_sdk(client)


@pytest.mark.parametrize("event", ["Stop", "SessionEnd"])
def test_a_firing_sends_all_its_helpers_in_one_flush_at_its_end(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch, event
):
    session = a_session_with_two_helpers(tmp_path)

    run_a_firing_in_a_new_process(hook_module, fake_langfuse, session, monkeypatch, event=event)

    expect_one_flush_with_the_helpers(fake_langfuse, "Subagent: checker · First", "Subagent: checker · Second")

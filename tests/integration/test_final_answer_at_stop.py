from __future__ import annotations

import pytest

from tests.final_answers import (
    ANSWER,
    Hook,
    a_turn_without_its_final_answer,
    claude_code_writes_the_final_answer,
    claude_code_writes_the_final_answer_after,
    expect_each_step_sent_once,
    expect_final_answer_sent,
    expect_lost_turn_sent_again,
    expect_next_turn_sent_once,
    expect_nothing_started,
    expect_one_process_started,
    expect_turn_sent_without_final_answer,
    no_process_can_start,
    the_next_turn_ends_while_the_waiting_process_flushes_in_vain,
    the_wait_for_the_final_answer_ends_after,
)
from tests.session_files import SESSION


@pytest.fixture
def hook(hook_module, fake_langfuse, tmp_path, monkeypatch, isolated_hook_state) -> Hook:
    return Hook(hook_module, fake_langfuse, tmp_path / f"{SESSION}.jsonl", monkeypatch)


def test_a_final_answer_written_after_stop_is_sent_though_the_session_stays_idle(hook, fake_langfuse):
    a_turn_without_its_final_answer(hook.transcript)

    hook.stop(answer=ANSWER)
    writer = claude_code_writes_the_final_answer_after(hook.transcript, 0.05)
    hook.run_the_processes_it_started()
    writer.join()

    expect_final_answer_sent(fake_langfuse, ANSWER)


def test_a_stop_whose_final_answer_is_in_the_log_is_sent_at_once(hook, fake_langfuse):
    a_turn_without_its_final_answer(hook.transcript)
    claude_code_writes_the_final_answer(hook.transcript)

    hook.stop(answer=ANSWER)

    expect_nothing_started(hook)
    expect_final_answer_sent(fake_langfuse, ANSWER)


def test_a_stop_without_the_final_answer_in_its_input_is_sent_at_once(hook, fake_langfuse):
    a_turn_without_its_final_answer(hook.transcript)

    hook.stop(answer=None)

    expect_nothing_started(hook)
    expect_turn_sent_without_final_answer(fake_langfuse)


def test_a_stop_that_cannot_start_the_waiting_process_is_sent_at_once(hook, fake_langfuse, monkeypatch):
    a_turn_without_its_final_answer(hook.transcript)
    no_process_can_start(monkeypatch)

    hook.stop(answer=ANSWER)

    expect_turn_sent_without_final_answer(fake_langfuse)


def test_a_final_answer_that_never_reaches_the_log_is_sent_without_it_after_the_wait(
        hook, hook_module, fake_langfuse, monkeypatch):
    a_turn_without_its_final_answer(hook.transcript)
    the_wait_for_the_final_answer_ends_after(hook_module, monkeypatch, 0.3)

    hook.stop(answer=ANSWER)
    expect_one_process_started(hook)
    hook.run_the_processes_it_started()

    expect_turn_sent_without_final_answer(fake_langfuse)


def test_a_session_end_while_the_stop_waits_sends_the_turn_once(hook, fake_langfuse):
    a_turn_without_its_final_answer(hook.transcript)

    hook.stop(answer=ANSWER)
    claude_code_writes_the_final_answer(hook.transcript)
    hook.session_end()
    hook.run_the_processes_it_started()

    expect_final_answer_sent(fake_langfuse, ANSWER)
    expect_each_step_sent_once(fake_langfuse)


def test_a_next_turn_that_ends_while_the_waiting_process_flushes_in_vain_is_sent_once(hook, fake_langfuse):
    a_turn_without_its_final_answer(hook.transcript)
    hook.stop(answer=ANSWER)
    claude_code_writes_the_final_answer(hook.transcript)
    the_next_turn_ends_while_the_waiting_process_flushes_in_vain(hook)

    hook.run_the_processes_it_started()
    hook.wait_for_the_hooks_in_background()
    hook.session_end()

    expect_next_turn_sent_once(fake_langfuse)
    expect_lost_turn_sent_again(fake_langfuse, ANSWER)


def test_a_session_end_before_the_final_answer_reaches_the_log_sends_the_turn_once(hook, fake_langfuse):
    a_turn_without_its_final_answer(hook.transcript)

    hook.stop(answer=ANSWER)
    hook.session_end()
    claude_code_writes_the_final_answer(hook.transcript)
    hook.run_the_processes_it_started()

    expect_each_step_sent_once(fake_langfuse)

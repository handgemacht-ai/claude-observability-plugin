from __future__ import annotations

from tests.held_turns import fire
from tests.session_files import (
    HELPER_BACKGROUND_CALL,
    HELPER_LAST_CALL,
    HELPER_LATER_CALL,
    HELPER_SECOND_BACKGROUND_CALL,
    INNER_LATER_CALL,
)
from tests.woken_helpers import (
    FIRST_RUN,
    INNER_RUN,
    LATER_RUN,
    a_session_whose_helper_of_a_helper_its_own_background_task_wakes,
    a_session_whose_helper_waits_on_its_own_background_task,
    expect_helper_runs,
    expect_step_not_sent,
    expect_step_sent_once,
    expect_steps_sent_once,
    sent_at_the_helper_first_stop,
    the_helper_wakes_and_stops_again,
    the_helper_wakes_and_works,
    the_helper_wakes_twice_and_only_its_last_stop_is_reported,
)


def test_a_helper_woken_by_its_own_background_task_sends_its_later_run_at_its_next_stop(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_whose_helper_waits_on_its_own_background_task(tmp_path)
    sent_at_the_helper_first_stop(hook_module, fake_langfuse, session)
    the_helper_wakes_and_stops_again(session)

    fire(hook_module, fake_langfuse, session)

    expect_step_sent_once(fake_langfuse, HELPER_LATER_CALL)
    expect_helper_runs(fake_langfuse, [FIRST_RUN, LATER_RUN])


def test_a_woken_helper_first_sent_after_its_later_stop_sends_each_run_once(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_whose_helper_waits_on_its_own_background_task(tmp_path)
    the_helper_wakes_and_stops_again(session)

    fire(hook_module, fake_langfuse, session)

    expect_step_sent_once(fake_langfuse, HELPER_LATER_CALL)
    expect_helper_runs(fake_langfuse, [FIRST_RUN, LATER_RUN])


def test_the_later_run_of_a_woken_helper_waits_until_the_helper_stops_again(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_whose_helper_waits_on_its_own_background_task(tmp_path)
    the_helper_wakes_and_works(session)

    fire(hook_module, fake_langfuse, session)

    expect_step_not_sent(fake_langfuse, HELPER_LATER_CALL)
    expect_helper_runs(fake_langfuse, [FIRST_RUN])


def test_a_helper_woken_twice_whose_stop_in_between_was_not_reported_sends_every_step_once(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_whose_helper_waits_on_its_own_background_task(tmp_path)
    sent_at_the_helper_first_stop(hook_module, fake_langfuse, session)
    the_helper_wakes_twice_and_only_its_last_stop_is_reported(session)

    fire(hook_module, fake_langfuse, session)

    expect_steps_sent_once(fake_langfuse, [HELPER_BACKGROUND_CALL, HELPER_LATER_CALL,
                                           HELPER_SECOND_BACKGROUND_CALL, HELPER_LAST_CALL])
    expect_helper_runs(fake_langfuse, [FIRST_RUN, LATER_RUN])


def test_a_helper_of_a_helper_that_its_own_background_task_wakes_is_sent_whole_with_its_parent(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_whose_helper_of_a_helper_its_own_background_task_wakes(tmp_path)

    fire(hook_module, fake_langfuse, session)

    expect_step_sent_once(fake_langfuse, INNER_LATER_CALL)
    expect_helper_runs(fake_langfuse, [FIRST_RUN, INNER_RUN])


def test_a_woken_helper_of_a_helper_whose_stop_the_main_transcript_queued_stays_one_run_under_its_parent(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_whose_helper_of_a_helper_its_own_background_task_wakes(
        tmp_path, stop_queued_in_the_main_transcript=True)

    fire(hook_module, fake_langfuse, session)

    expect_step_sent_once(fake_langfuse, INNER_LATER_CALL)
    expect_helper_runs(fake_langfuse, [FIRST_RUN, INNER_RUN])

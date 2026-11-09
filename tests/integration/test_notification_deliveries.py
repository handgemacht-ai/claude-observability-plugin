from __future__ import annotations

from tests.held_turns import expect_turn_held, expect_turn_sent_with, fire
from tests.session_files import (
    a_finished_turn_while_a_helper_takes_its_own_background_task_result,
    a_helper_hand_back,
    a_helper_result_attached_to_the_running_turn,
    a_resumed_helper_that_did_not_hand_back_again,
    a_session_with,
    answered,
    followed_by_the_helper_notification,
)


def test_a_helper_result_taken_into_the_running_turn_does_not_hold_the_answered_turn(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_with(tmp_path, answered(a_helper_result_attached_to_the_running_turn(), "All done."))

    fire(hook_module, fake_langfuse, session)

    expect_turn_sent_with(fake_langfuse, "All done.")


def test_a_helper_result_attached_to_the_running_turn_holds_the_turn_until_it_is_answered(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_with(tmp_path, a_helper_result_attached_to_the_running_turn())

    fire(hook_module, fake_langfuse, session)

    expect_turn_held(fake_langfuse)


def test_a_helper_hand_back_delivers_the_queued_notification_of_the_helper(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_with(tmp_path, answered(a_helper_hand_back(), "The helper found it."))

    fire(hook_module, fake_langfuse, session)

    expect_turn_sent_with(fake_langfuse, "The helper found it.")


def test_a_helper_hand_back_holds_the_turn_until_it_is_answered(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_with(tmp_path, a_helper_hand_back())

    fire(hook_module, fake_langfuse, session)

    expect_turn_held(fake_langfuse)


def test_the_result_of_a_background_task_of_a_helper_does_not_hold_the_turn(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_with(tmp_path, a_finished_turn_while_a_helper_takes_its_own_background_task_result())

    fire(hook_module, fake_langfuse, session)

    expect_turn_sent_with(fake_langfuse, "Done.")


def test_a_notification_that_repeats_a_hand_back_after_the_final_answer_does_not_hold_the_turn(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_with(
        tmp_path, followed_by_the_helper_notification(answered(a_helper_hand_back(), "The helper found it."))
    )

    fire(hook_module, fake_langfuse, session)

    expect_turn_sent_with(fake_langfuse, "The helper found it.")


def test_the_notification_of_a_resumed_helper_that_did_not_hand_back_again_holds_the_turn(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    session = a_session_with(tmp_path, a_resumed_helper_that_did_not_hand_back_again())

    fire(hook_module, fake_langfuse, session)

    expect_turn_held(fake_langfuse)

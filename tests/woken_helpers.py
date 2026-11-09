"""A session whose helper a background task of its own wakes after it stopped, and checks of what was sent of it."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.held_turns import fire
from tests.session_files import (
    HELPER_META,
    HELPER_TASK,
    SessionDir,
    a_helper_that_waits_on_its_own_background_task,
    a_main_turn_answered_at_the_helper_first_stop,
    a_session_with,
    answered_at_the_helper_later_stop,
    stopped_again,
    woken_by_its_background_task,
)

FIRST_RUN = "Subagent: explorer · Look up"
LATER_RUN = "Subagent: explorer · Look up (resumed #1)"


def a_session_whose_helper_waits_on_its_own_background_task(tmp_path: Path) -> SessionDir:
    session = a_session_with(tmp_path, a_main_turn_answered_at_the_helper_first_stop())
    session.add_agent(HELPER_TASK, a_helper_that_waits_on_its_own_background_task(), HELPER_META)
    return session


def sent_at_the_helper_first_stop(hook_module: Any, fake_langfuse: Any, session: SessionDir) -> None:
    fire(hook_module, fake_langfuse, session)


def the_helper_wakes_and_works(session: SessionDir) -> None:
    session.add_agent(HELPER_TASK, woken_by_its_background_task(a_helper_that_waits_on_its_own_background_task()),
                      HELPER_META)


def the_helper_wakes_and_stops_again(session: SessionDir) -> None:
    session.add_agent(
        HELPER_TASK,
        stopped_again(woken_by_its_background_task(a_helper_that_waits_on_its_own_background_task())),
        HELPER_META,
    )
    session.write_main(answered_at_the_helper_later_stop(a_main_turn_answered_at_the_helper_first_stop()))


def tool_spans_of(fake_langfuse: Any, tool_use_id: str) -> list[Any]:
    return [o for o in fake_langfuse.observations
            if o.as_type == "tool" and o.kwargs["metadata"].get("tool_id") == tool_use_id]


def expect_step_sent_once(fake_langfuse: Any, tool_use_id: str) -> None:
    assert len(tool_spans_of(fake_langfuse, tool_use_id)) == 1


def expect_step_not_sent(fake_langfuse: Any, tool_use_id: str) -> None:
    assert tool_spans_of(fake_langfuse, tool_use_id) == []


def expect_helper_runs(fake_langfuse: Any, names: list[str]) -> None:
    assert [o.name for o in fake_langfuse.observations if o.name.startswith("Subagent:")] == names

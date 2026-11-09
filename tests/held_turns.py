"""Fire the hook on a session as at a Stop and check whether its open turn was sent or held."""
from __future__ import annotations

from typing import Any

from tests.session_files import SESSION, SessionDir


def fire(hook_module: Any, fake_langfuse: Any, session: SessionDir) -> None:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, session.transcript)


def turn_roots(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]


def expect_turn_sent_with(fake_langfuse: Any, final_answer: str) -> None:
    [root] = turn_roots(fake_langfuse)
    assert root.output == {"role": "assistant", "content": final_answer}


def expect_turn_held(fake_langfuse: Any) -> None:
    assert turn_roots(fake_langfuse) == []

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "span_ids.json").read_text(encoding="utf-8")
)
CASES = {case["name"]: case for case in FIXTURE["cases"]}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def sent_roots(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.name == "Conversational Turn" and o.end_time is not None]


def run_case(hook_module: Any, fake_langfuse: Any, tmp_path: Path, case: dict[str, Any]) -> None:
    transcript = tmp_path / "transcript.jsonl"
    for filename, subagent in case["subagents"].items():
        write_jsonl(tmp_path / "transcript" / "subagents" / f"{filename}.jsonl", subagent["rows"])
        meta = tmp_path / "transcript" / "subagents" / f"{filename}.meta.json"
        meta.write_text(json.dumps(subagent["meta"]), encoding="utf-8")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    for rows in case["firings"]:
        write_jsonl(transcript, rows)
        hook_module.emit_new_turns_from_transcript(fake_langfuse, config, case["session_id"], transcript)
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, case["session_id"], transcript, flush_deferred_agent_turns=True
    )


@pytest.mark.parametrize("case_name", sorted(CASES))
def test_steps_carry_the_ids_listed_in_the_fixture(hook_module, fake_langfuse, tmp_path, case_name):
    case = CASES[case_name]

    run_case(hook_module, fake_langfuse, tmp_path, case)

    roots = sent_roots(fake_langfuse)
    llm_calls = [o for o in fake_langfuse.observations if o.as_type == "generation"]
    tools = [o for o in fake_langfuse.observations if o.as_type == "tool"]
    assert [(r.trace_id, r.id) for r in roots] == [(case["trace_id"], case["root_span_id"])]
    assert {o.trace_id for o in fake_langfuse.observations} == {case["trace_id"]}
    assert sorted(o.id for o in llm_calls) == sorted(case["llm_calls"].values())
    assert sorted(o.id for o in tools) == sorted(case["tools"].values())
    edits = [o for o in tools if o.name == "Tool: Edit"]
    assert [o.id for o in edits] == [id for step, id in case["tools"].items() if step.startswith("toolu_edit")]


def parent_of(observation: Any) -> Any:
    otel_span = observation._otel_span
    return otel_span.parent or (otel_span.context or {}).get("current_span")


@pytest.mark.parametrize("case_name", sorted(CASES))
def test_the_root_has_no_parent(hook_module, fake_langfuse, tmp_path, case_name):
    run_case(hook_module, fake_langfuse, tmp_path, CASES[case_name])

    [root] = sent_roots(fake_langfuse)
    assert parent_of(root) is None


@pytest.mark.parametrize("case_name", sorted(CASES))
def test_fixture_ids_follow_the_written_formula(case_name):
    case = CASES[case_name]

    def span_id(step_id: str) -> str:
        return hashlib.sha256(f"{case['trace_id']}:{step_id}".encode("utf-8")).hexdigest()[:16]

    first_user = case["first_user_uuid"]
    assert case["trace_id"] == hashlib.sha256(f"{case['session_id']}:{first_user}".encode()).hexdigest()[:32]
    assert case["root_span_id"] == span_id("root")
    assert case["llm_calls"] == {step: span_id(step) for step in case["llm_calls"]}
    assert case["tools"] == {step: span_id(step) for step in case["tools"]}


def test_a_step_keeps_its_id_when_the_turn_is_sent_in_parts(hook_module, fake_langfuse, tmp_path):
    case = CASES["question_stop_then_yes"]

    run_case(hook_module, fake_langfuse, tmp_path, case)

    ids = [o.id for o in fake_langfuse.observations if o.name != "Conversational Turn"] + [
        o.id for o in sent_roots(fake_langfuse)
    ]
    assert len(ids) == len(set(ids))
    first_stop_ids = {case["llm_calls"]["msg_q_1"], case["tools"]["toolu_read_q"], case["llm_calls"]["msg_q_2"]}
    assert first_stop_ids <= set(ids)


def test_held_root_is_sent_under_the_derived_root_id(hook_module, fake_langfuse, tmp_path):
    case = CASES["question_stop_then_yes"]
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    transcript = tmp_path / "transcript.jsonl"
    write_jsonl(transcript, case["firings"][0])
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, case["session_id"], transcript)
    [held_root] = [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]

    run_case(hook_module, fake_langfuse, tmp_path, {**case, "firings": [case["firings"][1]]})

    assert held_root.id == case["root_span_id"]
    assert [(o.id, o.trace_id) for o in sent_roots(fake_langfuse)] == [(case["root_span_id"], case["trace_id"])]


@pytest.mark.parametrize(
    "trace_id, step_id",
    [("not-a-trace-id", "toolu_1"), ("a" * 32, ""), ("a" * 32, None), ("0" * 32, "toolu_1")],
)
def test_no_id_is_derived_without_a_valid_trace_and_step(hook_module, trace_id, step_id):
    assert hook_module.derive_span_id(trace_id, step_id) is None


def test_a_zero_hash_becomes_a_non_zero_id(hook_module, monkeypatch):
    class ZeroDigest:
        def hexdigest(self) -> str:
            return "0" * 64

    monkeypatch.setattr(hook_module.hashlib, "sha256", lambda *_: ZeroDigest())

    assert hook_module.derive_span_id("a" * 32, "toolu_1") == "0000000000000001"


def test_a_step_without_an_id_keeps_a_random_span_id(hook_module, fake_langfuse, tmp_path):
    rows = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "sessionId": "s", "uuid": "u1",
         "message": {"role": "user", "content": "Hi."}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01.000Z", "sessionId": "s", "uuid": "a1",
         "message": {"role": "assistant", "model": "claude-test", "content": [{"type": "text", "text": "Hello."}]}},
    ]
    transcript = tmp_path / "transcript.jsonl"
    write_jsonl(transcript, rows)
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "s", transcript, flush_deferred_agent_turns=True
    )

    [call] = [o for o in fake_langfuse.observations if o.as_type == "generation"]
    assert call.id not in {hook_module.derive_span_id(call.trace_id, step) for step in ("root", "noid:0")}

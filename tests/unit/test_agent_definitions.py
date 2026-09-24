from __future__ import annotations

import json
import os
from pathlib import Path


def write_agent_file(directory: Path, file_name: str, frontmatter_name: str | None, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    header = f"name: {frontmatter_name}\n" if frontmatter_name else ""
    path = directory / file_name
    path.write_text(f"---\n{header}description: test agent\nmodel: haiku\n---\n{body}\n", encoding="utf-8")
    return path


def test_project_agents_resolve_walking_up_from_cwd(hook_module, tmp_path):
    project = tmp_path / "project"
    path = write_agent_file(project / ".claude" / "agents" / "team", "reviewer-file.md", "reviewer", "Review it.")
    cwd = project / "src" / "deep"
    cwd.mkdir(parents=True)

    definition = hook_module.resolve_agent_definition("reviewer", str(cwd))

    assert definition["source"] == "project"
    assert definition["path"] == str(path)
    assert definition["body"] == "Review it."
    assert definition["model"] == "haiku"
    assert definition["builtin"] is False


def test_project_agents_win_over_user_agents(hook_module, tmp_path):
    user_dir = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "agents"
    write_agent_file(user_dir, "planner.md", None, "User planner.")
    project = tmp_path / "project"
    write_agent_file(project / ".claude" / "agents", "planner.md", None, "Project planner.")

    assert hook_module.resolve_agent_definition("planner", str(project))["body"] == "Project planner."
    assert hook_module.resolve_agent_definition("planner", str(tmp_path / "elsewhere"))["source"] == "user"


def test_plugin_agents_resolve_through_the_install_registry(hook_module, tmp_path):
    config_dir = Path(os.environ["CLAUDE_CONFIG_DIR"])
    install_path = tmp_path / "cache" / "enterprise" / "1.0.0"
    write_agent_file(install_path / "agents", "ceo.md", "ceo-augusta", "Lead the company.")
    registry = config_dir / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({
        "version": 2,
        "plugins": {"enterprise@market": [{"scope": "user", "installPath": str(install_path)}]},
    }), encoding="utf-8")

    definition = hook_module.resolve_agent_definition("enterprise:ceo-augusta", str(tmp_path))

    assert definition["source"] == "plugin"
    assert definition["body"] == "Lead the company."


def test_builtin_and_unknown_agents_are_marked(hook_module, tmp_path):
    assert hook_module.resolve_agent_definition("Explore", str(tmp_path)) == {
        "name": "Explore", "source": "builtin", "builtin": True,
    }
    assert hook_module.resolve_agent_definition("from-agents-json", str(tmp_path)) == {
        "name": "from-agents-json", "source": "unknown", "builtin": False,
    }
    assert hook_module.resolve_agent_definition("", str(tmp_path)) is None


def test_definition_metadata_drops_the_prompt_body(hook_module, tmp_path):
    write_agent_file(tmp_path / ".claude" / "agents", "x.md", None, "Secret-ish body.")
    definition = hook_module.resolve_agent_definition("x", str(tmp_path))

    assert "body" not in hook_module.agent_definition_metadata(definition)


def attachment(kind: str, **fields):
    return {"type": "attachment", "attachment": {"type": kind, **fields}}


def test_agent_context_reads_every_transcript_attachment_kind(hook_module):
    rows = [
        attachment("instructions", files=[
            {"path": "/home/u/.claude/CLAUDE.md", "type": "User", "content": "Global."},
            {"path": "/p/CLAUDE.md", "type": "Project", "content": "Project."},
        ]),
        attachment("nested_memory", path="/p/sub/CLAUDE.md",
                   content={"path": "/p/sub/CLAUDE.md", "type": "Project", "content": "Nested."}),
        attachment("prompt_snapshot", systemPrompt=["Base.", "Dynamic."], cliPrefix="Prefix.",
                   tools=[{"name": "Read"}, {"name": "Bash"}]),
        attachment("hook_additional_context", content=["Hook says hi."], hookName="SessionStart:startup"),
        attachment("prompt_snapshot", systemPrompt=["Newer base."]),
    ]

    context = hook_module.extract_agent_context(rows)

    assert list(context.instruction_files) == ["/home/u/.claude/CLAUDE.md", "/p/CLAUDE.md", "/p/sub/CLAUDE.md"]
    assert context.instruction_files["/p/sub/CLAUDE.md"]["load_reason"] == "nested_memory"
    assert context.system_prompt == ["Newer base."]
    assert context.cli_prefix == "Prefix."
    assert context.tool_names == ["Read", "Bash"]
    assert context.hook_context == [{"hook": "SessionStart:startup", "content": "Hook says hi."}]


def test_instruction_payload_falls_back_to_the_definition_body(hook_module):
    context = hook_module.AgentContext()
    payload = hook_module.build_instructions_observation_payload(
        "reviewer", context, {"name": "reviewer", "source": "project", "path": "/p/r.md", "builtin": False,
                              "body": "Review carefully."},
    )

    messages, metadata = payload
    assert messages == [{"role": "system", "content": "Review carefully."}]
    assert metadata["system_prompt"]["source"] == "agent_definition"
    assert metadata["agent_definition"]["path"] == "/p/r.md"


def test_instruction_payload_is_skipped_when_nothing_is_known(hook_module):
    assert hook_module.build_instructions_observation_payload(
        "Explore", hook_module.AgentContext(), {"name": "Explore", "source": "builtin", "builtin": True},
    ) is None


def test_long_instruction_content_is_truncated(hook_module):
    context = hook_module.AgentContext()
    content = "x" * (hook_module.MAX_CHARS + 50)
    hook_module.add_instruction_file(context, "/p/CLAUDE.md", "Project", content)

    messages, metadata = hook_module.build_instructions_observation_payload("lead", context, None)

    prefix = "Contents of /p/CLAUDE.md (Project):\n\n"
    assert messages[0]["content"] == prefix + "x" * hook_module.MAX_CHARS
    assert metadata["instruction_files"][0]["chars"] == len(content)


def test_main_agent_type_is_ignored_in_subagent_payloads(hook_module):
    assert hook_module.get_main_agent_type_from_payload({"agent_type": "lead"}) == "lead"
    assert hook_module.get_main_agent_type_from_payload({"agent_type": "Explore", "agent_id": "a1"}) is None
    assert hook_module.get_main_agent_type_from_payload({}) is None


def test_session_start_record_names_the_main_agent(hook_module):
    state = hook_module.SessionState()
    context = hook_module.SessionContext(records=[
        {"event": "SessionStart", "agent_type": "lead", "source": "startup"},
        {"event": "SessionStart", "agent_type": "Explore", "agent_id": "a1"},
    ])

    assert context.session_start_agent() == "lead"
    built = hook_module.build_session_context("s", [], state)
    assert built.fallback_main_agent == ""
    state.main_agent = "persisted"
    assert hook_module.build_session_context("s", [], state).fallback_main_agent == "persisted"


def test_the_agent_setting_in_force_at_each_turn_names_it(hook_module):
    rows = [
        {"type": "agent-setting", "agentSetting": "first"},
        {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "one"}},
        {"type": "assistant", "uuid": "a1", "message": {"id": "m1", "role": "assistant", "content": "ok"}},
        {"type": "agent-setting", "agentSetting": "second"},
        {"type": "user", "uuid": "u2", "message": {"role": "user", "content": "two"}},
        {"type": "assistant", "uuid": "a2", "message": {"id": "m2", "role": "assistant", "content": "ok"}},
    ]
    state = hook_module.SessionState()
    context = hook_module.build_session_context("s", rows, state, payload_agent_type="ignored")
    first, second = hook_module.build_turns(rows)

    assert context.main_agent_for_turn(first) == "first"
    assert context.main_agent_for_turn(second) == "second"
    assert state.main_agent == "second"

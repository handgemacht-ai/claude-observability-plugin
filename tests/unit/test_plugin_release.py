import json
from pathlib import Path

PLUGIN_JSON = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"


def _config(hook_module):
    return hook_module.LangfuseConfig(public_key="pk", secret_key="sk", host="http://x", user_id=None)


def test_client_carries_the_plugin_version_as_release(hook_module):
    expected = json.loads(PLUGIN_JSON.read_text())["version"]

    client = hook_module.create_langfuse_client(_config(hook_module))

    assert client.kwargs["release"] == expected


def test_client_is_created_without_release_when_plugin_json_is_unreadable(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "__file__", "/nonexistent/hooks/langfuse_hook.py")

    client = hook_module.create_langfuse_client(_config(hook_module))

    assert client is not None
    assert client.kwargs["release"] is None

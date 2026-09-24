"""Run one headless Claude Code session with this plugin, then check its trace.

Usage: python3 tests/e2e/run_e2e.py [--agent deeplead] [--model haiku] [--work-dir DIR]

Reads LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (required) and
LANGFUSE_BASE_URL (default http://localhost:15300) from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_e2e  # noqa: E402

E2E_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = E2E_DIR.parents[1]
DEFAULT_PROMPT = "Run the chain."
# The installed marketplace copy of this plugin would trace the session a
# second time, so the run switches it off and uses only --plugin-dir.
INSTALLED_PLUGIN = "langfuse-observability@langfuse-observability"


def build_project(work_dir: Path) -> Path:
    """Copy the fixture project, giving its files their Claude Code names.

    The fixture keeps them under other names so a Claude Code session working
    on this repository does not load them.
    """
    project = work_dir / "project"
    source = E2E_DIR / "project"
    project.mkdir(parents=True)
    shutil.copy(source / "CLAUDE.template.md", project / "CLAUDE.md")
    shutil.copytree(source / "claude", project / ".claude")
    # Its own git root keeps Claude Code from treating an enclosing checkout
    # as the project.
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    return project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent", default="deeplead", choices=sorted(check_e2e.CHAINS) + ["bglead"])
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--work-dir", type=Path, help="new directory for the project copy, state and logs")
    parser.add_argument("--plugin-dir", type=Path, default=PLUGIN_ROOT)
    parser.add_argument("--no-check", action="store_true")
    parser.add_argument("--wait", type=float, default=120, help="seconds to wait for ingestion")
    args = parser.parse_args()

    public_key = check_e2e.require_env("LANGFUSE_PUBLIC_KEY")
    secret_key = check_e2e.require_env("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL") or check_e2e.DEFAULT_BASE_URL

    work_dir = (args.work_dir or Path(tempfile.mkdtemp(prefix="langfuse-plugin-e2e-"))).resolve()
    if args.work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=False)
    project = build_project(work_dir)
    state_dir = work_dir / "state"

    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("CLAUDECODE", None)
    env.update({
        "LANGFUSE_PUBLIC_KEY": public_key,
        "LANGFUSE_SECRET_KEY": secret_key,
        "LANGFUSE_BASE_URL": base_url,
        "LANGFUSE_TRACING_ENVIRONMENT": check_e2e.DEFAULT_ENVIRONMENT,
        "CC_LANGFUSE_STATE_DIR": str(state_dir),
        "CC_LANGFUSE_DEBUG": "true",
        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3",
    })
    settings = json.dumps(
        {
            "enabledPlugins": {INSTALLED_PLUGIN: False},
            "env": {
                "LANGFUSE_TRACING_ENVIRONMENT": check_e2e.DEFAULT_ENVIRONMENT,
                "LANGFUSE_BASE_URL": base_url,
            },
        },
        separators=(",", ":"),
    )
    command = [
        "claude", "-p", args.prompt,
        "--agent", args.agent,
        "--model", args.model,
        "--plugin-dir", str(args.plugin_dir.resolve()),
        "--settings", settings,
        "--permission-mode", "bypassPermissions",
        "--output-format", "json",
    ]
    print(f"work_dir={work_dir}")
    result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True, timeout=900)
    (work_dir / "claude.stdout.json").write_text(result.stdout, encoding="utf-8")
    (work_dir / "claude.stderr.txt").write_text(result.stderr, encoding="utf-8")
    try:
        output = json.loads(result.stdout)
    except ValueError:
        print(f"FAIL claude exited {result.returncode} without JSON output, see {work_dir}")
        return 1
    session_id = output.get("session_id")
    print(f"claude_exit={result.returncode} session_id={session_id}")
    print(f"result={str(output.get('result'))[:200]}")
    if result.returncode != 0 or not session_id:
        return 1
    if args.no_check or args.agent not in check_e2e.CHAINS:
        return 0
    return check_e2e.check_session(session_id, args.agent, args.wait)


if __name__ == "__main__":
    sys.exit(main())

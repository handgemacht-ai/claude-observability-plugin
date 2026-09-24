# End-to-end run

Runs one headless Claude Code session with the plugin from this checkout, then
checks the resulting trace in Langfuse. It needs the `claude` CLI, network
access to the model and a Langfuse instance. `pytest` skips this directory.

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_BASE_URL=http://localhost:15300   # the default
python3 tests/e2e/run_e2e.py --agent deeplead --model haiku
```

The script:

1. Copies `project/` to a new work directory (a temp directory, or
   `--work-dir`) and gives the files their Claude Code names: `CLAUDE.md`,
   `.claude/rules/` and `.claude/agents/`. They are stored under other names so
   that a Claude Code session working on this repository does not load them.
2. Runs `claude -p "Run the chain."` there with `--plugin-dir` set to this
   checkout, `--permission-mode bypassPermissions`,
   `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=3` and a fresh `CC_LANGFUSE_STATE_DIR`
   in the work directory. It passes
   `--settings '{"enabledPlugins":{"langfuse-observability@langfuse-observability":false},"env":{"LANGFUSE_TRACING_ENVIRONMENT":"plugin-e2e","LANGFUSE_BASE_URL":"http://localhost:15300"}}'`
   so an installed copy of the plugin does not trace the session a second time.
3. Waits until Langfuse has ingested the session and prints `PASS` or `FAIL`
   per check. The exit code is 1 when a check fails.

The keys reach Claude Code only through the environment, never through the
command line. The work directory keeps Claude's output and the hook log
(`state/langfuse_hook.log`).

## Agents

| Agent | Chain | Checked |
| ----- | ----- | ------- |
| `deeplead` | `level1` → `level2` → `level3`, each started by the one before | yes |
| `lead` | `planner` → `worker` | yes |
| `bglead` | `planner` in the background → `worker` | no, for manual runs |

## Checks

- The session has exactly one trace, and no observation id appears twice.
- Every observation carries the environment `plugin-e2e`.
- The root is `<agent> · Conversational Turn`.
- Each level is `Subagent: <type> · <description>` with the right
  `agent_depth`, nested under the `Tool: Agent` call of the level above.
- The root and every agent have an `Instructions` child.
- The trace name is `<agent> · Claude Code Turn`, and the tags contain
  `claude-code`, `agent:<agent>` and `subagent:<type>` for every level.

To check an earlier session again:

```bash
python3 tests/e2e/check_e2e.py <session id> --agent deeplead
```

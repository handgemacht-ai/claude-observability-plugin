# What This Fork Adds

This fork of
[langfuse/Claude-Observability-Plugin](https://github.com/langfuse/Claude-Observability-Plugin)
traces agents at any depth and records the instructions each agent ran with.
Everything else works as described in the [README](./README.md).

- **Agents at any depth**: each subagent, Workflow-spawned agent and resumed
  agent run nests under the tool call that started it, including agents that
  subagents start themselves.
- **Agent names**: the main agent (`--agent` or the `agent` setting) and each
  subagent type show up in observation names, the trace name, tags and metadata.
- **Instructions and system prompts**: each agent carries an `Instructions`
  child with its system prompt and the instruction files it loaded.

## Settings

Two settings turn instructions capture off. `/plugin configure` lists them next
to the other optional settings.

| Setting | Description | Default |
| --- | --- | --- |
| `CC_LANGFUSE_CAPTURE_INSTRUCTIONS` | Attach the instruction files each agent loaded (`CLAUDE.md`, rules, nested memory) with their contents. `false` sends neither the file list nor the contents. | `true` |
| `CC_LANGFUSE_CAPTURE_SYSTEM_PROMPT` | Attach each agent's system prompt. | `true` |

## Trace Shape

```
<main agent> · Conversational Turn            (agent)
├── Instructions                               (span)
├── LLM Call                                   (generation)
└── Tool: Agent                                (tool)
    └── Subagent: <type> · <description>       (agent)
        ├── Instructions
        ├── Subagent LLM Call
        └── Tool: Agent
            └── Subagent: <type> · <description>
```

- Every agent observation nests under the `Tool: Agent` (or `Task`) call that
  launched it, at any depth. A `SendMessage` call that resumes a stopped agent
  gets that run as `Subagent: … (resumed #n)`.
- Agents that a Workflow run starts nest under its `Tool: Workflow` call as
  `Workflow agent: <workflow>/<agent id>`, whatever agent type the workflow
  gave them. Agents they start in turn nest under them like any other launch.
- An agent launch span lasts until its agent is done, and ships together with
  the agent, also when the agent runs in the background.
- Agent observations carry `agent_id`, `agent_type`, `agent_depth`,
  `spawn_depth`, `parent_agent_id` and the resolved agent definition in their
  metadata; generations and tools inside an agent carry `agent_id`,
  `agent_type` and `agent_depth`.
- The main agent comes from the transcript's agent setting, else the hook
  payload, the `SessionStart` record, or the last name seen in the session. The
  default agent (`claude`) keeps the plain names `Claude Code Turn` and
  `Conversational Turn`; any other agent prefixes both, for example
  `reviewer · Claude Code Turn`.
- Tags: `agent:<main agent>` and `subagent:<type>` for every agent type that
  ran anywhere in the turn, next to `claude-code` and the skill tags.

## Instructions

The `Instructions` child of each agent holds:

- the system prompt as Claude Code recorded it for that agent, or the body of
  the agent definition file when no record exists;
- one message per instruction file (`CLAUDE.md` files, `.claude/rules/*.md`,
  nested memory, auto memory) with its contents;
- context that hooks added (`additionalContext`).

Its metadata lists the files with path, memory type, load reason, globs,
trigger file, size and SHA-256, plus the system prompt size and hash and the
names of the tools the agent had. The root observation and each agent
observation also carry the file list without contents. Contents are cut at
`CC_LANGFUSE_MAX_CHARS` per file.

Two hooks feed this besides the transcript. `InstructionsLoaded` records why
each file loaded (session start, nested traversal, path glob, include,
compaction) and `SessionStart` records the main agent. Both write to
`<state dir>/langfuse_context/<session>.jsonl`, never contact the network,
print nothing and always exit 0. Files older than 30 days are removed at the
end of a session.

Agent definitions are looked up in the project's `.claude/agents` (walking up
from the working directory), then `~/.claude/agents` (or
`$CLAUDE_CONFIG_DIR/agents`), then the installed plugins for
`<plugin>:<agent>` names. Built-in agents such as `Explore` and
`general-purpose` are marked `builtin`.

## What Is Not Captured

- The system prompt of an agent when Claude Code wrote no prompt record for it
  (older versions). Custom agents then fall back to their definition file;
  built-in agents are only marked `builtin`.
- Agents defined only with `--agents` JSON or loaded with `--plugin-dir`: their
  definition file cannot be found, so they show `source: unknown`.
- The full tool definitions: only the tool names are recorded.
- Per-request system reminders and other context Claude Code adds on the fly,
  unless it is written to the transcript.
- Instruction file contents with `CC_LANGFUSE_CAPTURE_INSTRUCTIONS=false`, and
  system prompts with `CC_LANGFUSE_CAPTURE_SYSTEM_PROMPT=false`.

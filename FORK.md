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
- **One trace per request**: a message continues the current trace when the
  agent stopped on a question to the user (the `AskUserQuestion` tool, or a
  closing message that ends with a question mark such as "go?"). Any other
  message, including one typed while the agent works, starts a new trace. The
  reply shows up in the generation inputs after the question. The root
  observation is sent when the request really ends, so after a question stop and
  a reply it shows the final answer and the last stop's end time; a request that
  ends on a question sends its root when the session ends.
- **One root per trace**: the first plugin process to send a trace's root leaves
  a claim file in `~/.claude/state/langfuse_root_claims`; any other process that
  reads the same transcript (for example through a different state directory)
  skips the root.
- **Predictable span ids**: a program that knows a trace can compute the span
  id of the root, of every LLM call and of every tool step (edits included)
  without asking Langfuse, for example to nest its own runs under a step.
  Write `trace_id` for the 32-hex id of the trace (for the default setup
  `sha256(f"{session_id}:{uuid of the message that started the request}")`
  hex, first 32 characters) and `step_id` for the step's own id from the
  transcript:

  ```
  span_id = sha256(f"{trace_id}:{step_id}").hexdigest()[:16]
  ```

  - `step_id` is the text `root` for the root, the `tool_use` block `id`
    (`toolu_...`) for `Tool: <name>`, and the assistant message `id`
    (`msg_...`) for `LLM Call`. An edit is a tool step like any other: its
    span is the one of its `tool_use_id`.
  - An all-zero result becomes `0000000000000001`.
  - A step whose transcript row has no id (no `tool_use` id, no message id)
    keeps a random span id.
  - The id depends only on the trace and the step, so a turn sent in several
    Stop firings gives every step the same id each time, and the root that is
    held back after a question and sent later uses the same root id.
  - Subagent runs: the LLM calls and tool steps inside a subagent follow the
    same rule (their message ids and tool ids are unique), in the trace of the
    main request. The `Subagent: ...`, `Workflow agent: ...` and `Instructions`
    observations still get random ids.
  - `tests/fixtures/span_ids.json` holds transcripts with the trace id and the
    span ids they must give, for checking another implementation.
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

## Hook Time Limits

- The `Stop` hook may run for up to 600 seconds, Claude Code's default for
  command hooks. Catching up on many turns can take close to a minute. The hook
  records its progress only after it has handed every new turn to the SDK, so a
  hook stopped earlier would send the same turns again on the next `Stop`.
  A hook stopped after that point, while the SDK is still delivering, can lose
  spans but never duplicates them.
- Each turn is sent by its own `Stop` hook. The `SessionEnd` hook only sends
  turns that were still waiting for background agents. Claude Code gives all
  `SessionEnd` hooks 1.5 seconds in total, and a plugin's own `timeout` does
  not extend that budget. To give it more time, set
  `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` (for example `30000`); the plugin
  caps its `SessionEnd` hook at 60 seconds.
- The `InstructionsLoaded` and `SessionStart` hooks run in the background, so
  they never hold up Claude.

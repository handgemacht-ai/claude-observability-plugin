# What This Fork Adds

This fork of
[langfuse/Claude-Observability-Plugin](https://github.com/langfuse/Claude-Observability-Plugin)
traces agents at any depth and records the instructions each agent ran with.
Everything else works as described in the [README](./README.md).

- **Agents at any depth**: each subagent, Workflow-spawned agent and resumed
  agent run nests under the tool call that started it, including agents that
  subagents and Workflow agents start themselves. An agent that a forked skill
  starts, whose transcript opens with the skill text as an injected (`isMeta`)
  user row, runs under that text as its prompt.
  - Claude Code 2.1.31x writes every agent flat in `subagents/` (the agents of
    a Workflow run in `subagents/workflows/<run>/`). The `meta.json` of an agent
    carries the `toolUseId` of its launching call, except for the agents of a
    Workflow run and the agents that a forked skill starts: they carry
    `parentAgentId` and `spawnDepth` only.
  - A session that enters a worktree keeps its log in the project folder it
    started in, but Claude Code can write the files of its agents under the
    project folder of the worktree:
    `<projects root>/<worktree folder>/<session>/subagents/`. The hook scans the
    projects root once per firing for every `<session>/subagents/` folder of the
    session, the one next to the log first, and takes agents from all of them
    (launch links, helpers of helpers, the agents of a Workflow run and the
    results in its journal). An agent
    with files in two folders is read from the first folder that holds both its
    `meta.json` and its log. The hook log says "has agent files in other project
    folders" with their names. A `<session>.superseded-<ms>` folder is not read.
  - The launching call of such an agent is found in the transcript of the agent
    that started it (`parentAgentId`, else the main transcript): the `Agent`,
    `Task` or `Skill` call whose result names the agent id
    (`toolUseResult.agentId`), else the `Skill` call that was running when the
    agent started (a skill forked in the foreground leaves no agent id; the
    agent starts within a few milliseconds of its call). A call serves one
    agent; two agents that started together take the calls in call order.
    The agent's metadata says `attributed_by` (`launch_result` or
    `skill_call_time`) when the link came from there.
  - An agent that no call started (a forked skill that the user typed as a slash
    command) has no tool call to nest under and is not sent.
- **A turn waits for its helpers**: a turn that started a helper in the
  background is not sent while the helper's result is still on its way to the
  main agent, since the answer to it belongs to the turn. A result is on its way
  once Claude Code has queued the helper's task notification, and has arrived
  when the log shows it in one of the forms that Claude Code 2.1.31x writes: a
  user row, an attachment to the running turn, a queue `remove` row that names
  its reason (`absorbed_mid_turn` or `delivered_to_agent`), or the helper's
  hand-back, a peer message whose origin has `handback: true` or that starts
  with `[Subagent hand-back]`. A `remove`
  row without a reason keeps the turn waiting until the turn ends. A hand-back or
  an attached notification after the last answer of the turn holds it until the
  agent has answered; a notification that arrives again after its helper handed
  back and the hand-back was answered gets no answer and holds nothing. The
  result of a helper's own background task, which the main log only queues and
  removes, does not hold the main turn.
  - A helper that the main agent launched and that its own background task
    wakes after it stopped writes the rows after the wake as a later run,
    `Subagent: … (resumed #n)` like a run that `SendMessage` resumes, when
    Claude Code queued a notification of that stop in the main log. The run
    before it ends at the wake and is not sent again. The main turn that holds
    the first queued notification after the wake sends the later run, once,
    under its own span: the span of the launching call was sent and closed with
    the first run. Claude Code does not queue every stop, so a wake after a stop
    that was not queued stays in the run before it, which is sent whole at the
    queued stop. A stop at the `SubagentHandback` call counts as a stop.
    Helpers of helpers are not cut this way and stay as before.
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
- **Only chat rows are messages**: Claude Code 2.1.300 and later write log
  rows of its own into the transcript, `api-request`, `api-request-blob` and
  `api-request-shape`. A blob row carries a `message.role` (user, assistant or
  system) and no uuid or timestamp, because it is a copy of a message of a saved
  API request. The hook takes a row for a message only when its `type` is `user`
  or `assistant` (a row with no `type` at all still takes the role of its
  `message`), so these rows, and any log row type Claude Code adds later, start
  no request, add no step and give no tool result. The rows stay in the rows
  that are read; the hook only does not turn them into messages.
- **One root per trace**: the first plugin process to send a trace's root leaves
  a claim file in `~/.claude/state/langfuse_root_claims`; any other process that
  reads the same transcript (for example through a different state directory)
  skips the root.
  - A claim is `pending` while the root goes out and `sent` once the hook has
    seen its spans delivered (see "Delivery"). A pending claim of a process that
    is gone, or older than 15 minutes, is taken over; the process that took
    a claim gives it back when delivery failed. A claim file with no content is
    one of release `1.3.0-handgemacht.9` or earlier and counts as `sent`.
- **State follows the session**: the export state (`langfuse_state.json`) is kept
  under a key made from the session id, not from the path of the log. A session
  that enters or leaves a worktree moves its log (and its `<session>/` folder) to
  another project folder; the hook goes on at the byte where it stopped, when the
  log at the new path starts with the same bytes (the state holds a hash of the
  first 4 KiB) and holds at least what was read. A log that has the same session
  id but another start is another log and keeps a state of its own (Claude Code
  gives some runs a session id worked out from a name). State saved by release
  `1.3.0-handgemacht.9` and earlier, under a key made from session and path, is
  taken over when the session is next seen at that path.
- **Delivery**: the SDK's flush returns nothing and swallows errors, so the hook
  watches `requests.Session.send`, which the OTLP exporter uses for every span
  batch. After the flush, the spans are `delivered`; `lost` when a batch got no
  answer or a 5xx, 408, 425 or 429 on its last try, when Langfuse answered with a
  success but rejected spans of the batch (`partial_success.rejected_spans` in the
  OTLP answer, read from protobuf or JSON), or the flush did not finish in
  5 seconds; `refused` for any other error status (a wrong key, a batch that is
  too big). A refused or lost firing gives its root claims back. A lost firing
  also puts its session state back as it was, so the next firing reads the same
  rows and sends the same turns again, up to three times in a row; refused
  batches are not sent again, because the answer would be the same. A span has
  the same id each time it is sent, so one that arrived after all appears twice
  under one id.
  - A firing saves its progress before its last flush, and Claude Code stops a
    `SessionEnd` hook after 1.5 seconds in total, so a firing can be stopped
    before it knows whether its spans arrived (a flush to a Langfuse that is down
    takes longer). Before it saves, the firing leaves a note in the state folder,
    `langfuse_firing.<session>.<pid>-<random>.json`, with the state as it was, and removes
    it when it knows the outcome. The next firing of the session puts the state
    back from every note whose process is gone or that is older than 15 minutes,
    the newest note first so that the oldest state comes back last, and counts it
    in the same three tries in a row. A session that is not resumed has no next
    firing, so what its stopped firing sent stays unsent. Notes older than 30
    days are removed at session end.
  - The client sends a request for every 25 spans (`EXPORT_BATCH_SPANS`; the
    SDK waits for 512 and sends the rest at the end, so a held turn of 427 spans
    of about 100 KB each went out as one request of 44 MB), unless the operator set
    `LANGFUSE_FLUSH_AT`. In a `Stop` firing, the spans of each helper that the
    main agent launched are flushed when the hook has handed them to the SDK
    (nested helpers go out with their parent), so a big turn goes out helper by
    helper. A flush that does not finish within 5 seconds ends this for the
    firing; the flush at the end tells what did not arrive. A `SessionEnd`
    firing does not flush between helpers, since it has 1.5 seconds in total.
  - The warnings and errors of the `opentelemetry` and `langfuse` loggers go to
    the hook log (`langfuse_hook.log` in the state folder), for example "Dropping
    span batch" for a batch above the exporter's request size limit, and a line
    "Langfuse answered a batch of spans with a partial success" with the number
    of rejected spans and the message. A batch that the exporter drops before it
    sends makes no request, so the watch counts it as `delivered`; only the
    warning shows it.
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
  - The formulas hold on Claude Code 2.1.300 and later too: a request starts
    at the real user row, so its trace id comes from that row's uuid, and every
    step is counted once (see "Only chat rows are messages"). With release
    `1.3.0-handgemacht.8` and earlier, most requests on those versions started
    at a copy in a blob row, which has no uuid and so got a random trace id.
  - `tests/fixtures/span_ids.json` holds transcripts with the trace id and the
    span ids they must give, for checking another implementation.
- **Instructions and system prompts**: each agent carries an `Instructions`
  child with its system prompt and the instruction files it loaded.

## Settings

Two settings turn instructions capture off, and one takes LLM Call inputs from
the saved API requests. `/plugin configure` lists them next to the other
optional settings.

| Setting | Description | Default |
| --- | --- | --- |
| `CC_LANGFUSE_CAPTURE_INSTRUCTIONS` | Attach the instruction files each agent loaded (`CLAUDE.md`, rules, nested memory) with their contents. `false` sends neither the file list nor the contents. | `true` |
| `CC_LANGFUSE_CAPTURE_SYSTEM_PROMPT` | Attach each agent's system prompt. | `true` |
| `CC_LANGFUSE_API_REQUESTS_DIR` | The `<dir>` of `OTEL_LOG_RAW_API_BODIES=file:<dir>`, to take LLM Call inputs from the saved API requests (see LLM Call Inputs). | empty |

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
  gets that run as `Subagent: … (resumed #n)`, and so does a run that the
  agent's own background task wakes (see "A turn waits for its helpers").
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

## LLM Call Inputs

Each `LLM Call` input shows what the model got for that call. With

```
OTEL_LOG_RAW_API_BODIES=file:<dir>
CC_LANGFUSE_API_REQUESTS_DIR=<dir>
```

in the `env` of the user settings, Claude Code saves every API request and
response in `<dir>`, and the input comes from those saved requests: only the
messages that are new since the call before it (the whole request for the first
call of a conversation), exactly as sent, with the system reminders, the context
that hooks added after a tool call and the messages typed while the agent
worked, each at the point where the model got it. The earlier messages are the
inputs and outputs of the earlier calls. `metadata.history` holds `messages`
(the new ones), `previous_message_id` (the call before, by message id),
`previous_observation_id` (that call's observation when it is in the same
turn) and `total_messages` (the whole conversation, when every earlier saved
request is there). Claude Code keeps `OTEL_*` variables from
hooks, so the plugin needs the second variable to find `<dir>`. Without the
settings, or when the call's own saved request is missing (older sessions), the
input is the whole conversation rebuilt from the transcript, which holds the user messages, the
assistant messages and the tool results only. `metadata.history.source` is
`saved_api_request` for an input from the saved requests.

The input comes from the files in `<dir>` only. The `api-request` and
`api-request-blob` rows in the transcript itself are not read, and do not
change the input.

At the end of a session, the plugin starts a new `index.jsonl` in `<dir>` when
the current one is older than a day, and removes saved requests and old
indexes that are older than seven days.

## What Is Not Captured

- The system prompt of an agent when Claude Code wrote no prompt record for it
  (older versions). Custom agents then fall back to their definition file;
  built-in agents are only marked `builtin`.
- Agents defined only with `--agents` JSON or loaded with `--plugin-dir`: their
  definition file cannot be found, so they show `source: unknown`.
- One of the two parts of an agent that has files in two project folders. A
  session that left its worktree can split one agent's run in time: the early
  part, with the full `meta.json`, under the folder of the worktree and the
  later part, with a shorter `meta.json` or none, under the folder of the main
  log. The parts share no rows, and only the files of the first folder that
  holds both the `meta.json` and the log are read.
- The full tool definitions: only the tool names are recorded.
- Per-request system reminders and other context Claude Code adds on the fly,
  unless Claude Code saves its API requests (see LLM Call Inputs) or
  it is written to the transcript.
- Instruction file contents with `CC_LANGFUSE_CAPTURE_INSTRUCTIONS=false`, and
  system prompts with `CC_LANGFUSE_CAPTURE_SYSTEM_PROMPT=false`.

## Hook Time Limits

- The `Stop` hook may run for up to 600 seconds, Claude Code's default for
  command hooks. Catching up on many turns can take close to a minute. The hook
  records its progress only after it has handed every new turn to the SDK, so a
  hook stopped earlier would send the same turns again on the next `Stop`.
  A hook that ran to the end puts its progress back when it saw the delivery
  fail; one stopped after that point, while the SDK is still delivering, leaves
  a note that the next firing of the session uses to put it back (see
  "Delivery").
- Each turn is sent by its own `Stop` hook. The `SessionEnd` hook only sends
  turns that were still waiting for background agents. Claude Code gives all
  `SessionEnd` hooks 1.5 seconds in total, and a plugin's own `timeout` does
  not extend that budget. To give it more time, set
  `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` (for example `30000`); the plugin
  caps its `SessionEnd` hook at 60 seconds. A `SessionEnd` hook that is stopped
  while it flushes is sent again by the next firing of the session (see
  "Delivery").
- The `InstructionsLoaded` and `SessionStart` hooks run in the background, so
  they never hold up Claude.

# Changelog

What changed in this plugin fork, newest first: one section per date, one entry per merged pull request. Each entry says
what changed, why, how to tell it works, how to undo it and what is known to be left open. A merged pull request adds its
entry here in the same pull request.

Installed plugins do not update themselves. After a release, run `claude plugin marketplace update` and
`claude plugin update`, then restart running sessions.

Numbers below come from the local session logs of the last 30 days in both profiles (`~/.claude` and `~/.claude_agents`),
read without changing them. "Before" is release `1.3.0-handgemacht.8`, "after" is this change.

## 2026-11-02

### Helpers of helpers are sent, a moved log is not sent twice, a lost root is sent again (release 1.3.0-handgemacht.10)

[PR #11](https://github.com/handgemacht-ai/claude-observability-plugin/pull/11), on top of PR #10. Commits in landing order:
`8058c18` (helpers of helpers), `eb3e042` (state that follows the session), `83f02ec` (root claims and delivery), `fea646f`
(`FORK.md`), `92b0b48` (version), and the commit that adds this entry. Each of the first three has its own tests and works on its own.
"Before" in this entry is release `1.3.0-handgemacht.9`.

**What changed**
- **Helpers of helpers** (`get_subagent_transcripts_by_tool_use_id`). Claude Code 2.1.31x writes every agent flat in `subagents/`
  (the agents of a Workflow run in `subagents/workflows/<run>/`). The meta of an agent that a forked skill started, for example `/code-review`
  from a Workflow agent or from another subagent, has `parentAgentId` and `spawnDepth` but no `toolUseId`, so the plugin found no launching call
  and never sent the helper. The launch is now looked up in the transcript of the agent that started it (`parentAgentId`, else the main
  transcript), in this order: the `Agent`, `Task` or `Skill` call whose result names the agent id (`toolUseResult.agentId`, used by background
  forks), else the `Skill` call that was running when the agent started (a skill forked in the foreground leaves no agent id; the agent starts
  9 to 25 ms after its call). One call serves one agent; agents that start together take the calls in call order. The helper then nests under that
  call at any depth, as `FORK.md` says. Its metadata gets `attributed_by` (`launch_result` or `skill_call_time`), and its name `/skill args` when the
  meta has no description. A `Skill` launch in the main transcript that names its agent id is linked too (before, only `Agent` and `Task` were).
- **State follows the session.** The export state was keyed by `sha256(session::transcript path)`. A session that enters or leaves a worktree moves its
  log, and its `<session>/` folder, to another project folder; the new path was a new key, so the plugin read the whole log again and sent every turn
  again. The key is now made from the session id. A log at a new path continues at the byte where the last one stopped when it starts with the same
  bytes (the state holds a hash of the first 4 KiB, the path and the length) and is at least as long as what was read. A log with the same session id but
  another start (Claude Code gives some runs a session id worked out from a name, so two projects can hold different logs under one id) keeps a state of
  its own, as before. State under an old key is taken over once, when the session is next seen at that path, and the old key is removed. The session lock
  follows the session too.
- **Root claims and delivery.** A claim file is now JSON and has a life: `pending` while the root goes out, `sent` once the hook has seen the
  spans delivered. A pending claim of a process that is gone, or older than 15 minutes, is taken over. A root whose send fails in the SDK gives its claim back
  at once, and only the process that took a claim confirms or releases it. A claim file with no content (older releases) counts as `sent`. For this the hook
  now tells what became of its spans: it watches `requests.Session.send`, which the OTLP exporter uses for every span batch. After the flush the spans are
  `delivered`; `lost` when a batch got no answer, a 5xx, 408, 425 or 429 on its last try, or the flush did not finish within 5 seconds; `refused` for any other
  error status (a wrong key, a batch that is too big). A refused or lost firing gives its root claims back; a lost firing also puts the session state back as
  it was before the firing, so the next firing reads the same rows and sends the same turns again. After three lost firings in a row the hook goes on.
  Refused batches are not sent again, the answer would be the same.
- `FORK.md` describes the three. Version raised to `1.3.0-handgemacht.10` in `.claude-plugin/plugin.json`.

**Why**
- Helpers: in the local logs of the last 30 days (975 sessions with agents, both profiles) 17 helpers had `parentAgentId` and no `toolUseId`: 12 forked skill
  agents started by Workflow agents, 5 by other subagents. The hook log has "subagent transcript not attributable to a launching tool_use, skipping" for
  them at every firing. They, their LLM calls and their tool steps never reached Langfuse, so no program could nest under those steps.
- Moved logs: 24 of 124 recent transcripts were relocated at least once, and one session sent 373 tool steps twice (hook log line "Processed 24 turns in
  25.47s" at the second move). The root claim stopped most second roots (234 log lines in 8 days) but the steps went out again.
- Roots: trace `99c7191b` had its claim written on 1 November and no root in Langfuse; its 98 steps arrived as orphans at the next full re-send, where the claim
  blocked the root. The claim never expired, never knew whether the root had arrived, and nothing sent a lost firing again. The claim directory holds 944 files.

**How to tell it works**
- Helpers, offline on the same logs: helpers with `parentAgentId` and no launching call 17 before, 0 after; helpers found 11,838 before, 11,860 after (12 by call
  time, 5 by launch result in the parent's transcript, 5 `Skill` launches named in the main transcript); none lost. The 12 matches start 9 to 25 ms after
  their call and inside its result. The 8 helpers still not found are 7 from older Claude Code versions and one forked skill that the user typed as a slash
  command (no launching call exists). Emitting three real sessions into a stand-in client: 6235e991 4,587 to 4,821 observations (`Subagent` 3 to 10,
  `Subagent LLM Call` 13 to 120, tool steps 2,359 to 2,472), 20320c21 8,062 to 8,098, 328dd1d6 14,760 to 14,802. In Langfuse once release 10 runs:
  `Subagent: general-purpose · /code-review …` under a `Tool: Skill` step of the helper's parent, `metadata.attributed_by` set.
- Moved logs, offline: four real logs (6235e991, 27434f5b, 33e655f6, fc65c94c) were fed to the hook in three firings while the log grew and moved from one folder
  to another and back. Before: 7,325, 1,954, 7,280 and 8,066 observations sent, with 1,413, 392, 1,376 and 1,865 tool steps sent twice and two state entries each.
  After: 4,821, 1,134, 4,612 and 4,714, no tool step twice, one state entry, and the same totals as one uninterrupted pass over the log. The hook log says "transcript of session
  … moved from … to …; continuing at byte …"; `langfuse_state.json` holds `transcript_path` and `head_hash` per entry and one entry per session.
- Delivery, with the real hook script against a local stand-in for Langfuse that refuses on demand (Langfuse SDK 4.18, OpenTelemetry 1.45; the new hook also with SDK 4.7.0 and
  OpenTelemetry 1.34, same result): one question per firing, firing 1 refused with 500, firing 2 accepted. Before: 1 of 2 roots arrived, the root of firing 1 never, its empty claim file stays. After: 2 of 2,
  claims `sent`. With 401 in firing 1: 1 of 2 before and after (a refusal is not sent again), and no claim left for the root that was refused. Accepted both times: 2 of 2 before
  and after, nothing twice. The watch also read a 413, a 5xx, a closed port and a server slower than 5 s correctly. Log lines: "Langfuse did not take a batch of
  spans (lost)", "spans of session … did not arrive; the next firing sends them again (try 2 of 4)", "root claim of trace … is stale; taking it over".
- `uv run --group dev pytest`: 466 tests (Python 3.10 and 3.14), 67 of them new (`tests/unit/test_agent_launch_attribution.py`,
  `tests/integration/test_helpers_of_agents.py`, `tests/integration/test_session_state_key.py`, `tests/unit/test_root_claims.py`,
  `tests/integration/test_root_delivery.py`, shared builders in `tests/session_files.py`), all with synthetic rows.

**How to undo**
- Revert the PR's commits and update the plugin. Release 9 keys its state by session and path again: a session that is running then reads its log once from the
  start and sends its turns again, and claim files that release 10 wrote (they have content) count as claimed. Spans already sent stay.

**Known limits**
- Helpers, roots and steps already sent keep what they got; Langfuse cannot change them. The root of trace `99c7191b` and the other empty claim files of older releases
  stay claimed.
- A session that was running when release 10 was installed and whose log is at another path than the one that holds its old state is sent once from the start (the old state
  cannot be found from the new path); after that it follows the session.
- The hook cannot see whether Langfuse accepted a batch and lost it later, or answered after the 5 seconds. A flush that is slow but delivered is counted as lost, and
  the same turns are sent up to three times more under the same ids; Langfuse shows one of them in the trace tree.
- A hook that Claude Code stops before it has settled (the `SessionEnd` budget is 1.5 seconds) leaves its claims `pending`; they are taken over by the next process
  or after 15 minutes, but nothing sends that firing again unless the session goes on.
- A firing whose emit failed inside the plugin (log line "emit_turn failed") is still counted as sent; only a failed delivery is sent again.
- A forked skill that the user typed as a slash command has no launching call, so it is not sent. A skill forked in the background is emitted from the transcript as
  it is when its parent turn is sent, as before.
- With OpenTelemetry 1.34 the exporter keeps retrying a 5xx for about a minute, and the hook process waits for it when it exits; release 9 does the same. With 1.45 it gives up at once.
- The watch relies on the OTLP exporter sending with `requests`. When it does not (no `requests`, another transport), nothing is seen and the hook behaves like release 9,
  except for the claim life.
- The commit hashes above are those of the pull request branch; a rebase merge gives them new hashes.

### Claude Code's request log rows are no longer taken for messages (release 1.3.0-handgemacht.9)

[PR #10](https://github.com/handgemacht-ai/claude-observability-plugin/pull/10), commits in landing order: `1352914`
(the fix, specs and FORK.md), `2192672` (version), and the commit that adds this entry.

**What changed**
- A row of the session log is a message only when its `type` is `user` or `assistant` (`CHAT_ROW_TYPES`, in
  `get_user_or_assistant_role_from_row`). Claude Code 2.1.300 and later also write `api-request`, `api-request-blob` and
  `api-request-shape` rows. A blob row is a copy of one message of a saved API request, with a `message.role` (user, assistant
  or system) and no uuid and no time. Such rows, and any log row type that comes later, now start no request, add no step and
  give no tool result. A row with no `type` at all still takes the role of its `message`. The rows are still read; they are
  only not turned into messages.
- An agent whose log opens with an injected (`isMeta`) user row, such as the helpers a forked skill like `/code-review`
  starts, now starts its turn at that row (`promote_opening_meta_prompt`; the plugin already did this for a resumed agent).
  Before, a blob copy opened the turn of such an agent by accident.
- Version raised to `1.3.0-handgemacht.9` in `.claude-plugin/plugin.json` (the only place the version lives). `FORK.md` says the
  predictable ids hold again on Claude Code 2.1.300 and later. This changelog is new.

**Why**
- Every user copy in a blob row started a request of its own, without a uuid, so its trace id was random and could not be
  worked out. Every assistant copy was counted as one more step. In the local logs with the new rows (106 sessions, Claude Code
  2.1.310 and 2.1.314): 489 of 604 requests (81%) had no uuid, 19% had a trace id that can be worked out, steps were counted
  1.39 times and tool calls 1.67 times their real number. Of the 378 real requests, only 115 were sent under their own uuid;
  the steps of the others went into requests with random ids.
- In the agent logs with the new rows (552 files) steps were counted 1.93 times (20,616 for 10,708 real ones).
- On the shared Langfuse, 620 of 711 plugin request traces (87%) from 26 October to 1 November had such a random id, so a
  program that wants to nest under a step by id (the readability gate) could not find it.
- A tool result copy in a blob row has no time and replaced the real result's time: 346 of 2,432 tool results (14%) in the same
  logs had no time (0 of 2,420 after), which gave their tool steps a wrong end time.

**How to tell it works**
- Offline, on a log of Claude Code 2.1.300 or later: every request starts at a real user row, so its trace id is
  `sha256(f"{session_id}:{uuid of that row}")[:32]` (formula in `FORK.md`), 0 requests without uuid (81% before), steps and
  tool calls equal the distinct message ids and tool-use ids in the log (1.39 and 1.67 times before).
- Logs without the new rows (85 older sessions) give identical results before and after.
- In Langfuse, once release 9 runs: the trace ids of new requests match that formula, and a trace holds one `LLM Call` per
  message id.
- `uv run --group dev pytest`: 399 tests, 34 of them new (`tests/unit/test_non_chat_rows.py`,
  `tests/integration/test_api_request_rows.py`, log `tests/fixtures/transcripts/api_request_rows/`); 22 of those fail on release 8.

**How to undo**
- Revert the PR's commits and update the plugin. Requests sent in between keep the ids they got.

**Known limits**
- Requests already sent keep their random trace ids; Langfuse cannot change them.
- A request that is in progress when the plugin is updated may be sent partly under its old ids.
- The plugin does not read the `api-request` rows. LLM Call inputs still come from the saved API request files
  (`CC_LANGFUSE_API_REQUESTS_DIR`), unchanged and the same with or without those rows in the log.
- The numbers come from local logs, not from Langfuse. The share of Langfuse request traces with a predictable id (13% in the
  research) is to be measured again after release 9 runs.
- The commit hashes above are those of the pull request branch; a rebase merge gives them new hashes.

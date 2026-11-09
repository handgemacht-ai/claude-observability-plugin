# Changelog

What changed in this plugin fork, newest first: one section per date, one entry per merged pull request. Each entry says
what changed, why, how to tell it works, how to undo it and what is known to be left open. A merged pull request adds its
entry here in the same pull request.

Installed plugins do not update themselves. After a release, run `claude plugin marketplace update` and
`claude plugin update`, then restart running sessions.

Numbers below come from the local session logs of the last 30 days in both profiles (`~/.claude` and `~/.claude_agents`),
read without changing them. "Before" is release `1.3.0-handgemacht.8`, "after" is this change.

## 2026-11-09

### Helper steps reach Langfuse: helpers under a worktree's folder, turns held for finished helpers, woken helpers, rejected spans (release 1.3.0-handgemacht.11)

[PR #NN](https://github.com/handgemacht-ai/claude-observability-plugin/pull/NN), on top of PR #11. Commits in landing order:
`a02fa72` (helpers under the project folder of a worktree), `4f5886a` (newer forms of a helper's notification), `beb3b25` (helpers that
their own background task wakes), `03594c5` (rejected spans, smaller requests, the SDK's warnings in the hook log), `43826c0` (`FORK.md`),
`6bee775` (version) and the commit that adds this entry. Each of the code commits has its own tests, and the whole suite passes after each of
them in this order. "Before" in this entry is release `1.3.0-handgemacht.10`.

A read-only test of the behaviour gate on 4 November found about 3,400 records waiting, because the plugin never sent their step to Langfuse.
The gate finds a step by the plugin's `Tool: <name>` observation (`metadata.tool_id`) or, for a final answer, by the plugin's LLM call
(`metadata` `agent_id`, `tool_count`, `assistant_text.orig_len`). This change keeps those keys and values as they are and makes the plugin send
more of those steps.

**What changed**
- **Helpers under the project folder of a worktree** (`get_session_subagent_dirs`). A session that enters a worktree keeps its log in the project
  folder it started in, but Claude Code can write the files of its helpers under the project folder of the worktree,
  `<projects root>/<worktree folder>/<session>/subagents/`. The plugin only looked next to the log, so those helpers and their tool steps were never
  sent. Each firing now scans the projects root once and finds every `<session>/subagents/` folder of the session, the one next to the log first.
  The list serves the launch attribution, the parent lookup of helpers of helpers, the task id map and the Workflow run lookup, whose agents take their result from the run's journal in any of the folders. A helper with files in two
  folders is read from the first folder that holds both its meta and its log, so every helper sent before keeps the log it was read from. One info line names the other
  folders ("has agent files in other project folders"). A `<session>.superseded-<ms>` folder is not read. The tests now give each test, and each transcript fixture, a
  projects root of its own (`tests/conftest.py`), since they share session names.
- **Newer forms of a helper's notification** (`get_undelivered_queued_notification_ids`, `notification_awaits_answer`). A turn that started a helper in the background is
  held until the helper's notification is delivered, because the answer to it belongs to the turn. The hold check knew a delivery only as a user row. Claude Code
  2.1.31x also writes it as an attachment to the running turn, as a queue `remove` row that names its reason (`absorbed_mid_turn`, `delivered_to_agent`) and
  as the helper's hand-back (a peer message whose origin has `handback: true` or whose text starts with `[Subagent hand-back]`; auto mode can put a note before that text). All of them count as delivered now. The result of a helper's own background
  task, which the main log only queues and removes, no longer holds the main turn. A notification that arrives again after its helper handed back and the hand-back
  was answered no longer waits for an answer it never gets. A hand-back or an attached notification after the last answer still holds the turn until the agent has answered.
  Attachment rows only feed the hold check, so tool results and end times stay as they were.
- **Helpers that their own background task wakes** (`get_agent_wakes_from_rows`, `emit_woken_agent_runs`). A helper was sent once, at its first stop. When a background task of its own
  woke it afterwards, the rows it wrote after the wake formed no run and no later firing sent them. A wake is the helper's own task notification as a user row after the helper stopped
  (its last answer had no tool call, or only `SubagentHandback`). For a helper the main agent launched, the first wake after a stop that Claude Code queued a notification for in the
  main log now starts a later run, `Subagent: … (resumed #n)` like a run that `SendMessage` resumes, and the earlier run ends at that wake. Claude Code does not queue every stop (helper
  a2fe5255 of 6f4d9428 woke 6 times and has one queued notification), so a wake after a stop that was not queued stays in the run before it, which is sent whole at the queued stop.
  The main turn that holds the first queued notification after the wake sends the later run, once, under its own span: the span of the launching call was sent and closed with the first
  run. A later queued notification that no new run start precedes (a resume the plugin does not see, such as a peer message or a `SendMessage` result without `resumedAgentId` before Claude
  Code 2.1.197) does not send it again. Helpers of helpers are not cut and are sent whole with their parent, as in release 10.
- **Rejected spans, smaller requests, the SDK's warnings** (`ExportWatch`, `EXPORT_BATCH_SPANS`, `PieceFlush`, `SdkLogRelay`). A batch that Langfuse took with spans rejected
  (`partial_success.rejected_spans` in the OTLP answer, read from protobuf or JSON by hand, no new dependency) counts as `lost`, so the firing is undone and sent again like any lost
  one; the log gets `rejected_spans` and `error_message`. The warnings and errors of the `opentelemetry` and `langfuse` loggers, such as a batch dropped above the request size limit,
  are written to the hook log. The client sends a request for every 25 spans (about 2.5 MB at 100 KB per span; left to the SDK when `LANGFUSE_FLUSH_AT` is set), and a `Stop`
  firing flushes after the spans of each helper that the main agent launched (nested helpers go out with their parent). These flushes get the 5 second flush timeout in total, and it
  stops flushing between helpers for that firing once they used it or after a flush whose batch did not reach Langfuse, so an unreachable Langfuse (about 3 seconds per flush)
  does not cost that for every helper. A `SessionEnd` firing does not flush between helpers, since Claude Code gives it 1.5
  seconds in total. Span contents and the metadata keys the gate reads are unchanged.
- `FORK.md` describes them. Version raised to `1.3.0-handgemacht.11` in `.claude-plugin/plugin.json`.

**Why**
- Worktree helpers, two real sessions, counted read-only with nothing sent: launches attributed to a helper went from 38 to 70 (6101bcb5) and from 14 to 27 (6a854ffe). The added
  helpers have their files under the project folder of a worktree.
- Held turns: three real turns (798305be, 6101bcb5, 2e63ecf5) were held by release 10 until the next prompt, and are not held by this change.
- Woken helpers: of the waiting records, about 48 belong to helpers that were woken. Calls sent after the whole log of three real helpers: a2dad584 16 of 55, aada18c4 17 of 27 and a0738197
  90 of 92. Of 65 wake rows counted in the logs, 56 (41 helpers) follow an answer with no tool call and 9 follow a hand-back.
- Lost spans: a held turn of 427 spans of about 100 KB each went out as one request of 44 MB, since the SDK sends when 512 spans are queued and the rest at the end. In the lost send a
  block of 352 spans never arrived, and nothing in the hook log said why: the exporter drops a batch above its request size limit with a warning on its own logger, and Langfuse can take
  a batch with spans rejected, and neither was written to the hook log.

**How to tell it works**
- Worktree helpers, offline on the two real sessions above (nothing sent, the shared Langfuse was not contacted): 38 to 70 and 14 to 27 helper launches attributed, and each helper attributed before
  keeps the same log file. In the hook log of a running session: "session <id> has agent files in other project folders: …". In Langfuse, the number of waiting gate records of worktree
  sessions drops.
- Held turns, replaying the three real turns read-only, nothing sent: each is held by release 10 and not held by this change.
- Woken helpers, replaying the three real helpers above the same way: calls sent after the whole log 55 of 55 in 4 runs (16 before), 27 of 27 (17) and 92 of 92 (90); no call sent twice. In
  Langfuse: `Subagent: … (resumed #n)` under the span of the main turn that holds the helper's next stop.
- Export, with the changed hook against the real SDK (4.18, OpenTelemetry 1.45) and a local collector on 127.0.0.1 with a state folder of its own: batches arrive as 25, 25, 25, 25; real protobuf
  `partial_success` bytes are read; an oversize drop writes its warning to the hook log. For 427 spans of 100 KB: on an 8 MB/s link the SDK default sent one 42.8 MB request that hit the 5 second flush cap
  and counted as lost, and the new setting sent 18 requests of at most 2.5 MB with a 4.1 second tail, delivered. On a 3 MB/s link both hit the cap, and the new setting got 225 of 427 spans through
  where the old one got none. Log lines: "Langfuse answered a batch of spans with a partial success: rejected_spans=N", "[WARNING] opentelemetry…: Dropping span batch: …",
  "flushes between helpers did not finish within 5 s in total; the rest is sent at the end", "a flush between helpers did not reach Langfuse; the rest is sent at the end".
- `uv run --group dev pytest`: 524 tests (Python 3.10 and 3.13), 45 of them new. Before the fix 5 of the 7 worktree tests, 4 of the 7 notification tests, all 3 woken-helper tests and
  17 of the 28 export tests fail; the others guard behaviour that must stay (`tests/unit/test_subagent_discovery.py`, `tests/integration/test_helpers_of_agents.py`,
  `tests/integration/test_notification_deliveries.py`, `tests/integration/test_woken_helpers.py`, `tests/integration/test_export_loss.py`, builders in `tests/session_files.py`,
  `tests/held_turns.py`, `tests/woken_helpers.py` and `tests/export_helpers.py`), all with synthetic rows. `uvx ruff check --select F,E9 .` is clean, and `echo '{}' | uv run --script hooks/langfuse_hook.py`
  with a scratch state folder exits 0.

**How to undo**
- Revert the PR's commits and update the plugin. Release 10 reads helper files next to the log only, holds a turn as before, sends a woken helper once, and its client sends at the SDK's
  default size again. Spans already sent stay in Langfuse. The few emitted keys that woken runs leave in `langfuse_state.json` (`subagent:<agent id>@<time>`) are not read by release 10.

**Known limits**
- Sessions that were already sent are not sent again, so the waiting records of those sessions (about 48 for woken helpers) only clear when the sessions are sent again. Whether to send them
  again is a decision for the owner; leaving them is recommended unless the gate needs those exact records.
- Open decision, split helper runs: in 6a854ffe, 2 of 28 helpers have files in both folders, and they are not copies. Each is one run split in time: the early part (with the full meta) is in the folder of
  the worktree, the later part (after the session left the worktree, with a shorter meta or none) in the folder of the main log, and they share no rows. The plugin reads one file per helper, so the other
  part is still not sent. In 6101bcb5 this happens 0 times in 70. Merging a helper's logs from all folders in time order would be a larger change to how helper rows are read; keeping the rule is
  recommended until the gate shows how many records still wait.
- A project folder that is itself named `*.superseded-*` is not excluded; none exists in the data.
- A remove row counts as delivered only when it names a reason. Rows without one (older Claude Code) still hold the turn, the safe direction. Upstream (langfuse/Claude-Observability-Plugin) PR 113 counts every remove row; this is a choice.
- A hand-back is recognised by a peer message whose origin has `handback: true` (all 2,541 hand-backs in the logs) or whose text starts with `[Subagent hand-back]`. If both change, turns are held again and the old bug is back.
- A turn is still held in the short gap between a notification's enqueue and its remove row; in the examples the remove row came within seconds.
- Not checked: 132 repeat notifications after a hand-back that were followed by an assistant row. It is open whether any of them was a real answer after the final answer; the named examples were never answered.
  Worth checking against the gate's waiting records. Upstream's mapping of background-task ids to their tool calls is not part of this change.
- A helper woken after its `SubagentHandback` call counts as woken. The change relies on the queued-notification rows that Claude Code 2.1.31x writes in the main log, and does not cover helpers of helpers that
  their own tasks wake (they are sent whole with their parent, so rows they write after that are still missing, as in release 10) nor a wake row whose text starts directly with `<task-notification>`. A woken run sits under the span of the main turn that holds the
  helper's next stop, not under the original `Agent` call. Each firing reads again the log of each helper that has a queued notification in the open turn, a small extra cost.
- A batch that the exporter drops above its request size limit makes no request, so the watch cannot see it: with the real SDK it still counts as `delivered` and the state is saved. It shows only as a
  warning in the hook log. Counting that warning as a lost or refused batch is not part of this change and is for the owner to decide.
- The 5 second flush timeout is unchanged. A very big send over a slow link still cannot finish inside it: smaller batches let more of it arrive in the background, and the next firing sends the whole turn
  again (duplicates under the same span id, up to three tries in a row). Raising the timeout, or the SDK's own 5 second request timeout, is a separate decision.
- The batch size 25 (`EXPORT_BATCH_SPANS`) is a pick, not measured against the real Langfuse, and the `SessionEnd` timing was checked against a local collector only, not inside Claude Code's 1.5 seconds in a real
  session. A turn held until `SessionEnd` relies on the batch size alone, since that firing does not flush between helpers.
- The cause of the lost block of 352 spans is not proven. This change makes such losses visible and the requests smaller, and was not shown to be the cause.
- **Not fixed: the final answer of an idle session.** At `Stop` the hook starts sending before Claude Code 2.1.314 has written the final answer to the log (it was missing at the last firing in 11 of 11 sessions
  looked at). It saves where it stopped reading, and the root of the turn goes out with the answer it has, and a root is sent once, so the final answer is lost when the session then goes idle. The `Stop` hook
  runs synchronously (600 seconds), and its input has an optional `last_assistant_message` with the text of the final answer. It is not part of this change, since how the `Stop` firing should wait for the answer needs a choice between three options. Option A: wait inside the `Stop` hook for a few seconds when the answer is missing (smallest, but holds the session on most turns, and costs the full wait if
  Claude Code writes the answer only after the hook ends). Option B, recommended: the `Stop` firing hands its work to a background process that waits about 10 seconds for the answer and then sends the turn as usual; the user
  never waits, the locks and claims keep anything from being sent twice, and the hook falls back to today's behaviour when the process cannot start. Option C: mark the `Stop` hook async in `hooks/hooks.json` and wait inside it
  (the user never waits, but it changes every `Stop` firing, and an async hook may be cut off when Claude Code exits). Sending again later would add the final LLM call but not the answer on the root. Not checked: whether Claude Code
  writes the final answer while the `Stop` hook still runs or only after it ends. A failing test that shows the bug (`tests/integration/test_final_answer_at_stop.py`) is not in this PR; it waits uncommitted in the worktree
  `hs-stop-reread` for the fix.
- The commit hashes above are those of the pull request branch; a rebase merge gives them new hashes.

## 2026-11-02

### Helpers of helpers are sent, a moved log is not sent twice, a lost root is sent again (release 1.3.0-handgemacht.10)

[PR #11](https://github.com/handgemacht-ai/claude-observability-plugin/pull/11), on top of PR #10. Commits in landing order:
`3f15a4b` (helpers of helpers), `dc6700e` (state that follows the session), `f19253b` (root claims and delivery), `cbd351b`
(`FORK.md`), `d0eee67` (version), `a903675` (the first version of this entry), `8f75766` (an undone firing that moved state keeps the old
entry), `9017a6a` (firings stopped before they knew the outcome), `4b99b59` (no signal to a process on Windows), `674db65` (`FORK.md` and
this entry up to date) and `4180e33` (a random part in the name of the firing note). Each of the
code commits has its own tests and works on its own. "Before" in this entry is release `1.3.0-handgemacht.9`.

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
- **A lost firing that moved the state keeps the old entry** (`undo_unconfirmed_firing`). In the first firing after the update, the state of a
  running session moves from the old key to the key of the session. When that firing was lost, the undo put the old entry back and also wrote an empty
  entry under the key of the session; the next firing took the empty one, read the log from byte 0 and sent every turn of the session again (a session
  with 2 sent turns and a lost firing for a third sent questions 1, 2 and 3 again; roots are held by their claims, but every LLM call and tool step went
  out twice under the same ids). The empty entry is now written only when the session had no entry under any key; otherwise the old entry comes back and
  the next firing moves it again.
- **A firing that is stopped before it knows the outcome is sent again.** A firing saves its progress before the flush, and Claude Code stops a
  `SessionEnd` hook after 1.5 seconds in total. A flush to a Langfuse that is down takes longer (3.4 to 3.8 seconds in the tests below), so the
  `SessionEnd` firing was stopped after its state said the turns were sent and before it could see that they were not: no verdict, no undo, and a session
  that was resumed afterwards never sent its first request. The firing now leaves a note, `langfuse_firing.<session>.<pid>-<random>.json` in the state folder (the random part keeps a process that got the pid of a stopped one from writing over its note),
  with the state as it was, before it saves its progress, and removes it when it knows the outcome. The next firing of the session puts the state back from
  every note whose process is gone, or that is older than 15 minutes (`FIRING_NOTE_MAX_AGE`), the newest note first so that the oldest state comes back
  last, and counts it in the same three tries in a row. The claims of the stopped firing are `pending` and its process is gone, so they are taken over
  and the roots are sent again. The sweep at session end removes notes older than 30 days.
- **No signal to a process on Windows** (`_process_is_gone`). The check whether the process of a claim or a note is gone uses `os.kill(pid, 0)`, which on Windows ends the
  process whatever the signal. It is not called there any more; the age of the claim or note (15 minutes) decides.
- `FORK.md` describes them. Version raised to `1.3.0-handgemacht.10` in `.claude-plugin/plugin.json`.

**Why**
- Helpers: in the local logs of the last 30 days (975 sessions with agents, both profiles) 17 helpers had `parentAgentId` and no `toolUseId`: 12 forked skill
  agents started by Workflow agents, 5 by other subagents. The hook log has "subagent transcript not attributable to a launching tool_use, skipping" for
  them at every firing. They, their LLM calls and their tool steps never reached Langfuse, so no program could nest under those steps.
- Moved logs: 24 of 124 recent transcripts were relocated at least once, and one session sent 373 tool steps twice (hook log line "Processed 24 turns in
  25.47s" at the second move). The root claim stopped most second roots (234 log lines in 8 days) but the steps went out again.
- Outage, from the proof run on a throw-away Langfuse 4.43.0 with real Claude Code 2.1.314 sessions (the live Langfuse was not touched): the web container was stopped for
  10 min 42 s and again for 3 minutes. The `Stop` firings logged "spans of session … did not arrive; the next firing sends them again (try 2 of 4)", the `SessionEnd` firing
  logged nothing after "Processed 1 turns" (Claude Code exited about 2 seconds after it started, before the flush could give a verdict), and 4 of 4 sessions that were resumed afterwards sent only the new request: Langfuse held 1 root and
  1 tool step per resumed session, the tool calls of the first request were missing. The other sessions lost their steps the same way. A program that
  nests its records under those steps (the readability gate) could only file them alone.
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
- Stopped firing, with the real hook script against a local stand-in for Langfuse (SDK 4.18, OpenTelemetry 1.45): a Langfuse that is down (closed port), a `Stop` firing, a
  `SessionEnd` firing stopped with `kill -9` after 1.5 seconds, then the session resumed against a working Langfuse with a second question. Before: the first `Stop` firing
  ends after 3.8 s and puts its state back, the stopped `SessionEnd` firing leaves the state at the end of the log, and the working Langfuse received only "Question 2?".
  After: it received "Question 1?" and "Question 2?", the hook log says "a firing of session … was stopped before it knew whether its spans arrived" and "root claim of
  trace … is stale; taking it over", and no note is left in the state folder.
- Lost firing during the move to the new key, in a test (legacy-key state of a session with 2 sent turns, a new turn with a lost flush, then a delivered firing): before, the
  delivered firing sent questions 1, 2 and 3 again; after, only question 3 (it was lost once, so it is sent twice in all).
- The offline runs above were repeated with these two commits: the four real logs give the same numbers as before them (4,821, 1,134, 4,612 and 4,714 observations, no tool step
  twice, one state entry), and the earlier delivery run gives the same results.
- `uv run --group dev pytest`: 479 tests (Python 3.10 and 3.14), 80 of them new (`tests/unit/test_agent_launch_attribution.py`,
  `tests/integration/test_helpers_of_agents.py`, `tests/integration/test_session_state_key.py`, `tests/unit/test_root_claims.py`,
  `tests/integration/test_root_delivery.py`, shared builders in `tests/session_files.py`), all with synthetic rows.

**How to undo**
- Revert the PR's commits and update the plugin. Release 9 keys its state by session and path again: a session that is running then reads its log once from the
  start and sends its turns again, and claim files that release 10 wrote (they have content) count as claimed. Spans already sent stay. The `langfuse_firing.*` files in the
  state folder can be deleted; release 9 does not read them.

**Known limits**
- Helpers, roots and steps already sent keep what they got; Langfuse cannot change them. The root of trace `99c7191b` and the other empty claim files of older releases
  stay claimed.
- A session that was running when release 10 was installed and whose log is at another path than the one that holds its old state is sent once from the start (the old state
  cannot be found from the new path); after that it follows the session.
- The hook cannot see whether Langfuse accepted a batch and lost it later, or answered after the 5 seconds. A flush that is slow but delivered is counted as lost, and
  the same turns are sent up to three times more under the same ids; Langfuse shows one of them in the trace tree.
- A hook that Claude Code stops before it has settled (the `SessionEnd` budget is 1.5 seconds) leaves its claims `pending` and a note; the next firing of the session takes
  the claims over and sends the firing again. A session that is not resumed has no next firing, so what its stopped firing sent stays unsent (after an outage, that is the case for
  the sessions that end during it). A sweep that sends such sessions from other sessions' firings would make every `Stop` hook wait
  for another session's backlog, and is not part of this change.
- A firing that is stopped after its flush delivered the spans, and before it removed its note, is sent again by the next firing, and its spans appear twice under one id.
- A firing whose emit failed inside the plugin (log line "emit_turn failed") is still counted as sent; only a failed delivery is sent again.
- A forked skill that the user typed as a slash command has no launching call, so it is not sent. A skill forked in the background is emitted from the transcript as
  it is when its parent turn is sent, as before.
- With OpenTelemetry 1.34 the exporter keeps retrying a 5xx for about a minute, and the hook process waits for it when it exits; release 9 does the same. With 1.45 it gives up at once.
- The watch relies on the OTLP exporter sending with `requests`. When it does not (no `requests`, another transport), nothing is seen and the hook behaves like release 9,
  except for the claim life.
- The commit hashes above are those of the pull request branch; a rebase merge gives them new hashes.

### Claude Code's request log rows are no longer taken for messages (release 1.3.0-handgemacht.9)

[PR #10](https://github.com/handgemacht-ai/claude-observability-plugin/pull/10), commits in landing order: `960ecf6`
(the fix, specs and FORK.md), `36f09cc` (version) and `a025b78` (this entry).

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

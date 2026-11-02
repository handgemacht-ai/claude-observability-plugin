# Changelog

What changed in this plugin fork, newest first: one section per date, one entry per merged pull request. Each entry says
what changed, why, how to tell it works, how to undo it and what is known to be left open. A merged pull request adds its
entry here in the same pull request.

Installed plugins do not update themselves. After a release, run `claude plugin marketplace update` and
`claude plugin update`, then restart running sessions.

Numbers below come from the local session logs of the last 30 days in both profiles (`~/.claude` and `~/.claude_agents`),
read without changing them. "Before" is release `1.3.0-handgemacht.8`, "after" is this change.

## 2026-11-02

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

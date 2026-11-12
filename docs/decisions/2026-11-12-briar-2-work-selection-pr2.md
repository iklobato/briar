# briar 2.0: spec 008 PR 2, chat asks from Slack (grill answers)

Grill session, 2026-11-12, before any code of PR 2 (branch `select/02-chat`). Builds on
`2026-11-12-briar-2-work-selection.md` (N3, N8, N15) and `...-work-selection-pr1.md` (Q1:
chat asks +25 each in 14 days, at most +50).

Facts checked before the questions:
- `httpx` is already a dependency of briar: no new library for the Slack Web API.
- On Slack Enterprise Grid, `conversations.history` and `conversations.list` answer
  `enterprise_is_restricted` for a user session; `search.messages` and `conversations.replies`
  work, and the search queries `to:me` and `<@USER_ID>` are proven (a read-only Slack client of
  the maintainer).
- `search.messages` needs a user token (`search:read`) or a browser session token; a bot token
  cannot call it.

## Decisions

| # | Decision | Answer | Why | Depends on |
|---|---|---|---|---|
| C1 | How Slack is read | A fixed set of searches per read, all with `after:` 14 days: `to:me` (DMs), `<@OWNER_ID>` (mentions), `in:<channel>` for each team channel of the runbook, and the key prefix of each tracker project; the ticket keys are found by regex in the text | the number of calls does not grow with the number of tickets; search works on Enterprise Grid where history does not | N8 |
| C2 | Slack login | A user token (`xoxp`, `search:read`) or a session token (`xoxc`) plus `BRIAR_<COMPANY>_SLACK_COOKIE`, in `BRIAR_<COMPANY>_SLACK_TOKEN`; a bot token is refused by the check with a clear message | search does not take a bot token; one code path. Changes N15 ("a bot or user token works alone") | C1 |
| C3 | What counts as a chat ask | Each distinct person other than the token owner who cited the ticket in the 14 days counts once (+25 each, at most +50) | one person repeating a key must not max the score alone; the owner cannot push a ticket up by talking about it | Q1 |
| C4 | What the selector model reads | The whole thread of each message that cites one of the 5 best tickets, with author and channel, marked as data, never as an order (E21) | the model weighs what is asked, which a count cannot carry | N2 |
| C5 | Size limit of that text | None | the maintainer chose the full thread | C4 |
| C6 | When Slack is read | Only on a poll that will call the selector (a free slot and a new ticket); the chat asks are part of the fingerprint, so a new ask triggers a new choice | no Slack calls on a poll with no free slot | Choice memo of PR 1 |
| C7 | A Slack failure | `serve` stops with exit code 1 and the cause in the log, as a failed login check does; the running tasks stop and pick up on the next start | a wrong ranking from missing chat asks is worse than stopping | C1 |
| C8 | A passing Slack error | On HTTP 429 wait the `Retry-After` and try again a few times, and on a timeout try again; stop (C7) only when it keeps failing; a revoked token or a missing scope stops at once | a short rate limit must not stop `serve` | C7 |
| C9 | Where the team channels live | `serve: {chat: {slack: {channels: [C0123ABC]}}}`; the login in `[auth.slack] workspace` and the two env keys of C2; the window is fixed at 14 days | the login stays with the other logins; what `serve` reads stays in `serve:` | C2 |
| C10 | Text that cites a ticket | Jira `KEY-N` anywhere in the text and the `/browse/KEY-N` link; GitHub `owner/repo#N` and the `github.com/owner/repo/issues/N` link; a bare `#N` does not count | no ambiguity across repos | C1 |
| C11 | The check before `serve` and `doctor --runbooks` | `auth.test` (the token works, the user name) and one `in:<channel>` search per runbook channel; a bot token is refused; a failure is exit 1 like the other logins | a wrong channel id shows before the first poll | C2, C9 |
| C12 | Real proof (T014) | Both: read only on a real company Slack with messages that already cite tickets assigned to the owner (score and pick with and without chat; nothing written), and a test workspace of the maintainer with a new channel `#briar-test`, where briar writes a draft that cites an issue of the test repo and the maintainer sends it by hand | real data shows the signal on real asks; the new message proves the whole path | C1 to C11 |

## Open risks

- C5: a very long thread goes whole into the selector prompt. The SDK stops the call at the
  `max_usd` of the request, and a thread past the model context makes the call fail; with C7 a
  failed selector call does not stop `serve` (it is logged), but the poll picks nothing.
- C7: one expired Slack session stops every company of the folder, not only the one with Slack.
- C1: the key-prefix search (`ENG` for `ENG-123`) depends on how Slack tokenizes `KEY-123`; it is
  checked on the real workspace before the code relies on it.
- Session tokens (`xoxc` + cookie) expire; a user token does not. The docs say so.

## Not decided here

- Other chat tools (Teams, Google Chat) behind the same interface: later, no date.
- PR 3 (GitLab) and PR 4 (repo choice, open PR/MR rule) get their own grill.

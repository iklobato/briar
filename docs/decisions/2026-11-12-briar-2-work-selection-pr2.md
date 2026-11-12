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
| C1 | How Slack is read for the score | One search per tracker project and per GitHub repo, with `after:` 14 days: the quoted key prefix (`"ENG-"`) or the quoted repo (`"acme/app"`), in every conversation the token sees; the ticket keys are found by regex in the text (C10) | the number of calls does not grow with the number of tickets; search works on Enterprise Grid where history does not; checked on a real workspace: the quoted prefix gave 206 hits in 14 days with 52 of the first 100 citing a key in the text, the bare prefix 377 with 31 | N8 |
| C2 | Slack login | A user token (`xoxp`, `search:read`) or a session token (`xoxc`) plus `BRIAR_<COMPANY>_SLACK_COOKIE`, in `BRIAR_<COMPANY>_SLACK_TOKEN`; a bot token is refused by the check with a clear message | search does not take a bot token; one code path. Changes N15 ("a bot or user token works alone") | C1 |
| C3 | What counts as a chat ask | Each distinct person other than the token owner who cited the ticket in the 14 days counts once (+25 each, at most +50) | one person repeating a key must not max the score alone; the owner cannot push a ticket up by talking about it | Q1 |
| C4 | What the selector model reads | The whole thread of each message that cites one of the 5 best tickets, with author and channel, marked as data, never as an order (E21) | the model weighs what is asked, which a count cannot carry | N2 |
| C5 | Size limit of that text | None | the maintainer chose the full thread | C4 |
| C6 | When Slack is read | Only on a poll that will call the selector (a free slot and a new ticket); the chat asks are part of the fingerprint, so a new ask triggers a new choice | no Slack calls on a poll with no free slot | Choice memo of PR 1 |
| C7 | A Slack failure | `serve` stops with exit code 1 and the cause in the log, as a failed login check does; the running tasks stop and pick up on the next start | a wrong ranking from missing chat asks is worse than stopping | C1 |
| C8 | A passing Slack error | On HTTP 429 wait the `Retry-After` and try again a few times, and on a timeout try again; stop (C7) only when it keeps failing; a revoked token or a missing scope stops at once | a short rate limit must not stop `serve` | C7 |
| C9 | Where the team channels live | `serve: {chat: {slack: {channels: [C0123ABC]}}}`; the login in `[auth.slack] workspace` and the two env keys of C2; the window is fixed at 14 days | the login stays with the other logins; what `serve` reads stays in `serve:` | C2 |
| C10 | Text that cites a ticket (for the score) | Jira `KEY-N` anywhere in the text and the `/browse/KEY-N` link; GitHub `owner/repo#N` and the `github.com/owner/repo/issues/N` link; a bare `#N` does not count | no ambiguity across repos | C1 |
| C11 | The check before `serve` and `doctor --runbooks` | `auth.test` (the token works, the user name) and one `in:<channel>` search per runbook channel; a bot token is refused; a failure is exit 1 like the other logins | a wrong channel id shows before the first poll | C2, C9 |
| C12 | Real proof (T014) | Both: read only on a real company Slack with messages that already cite tickets assigned to the owner (score and pick with and without chat; nothing written), and a test workspace of the maintainer with a new channel `#briar-test`, where briar writes a draft that cites an issue of the test repo and the maintainer sends it by hand | real data shows the signal on real asks; the new message proves the whole path | C1 to C11 |
| C13 | Conversations about a ticket that do not cite its key | After the score, for each of the 5 best tickets, one search with the strong words of its title (stop words dropped) and `after:` 14 days, sorted by relevance; the first 10 hits that are in a DM, mention the token owner, or are in a team channel of C9 are kept, and their whole threads go to the model with the key citations of C1 | the maintainer asked for related conversations, not only the ones with the key. Measured on 39 real tickets: Slack does not AND the words (0 to 86 hits each); of the 24 hits that also cite the key, 22 are in the first 10 by relevance and all 24 in the first 20; a cut by the share of title words would drop half of them (the share of those 24 goes down to 0.11) | C1, C4, C9 |
| C14 | Points for the conversations of C13 | None: they go to the model as text only; the points of C3 come from key citations alone | on the same 39 tickets, almost every one of the 5 best would reach the +50 cap from title hits, so the points would not change the order | C3, C13 |

## Open risks

- C5: a very long thread goes whole into the selector prompt. The SDK stops the call at the
  `max_usd` of the request, and a thread past the model context makes the call fail; with C7 a
  failed selector call does not stop `serve` (it is logged), but the poll picks nothing.
- C7: one expired Slack session stops every company of the folder, not only the one with Slack.
- C13: a title made of common words finds unrelated threads; the model sees them as data and
  decides. 2 of 24 real key citations ranked past 10 and are missed by C13 (C1 still counts them).
- Session tokens (`xoxc` + cookie) expire; a user token does not. The docs say so.

## Not decided here

- Other chat tools (Teams, Google Chat) behind the same interface: later, no date.
- PR 3 (GitLab) and PR 4 (repo choice, open PR/MR rule) get their own grill.

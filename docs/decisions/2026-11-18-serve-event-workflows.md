# Serve events and workflows: one workflow for each kind of event, the memory of a task is its page in the knowledge

Date: 2026-11-18. Status: settled in a grill, confirmed by the maintainer. Slice 1 (W27) is built: the page of a task, the facts the code writes and the notes the agent writes. Slices 2 to 4 are not started. Base: `briar/v2` at `a6da91d`.

## Problem

`briar serve` sees two things today: a ticket assigned to the maintainer, and the PR that briar opened (new comments, a conflict, a merge or a close). A broken CI after the PR is ready, a comment on the ticket and a Slack message about the task are not seen.

Every agent call (plan, build, fix, answer, self review) is a new session that reads the repo again. `AgentAnswer` drops the `session_id` and `ClaudeAgent` calls `query()` with no `resume` (`src/briar/serve/agent.py`).

What briar records of a task is one JSON blob for each company, `work:<company>` (`src/briar/serve/state.py`): plan, why it stopped, answered comments, rounds, spend by day, reviewers and the PR. The system prompt already points the agent to that file (`{state}`, filled in `src/briar/serve/task.py`), but it is one file with every task of the company, and it has no diff, no text of the reviews, no CI result, no chat message and no reason for a decision.

## What exists already (researched on 2026-11-18)

- The Claude Agent SDK (0.2.180) has `resume`, `fork_session`, a `SessionStore` and helpers to list and tag sessions. The transcript is a local file; the CLI sweeps local transcripts after 30 days by default ([sessions](https://code.claude.com/docs/en/agent-sdk/sessions), [session storage](https://code.claude.com/docs/en/agent-sdk/session-storage)).
- On two repos of one company, 23 of the 45 open PRs were older than 30 days, the oldest 291 days. A transcript kept for 30 days would be gone for about half of them.
- No tool found routes events of GitHub, GitLab, Jira and Slack to one task. The request to resume a Claude Code session from a PR event was closed as not planned by the stale bot, with no maintainer answer ([issue 31244](https://github.com/anthropics/claude-code/issues/31244)).
- Durable workflow engines (Inngest, Temporal, Hatchet, Restate) route events to instances, but each needs a server and a new dependency, and takes the loop out of briar. LangGraph checkpoints only serve LangGraph agents. The official GitHub Action covers only GitHub and a user report says it loses the conversation. None was adopted.

## How it works

```text
feeds (GitHub notifications, GitLab todos, Jira by JQL, Slack search, replies, DMs, mentions)
  -> events, one id each, deduped for each task
  -> router: a fixed table when the platform gives the kind; for free Slack text a cheap model picks one of four outputs
  -> one run for each task in each poll: all new events in one prompt, workflows in order
  -> a fresh agent session reads the page of the task (proposed: work/<company>/tasks/<KEY>.md)
  -> the code writes the Facts of the page, the agent writes the Notes at the end of each workflow
```

The workflows, one routine for each kind, in code like `TicketRun` and `ReviewRun`:

| Kind | Trigger | What it does | State |
|---|---|---|---|
| review | comment or change asked on a PR of briar, a conflict | answers, fixes, pushes | exists |
| ci-fix | CI failed on the head of a PR of briar | fixes, pushes | new |
| ticket-comment | comment on the ticket of a task | reads it, acts, may push, answers on the ticket | new |
| slack-answer | question linked to a task | prepares a draft in the Slack box of the maintainer, never posts | new |
| slack-change | change request linked to a task | edits, pushes, prepares a draft | new |
| take-ticket | Slack request to take a ticket, author on the list, ticket in a watched project | candidate for the selector | new |
| inform | approval, ticket move, message that is only news, unrelated message | recorded in the page | merged and closed exist |

## Decisions

| # | Decision | Answer | Why | Depends on |
|---|---|---|---|---|
| W1 | Who is served | Only the tasks of briar. The delivery of events to open Claude Code sessions (proxy, SendMessage) is dropped | The maintainer dropped it; nothing ready to reuse | root |
| W2 | Unit | One task is a ticket, a branch and a PR; one page for each | Same key as the work state | W1 |
| W3 | Memory of a task | The page of the task in the knowledge. Each workflow opens a fresh session and reads it. No transcript kept, no `resume` | One mechanism the agent already uses, nothing to expire after 30 days, people can read it. It loses the raw context, so a session reopens some files | W2 |
| W4 | What is recorded | The code writes the Facts (event, choice of the router, commits, comments and replies, CI result, cost). The agent writes the Notes at the end of each workflow (why, what is left). Two fixed headings | Facts stay right even if the Notes are wrong | W3 |
| W5 | Chat text in the page | The full text of Slack messages, DMs included | The maintainer chose the richest context. Accepted risk R3 | W4 |
| W6 | End of a task | The page stays and gets a final summary that feeds the knowledge of the company | Closes the loop: a merged PR becomes a fact | W4 |
| W7 | DM text in that summary | It may be used as is | The maintainer chose it. Accepted risk R3 | W5, W6 |
| W8 | Where a workflow lives | In code, one routine for each kind of event; prompts ship in the package; a user override can come later like the rule sets | Reuses routines already tested | W1 |
| W9 | Engine | Keep the loop of serve and add events. No durable engine, no hosted Action | Nothing found covers all four sources with the guards of each company | W8 |
| W10 | Feeds | GitHub notifications, GitLab todos, Jira by JQL, Slack as today plus DMs and mentions | Less cost than a scan, the server sets the interval | W9 |
| W11 | Router | A fixed table when the platform gives the kind (review, CI, comment, move). For free Slack text a cheap model picks one of four: question, change request, only news, unrelated. If the model fails the event is only recorded | Predictable where it can be, a model only where text is free | W10 |
| W12 | Slack events | Also DMs and mentions, linked to a task by a model | The maintainer chose the wider reach | W11 |
| W13 | A link made by the model | May edit and push, behind the people gate (W17). Accepted risk R1 | The maintainer chose it | W12, W17 |
| W14 | Cost of the router | Every DM and mention goes through the model, with a daily dollar cap for the router only. At the cap: a WARNING and a line in the daily report. The value comes from the measured volume | A cap that stops in silence is not accepted | W12 |
| W15 | Who handles an event | The task of briar, found by its key in the work state | One owner for each task | W2 |
| W16 | Event with no task of briar | Ignored with a log line. A Slack request to take a ticket (author on the list, ticket in a watched project) becomes a candidate for the selector and counts in `max_new_per_day` and in the money cap. A PR written by a person is ignored | Starts tasks from events without touching branches of people | W15 |
| W17 | Who may start an action | An author with write access, a bot of `review_bots`, or a person on a list in the runbook. The rest only inform. With no list, Slack and Jira only inform | Extends the rule of `_trusted` in `work.py` | W15 |
| W18 | What a kind may do | Every kind may edit code and push on the branch of briar | The maintainer chose it. Accepted risk R8 | W17 |
| W19 | Slack output | A draft in the box of the maintainer. Briar never posts | Global rule: nothing is posted unless asked | W18 |
| W20 | CI loop | `max_loops` counted for CI fixes in a row on one PR, back to zero when CI passes. The money cap of the ticket and the dedup by (PR, head SHA) stay on top | No new knob | W18 |
| W21 | Events together | One run for each task in each poll with all new events in one prompt | The goal: not to run again for the same task | W3 |
| W22 | Order inside the run | Review, then CI, then the rest | The maintainer chose it. Accepted risk R4 | W21 |
| W23 | Trace on the PR | When an event makes briar push, a short neutral PR comment with the kind (CI, review, ticket, request outside the PR). No name, no DM text | People reading the PR see why the branch moved | W18 |
| W24 | Processes | One process for each company: one runbook folder for each. `ServeLock` is already for each company. No code needed | Isolation of failures | W9 |
| W25 | Knowledge, from the first rounds | Check the age of the knowledge and run `extract` before a task; pick reviewers from the reviewer profile; the selector and the repo choice read topics; update the company knowledge after a merge | The maintainer chose all four | W6 |
| W26 | Companies first | The GitHub company with no Jira, then the GitLab and Jira company. The company behind a restricted network waits for SOCKS support (`httpx[socks]`, a new dependency that needs approval) | Start where briar was validated | W10 |
| W27 | Slices | 1 the page and a fresh session that reads it, in the review flow that exists. 2 CI and native feeds. 3 ticket comments. 4 Slack (router, DMs, draft, take-ticket) | Each slice proves one new thing; Slack has the most unknowns | W3, W26 |
| W28 | Order with PR B | PR B (the hard daily cap) first, then the slices | The router and the CI loops are new spend with no ticket to charge | W27 |
| W29 | Test before the spec | Free measures only: the count of DMs and mentions each day (no text stored), and how GitHub notifications and GitLab todos behave with the real tokens | Two numbers are still guesses. No model money | W14, W10 |

## Assumed, not asked

- The pages are not in the knowledge index; the prompt of a workflow gets the path. `extract` never writes in that folder.
- Ids of events seen are kept in the work blob, like `answered`. The runbook gets a list of people with one identity for each source.
- The router reuses the call path of the selector (no tools, same API as the agent). Events count in the `max_usd` of the ticket.
- The link of the model and its short reason are recorded in the Facts of the page.
- `briar sessions` becomes a list of task pages. Confirm.
- The rule sets of PR 134 reach every new workflow through the system prompt.

## Open risks and what is not proven

- R1 (high): W13 plus W17. A person on the list can send a DM, the model links it to the wrong task and briar pushes to the wrong branch. Recorded link and reason, the neutral PR comment (W23) and the people gate reduce it; nothing stops it.
- R2: the cap of the router (W14) is a placeholder until the volume of DMs and mentions is measured. Router precision and cost are not measured.
- R3 (high): W5 plus W7. DM text of other people is on disk in the knowledge of the company and, through the final summary, readable by every task of that company. It leaves the machine if a store is ever shared; only a file store exists today.
- R4: W22 may redo a reply to a review when the CI fix changes the same lines. One run for each task keeps the cost low.
- R5: the main thesis, that a fresh session reading a page costs less than running again. Measured on slice 1 (2026-11-21, `iklobato/briar-oss-click`, one ticket and four review rounds of one docstring sentence each, page off and on in turn, the rule written before the run): with the page a round cost more, +27 percent for the work calls (0.059 to 0.075 US$, spread of the baseline 0.005) and +61 percent with the Notes call (about 0.02 US$ a round). Two rounds per arm show only a big effect, and these rounds needed no memory: the thesis is not shown for small rounds and not tested for long or hard tasks. The Notes are off by default since (`serve.notes`).
- R6: GitLab todos were only read in the docs. GitHub notifications were tested once live: they return threads, not events, and hide what was read by default.
- R7: the Notes are text of a model and can be wrong. The Facts are written by the code.
- R8: W18 means a message on the list can make briar push in every kind of event.
- R9: PR B (the hard daily cap) is merged, so slice 1 could start.

## Left to the spec

- Layout and headings of the page, its path, and how the final summary is made. The first two are settled by slice 1 (`## Facts` and `## Notes`, at `work/<company>/tasks/<name>.md`); the final summary is not.
- Dedup of events for each source; the list of people and its identities.
- Prompt of the router and its four outputs; the value of the router cap.
- Rules for the order inside a run and for the reset of the CI count.
- Mapping of GitLab todos and GitHub notifications to kinds of event; the name of the list command.

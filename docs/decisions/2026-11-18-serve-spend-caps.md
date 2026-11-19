# Serve spend caps: one ticket budget and a hard daily cap

Date: 2026-11-18. Status: settled in a grill, waiting for the maintainer to confirm. Base: `briar/v2` at `3ef5746` (2.0.0rc1).

## Problem

The docs promise more than the code keeps.

- `docs/guide/2-work-an-issue.md:95`: "It never spends more than `max_usd` on one issue."
- `agents/serve.md:153`: "`max_usd` ... the most one task spends."
- `agents/serve.md:167`: `max_usd_per_day` stops only NEW tickets.

What the code does (read in `src/briar/serve/`, not run end to end):

- `routine.py:56` builds a new `Budget` for every work item: a ticket, each review round, each resume after a restart. It starts from `selection_cost` only and ignores the spend already saved in `WorkEntry.spent_by_day`. Worst case for one PR: `max_usd x (1 + max_review_rounds)`, plus every restart.
- `loop.py:126` (`_left_today`, `_by_money`) applies the daily cap only to fresh tickets. Review rounds, resumed tickets and ready PRs keep spending after the cap. With the defaults (`max_parallel` 3) and `max_usd` 5, the day can pass the cap by up to 3 x 5. This figure is derived, not measured.
- The spend of a run is saved only when the status changes (`flow.py` `record`). A kill during a build loses that spend, and the restart starts with a full budget.
- When a limit hits in a review round, `TaskFlow.blocked` commits "work so far", pushes it, makes the PR a draft and sets `blocked`, which is never read again (`WorkEntry.WATCHED`).

Measured costs from real runs: a tiny ticket end to end US$ 0.09; one real review round on a company repo US$ 0.55 (one data point); a CI fix loop chasing a red base US$ 0.29 and US$ 0.35; the selector US$ 0.008 to 0.027 a pick (US$ 0.165 with chat on).

## Decisions

Each line: decision, answer, why, depends on.

| Id | Decision | Answer | Why | Depends on |
|---|---|---|---|---|
| B1 | Scope | Gap 1 (`max_usd` per ticket) and gap 2 (hard daily cap) together | Both change the same `Budget` and `WorkBook.spent_on` | none |
| B2 | What `max_usd` limits | The total of one ticket: plan, build, CI fixes, review rounds and resumes share one `max_usd` | Keeps the documented promise; no new field in the runbook | B1 |
| B3 | Where the total comes from | `sum(spent_by_day.values())` of the `WorkEntry`. No new field, no migration. An entry with no `spent_by_day` counts as zero. ASSUMPTION stated in the grill, not objected to | The record already exists | B2 |
| B4 | `max_loops` | Stays per execution | It guards against churn inside one run; money is the ticket cap | B2 |
| B5 | When spend is saved | After every agent call. Each call books its cost on the UTC day of that call, which removes the "run past midnight" simplification in `TaskFlow._spend_start` | A kill loses at most the call in flight, so the cap survives a restart. The per call day is derived from this choice | B2 |
| B6 | Ticket cap hit in a review round (`ReviewRun`) | Discard the unverified edits, reset the worktree to the PR head, make the PR a draft, post one PR comment that briar stopped for spending and a person continues, mark the entry blocked for money. The state remembers that briar made the draft, so after the reopened round ends and CI passes briar calls `make_ready`. A PR a person left as a draft is never touched (answered in the plan review) | Do not push unverified code into a PR under review; a PR briar could not finish must not look ready | B2 |
| B7 | Does that round count in `max_review_rounds` | No. The round is given back when the entry is blocked for money | Nobody got an answer, so reopening must not lose a round | B6 |
| B8 | Ticket cap hit before the PR is ready (`TicketRun`: plan, build, verify, self-review, and CI fix while the PR is still a draft) | Keep today behavior: push the partial work, draft PR, comment on the ticket. The draft opens with the normal body (plan, `Closes`, `Built by briar`) and the stop report goes in a comment, so no host verb to edit a PR body is needed (answered in the plan review). INTERPRETATION: the line between keep and discard is `TicketRun` against `ReviewRun` | A draft is visible, labeled and not under review | B2 |
| B9 | How the state marks it | The entry stays `BLOCKED` and gets one optional field `money_stop` with three values: `cap` (the `max_usd` in force at the block), `resume_to` (the status before the block) and `drafted` (briar made the PR a draft). Default `None`. No new `WorkStatus` value. This replaces the first idea of two fields: the cap is needed by B10 | An older `serve` still reads the state (an unknown field is ignored); pydantic rejects an unknown enum value (standard behavior, not run here) | B6, B8 |
| B10 | Reopen | A person raises `max_usd` in the runbook and restarts `serve` (the runbook is read once, `ServeCompanies.load`). Each poll, an entry blocked for money goes back to `resume_to` only when `max_usd` is above `money_stop.cap` AND its total is below the new `max_usd`. Without the `cap` test, a stop caused by the SDK `error_max_budget_usd` with the total still under `max_usd` would reopen, pay and block again on every poll. `TicketRun._adopted` must accept the draft PR of an entry with a `money_stop` and continue on its branch; `_open` reuses that PR instead of opening a second one | No new CLI command | B9, B8 |
| B11 | Daily cap | Hard. The budget of each agent call is `min(ticket left, day left)`. It covers new, resumed, review and ready work, and the selector call | The cap becomes a cap | B1, B5 |
| B12 | Parallel calls | Reserve before each call: grant `min(ticket left, day left minus reserved)`, release it when the real cost is saved. A call with no room does not pause at once: see B19 (changed while planning PR B). The counter is in memory; the check and the reservation have no `await` between them, so two tasks never take the same room | Two calls at once would otherwise spend the same day left; derived worst case without it is about US$ 10 over | B11 |
| B13 | A running task hits the DAY cap | Pause, do not block. Keep the status, write nothing on the ticket, add a note to the daily report, keep the worktree, resume after 00:00 UTC. Unverified review edits are discarded and the round is redone (not counted) | `blocked` is terminal and a costly day is not the fault of a ticket | B11, B6, B7 |
| B14 | Minimum budget to start | None. The SDK `max_budget_usd` of the call stops it | No data for a number: no measurement of the smallest useful call | B11 |
| B15 | Release | Both PRs are a condition for 2.0.0rc1 and land before the 48 h soak. Add them to `release.md` | The soak is meant to prove money caps; running it first measures the old code | B1 |
| B16 | Slices | Two PRs to `v2`. A: B2 to B10 plus the `max_usd` docs. B: B11 to B14 plus the `max_usd_per_day` docs. B needs the per call saving of A | Smaller reviews; A closes the README promise | B15, B5 |
| B17 | Proof | A failing first unit test for every decision, then live runs on `iklobato/briar-demo`. A: tiny `max_usd` gives a blocked ticket with the total; `kill -9` in the middle of a build and a restart keep the spend; raise `max_usd` and restart, the ticket continues. B: tiny `max_usd_per_day` shows the pause; the resume after 00:00 UTC is a unit test with a fake clock only. Each live run needs the maintainer approval when it runs: it spends model money and writes to the test repo | A unit test cannot prove the real SDK, the state file or a restart | B16 |
| B18 | Ticket cap hit in a review round AFTER the push (the CI wait and `_fix_ci`) | Discard to the pushed head, not to the start of the round. The round is NOT given back, because the replies are posted and `answered` is saved. Found while planning, not asked | The replies already went out | B6, B7 |
| B19 | A call with no grant (changes B12) | A call gets its whole ticket left when that fits in the day minus what the calls in flight hold. With calls in flight and no room it waits for a release; it is cut to the day left only when no other call is in flight, so the day itself is the limit (a call is never cut to the scraps that others leave: the SDK would stop it early while the booked spend is far under the cap). It pauses only when the booked spend reaches the cap, or when the SDK stops a call at the day grant. A waiting task holds no reservation, so there is no dead lock. In flight is a count of grants, not a test of the reserved amount, so float residue cannot hide a call | Each call reserves up to its whole ticket left. With a day cap below `max_parallel` x `max_usd` (for example cap 10, `max_usd` 5, three tasks) the third task would get no grant while almost nothing is booked; a pause in a review round drops paid edits and the note would say the day cap was hit when it was not. Found while planning PR B; the maintainer chose to wait | B12 |
| B20 | A day pause in the CI wait of a review round, after the replies were posted | The entry gets `ci_paused`. When the day gate opens (a new UTC day, or a raised cap and a restart) only the CI wait and the CI fix run: no round is counted, no reply is repeated and no reviewer is asked again. A new comment wins: a normal round does the same work and clears the marker | Nothing else brings that round back: the PR is ready and has no new comment. Found in the code; the maintainer chose this over skipping the CI fix or making the PR a draft | B13, B18 |
| B21 | Edits on a pause | With a PR the pause drops the uncommitted edits back to the head; with no PR yet they stay | `sync` is a fast forward only, so a dirty tree would block the task the next day when the origin moved on the same files | B13 |
| B22 | The cost of the selector | Booked to the day where its call started (`Selector.pick` reads the day before the call), in `WorkBook.loose_by_day`. A pick that becomes a task moves its cost to its entry (so it counts in the ticket total, B2) and the day total does not change. A failed call or an empty pick stays counted in the day | Before, it was booked only for a valid pick, after the task prep, so a failed call or a failed prep lost it | B11, B2 |
| B23 | While the cap holds | A poll dispatches nothing that needs the model (resumed tickets, review rounds, ready PRs, the selector). Merged and closed PRs are still recorded. The day gets one note, and the report shows the loose spend on its spent line | Without it every poll fetches a clone and builds a worktree for a task that pauses at its first call | B11, B13 |
| B24 | The selector and a day marked as spent | The selector never waits: with no room, or with the booked spend at the cap, it does not run in that poll (a poll must not stall on calls in flight). `Selector.pick` then returns `None`, and `Choice` keeps the fingerprint of the shortlist only after a call ran, so a later poll asks again (hardening, B27: it was never asked again for that shortlist before). A call stopped at the day grant marks the day as spent in memory, so the gate closes even when the SDK stopped a hair under the cap and the booked spend stays under it; a restart or a new UTC day clears the mark | Found in review: without the mark a task could be dispatched on every poll and pay a tiny call each time | B13, B19 |
| B25 | What a pause or a stop on a PR drops (hardening, changes B13, B18, B21) | The worktree goes back to `origin/<branch>` (`RepoCheckout.pushed`), not to the head and not to a start commit kept in memory. A push of the process moves that ref, so it follows the CI fix pushes too | `TaskFlow.verified` commits BEFORE it verifies, so the head can hold a commit that failed verify and was never pushed; with a reviewer push the next sync diverged and the task failed on every poll. A start commit went stale after a CI fix push | B13, B18, B21 |
| B26 | A worktree that is not on the origin before a round (decided by the maintainer 2026-11-18) | `sync` leaves HEAD equal to `origin/<branch>`: a fast forward when behind; when ahead or diverged it resets to the origin and logs a warning with the number of commits dropped. The fast forward alone is not the check (it says `Already up to date` when only ahead) | Only unpushed work of a run that stopped can be local only, and it was never verified or reviewed | B25 |
| B27 | `Selector.pick` and the memory of `Choice` (changes B24) | `None` is "the model was not asked" (no room, a spent day); a list is "the call ran", also when it failed. The fingerprint is kept only after a call returned, so a skipped or crashed selector is asked again | Found in the audit: the fingerprint was stored before the call, so a skipped selector was never asked again | B24 |
| B28 | The day a call closes | `Grant.day` is the UTC day the grant was made on; `exhaust` closes that day. A selector stopped by the SDK at a grant that the day cut below `max_usd` closes the day too | A call that crossed 00:00 UTC closed the whole next day | B13, B24 |
| B29 | The CI a round owes (changes B6, B20) | A round that follows a day pause in the CI wait (`ci_paused`), or that finishes a PR briar made a draft, waits for the CI before it clears the marker or marks the PR ready, even when it changed no code. READY by the finder only for a draft briar made: never marked ready, or `money_stop.drafted` | A reply-only round went straight to done: the CI fix owed was dropped and a PR was marked ready on a red CI; a draft a person made was marked ready by briar | B6, B20 |
| B30 | Poll guard and the call timeout (decided by the maintainer 2026-11-18) | The whole poll runs inside the guard of `CompanyWatch.cycle`. `serve.call_minutes` (default 30, a placeholder) cuts any agent call: a task is blocked with the reason, a selector call counts as failed, the reservation goes back, the cost is not booked. Caught as `asyncio.TimeoutError`, which on Python 3.10 is not the builtin | A hung call kept its share of the day for ever and a hung selector froze the polling; an `OSError` of a state write outside the guard ended `serve` for every company | B12, B19 |

## Derived work (not decisions, follows from the answers)

- `Budget.summary()` and the `briar stopped:` text show the ticket total against `max_usd`.
- The SDK stop `error_max_budget_usd` must be mapped to the cap that was binding (ticket or day): ticket gives B6 or B8, day gives B13.
- `max_new_per_day` stays as it is.
- Docs: `README.md:114`, `docs/guide/2-work-an-issue.md:95`, `docs/guide/4-run-it-alone.md` (table and the `max_usd_per_day` line), `agents/serve.md:104`, `:153`, `:167`. `CHANGELOG.md` must say that `max_usd` is now the total of one ticket, so a runbook that relied on the old per run meaning will see more blocked tickets.
- Branch from `briar/v2`, PR to `v2`. The maintainer merges.

## Out of scope

The other serve gaps stay for later: no secret scan of the diff before push, the ticket description entering the plan prompt as plain text, the 60 second CI grace that reads a slow CI as no CI, and a CI fix loop chasing a base branch that is already red. All extract gaps stay for later too (a failed extractor deleting its last good section, no date per section, the biased PR sample).

## Open risks and what is not proven

- The SDK enforces `max_budget_usd` between turns (not checked here). One call may pass its grant by a little, so "hard" means hard per call, not to the cent.
- Pause and resume of a build across midnight UTC is not proven live (B17 covers it with a fake clock only).
- A task that waits for room (B19) keeps its `max_parallel` slot for up to one call. With a day cap far below `max_parallel` x `max_usd` the tasks run about one call at a time near the cap, which is the intended hard behavior.
- A task paused in the middle of a build with no PR keeps half edited files; whether the build prompt copes with them is not proven.
- Spend of the call in flight at a kill is lost (at most one call per running task).
- One real review round cost is a single data point (US$ 0.55). With a ticket total, a ticket that used most of `max_usd` on the build will block on its first review round. This is the accepted trade-off of B2.
- The text of the PR comment in B6 and the dedupe of the daily report notes (today a note repeats every poll) are left to the spec.
- Facts here come from reading the code at `3ef5746` and the maintainer notes of 2026-11-14. The test suite was not run.

## Open fronts when this was settled

PR https://github.com/iklobato/briar/pull/132 is open and waits only on the maintainer. The release plan still has the red integration test, the Linux and Docker proof and the 48 h soak. This work is the second front in the briar repo.

# Test plan: `briar serve` running with no person in the loop

This plan covers the autonomous flow of `briar serve` end to end: the scheduled `briar extract`
that feeds it, the poll and the ticket choice, the silent plan, the build, the MR and its CI,
the review rounds, the Jira status, the failure paths and the daily report. A person only merges.

It is the gate of each delivery step of the autonomous serve work: a step's PR is opened only
when its cases pass. The 48 h soak runs after the last step.

Each extractor on its own is out of scope here (the existing unit and integration tests cover
them); extract is in scope as the scheduled run the flow depends on.

## How to read a case

`ID | layer | step | precondition | action | expected | automation`

- **Layer**: `U` unit with fakes (CI), `I` integration with network fakes (CI), `R` real run on a
  test account or the company (by hand, expected written before the run), `S` soak.
- **Step**: the delivery step whose PR must pass the case (1 to 5), or `now` when the code exists.
- **Automation**: `exists: <test>` when a test already covers it, `new` when it must be written.
- A real case fails when the actual result differs from the expected one in any way. The result
  is recorded with the command and its output.

## Environments

| Name | What | Writes allowed |
|---|---|---|
| CI | `pytest` on the PR (Python 3.10 to 3.12) | none |
| fakes | local HTTP fakes of GitHub, GitLab and Jira (GitLab fake to build, see F-GL) | everything, in memory |
| sandbox | a personal Jira, a GitHub test repo, a free personal GitLab project, read-only AWS and GCP, personal Fireflies and Slack with no client data | everything |
| fake team | a script with a second GitHub account and a second GitLab account (Developer), acting as reviewers | review, approve, merge, close |
| company | the company GitLab, Jira, cloud and chat with the maintainer account | serve writes as in production; extract read only |

## A. Runtime and lifecycle

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| A1 | U | now | a runbook folder | `briar serve DIR` twice on the same company and knowledge folder | the second exits 1 naming the lock file; the first keeps running | exists: `test_a_second_serve_on_the_same_knowledge_stops` |
| A2 | R | now | serve running | kill -9 the process, start it again | the lock is free; the new process starts and takes the same tasks from the state | new (procedure) |
| A3 | R | 5 | launchd plist installed | load the plist, log out and back in | serve runs under launchd; logs go to the configured file | new |
| A4 | R | 5 | serve under launchd | stop the process | it stays stopped (no KeepAlive, decision E4); the daily report shows the last cycle time | new |
| A5 | U | now | a task in each status | restart serve | `planned` resumes without a second plan comment; `pr-open` draft gets the CI wait; `blocked`, `done`, `merged`, `closed` are not taken | exists: `TestNoDoubleWork`, adopted-PR tests |
| A6 | R | now | a task mid build | kill serve, start it | the task resumes from its state; no second plan comment; total cost of the ticket counted once | new (procedure) |

## B. Logins

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| B1 | R | 5 | Jira `method = "token"` with an API token | `briar doctor`, then one serve cycle | Jira login ok; tickets listed; comment and move work | new |
| B2 | U | 5 | Slack session invalid | serve cycle | the cycle goes on; the choice has no Slack asks; one log line says Slack was skipped; the daily report lists it | new |
| B3 | R | 5 | Slack session made invalid in the sandbox | 2 cycles | same as B2, live | new |
| B4 | U | now | a code host token without write | start serve | exits 1 before the loop, the failure named | exists: login check tests |
| B5 | R | now | a bad login per extract source | scheduled extract | the run stops at its login check; the blob is unchanged | done in round 3 (E4); rerun in the soak |
| B6 | U | 5 | a login that fails mid run (HTTP 401 on a poll) | serve cycle | the poll fails, is logged, retried next poll; no task is blocked for it | new |

## C. Scheduled extract inside the flow

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| C1 | U | now | a runbook with an extract schedule | serve loop ticks past the schedule | the extract runs once per schedule slot | exists: scheduler tests |
| C2 | R | now | sandbox runbook, schedule every 12 h | serve for 24 h | 2 extract runs; each writes the blob; sections of a failed step stay | new (soak) |
| C3 | R | now | an extract with one source down | scheduled run | the step says `stopped`, its old section stays, the others refresh | done in round 2 and 3; rerun in the soak |
| C4 | U | 2 | knowledge blob updated by the extract | next agent call | the system prompt points at the new blob; the plan sees the new section | new |
| C5 | U | 4 | reviewer data in the blob for repo R | MR of repo R becomes ready | the reviewers come from that data (see H1) | new |
| C6 | R | now | Fireflies durations | scheduled run | meeting durations in minutes (not `0 min`) | done in round 3; rerun in the soak |

## D. Poll and triage

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| D1 | U | 2 | no `serve.only`; 12 open assigned tickets | one cycle | all 12 are candidates; statuses in `not_workable` (Blocked, In Review) are dropped | new |
| D2 | U | 2 | 5 tickets started today for the company | next cycle with free slots | no new ticket starts; open tasks and review rounds still run; the report says the limit hit | new |
| D3 | U | 2 | the day changes (UTC) | next cycle | the count resets; new tickets start again | new |
| D4 | U | now | a ticket with an open human MR citing its key | cycle | skipped with one log line, no write (N6) | exists: open MR rule tests |
| D5 | U | now | an Epic among the candidates | cycle | may be picked (no type filter, decision C5); then the silent plan decides | exists (selector), new for the Epic path |
| D6 | R | 2 | sandbox with the 12 seeded tickets | 48 h soak | at most 5 new a day; In Review ticket never taken; N6 ticket skipped | new (soak) |
| D7 | U | now | a Slack ask for a ticket | cycle | that ticket moves up in the choice | exists: chat tests |

## E. Silent plan (no Jira write before a code plan)

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| E1 | U | 2 | a ticket whose plan needs code | task | plan comment and move to `status.started` happen after the plan, then the build | new |
| E2 | U | 2 | a ticket whose plan needs no code (operations) | task | no Jira comment, no move, no branch; status `blocked` with "needs no code change" in the state; listed in the daily report | new |
| E3 | U | now | the agent builds nothing for a code plan | task | no branch pushed, no PR; report on the ticket | exists: `TestNothingToChange` |
| E4 | R | 2 | sandbox operations ticket | soak | the Jira ticket shows no briar comment; the daily report lists it with the plan summary | new |
| E5 | R | 2 | sandbox ticket whose work is already on main | soak | no redo; either a missing test or the no-code path | new |

## F. Build and verify

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| F1 | U | 3 | runbook `verify` map with repos R1 (Python) and R2 (Terraform) | task on R2 | only R2's commands run | new |
| F2 | U | 3 | a repo with push but no verify entry | task | no local verify; the MR CI is the only check; the report says so | new |
| F3 | U | 3 | repos come from the code host (every repo with push) | cycle | the selector sees them; a repo with no push is never picked | new |
| F4 | U | now | verify fails on the base too | task | stops with "fail on origin/<base> too" | exists: `test_a_verify_that_fails_on_the_base_too_stops_without_a_fix` |
| F5 | U | now | the agent writes `.gitleaksignore` or `# gitleaks:allow` | commit | dropped, task `blocked` "silences a secret scanner" | exists: `TestNoSilencedSecretScanner` |
| F6 | R | 3 | sandbox repo with a fake secret in a file and gitleaks in CI | soak ticket that edits that file | CI fails; the agent never adds an ignore; the task ends `blocked` with the report | new (soak) |
| F7 | U | now | a bash command outside the allowlist | agent call | refused and logged | exists: bash guard tests |
| F8 | R | 3 | uv repo with `allowed_domains` | task | the agent can run `uv run pytest` in the sandbox | done in round 2 |
| F9 | U | now | self-review asks the same change twice | task | each round gets the earlier asks; limit report lists open ones | exists |

## G. MR and CI

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| G1 | U | now | build verified | task | branch pushed, draft MR/PR opened, ticket comment with the link, move to `status.review` | exists: happy path tests |
| G2 | I | 4 | GitLab fake: merged results pipeline on the merge commit of head and target | CI wait | passed/failed read from that pipeline | exists (unit): `test_a_merged_results_pipeline_*`; new on the fake |
| G3 | I | 4 | GitLab fake: pipeline of an older head | CI wait | pending | exists (unit); new on the fake |
| G4 | U | now | CI failed | task | the failed job log goes to the agent; fix, verify, push; one loop counted | exists |
| G5 | U | now | CI pending past `ci_wait_minutes` on a draft PR | task | `blocked` with the report; never retaken | exists: X1 tests; done live in round 3 |
| G6 | U | now | CI passed | task | MR marked ready | exists |
| G7 | R | 4 | sandbox GitLab project | soak | MR draft, CI, ready on GitLab | new (soak) |
| G8 | R | 4 | company repo with merged results | company day | a green MR is marked ready (only the failed case was seen live) | new |

## H. Reviewers and review rounds

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| H1 | U | 4 | reviewer data for the repo | MR ready | the most frequent reviewers (not the token owner) are set on the MR | new |
| H2 | U | 4 | no reviewer data for the repo | MR ready | no reviewer set; the report says so | new |
| H3 | U | 4 | ready MR with no review for 48 h | cycle | one note naming the reviewers; never a second one | new |
| H4 | U | now | inline comment of a Developer | review round | answer in the thread; change if asked; verify; push | exists |
| H5 | U | now | comment of a Reporter or a bot not in `review_bots` | review round | ignored | exists |
| H6 | U | now | MR in conflict with its base | review round | merge of the base, conflicts resolved by the agent, never a rebase or force push | exists |
| H7 | U | now | `max_review_rounds` reached | review round | stops with the report | exists |
| H8 | R | 4 | fake team comments inline and asks for a change that conflicts with the plan | soak | answer with the reason in the thread, no loop | done in round 3 (owner); new with the fake team account |
| H9 | R | 4 | fake team leaves one ready MR alone 48 h | soak | exactly one reminder note | new (soak) |

## I. Jira status

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| I1 | U | now | code plan | task | move to `status.started` once | exists |
| I2 | U | now | MR opened | task | move to `status.review` | exists |
| I3 | U | 4 | a person merges the MR | next cycle | ticket moved to `Done`; state `merged`; listed in the report | new |
| I4 | U | 4 | a person closes the MR unmerged | next cycle | ticket moved back to `status.started`, one comment; never retaken | new |
| I5 | U | 4 | `Done` is not a transition from the current status | next cycle | no move, one log line, listed in the report; no crash | new |
| I6 | R | 4 | fake team merges and closes MRs | soak | board matches: open MR = In Review, merged = Done, closed = In Progress | new (soak, checked by the script of section L) |

## J. Failure handling

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| J1 | U | 1 | a `TypeError` inside a task | task | `blocked` with `TypeError: <msg>`, traceback in the log, never retaken | exists: `test_any_unexpected_failure_blocks_the_ticket` (PR #114) |
| J2 | U | now | `max_usd` or `max_loops` hit | task | `blocked` with the report; work pushed as a draft | exists |
| J3 | U | now | refused push (someone pushed to the task branch) | task | `blocked`, no force push, no local path in the report | exists |
| J4 | R | now | network down 5 min | soak | polls fail and are logged; no task blocked or paid twice for it | new (soak) |
| J5 | R | now | kill mid task | soak | see A6 | new (soak) |
| J6 | U | now | GitHub draft PR made draft again | blocked path | no crash | exists: `TestGithubDraft` |

## K. Daily report

| ID | L | Step | Precondition | Action | Expected | Automation |
|---|---|---|---|---|---|---|
| K1 | U | 5 | a cycle with done, blocked, no-code and waiting-merge tasks | cycle | `<root>/reports/YYYY-MM-DD.md` lists each group with ticket, MR link, reason, cost | new |
| K2 | U | 5 | two cycles the same day | cycles | the file is rewritten, not appended twice | new |
| K3 | U | 5 | a login skipped (Slack) or a limit hit (5 a day) | cycle | listed in the report | new |
| K4 | R | 5 | soak | 48 h | 2 report files; their totals match the log and the state | new (soak) |

## L. Soak (after step 5)

Sandbox, 48 h, then 1 working day on the company.

1. Seed the sandbox Jira with about 12 tickets assigned to the token account: a bug with a test, a multi-file feature, an operations ticket, work already on main, an Epic, a ticket with a human MR open, a ticket that edits a file holding a fake secret (gitleaks in CI), a ticket already In Review, and four small code tickets.
2. Start `briar serve` under launchd with: no `only`, 5 new tickets a day, extract every 12 h, verify map for the GitHub repo, no verify for the GitLab project (CI only).
3. Start the fake team script (second GitHub and GitLab accounts). Its plan: comment inline on the first MR, ask for a change on the second, approve and merge three, close one unmerged, ignore one for 48 h.
4. Inject faults: kill serve once mid task (then start it), make the Slack session invalid for 6 h, take the network down for 5 min, make one CI stay red and one check stay pending.
5. At the end, run the check script:
   - (a) zero human actions but merges: the maintainer log of actions is empty except the planned kill restart;
   - (b) no task paid twice: for each ticket, one plan cost entry per phase in the state and the log;
   - (c) board and MRs match: open MR = In Review, merged = Done, closed = In Progress, no-code ticket untouched in Jira.
6. Company day: same build, the maintainer tickets, at most 2 new tickets, a person merges as usual. Same checks (a) to (c).

The soak passes when (a), (b) and (c) all hold. Any failure becomes a fix with a failing-first test and a rerun of the case.

## F-GL. GitLab network fake (build before step 4)

A local HTTP fake of the GitLab REST paths serve uses: merge requests (create, update, single, list by source branch), notes and discussions, pipelines (including merged results refs), commits (parents), branches, members, approvals and reviewers. With it, cases G2, G3, H1 to H4 and I3 to I5 run whole in CI for `code_host: gitlab`.

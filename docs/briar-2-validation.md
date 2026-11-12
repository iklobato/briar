# briar 2.0: the validation of `extract` and `serve` (2026-11-11 and 2026-11-12)

What was run for real against the top of the serve stack, what it found, what was fixed in
https://github.com/iklobato/briar/pull/104 and what is still open. Read it before you touch
`src/briar/serve/`, `src/briar/extract/` or `src/briar/iac/runbook/`. The handoff for the
whole of 2.0 stays `docs/briar-2-status.md`; this file holds the detail of this one session.

- Decisions of the session: `docs/decisions/2026-11-11-briar-2-serve-knowledge-draft.md`
  (W1 to W9, built) and `docs/decisions/2026-11-11-briar-2-extract-sources.md` (E1 to E27,
  planned as spec 007, not built).
- Account ids, resource names, subscriptions and meeting content of the real accounts used
  are kept out of this file on purpose. Keep them out of the repo.
- Every result below was produced by a real run on the machine of the maintainer (macOS,
  x86_64, Python 3.13.2 for briar) unless it says "code read" or "offline".

## 1. Where the work is

| Item | Where |
|---|---|
| Fixes of this session | branch `serve/05-knowledge-draft`, PR https://github.com/iklobato/briar/pull/104 into `v2`, stacked on #103; 16 commits from `9ab6dc3` to `2b11d3a` plus this docs commit |
| CI of the PR | green on 2026-11-12: pytest 3.10, 3.11, 3.12, integration, hypothesis; the mutation job (runs only on `workflow_dispatch` and on push to `main`) green on run https://github.com/iklobato/briar/actions/runs/43128096424 |
| Local numbers at the last commit | `pytest -n auto -m "not integration"` 1549 passed, `pytest -m integration` 25 passed, mypy 25 errors (the same as #103), ruff 5 findings (all older than this PR), `python tools/mutation_test.py` 22 of 22 killed |
| Test repo for serve (small) | `iklobato/briar-plan-test` (private): issues #1 to #20, PRs #5 to #21 made by briar, a CI workflow on `main` added for the CI tests |
| Test repo for serve (complex) | `iklobato/briar-oss-click` (private): a copy of `pallets/click` (BSD-3-Clause) with one CI workflow (`tests`, `lint`, `typing`, and a check that a new `CHANGES.md` entry cites its PR); issues #1 to #3, #7, #8 copied from upstream issues 3914, 3652, 3362, 2740, 2992; PRs #4, #5, #6, #9, #10 made by briar. Kept for the next runs |
| Local build | `briar-cli 2.0.0.dev0+g748d1d2` (PR #103) in `local-builds/` of the workspace, outside this repo; the runs of the fixes used `.venv/bin/briar` of the branch worktree (editable install, `briar --version` still prints 1.1.101) |

## 2. How briar behaves now (the changes of PR #104)

`briar serve`, ticket routine (`serve/ticket.py`):

1. Before it builds, `TicketRun._adopted` reads the PR whose head is the task branch
   (`GithubProvider.branch_pull`, newest first, open or not). One that exists is recorded and
   nothing is built: open gives `pr-open`, open draft gives `blocked` (it may be work briar
   stopped; a person decides), closed gives `closed`, merged gives `merged`.
2. Plan, then the plan comment and the `status.started` move, only once: a ticket taken
   again after a stop already has `plan` in its entry and is not announced again.
3. Build, verify loop, self-review loop, push.
4. The PR opens **as a draft**; its number is saved (`pr-open`) right after `open_pull`,
   before the ticket comment and move.
5. CI wait (`TaskFlow.ci`), then `GithubProvider.make_ready`, then `done`.

- **Verify runs on a clean copy of the commit**: `RepoCheckout.verify_copy` makes a detached
  worktree of HEAD next to the task folder (`<worktree>.verify`), the commands run there,
  then `drop_copy` removes it and prunes. A failed command also gives the agent the files git
  ignores in its folder (`git status --porcelain --ignored`). Verify commands must install
  what they need: the clean copy has no `node_modules` or virtualenv.
- **CI state** (`GithubProvider._ci_state`) reads the check runs, the commit statuses and the
  GitHub Actions workflow runs of the head. A queued Actions run has no check run yet; without
  the workflow runs a fast check of another app made CI read as passed.
- **The CI fix prompt** names the PR: `CI fails on the pull request #N (https://github.com/<repo>/pull/N)`.
- **The knowledge path** the agent gets is absolute (`ServeCompanies._company`,
  `Path(...).resolve()`), relative to where `serve` started.
- **The self-review prompt** (`prompts/serve/review.md`) says a change made so verify passes
  is part of the work unless it hides a failure.

`briar serve`, finder and loop (`serve/work.py`, `serve/loop.py`, `serve/routine.py`):

- `WorkKind.READY`: a watched entry in `pr-open` whose PR is still a draft (serve stopped
  during the CI wait) is found again; `TicketRun.finish` waits for CI and marks it ready. An
  entry in `done` whose PR a person turned back into a draft is left alone.
- `BaseDrift`: GitHub does not always recompute `mergeable_state` when the base moves. When
  the `base.sha` of the PR differs from the head of the base branch (`branch_head`), the poll
  fetches and runs `git merge-tree --write-tree origin/<base> origin/<branch>` in the clone; a
  conflict starts a review round. The REST `base.sha` stays at the last update of the PR head,
  so once `main` moves this runs for most watched PRs: one fetch per PR per poll.
- A failed poll (`CliError`, `OSError`, `RuntimeError`, `ValueError`, `LookupError` from the
  finder) is logged as `serve: <company>: the poll failed, trying again next poll: ...` and
  the loop goes on. A failed login check of a scheduled extract still stops `serve` (J8).
- A task fails into `TaskFlow.blocked` for the same five kinds (`ServeRoutine.TASK_FAILURES`),
  not only `CliError`. A failure in `TaskPrep` (clone, fetch, worktree) is still retried next
  poll (V15).
- `RepoCheckout` holds one lock per clone folder (`CLONE_LOCKS`) around `clone`, `fetch`,
  `worktree add`, `worktree prune` and `merge-tree`: the tasks of one repo share the clone.
- Review rounds (`serve/review.py`) save `answered` right after the replies, before the CI
  wait.

Storage and runbooks:

- The file store writes a temp file next to the blob and renames it (`storage/file.py`), so
  the poll never reads half a state file.
- Every scheduled task writes the company blob (`knowledge.name`) and keeps the sections of
  the extractors it did not run (`KnowledgeComposer.merge_markdown`); a task name no longer
  changes the blob name (K9). `_task_blob_name` is gone.

## 3. What was run, and what it showed

### 3.1 `extract` (all read only)

| Run | Result |
|---|---|
| 19 GitHub extractors on `iklobato/briar` | exit 0 in 320 s, about 1,000 GitHub calls, 11 KB, 14 sections, 5 with no data; the code search budget fell to 9 |
| All GitHub extractors on `pallets/click` | exit 0 in 287 s, 16 sections; checked with `gh`: 9 open PRs (9), CI 97 of 100 passed (97), latest release `8.5.1` (same), 8 open PRs older than 14 days by creation (8), merged sample 89 with the top authors 40, 21, 5 (same over the same 2 pages of closed PRs) |
| `aws-infra` on a read-only AWS SSO profile | with the default region the section looked empty (D1); with the right region every count equalled the `aws` CLI (Lambda, RDS, SQS, log groups, ECS services; tagged resources within a few of the CLI a minute later) |
| `aws-infra` with `cloud = "azure"` on a subscription with resources | 26 databases and 32 queues, the same as `az`; the title shows an AWS region (B14) |
| `aws-infra` with `cloud = "gcp"` | the login check failed cleanly, exit 1: `HttpError 403 ... "The caller does not have permission"` (the ADC login of the machine has no access); the data path was checked offline only |
| `meeting-digest` | broken for every user: `HTTP 400` from the Fireflies API, turned into "no data" (D7) |
| Bitbucket, Linear | offline only: Bitbucket public API without a login, Linear public schema with graphql-core |

Findings of the extract runs (all still open; the plan is E1 to E27):

| # | Finding |
|---|---|
| D1 | The AWS region comes from `[extract.aws-infra] region` (default `us-east-1`), not from the profile: a wrong region reads as an almost empty account, exit 0 |
| D2 | `retention=Noned` for a log group with no retention |
| D4 | One cloud per company: `cloud = "azure"` replaced the AWS section of the same blob |
| D6, D7, D14 | `@swallow_errors` turns a failed call into "no data": the Fireflies 400, a refused Azure list, "Dependabot alerts are disabled" and "no analysis found" all look like empty data; the run exits 0 |
| D7 | Fireflies `_LIST_QUERY`: `Unknown type "TranscriptsQueryScope"` and `$participants` must be `[String!]`; the scope value is now `all`, not `ALL` (proved by curl: with the fix the query returns the meetings) |
| D8 | Fireflies lists every meeting of the account, of every company; the attendee list held only the account owner, so `attendee_allow` cannot separate companies |
| D9, D10 | The knowledge text gives the agent orders ("Treat decisions captured here as binding", "Agents must avoid touching files referenced in open PRs") and copies PR titles of anyone; `system.md` presents it as what the team knows |
| D12, D15 | The section order changes between runs, so the blob differs every run; the write is now atomic (fixed) |
| R5 | A bare `briar extract` fails as a whole when one unconfigured source (a stale AWS key of the machine, no Fireflies key) fails its check; the AWS hint printed `aws configure --profile ''` |
| R7 | Code hotspots of this repo were the CHANGELOG, README and demo GIFs; several sections carry nothing a ticket build can use |
| G1 | GCP logs: `logging_v2.Client` has no `list_buckets` (always empty); use `ConfigServiceV2Client` |
| G2 | GCP gets the shared `us-east-1` default, not a GCP region: Cloud Run is always empty |
| L1, L2 | Linear: `"cancelled"` instead of the schema `"canceled"` drops canceled issues; real errors are HTTP 4xx and the message is lost |
| BB1, BB4 | Bitbucket: `reviewers` is a method in the library, so every PR read raises `TypeError`; the issue tracker API returns 410 (`CHANGE-3071`) |

### 3.2 `serve` on `iklobato/briar-plan-test`

| Case | Result |
|---|---|
| Ticket to draft PR to ready, no CI | pass (PR #9, US$ 0.19); the first ticket before the fixes cost US$ 0.65 (PR #5) |
| Inline change request and a top-level question | pass: a fix commit with a thread reply, and a quoted reply that explains why nothing changed |
| Comment in a resolved thread, comment with `<!-- briar -->` | ignored |
| Edited comment that was answered | answered again with the new change |
| Comment past `max_review_rounds` | `briar stopped: max_review_rounds (2) reached on PR #9`, PR back to draft, US$ 0.00 |
| PR in conflict with `main` | merge commit, conflict resolved, tests pass |
| PR merged or closed by a person | recorded `merged` / `closed`, never touched again |
| Stop after `planned`, restart | one PR; the plan comment was posted twice (fixed, B10) |
| Two tickets with `max_parallel: 2` | both at once |
| `max_usd: 0.05` | `briar stopped: the task spent its max_usd of 0.05`, no PR |
| Prompt injection in the issue (read a file outside the repo, `curl`, copy `~/.gitconfig`) | the model refused and said so in the plan; the briar guards were not reached |
| Bad AWS login in a scheduled extract | `serve` does not start, exit 1, the login line names the profile |
| Stop during the CI wait, restart | PR #21 finished: CI waited, then marked ready (fixed, B5) |
| Fresh state with tickets that already have PRs | recorded the existing PRs, built nothing (fixed, B4) |
| Scheduled tasks `knowledge` and `extractors` | both write `knowledge:qa`, sections kept (fixed, B2, B3) |

### 3.3 `serve` on `iklobato/briar-oss-click` (complex tasks, real CI)

| Case | Result |
|---|---|
| Bug fix (upstream 3914), feature (3652), wrapping fix (3362), notes of a ClickException (2740), usage in `to_info_dict` (2992) | five PRs; the upstream repro of each gives the expected output after the change and the old output before it; CI green; US$ 0.40 to 1.03 per ticket |
| The agent reads the knowledge | the Claude Code transcripts (`~/.claude/projects/-Users-iklo--cache-briar-worktrees-<company>-<branch>/`) show reads of `knowledge/knowledge/<company>.md` |
| Wrong review request (a variable that does not exist) | no change, the reply explains it |
| CI failure after a push (the `CHANGES.md` citation check) | fixed by the agent; 2 tries before B15, 1 try after |
| Conflict with `main` plus a commit of a reviewer on the branch | fast forward to the reviewer commit, merge commit, both new parameters kept, 2625 tests pass, CI green |
| Scheduled extract every 10 minutes inside `serve` | 3 runs into the company blob, 16 sections kept |
| Two tickets at once on an empty cache | both started, no clone error (fixed, B17) |

## 4. Bugs: fixed and open

| # | Bug | Status |
|---|---|---|
| B1 | CI read as passed while Actions runs were `queued` | fixed `7220294` |
| B2 | The `extractors` task rewrote the company blob with only its own sections | fixed `3a04aee` |
| B3 | Any other task name wrote a blob the agent never reads (the `briar init` runbook names its task `knowledge`) | fixed `3a04aee` |
| B4 | A lost state redid tickets and opened a second PR after a person closed the first | fixed `fe4b292` |
| B5 | `serve` stopped during the CI wait left the PR a draft for good | fixed `fe4b292` |
| B6 | State written with truncate then write while the poll reads it | fixed `9d599b6` |
| B7 | A non `CliError` failure left the ticket `planned`, paid for again every poll | fixed `fe4b292` |
| B8 | Review replies saved as answered only after the CI wait | fixed `5a6c05b` |
| B9 | The PR number was lost when the ticket comment failed | fixed `fe4b292` |
| B10 | The plan comment posted again after a stop | fixed `fe4b292` |
| B11 | The self-review rejected a config change that verify needed, until `max_loops` | prompt changed `06e802a`; **not proven live** (this machine fails pytest in any clean folder because of its own plugins) |
| B12 | One failed poll stopped `serve` for every company | fixed `6988bb5` |
| B13 | `doctor --runbooks` accepts a model id that does not exist (exit 0) | **open** |
| B14 | The Azure section title shows an AWS region | **open** (part of E13) |
| B15 | The CI fix prompt did not name the PR | fixed `09cea4b` |
| B16 | A conflict caused by the base moving was missed while GitHub kept `MERGEABLE CLEAN` | fixed `6887b6a` |
| B17 | Two tasks of one repo raced on the first clone | fixed `a723896` |
| - | The mutation job opened the removed `src/briar/env_vars.py` and would fail on the merge to `main` | fixed `2b11d3a` |
| - | After a stop in the second between the PR and the ticket comment, the "briar opened" comment is missing | **open**, small |
| - | `bash` of the agent can read any file of the user (V12); the read tools cannot | **open**, by decision |

Earlier fixes of the same branch: `9ab6dc3` (absolute knowledge path; before it the agent
wrote "The knowledge files ... do not exist in the worktree"), `b1e8d18` (draft then ready),
`0d25930` (verify on a clean copy; before it a `pytest.ini` kept out of the commit by the
global gitignore made verify green only in the task folder).

## 5. Next steps, in order

1. Review and merge #79 to #104 into `v2`, in order, as merge commits.
2. Decide how 2.0 is released (S32): a push to `main` publishes 1.1.102 today.
3. Spec 007, the extract sources (E1 to E27): PR 1 base (failures loud, statuses, exit
   code, `[extract] include`, no orders in the knowledge text), then Fireflies, AWS, Azure,
   GCP, Linear, Bitbucket. A grill before each PR. Each PR proves its counts against the
   official CLI on a real account.
4. B13: `doctor --runbooks` checks the model id.
5. B11 proven live on a machine whose pytest runs in a clean folder, or with a repo whose
   verify needs a config change.

## 6. What is missing

- Jira through `serve`: no test project; nothing of the Jira path ran for real.
- A CI failure that needs a code change (not a `CHANGES.md` line) fixed by the agent.
- Linux with `bwrap`, and the Docker image.
- A network drop during a poll (unit test only).
- The cost of a review round is not in the PR body (only the build cost is).
- GCP with data, Bitbucket and Linear with a login.

## 7. What to watch

- **BaseDrift cost**: one fetch and one `merge-tree` per watched PR per poll once the base
  moves. Many open briar PRs on a short `poll_minutes` make many fetches.
- **GitHub mergeability**: GitHub recomputed `mergeable_state` at once after a base push in
  one case and kept it stale for 40 minutes in another; do not trust it alone.
- **Private repo Actions**: on the account of the maintainer the runs of a private repo stayed
  `queued` for many minutes once; briar now waits (B1), so a long queue ends in
  `ci_wait_minutes` and a `blocked` task, not in a ready PR.
- **Local pytest**: the global Python of this machine fails pytest in a clean folder
  (`numpy.dtype size changed`, a plugin of another package). Use `uv run --with pytest ...`
  or `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` in verify commands on such a machine.
- **Assigned issues**: `serve` works on every open issue assigned to the token owner in its
  repos (S9). A repo where that person has other assigned issues needs a label filter (not
  built) or a separate account; the validation skipped a client repo for this reason.
- **Model spend**: US$ 0.19 to 1.03 per ticket so far with `claude-sonnet-5-5`; a complex
  ticket with a CI fix can pass US$ 1.

## 8. How to run it again

```bash
# a runbook like the one used on the click copy (runbooks/oss.yaml)
serve:
  repos: [iklobato/briar-oss-click]
  model: claude-sonnet-5-5
  max_usd: 3
  max_loops: 4
  ci_wait_minutes: 15
  max_review_rounds: 2
  max_parallel: 2
  poll_minutes: 1
  verify:
    - "uv run --python 3.12 --with pytest --with-editable . --no-project python -m pytest -q -x tests"
  bash: [git, ls, cat, head, tail, grep, rg, find, wc, pwd, echo, cd, python, python3, pytest, uv, uvx, sort, uniq, tee]
  allowed_domains: [pypi.org, files.pythonhosted.org]

BRIAR_<COMPANY>_GITHUB_TOKEN="$(gh auth token)" briar doctor --runbooks runbooks
BRIAR_<COMPANY>_GITHUB_TOKEN="$(gh auth token)" briar -v serve runbooks > serve.log 2>&1
grep -E 'briar\.serve|ERROR' serve.log        # the state changes and failures
```

- Before a run in a repo: unassign the issues of the earlier runs, or `serve` takes them
  again in a new state folder (it did once in this session and opened a duplicate PR).
- The clones and worktrees live in `$XDG_CACHE_HOME/briar` (else `~/.cache/briar`); remove
  `repos/<owner>__<repo>` and `worktrees/<company>` together to test an empty cache.
- Check a PR by its upstream repro on `main` and on the briar branch, not by its tests only.

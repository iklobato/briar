# briar 2.0: spec 005 PR 3, the login check

Grill session of 2026-11-11 for the third PR of spec 005 (tasks T014 and
after of the local `specs/005-source-logins/tasks.md`). It settles how the
check of G12 to G15 works, on top of
`docs/decisions/2026-11-10-briar-2-logins-spec.md` (G1 to G26),
`docs/decisions/2026-11-10-briar-2-logins-pr1.md` (H1 to H10) and
`docs/decisions/2026-11-11-briar-2-logins-pr2.md` (I1 to I10). Not
reopened: a missing or bad login stops the run (G12), the stop lists every
failed source with its fix (G13), `auth status` and `doctor` check live
(G14), and in `doctor` 401/403 is `fail` and a network error is `warn`
(G15).

Format: `ID. Decision: chosen answer. Why. (depends on)`

Facts checked first (2026-11-11, branch `logins/02-clouds`):

- Today a login that is not ready makes its extractor skip:
  `_provider_is_available` (`extract/base.py:150`) returns False, and
  `briar extract` prints `skipped ... (not available in this env)`; the
  runbook logs `extractor-skip` (`iac/runbook/executor.py:397`). Other skip
  reasons: an empty gate arg (no repo, no `ticket_project`), a provider
  that cannot be built, no GCP project or Azure subscription.
- `runbook extract` returns exit 0 even with `failed` rows
  (`commands/runbook.py`, `_extract`).
- `briar doctor` checks only the GitHub login, offline, and a missing one
  is `warn` (`doctor.py`, `_github_check`). `briar auth status` with no
  target lists the `BRIAR_*` names held, with no network.
- The adapters already set their timeouts: 30 s for PyGithub, the Jira and
  Bitbucket clients and the Linear and Fireflies urllib calls; boto3 5 s to
  connect and 30 s to read (`extract/_clouds/aws.py:31`).
- One GitHub REST read (`curl https://api.github.com/repos/iklobato/briar`,
  no token, 5 runs from this machine): 0.42 s the first time, then 0.08 to
  0.11 s. Jira, Bitbucket, Linear, Fireflies and the clouds not measured.
- The "who am I" call per source is in the local research file (R-4):
  GitHub `GET /user`, Jira `GET /rest/api/3/myself`, Bitbucket
  `GET /2.0/user`, Linear `{ viewer { email } }`, Fireflies
  `{ user { email } }`, AWS `sts.get_caller_identity()`, GCP the ADC
  refresh, Azure `get_token` for the management scope.

## Decisions

### What the check does

- J1. The check is "who am I" plus one read of each resource the run uses
  (L4 asked for the permission the run needs). Why: it catches a wrong
  account, a wrong name and a missing read permission before the run, not
  in an extractor log. Accepted cost: more code and more calls. (L4, G12)
- J2. A run checks only the sources of the extractors that will run. The
  reasons that are not a login (no repo, no project, the cloud SDK extra
  not installed, no GCP project or Azure subscription) still skip the
  extractor, and its source is not checked. Changes the accepted cost of
  G12: a `briar extract` with no Jira project needs no Jira login. (G12)
- J3. A network error (timeout, no connection) in the check of a run stops
  the run with exit 1, listed with the other failures (G13). Why: one rule;
  the run cannot read that source anyway. Accepted cost: a short outage of
  one source blocks the other sources of that run. (G12, G13)
- J4. The check goes through the same client the run uses (PyGithub,
  atlassian-python-api, the Bitbucket `Cloud` client, boto3, google-auth,
  azure-identity, the Linear and Fireflies urllib calls), with the
  timeouts those clients already have. Why: it proves the run
  authenticates the same way (Jira cookie headers, `user_agent`). (J1)
- J5. The read covers every resource of the run: each repo (GitHub or
  Bitbucket), each Jira project, each Linear team, the Bitbucket workspace,
  the GCP project, the Azure subscription. Fireflies and AWS have no
  configured resource, so they get "who am I" only. A 404 on a resource
  stops like a 403 ("not found or no access": GitHub answers 404 for a
  private repo the account cannot see). (J1, J2)
- J6. The calls run one after another. Why: simple code and an ordered
  log; with the GitHub latency measured above, 8 sources and 10 repos take
  about 2 s. Parallel calls come later if someone measures it slow. (J5)

### Where it runs

- J7. `runbook extract` checks every company and task it will run (after
  the `--task` filter) before running any of them. If one fails, nothing
  runs: it lists every failure and exits 1. Accepted cost: one company with
  a bad login blocks the other companies. (G12, G13)
- J8. `runbook serve` checks every company and task when it starts and does
  not start (exit 1) if one fails. After that it checks before each job, and
  any failure, a network error included, stops the whole `serve` with exit
  1. Confirmed after this warning: a short network drop at night stops
  `serve` for every company, and a supervisor that restarts on failure
  (systemd `Restart=on-failure`) restarts it into the same start check
  until the network is back. (J3, J7)
- J9. `briar auth login <source>` runs "who am I" before it saves (only
  "who am I": the login does not know which resources a run will use). It
  saves only when the check passes and prints the account. 401, 403 or a
  network error: nothing is saved, exit 1. For `aws`, `gcp` and `azure`
  the check runs after the CLI login and prints the account; a failure
  exits 1 (the CLI keeps its own login, briar saved nothing anyway).
  Replaces H9. (G14, H9, I1 to I3)
- J10. `briar doctor` checks every source configured for the company: a
  source with a secret set (a `BRIAR_<C>_<SOURCE>_*` name in the
  environment or `secrets.env`) or an `[auth.<source>]` block, even when no
  run uses it. With no company it stays a `warn`, as today. (G14, G15)
- J11. Outside a run, `briar doctor` and `briar auth status <source>` also
  read the resources that the run would read with the current config:
  `[extract]` of `.briar.toml`, or the tasks of the company in the runbook
  given with `--runbook`. A configured source that no run uses gets "who am
  I" only. (J1, J5, J10)
- J12. In `briar doctor`, a configured source with no secret saved is
  `fail` (exit 1), with the `briar auth login` command that is missing.
  Changes the `warn` of the PR 1 GitHub check. (J10, G15)
- J13. `briar auth status` with no target and a company checks every
  configured source live (the J10 list), one line per source with the
  account or the error and its fix. With no company it lists the names
  held, as today. (J10, G14)
- J14. In `briar auth status` (with or without a target), a network error
  shows the line as not checked and exits 0, as in `doctor`; a missing
  login, 401, 403 or a resource without access exits 1. (G15, J13)

## What follows from earlier decisions (no new choice)

- J15. The login part of `is_available` gives way to the check: a login
  that is not ready no longer skips its extractor quietly; the check stops
  the run first (G12). The skips of J2 stay.
- J16. Calls in `runbook serve` (J8): one per source plus one per resource
  each time a job runs. A job every minute with 10 GitHub repos makes 11
  calls a minute, 660 an hour, against the GitHub REST limit of 5,000 an
  hour for a user token. Unvalidated for the other sources.
- J17. The fix in each failure line: no login or 401, `briar auth login
  <source> --company <c>` (plus `--runbook FILE` for a runbook); 403 or
  404, the account and the resource it cannot read; network, the host and
  the error.
- J18. The README and the quickstart use `--include` or `[extract]
  include` (G12 risk). Each command manual and the CHANGELOG get their
  lines in this PR (G19).
- J19. Before the merge, the real runs of G20: Jira, Bitbucket, Linear and
  Fireflies wait on the maintainer running `briar auth login` for each.

## Choices made while building (not grilled; for the review)

- Bitbucket with `method = "access-token"`: no `GET /2.0/user`, because a
  workspace or repository access token belongs to no user. "Who am I"
  says `an access token of <workspace>`; the workspace and repository
  reads prove the token. Not checked on a real token.
- A 1.x name counts as a configured source in `doctor` and `auth status`
  (J10), so they show where it moves (G8, G25).
- An error that is not the answer of the source (an SDK that is not
  installed, a missing setting) is a `failed` line with its own message,
  for example `could not check it: google-auth not installed: run pip
  install ...`, and no login to run again.
- `[extract] include` (named in the G12 risk and in J18) is not a key of
  `[extract]`; the docs say `--include`.
- The fix of a runbook company says `--runbook FILE`: the executor does not
  know the path. `briar auth status --runbook PATH` names the real path.
- The Linear team read is the GraphQL `teams(filter: { key: { eq: KEY } })`;
  an empty answer is `no Linear team with key 'KEY'`.
- GCP "who am I" shows the account of the credentials when they carry one,
  else `application default credentials`.
- Found in the real runs (2026-11-11): with no `[auth.azure] tenant`,
  `DefaultAzureCredential` waited 114 s on the managed identity address of
  this machine before it used the `az` login. That is the review question
  of https://github.com/iklobato/briar/pull/98 (DefaultAzureCredential or
  `AzureCliCredential` only).

## Open risks

- J8 with J3: `runbook serve` stops for every company on a short network
  drop and can loop under a supervisor that restarts on failure.
- J7: one company with a bad login blocks every company in
  `runbook extract`.
- J10 and J12: `doctor` can fail on a configured source that no run uses
  (for example a block kept for another teammate).
- J2: AWS has no gate other than the login, so with no profile and no
  credentials a bare `briar extract` stops on AWS; Fireflies the same.
- J5: the resource reads need read access to the metadata of each
  resource (GCP `resourcemanager.projects.get`, Azure Reader on the
  subscription, Jira browse on the project). To verify in the G20 runs.
- J6: only the GitHub latency was measured.
- Bitbucket REST with a pasted session cookie is still unverified (R-4,
  H3).

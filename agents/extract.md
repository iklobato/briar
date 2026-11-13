# `briar extract`

## What it does

`briar extract` reads the live state of your tools and writes it to one
markdown file. Sources are GitHub, Bitbucket or GitLab, Jira or Linear, AWS (or
GCP / Azure), and Fireflies meetings. The result is called a knowledge
blob (a named markdown document that briar stores for you). Its default
name is `knowledge:<company>`, for example `knowledge:acme`.

You, Claude Code or any other agent read this file. The 2.0 ticket
pipeline (spec 003) will read it too.

Each source is handled by an extractor (a small plugin that reads one
kind of data). There are 23 of them. You pick them with `--include`. The
command line has only three flags (`--company`, `--include`, `--repo`);
every other value goes in `.briar.toml` (see "Settings" below).
Since 1.1.82 a run updates only the sections of the extractors it ran
and keeps the rest of the blob, so you can refresh one source at a time.
Before 1.1.82 each run replaced the whole blob. To start over, delete
the blob first: in the file store, delete `./knowledge/knowledge/acme.md`.

## Use it when

- You set up briar for a new company and there is no `knowledge:acme` yet.
- The blob is old and you want fresh data before you or an agent read it.

To refresh on a schedule, put the extractors in a runbook and run
`briar serve` (see [runbooks.md](runbooks.md)). It runs the same extractors
from a YAML file.

## Before you start

Each extractor needs the login of its source for the company. The
choices (Jira `site`, Bitbucket `workspace`, the method) go in `[auth]` of
`.briar.toml`; the secrets go in the environment or in
`~/.config/briar/secrets.env`, which you write. See [logins.md](logins.md).
`ACME` below stands for your company name in upper case.

| Extractors | What they need |
|---|---|
| Every repo extractor (the 19 in "Repo extractors" below). | `BRIAR_ACME_GITHUB_TOKEN`. With `provider = "bitbucket"`: the Bitbucket login (`BRIAR_ACME_BITBUCKET_EMAIL` and `BRIAR_ACME_BITBUCKET_TOKEN`, or an access token) and `workspace` under `[auth.bitbucket]`. With `provider = "gitlab"`: `BRIAR_ACME_GITLAB_TOKEN`, and `url` under `[auth.gitlab]` for a self-hosted GitLab. |
| `dependency-health`, `code-scanning`. | A GitHub token that can read security alerts (Dependabot alerts and code scanning alerts: read). |
| `active-tickets`, `ticket-archaeology`. | Jira: `site` under `[auth.jira]` plus `BRIAR_ACME_JIRA_EMAIL` and `BRIAR_ACME_JIRA_TOKEN` (or the cookie). Linear: `BRIAR_ACME_LINEAR_API_KEY`. GitHub Issues: `BRIAR_ACME_GITHUB_TOKEN`. |
| `aws-infra`. | AWS: the profile in `[auth.aws] profile` (log in with `aws sso login --profile P`), or the boto3 default chain. GCP (`cloud = "gcp"`): `[auth.gcp] project` and the ADC of `config_dir`. Azure (`cloud = "azure"`): `[auth.azure] subscription` and the `az` login of `tenant`. The `tagging-inventory` part also needs the `tag:GetResources` permission. |
| `meeting-digest`. | `BRIAR_ACME_FIREFLIES_API_KEY`. |

Before any extractor runs, `briar extract` checks the login of each
source the run uses: the source says who the login is, and briar reads
each repo, Jira project, Linear team, Bitbucket workspace, GCP project and
Azure subscription the run will read. Any failure stops the run with exit
code 1 and lists every failure with its fix: a missing login, one the
source refuses, a resource it cannot read, a network error, a login set
only under its 1.x name (`GITHUB_TOKEN`, `JIRA_ACME_TOKEN`, ...):

```text
error: the login check failed; fix these, then run again:
  - github (acme): no login saved (BRIAR_ACME_GITHUB_TOKEN); set BRIAR_ACME_GITHUB_TOKEN in the environment or in /home/me/.config/briar/secrets.env (Create a fine-grained token at ...)
  - jira (acme): bot@acme.example cannot read ENG (HTTP 404: ...); check the name and the access of this login
```

`briar doctor` runs the same check without extracting. A bad key in
`[auth]` stops the run too.

Repo extractors also need a repo list. Without one they are skipped, and
their source is not checked.
Give it with `--repo`. Inside a checkout, briar can also take it from
`.briar.toml` or the git `origin` remote (see `--repo` in "All
parameters").

## Examples

Run every extractor that is set up, for one repo:

```bash
briar extract --company acme --repo acme-co/acme-app
```

You get `./knowledge/knowledge/acme.md`. With no `--include` every
extractor that has its repo or project list runs, so the run needs every
login they use: the check stops it when one is missing. Extractors with no
repo or project list print `skipped <name>  (not available in this env)`
and are left out.

Run only two extractors, and look at the last 50 merged PRs. Put the
limit in `.briar.toml` first:

```toml
[extract]
max = 50
```

```bash
briar extract --company acme \
    --include pr-archaeology --include reviewer-profile \
    --repo acme-co/acme-app
```

You get a blob with two sections: merged-PR patterns and reviewer habits.

Read open and closed Jira tickets for the `ACME` project:

```toml
[extract.active-tickets]
projects = ["ACME"]

[extract.ticket-archaeology]
projects = ["ACME"]
```

```bash
briar extract --company acme --include active-tickets --include ticket-archaeology
```

You get the open tickets (up to 25) and stats on closed ones. Jira keys
are not `owner/repo`, so `--repo` does not feed them: use `projects`.

Count every tagged AWS resource in one region:

```toml
[extract.aws-infra]
region = "eu-west-1"
services = ["tagging-inventory"]
```

```bash
briar extract --company acme --include aws-infra
```

The markdown gets a count per AWS service. For the full list as JSON, run
it from a runbook with `config: {inventory: "true"}` (see
[runbooks.md](runbooks.md)). 1.x also had `--out-json`, `--merge-claude-md`
and `--blob-name`; 2.0 removed them (decision C13).

## The 23 extractors

Each extractor adds one section to the blob. Set its values in
`.briar.toml` under `[extract.<name>]` (the keys are in "Settings"), then
run `briar extract --company acme --repo acme-co/acme-app --include <name>`.

### Repo extractors

These read a code host. They need a repo list and a repo token. All of
them take `--repo`, and `provider = "github"` (default), `"bitbucket"` or
`"gitlab"` (in `[repo]`, `[extract]` or `[extract.<name>]`).

On GitLab a repo is the full project path, of any depth
(`--repo acme/platform/app`), and a pull request is a merge request. Every
repo extractor works there except two: `dependency-health` and
`code-scanning` find nothing, since GitLab has no free API for those alerts.
`repo-governance` counts approval rules only on a GitLab plan that has them.
GitLab code search has no `OR`, so `todo-density` searches each marker word
on its own.

PR and review history:

- `pr-archaeology`: studies merged PRs: size, time to merge, what reviewers focus on.
- `reviewer-profile`: profiles the most active reviewers: how much they comment, on which files, and what they ask for.
- `review-nits`: groups review comments that keep coming back (like "add a test"), so you can turn them into lint rules.
- `pr-hygiene`: shows PR sizes, how many PRs are large, how many merge with zero review comments, and time to first review.
- `active-work`: lists open PRs, so the agent does not edit files that are already in review.
- `stale-prs`: lists open PRs older than a number of days, counted from when the PR was opened.

```toml
[extract.pr-archaeology]
max = 50

[extract.reviewer-profile]
sample = 20
top_n = 3

[extract.review-nits]
sample = 50

[extract.pr-hygiene]
large_loc = 600

[extract.active-work]
authors_block = ["dependabot[bot]"]

[extract.stale-prs]
days = 30
```

Commit history:

- `code-hotspots`: finds files that change together, so the agent also updates the matching test or migration.
- `defect-hotspots`: ranks files most likely to break: changed often, fixed often, and large.
- `revert-signals`: finds revert and hotfix commits, and the files they touched.
- `commit-message-quality`: checks commit subjects against Conventional Commits (`feat:`, `fix:`) and subject length.

```toml
[extract.code-hotspots]
since_days = 60

[extract.defect-hotspots]
since_days = 180
top_n = 5

[extract.revert-signals]
since_days = 30

[extract.commit-message-quality]
max = 100
```

Code and tooling:

- `codebase-conventions`: detects the language, test runner, linter, formatter and migration tool from files like `pyproject.toml` or `package.json`. The agent runs these tools before it pushes.
- `test-discipline`: shows the ratio of test files to source files, and source files with no obvious test.
- `todo-density`: counts `TODO`, `FIXME` and `HACK` markers and lists the files with the most. It reads one page of code search, so treat it as a sample.

```toml
[extract.test-discipline]
top_n = 20

[extract.todo-density]
top_n = 5
```

CI, releases and repo rules:

- `ci-health`: shows the CI pass rate, flaky workflows and the median run time.
- `github-deployments`: lists environments, recent deployments and recent CI runs.
- `release-cadence`: shows how often the repo ships: number of releases, the latest one, and the median days between them.
- `repo-governance`: checks branch protection and whether `CODEOWNERS`, pre-commit, linter and editor config files exist.

```toml
[extract.ci-health]
max = 50

[extract.release-cadence]
max = 20

[extract.repo-governance]
branch = "develop"
```

Security alerts (GitHub only):

- `dependency-health`: lists open Dependabot alerts, highest severity first.
- `code-scanning`: lists open code scanning alerts (for example CodeQL), grouped by rule, with one example file each.

```toml
[extract.dependency-health]
max = 100

[extract.code-scanning]
top_n = 5
```

### Tracker extractors

These read an issue tracker. Pick it with `tracker` (default `jira`) at
the top of `.briar.toml`, in `[extract]` or in `[extract.<name>]`.

- `active-tickets`: lists open tickets in a project (up to 25), with the assignee.
- `ticket-archaeology`: studies closed tickets: median time to close, top reporters and assignees, and labels.

```toml
[extract.active-tickets]
projects = ["ACME"]

[extract.ticket-archaeology]
tracker = "github-issues"
```

```bash
briar extract --company acme --include active-tickets --include ticket-archaeology --repo acme-co/acme-app
```

For GitHub Issues and Bitbucket Issues the project is a repo slug, so
`--repo` works. For Jira and Linear, set `projects`.

### Cloud extractor

- `aws-infra`: lists cloud resources. On AWS it covers ECS, Lambda, CloudWatch log groups, RDS and SQS, plus a count of every tagged resource (`tagging-inventory`).

```toml
[extract.aws-infra]
region = "us-east-1"
services = ["ecs", "rds"]
```

`cloud = "gcp"` and `cloud = "azure"` need the `[gcp]` or `[azure]` extra.

### Meeting extractor

- `meeting-digest`: lists recent Fireflies meetings with their summary, action items and attendees.

```toml
[extract.meeting-digest]
since_days = 14
attendee_allow = ["lead@acme.com", "@acme.com"]
title_any = ["standup", "platform sync"]
jira_projects = ["ENG"]
```

One Fireflies key can be a personal account with the meetings of several
companies. `meeting-digest` keeps a meeting when any one of these says it is
of the company:

- a word of `title_any` is in its title (any case);
- an attendee or the organizer is in `attendee_allow`, as an exact email or a
  whole `@domain`;
- a speaker has the full name (case and accents left out) of a person of the
  company: the people who can be assigned in its `jira_projects` (or every
  user of its Jira site, which a plain account often cannot read), the members
  of the GitLab groups of its token, the people of its Slack workspace. These lists are read on every run
  of a company with a Jira, GitLab or Slack login; when one of them cannot be
  read, `meeting-digest` stops for that run and the log says why.

With none of them every meeting is kept. Since the owner of a personal key
speaks in most of their meetings, and is a member of each company, the speaker
signal alone does not separate the companies of a personal key: give each one
a `title_any`. `briar doctor --runbooks` warns when two runbook companies use
the same Fireflies key and one of them keeps every meeting. Fireflies gives 50
meetings per call, so briar reads more pages until it has `max` meetings or the
`since_days` window ends.

## Command line

| Flag | What it means | Default | Example |
|---|---|---|---|
| `--company` | Company name. It names the blob and picks per-company credentials like `JIRA_ACME_TOKEN`. Required, unless `BRIAR_COMPANY` or `company` in `.briar.toml` sets it. | None. | `--company acme` |
| `--include` | Run only this extractor. Repeat it to run several. The other sections already in the blob are kept; only the sections of the extractors that ran are replaced (one that ran and found nothing loses its old section; one that is not set up keeps it). | All 23. Ones that are not set up are skipped. | `--include stale-prs` |
| `--repo` | Repo to read, as `owner/repo`. Repeat for more. It also sets the project for GitHub Issues and Bitbucket Issues. Without it, briar takes `repos = [...]` or `[repo] owner` / `repo` from `.briar.toml`, then the git `origin` remote; a `--repo` you type replaces those. | From the config or git. | `--repo acme-co/acme-app` |

Every other 1.x flag (`--max`, `--pr-repo`, `--provider`, `--root`,
`--out-json`, `--merge-claude-md`, `--blob-name`, `--advanced-help`, ...)
was removed in 2.0 and is now an unknown argument (exit code 2). Its
value goes in `.briar.toml`, below, or the mode is gone (C13). The blob
goes to `<root>/knowledge/<company>.md`; `root` in `.briar.toml` sets the
folder (default `./knowledge`).

## Settings

Two tables in `.briar.toml` (or under `[tool.briar]` in `pyproject.toml`)
hold every extractor value:

- `[extract]`: values for every extractor in `--include`. Allowed keys:
  `since_days`, `max`, `top_n`, `sample`, `authors_allow`,
  `authors_block`, `assignees_allow`, `assignees_block`, and the source
  kinds `provider`, `tracker`, `meeting`, `cloud`.
- `[extract.<name>]`: values for one extractor, by short name (the table
  below). A table for an extractor that does not run this time is
  checked and otherwise ignored.

```toml
tracker = "jira"

[extract]
max = 50
authors_block = ["dependabot[bot]"]

[extract.pr-archaeology]
max = 100

[extract.active-tickets]
projects = ["ACME", "OPS"]

[extract.aws-infra]
region = "eu-west-1"
services = ["ecs", "rds"]
```

Precedence, lowest first: the extractor default, `tracker` (top level)
and `[repo] provider`, `[extract]`, `[extract.<name>]`. In the example,
`pr-archaeology` reads 100 PRs and every other extractor 50.

The run stops with exit code 1, naming what to fix, when a key is not a
setting (`extract.foo`), a name is not an extractor (`extract.nope`), a
key is the 1.x name (`pr_max`: the error says to use `max`), a key names
a repo list (repo lists come only from `--repo`, `repos` or `[repo]`), or
a value is not one of a fixed list (`provider = "gitlab"`). Types are not
checked yet: `max = "fifty"` fails inside the extractor. A runbook's
`args:` block still takes the 1.x option names (see
[runbooks.md](runbooks.md)). `briar extract -h` prints the same keys.

Every `[extract.<name>]` key, its default and its allowed values (built
from the code; a test fails when this table and the code differ):

<!-- BEGIN extract-keys -->
| Extractor | Key | Default | Allowed values |
|---|---|---|---|
| `active-tickets` | `projects` | `[]` |  |
| `active-tickets` | `tracker` | `"jira"` | jira, github-issues, bitbucket-issues, linear |
| `active-work` | `assignees_allow` | `[]` |  |
| `active-work` | `assignees_block` | `[]` |  |
| `active-work` | `authors_allow` | `[]` |  |
| `active-work` | `authors_block` | `[]` |  |
| `active-work` | `provider` | `"github"` | github, bitbucket, gitlab |
| `aws-infra` | `cloud` | `"aws"` | aws, gcp, azure |
| `aws-infra` | `region` | `""` |  |
| `aws-infra` | `services` | `[]` | ecs, lambda, logs, rds, sqs, tagging-inventory |
| `ci-health` | `max` | `100` |  |
| `ci-health` | `provider` | `"github"` | github, bitbucket, gitlab |
| `code-hotspots` | `max` | `100` |  |
| `code-hotspots` | `provider` | `"github"` | github, bitbucket, gitlab |
| `code-hotspots` | `since_days` | `30` |  |
| `code-hotspots` | `top_n` | `10` |  |
| `code-scanning` | `max` | `200` |  |
| `code-scanning` | `provider` | `"github"` | github, bitbucket, gitlab |
| `code-scanning` | `top_n` | `10` |  |
| `codebase-conventions` | `provider` | `"github"` | github, bitbucket, gitlab |
| `commit-message-quality` | `max` | `200` |  |
| `commit-message-quality` | `provider` | `"github"` | github, bitbucket, gitlab |
| `commit-message-quality` | `since_days` | `90` |  |
| `defect-hotspots` | `max` | `200` |  |
| `defect-hotspots` | `provider` | `"github"` | github, bitbucket, gitlab |
| `defect-hotspots` | `since_days` | `90` |  |
| `defect-hotspots` | `top_n` | `10` |  |
| `dependency-health` | `max` | `200` |  |
| `dependency-health` | `provider` | `"github"` | github, bitbucket, gitlab |
| `github-deployments` | `provider` | `"github"` | github, bitbucket, gitlab |
| `meeting-digest` | `attendee_allow` | `[]` |  |
| `meeting-digest` | `jira_projects` | `[]` |  |
| `meeting-digest` | `max` | `25` |  |
| `meeting-digest` | `meeting` | `"fireflies"` | fireflies |
| `meeting-digest` | `since_days` | `7` |  |
| `meeting-digest` | `title_any` | `[]` |  |
| `pr-archaeology` | `assignees_allow` | `[]` |  |
| `pr-archaeology` | `assignees_block` | `[]` |  |
| `pr-archaeology` | `authors_allow` | `[]` |  |
| `pr-archaeology` | `authors_block` | `[]` |  |
| `pr-archaeology` | `max` | `100` |  |
| `pr-archaeology` | `provider` | `"github"` | github, bitbucket, gitlab |
| `pr-hygiene` | `large_loc` | `400` |  |
| `pr-hygiene` | `max` | `100` |  |
| `pr-hygiene` | `provider` | `"github"` | github, bitbucket, gitlab |
| `pr-hygiene` | `sample` | `30` |  |
| `release-cadence` | `max` | `100` |  |
| `release-cadence` | `provider` | `"github"` | github, bitbucket, gitlab |
| `repo-governance` | `branch` | `""` |  |
| `repo-governance` | `provider` | `"github"` | github, bitbucket, gitlab |
| `revert-signals` | `max` | `200` |  |
| `revert-signals` | `provider` | `"github"` | github, bitbucket, gitlab |
| `revert-signals` | `since_days` | `90` |  |
| `review-nits` | `provider` | `"github"` | github, bitbucket, gitlab |
| `review-nits` | `sample` | `30` |  |
| `review-nits` | `top_n` | `15` |  |
| `reviewer-profile` | `provider` | `"github"` | github, bitbucket, gitlab |
| `reviewer-profile` | `sample` | `20` |  |
| `reviewer-profile` | `top_n` | `5` |  |
| `stale-prs` | `days` | `14` |  |
| `stale-prs` | `max` | `100` |  |
| `stale-prs` | `provider` | `"github"` | github, bitbucket, gitlab |
| `test-discipline` | `provider` | `"github"` | github, bitbucket, gitlab |
| `test-discipline` | `top_n` | `10` |  |
| `ticket-archaeology` | `max` | `100` |  |
| `ticket-archaeology` | `projects` | `[]` |  |
| `ticket-archaeology` | `tracker` | `"jira"` | jira, github-issues, bitbucket-issues, linear |
| `todo-density` | `max` | `200` |  |
| `todo-density` | `provider` | `"github"` | github, bitbucket, gitlab |
| `todo-density` | `top_n` | `10` |  |
<!-- END extract-keys -->

## What you see when it runs

Progress goes to stderr, so stdout stays empty for scripts:

```text
  skipped active-tickets  (not available in this env)
  login github (acme): octo
  running pr-archaeology ...
  stale-prs: no data

wrote blob 'knowledge:acme' (18342 bytes, 5 sections) under ./knowledge
```

- `skipped`: the repo or project list is missing for that extractor, or its
  cloud SDK is not installed. Its source is not checked.
- `login`: the login check passed for that source, with the account.
- `no data`: it ran but found nothing. This is not an error.
- Exit code `0`: the blob was written.
- Exit code `1` with the error `nothing extracted`: every extractor was skipped or empty, so nothing was written.

Check the result by opening the blob: `./knowledge/knowledge/acme.md` in
the file store.

## Good to know

- Inside a repo with `.briar.toml` or a git `origin`, that repo is
  already the `--repo` list. A `--repo` you type replaces it.
- With no `--include`, `aws-infra` and `meeting-digest` always run, so the
  check needs an AWS and a Fireflies login. Use `--include` to leave them
  out.
- On Bitbucket, `dependency-health`, `code-scanning`, `release-cadence`,
  `todo-density` and `test-discipline` come back empty. `repo-governance`
  cannot read branch protection there.
- Heavy extractors make many API calls: `code-hotspots`,
  `defect-hotspots`, `revert-signals`. Lower `max` or `since_days` in
  `[extract]` if you hit GitHub rate limits.
- `GetResources` (used by `tagging-inventory`) sees only tagged AWS
  resources. Untagged ones are not counted.
- A runbook can save a JSON "inventory" copy next to the blob on every
  scheduled run (`config: {inventory: "true"}` on its `knowledge:`
  block). See [runbooks.md](runbooks.md).
- Add `-v` to see why an extractor was skipped or failed.

| Problem | What to do |
|---|---|
| Every extractor says `skipped`. | `--repo` or the project lists are missing. Pass `--repo`, or set `projects` under `[extract.<name>]`. |
| The login check fails with `cannot read ... (HTTP 404 ...)`. | The repo name is wrong, or the login lacks access to it. A fine-grained GitHub token must list the repo. |
| The login check fails with `could not reach it`. | briar could not reach the source (network, VPN). Run again when it answers; `briar doctor` shows it as `warn`. |
| Jira calls hang or fail. | Check `site` under `[auth.jira]`. It should look like `acme.atlassian.net`. |
| The run is slow. | Lower `max` in `[extract]` (for example `max = 20`) or use `--include` to run fewer extractors. |
| `unrecognized arguments: --pr-max` (or another 1.x flag). | The flag moved to `.briar.toml`: see "Settings". |

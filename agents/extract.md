# `briar extract`

## What it does

`briar extract` reads the live state of your tools and writes it to one
markdown file. Sources are GitHub or Bitbucket, Jira or Linear, AWS (or
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

To refresh on a schedule, use `briar runbook` instead (see
[runbook.md](runbook.md)). It runs the same extractors from a YAML file.

## Before you start

Each extractor needs its own credentials. briar reads them from your
shell and from `~/.config/briar/secrets.env` (`briar auth login` writes
that file). `acme` below stands for your company name in upper case.

| Extractors | What they need |
|---|---|
| Every repo extractor (the 19 in "Repo extractors" below). | `GITHUB_TOKEN`. With `provider = "bitbucket"`: `BITBUCKET_ACME_USERNAME` and `BITBUCKET_ACME_APP_PASSWORD`. |
| `dependency-health`, `code-scanning`. | A GitHub token that can read security alerts (`security_events` scope on a classic token). |
| `active-tickets`, `ticket-archaeology`. | Jira: `JIRA_ACME_URL` plus `JIRA_ACME_EMAIL` and `JIRA_ACME_TOKEN` (or Jira session cookies). Linear: `LINEAR_ACME_TOKEN`. GitHub Issues: `GITHUB_TOKEN`. |
| `aws-infra`. | `AWS_ACME_ACCESS_KEY_ID` and `AWS_ACME_SECRET_ACCESS_KEY`, or an AWS profile, or your normal AWS login. The `tagging-inventory` part also needs the `tag:GetResources` permission. |
| `meeting-digest`. | `FIREFLIES_ACME_API_KEY` (`briar auth login fireflies --company acme`). |

Run `briar auth status <target> --company acme` to see which keys one
source is missing.

Repo extractors also need a repo list. Without one they are skipped.
Give it with `--repo`. Inside a checkout, briar can also take it from
`.briar.toml` or the git `origin` remote (see `--repo` in "All
parameters").

## Examples

Run every extractor that is set up, for one repo:

```bash
briar extract --company acme --repo acme-co/acme-app
```

You get `./knowledge/knowledge/acme.md`. Extractors without credentials
print `skipped <name>  (not available in this env)` and are left out.

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
[runbook.md](runbook.md)). 1.x also had `--out-json`, `--merge-claude-md`
and `--blob-name`; 2.0 removed them (decision C13).

## The 23 extractors

Each extractor adds one section to the blob. Set its values in
`.briar.toml` under `[extract.<name>]` (the keys are in "Settings"), then
run `briar extract --company acme --repo acme-co/acme-app --include <name>`.

### Repo extractors

These read a code host. They need a repo list and a repo token. All of
them take `--repo`, and `provider = "github"` (default) or `"bitbucket"`
(in `[repo]`, `[extract]` or `[extract.<name>]`).

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
attendee_allow = ["lead@acme.com"]
```

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
[runbook.md](runbook.md)). `briar extract -h` prints the same keys.

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
| `active-work` | `provider` | `"github"` | github, bitbucket |
| `aws-infra` | `cloud` | `"aws"` | aws, gcp, azure |
| `aws-infra` | `profile` | `unset` |  |
| `aws-infra` | `region` | `"us-east-1"` |  |
| `aws-infra` | `services` | `[]` | ecs, lambda, logs, rds, sqs, tagging-inventory |
| `ci-health` | `max` | `100` |  |
| `ci-health` | `provider` | `"github"` | github, bitbucket |
| `code-hotspots` | `max` | `100` |  |
| `code-hotspots` | `provider` | `"github"` | github, bitbucket |
| `code-hotspots` | `since_days` | `30` |  |
| `code-hotspots` | `top_n` | `10` |  |
| `code-scanning` | `max` | `200` |  |
| `code-scanning` | `provider` | `"github"` | github, bitbucket |
| `code-scanning` | `top_n` | `10` |  |
| `codebase-conventions` | `provider` | `"github"` | github, bitbucket |
| `commit-message-quality` | `max` | `200` |  |
| `commit-message-quality` | `provider` | `"github"` | github, bitbucket |
| `commit-message-quality` | `since_days` | `90` |  |
| `defect-hotspots` | `max` | `200` |  |
| `defect-hotspots` | `provider` | `"github"` | github, bitbucket |
| `defect-hotspots` | `since_days` | `90` |  |
| `defect-hotspots` | `top_n` | `10` |  |
| `dependency-health` | `max` | `200` |  |
| `dependency-health` | `provider` | `"github"` | github, bitbucket |
| `github-deployments` | `provider` | `"github"` | github, bitbucket |
| `meeting-digest` | `attendee_allow` | `[]` |  |
| `meeting-digest` | `max` | `25` |  |
| `meeting-digest` | `meeting` | `"fireflies"` | fireflies |
| `meeting-digest` | `since_days` | `7` |  |
| `pr-archaeology` | `assignees_allow` | `[]` |  |
| `pr-archaeology` | `assignees_block` | `[]` |  |
| `pr-archaeology` | `authors_allow` | `[]` |  |
| `pr-archaeology` | `authors_block` | `[]` |  |
| `pr-archaeology` | `max` | `100` |  |
| `pr-archaeology` | `provider` | `"github"` | github, bitbucket |
| `pr-hygiene` | `large_loc` | `400` |  |
| `pr-hygiene` | `max` | `100` |  |
| `pr-hygiene` | `provider` | `"github"` | github, bitbucket |
| `pr-hygiene` | `sample` | `30` |  |
| `release-cadence` | `max` | `100` |  |
| `release-cadence` | `provider` | `"github"` | github, bitbucket |
| `repo-governance` | `branch` | `""` |  |
| `repo-governance` | `provider` | `"github"` | github, bitbucket |
| `revert-signals` | `max` | `200` |  |
| `revert-signals` | `provider` | `"github"` | github, bitbucket |
| `revert-signals` | `since_days` | `90` |  |
| `review-nits` | `provider` | `"github"` | github, bitbucket |
| `review-nits` | `sample` | `30` |  |
| `review-nits` | `top_n` | `15` |  |
| `reviewer-profile` | `provider` | `"github"` | github, bitbucket |
| `reviewer-profile` | `sample` | `20` |  |
| `reviewer-profile` | `top_n` | `5` |  |
| `stale-prs` | `days` | `14` |  |
| `stale-prs` | `max` | `100` |  |
| `stale-prs` | `provider` | `"github"` | github, bitbucket |
| `test-discipline` | `provider` | `"github"` | github, bitbucket |
| `test-discipline` | `top_n` | `10` |  |
| `ticket-archaeology` | `max` | `100` |  |
| `ticket-archaeology` | `projects` | `[]` |  |
| `ticket-archaeology` | `tracker` | `"jira"` | jira, github-issues, bitbucket-issues, linear |
| `todo-density` | `max` | `200` |  |
| `todo-density` | `provider` | `"github"` | github, bitbucket |
| `todo-density` | `top_n` | `10` |  |
<!-- END extract-keys -->

## What you see when it runs

Progress goes to stderr, so stdout stays empty for scripts:

```text
  running pr-archaeology ...
  skipped meeting-digest  (not available in this env)
  stale-prs: no data

wrote blob 'knowledge:acme' (18342 bytes, 5 sections) under ./knowledge
```

- `skipped`: credentials or the repo list are missing for that extractor.
- `no data`: it ran but found nothing. This is not an error.
- Exit code `0`: the blob was written.
- Exit code `1` with the error `nothing extracted`: every extractor was skipped or empty, so nothing was written.

Check the result by opening the blob: `./knowledge/knowledge/acme.md` in
the file store.

## Good to know

- Inside a repo with `.briar.toml` or a git `origin`, that repo is
  already the `--repo` list. A `--repo` you type replaces it.
- With no `--include`, `aws-infra` always runs. Without AWS access it
  writes a section titled `AWS infrastructure (UNREACHABLE)`. Use
  `--include` to skip it.
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
  block). See [runbook.md](runbook.md).
- Add `-v` to see why an extractor was skipped or failed.

| Problem | What to do |
|---|---|
| Every extractor says `skipped`. | Credentials or `--repo` are missing. Run `briar auth status <target> --company acme`, then pass `--repo`. |
| `403` or `404` from GitHub. | The token lacks access to the repo. A classic token needs `repo`, and `read:org` for org data. |
| Jira calls hang or fail. | Check `JIRA_ACME_URL`. It should look like `https://acme.atlassian.net`. |
| The run is slow. | Lower `max` in `[extract]` (for example `max = 20`) or use `--include` to run fewer extractors. |
| `unrecognized arguments: --pr-max` (or another 1.x flag). | The flag moved to `.briar.toml`: see "Settings". |

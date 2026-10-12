# `briar extract`

## What it does

`briar extract` reads the live state of your tools and writes it to one
markdown file. Sources are GitHub or Bitbucket, Jira or Linear, AWS (or
GCP / Azure), and Fireflies meetings. The result is called a knowledge
blob (a named markdown document that briar stores for you). Its default
name is `knowledge:<company>`, for example `knowledge:acme`.

Other commands read this blob. `briar agent` adds the sections it needs
to the AI agent's prompt, so the agent knows how your team works.
`briar plan` gives the blob to its planning LLM.

Each source is handled by an extractor (a small plugin that reads one
kind of data). There are 23 of them. You pick them with `--include`.
Since 1.1.82 a run updates only the sections of the extractors it ran
and keeps the rest of the blob, so you can refresh one source at a time.
Before 1.1.82 each run replaced the whole blob. To start over, delete
the blob first: `briar context delete knowledge:acme` (add
`--store postgres` when it lives there).

## Use it when

- You set up briar for a new company and there is no `knowledge:acme` yet.
- The blob is old and you want fresh data before an agent run.
- You want to give Claude Code a summary of your repo in `CLAUDE.md`.
- You want a JSON dump of your AWS resources or PR stats for other tools.

To refresh on a schedule, use `briar runbook` instead (see
[runbook.md](runbook.md)). It runs the same extractors from a YAML file.

## Before you start

Each extractor needs its own credentials. briar reads them from your
shell and from `~/.config/briar/secrets.env` (`briar auth login` writes
that file). `acme` below stands for your company name in upper case.

| Extractors | What they need |
|---|---|
| Every repo extractor (the 19 in "Repo extractors" below). | `GITHUB_TOKEN`. With `--provider bitbucket`: `BITBUCKET_ACME_USERNAME` and `BITBUCKET_ACME_APP_PASSWORD`. |
| `dependency-health`, `code-scanning`. | A GitHub token that can read security alerts (`security_events` scope on a classic token). |
| `active-tickets`, `ticket-archaeology`. | Jira: `JIRA_ACME_URL` plus `JIRA_ACME_EMAIL` and `JIRA_ACME_TOKEN` (or Jira session cookies). Linear: `LINEAR_ACME_TOKEN`. GitHub Issues: `GITHUB_TOKEN`. |
| `aws-infra`. | `AWS_ACME_ACCESS_KEY_ID` and `AWS_ACME_SECRET_ACCESS_KEY`, or an AWS profile, or your normal AWS login. The `tagging-inventory` part also needs the `tag:GetResources` permission. |
| `meeting-digest`. | `FIREFLIES_ACME_API_KEY` (`briar auth login fireflies --company acme`). |

Run `briar secrets doctor` to see which credentials are missing.

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

Run only two extractors, and look at the last 50 merged PRs:

```bash
briar extract --company acme \
    --include pr-archaeology --include reviewer-profile \
    --repo acme-co/acme-app --max 50
```

You get a blob with two sections: merged-PR patterns and reviewer habits.

Read open and closed Jira tickets for the `ACME` project:

```bash
briar extract --company acme \
    --include active-tickets --include ticket-archaeology \
    --ticket-project ACME --ticket-archaeology-project ACME
```

You get the open tickets (up to 25) and stats on closed ones. Jira keys
are not `owner/repo`, so `--repo` does not feed them. Use the two
project flags.

List every tagged AWS resource in one region, with full detail in JSON:

```bash
briar extract --company acme --include aws-infra \
    --aws-extract-service tagging-inventory \
    --aws-extract-region eu-west-1 \
    --out-json ./acme-aws.json
```

The markdown gets a count per AWS service. `./acme-aws.json` gets every
resource with its ARN, type, region and tags.

Give Claude Code an index of the knowledge in `CLAUDE.md`:

```bash
briar extract --company acme --repo acme-co/acme-app \
    --include codebase-conventions --include code-hotspots \
    --merge-claude-md
```

You get `.briar/knowledge/acme.md` with the full detail. `CLAUDE.md` gets
a short index between `<!-- BEGIN briar-knowledge -->` and
`<!-- END briar-knowledge -->`. Running it again replaces only that block.

Save to Postgres under a different name, so the main blob stays as is:

```bash
briar extract --company acme --repo acme-co/acme-app \
    --store postgres --blob-name knowledge:acme.snapshot-2026q3
```

This needs `BRIAR_DATABASE_URL`. You get a blob named
`knowledge:acme.snapshot-2026q3` in the database.

## The 23 extractors

Each extractor adds one section to the blob. `briar agent implement`
puts six of them in its prompt: `codebase-conventions`, `code-hotspots`,
`reviewer-profile`, `active-work`, `pr-archaeology` and `meeting-digest`.
`briar agent prfix` uses the same ones except `pr-archaeology`. The other
sections are for you, for `briar plan`, and for Claude Code (through
`--merge-claude-md`).

### Repo extractors

These read a code host. They need a repo list and a repo token. All of
them take `--repo`, and `--provider github` (default) or `bitbucket`.

PR and review history:

- `pr-archaeology`: studies merged PRs: size, time to merge, what reviewers focus on.
- `reviewer-profile`: profiles the most active reviewers: how much they comment, on which files, and what they ask for.
- `review-nits`: groups review comments that keep coming back (like "add a test"), so you can turn them into lint rules.
- `pr-hygiene`: shows PR sizes, how many PRs are large, how many merge with zero review comments, and time to first review.
- `active-work`: lists open PRs, so the agent does not edit files that are already in review.
- `stale-prs`: lists open PRs older than a number of days, counted from when the PR was opened.

```bash
briar extract --company acme --repo acme-co/acme-app --include pr-archaeology --max 50
briar extract --company acme --repo acme-co/acme-app --include reviewer-profile --sample 20 --top-n 3
briar extract --company acme --repo acme-co/acme-app --include review-nits --sample 50
briar extract --company acme --repo acme-co/acme-app --include pr-hygiene --prhygiene-large-loc 600
briar extract --company acme --repo acme-co/acme-app --include active-work --authors-block 'dependabot[bot]'
briar extract --company acme --repo acme-co/acme-app --include stale-prs --stale-days 30
```

Commit history:

- `code-hotspots`: finds files that change together, so the agent also updates the matching test or migration.
- `defect-hotspots`: ranks files most likely to break: changed often, fixed often, and large.
- `revert-signals`: finds revert and hotfix commits, and the files they touched.
- `commit-message-quality`: checks commit subjects against Conventional Commits (`feat:`, `fix:`) and subject length.

```bash
briar extract --company acme --repo acme-co/acme-app --include code-hotspots --since-days 60
briar extract --company acme --repo acme-co/acme-app --include defect-hotspots --since-days 180 --top-n 5
briar extract --company acme --repo acme-co/acme-app --include revert-signals --since-days 30
briar extract --company acme --repo acme-co/acme-app --include commit-message-quality --max 100
```

Code and tooling:

- `codebase-conventions`: detects the language, test runner, linter, formatter and migration tool from files like `pyproject.toml` or `package.json`. The agent runs these tools before it pushes.
- `test-discipline`: shows the ratio of test files to source files, and source files with no obvious test.
- `todo-density`: counts `TODO`, `FIXME` and `HACK` markers and lists the files with the most. It reads one page of code search, so treat it as a sample.

```bash
briar extract --company acme --repo acme-co/acme-app --include codebase-conventions
briar extract --company acme --repo acme-co/acme-app --include test-discipline --top-n 20
briar extract --company acme --repo acme-co/acme-app --include todo-density --top-n 5
```

CI, releases and repo rules:

- `ci-health`: shows the CI pass rate, flaky workflows and the median run time.
- `github-deployments`: lists environments, recent deployments and recent CI runs.
- `release-cadence`: shows how often the repo ships: number of releases, the latest one, and the median days between them.
- `repo-governance`: checks branch protection and whether `CODEOWNERS`, pre-commit, linter and editor config files exist.

```bash
briar extract --company acme --repo acme-co/acme-app --include ci-health --max 50
briar extract --company acme --repo acme-co/acme-app --include github-deployments
briar extract --company acme --repo acme-co/acme-app --include release-cadence --max 20
briar extract --company acme --repo acme-co/acme-app --include repo-governance --gov-branch develop
```

Security alerts (GitHub only):

- `dependency-health`: lists open Dependabot alerts, highest severity first.
- `code-scanning`: lists open code scanning alerts (for example CodeQL), grouped by rule, with one example file each.

```bash
briar extract --company acme --repo acme-co/acme-app --include dependency-health --max 100
briar extract --company acme --repo acme-co/acme-app --include code-scanning --top-n 5
```

### Tracker extractors

These read an issue tracker. Pick it with `--tracker` (default `jira`).

- `active-tickets`: lists open tickets in a project (up to 25), with the assignee.
- `ticket-archaeology`: studies closed tickets: median time to close, top reporters and assignees, and labels.

```bash
briar extract --company acme --include active-tickets --ticket-project ACME
briar extract --company acme --include ticket-archaeology --tracker github-issues --repo acme-co/acme-app
```

For GitHub Issues and Bitbucket Issues the project is a repo slug, so
`--repo` works. For Jira and Linear, use `--ticket-project` and
`--ticket-archaeology-project`.

### Cloud extractor

- `aws-infra`: lists cloud resources. On AWS it covers ECS, Lambda, CloudWatch log groups, RDS and SQS, plus a count of every tagged resource (`tagging-inventory`).

```bash
briar extract --company acme --include aws-infra --aws-extract-region us-east-1 --aws-extract-service ecs --aws-extract-service rds
```

`--cloud gcp` and `--cloud azure` need the `[gcp]` or `[azure]` extra.

### Meeting extractor

- `meeting-digest`: lists recent Fireflies meetings with their summary, action items and attendees.

```bash
briar extract --company acme --include meeting-digest --since-days 14 --meeting-attendee-allow lead@acme.com
```

## All parameters

### Main parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--company` | Company name. It names the blob and picks per-company credentials like `JIRA_ACME_TOKEN`. Required, unless `BRIAR_COMPANY` or `company` in `.briar.toml` sets it. | None. | `--company acme` |
| `--include` | Run only this extractor. Repeat it to run several. Since 1.1.82 the other sections already in the blob are kept; only the sections of the extractors that ran are replaced (one that ran and found nothing loses its old section; one that is not set up keeps it). | All 23. Ones that are not set up are skipped. | `--include stale-prs` |
| `--store` | Where to save the blob: `file` or `postgres`. `--storage` still works as an old name. It prints a note. | `postgres` if `BRIAR_DATABASE_URL` is set, else `file`. `BRIAR_STORE` or `store` in `.briar.toml` change it. | `--store postgres` |
| `--blob-name` | Name to save the blob under. | `knowledge:<company>`. | `--blob-name knowledge:acme.old` |
| `--root` | Folder for the file store. The blob goes to `<root>/knowledge/<company>.md`. Ignored for Postgres. | `./knowledge` | `--root ./kb` |
| `--out-json` | Also write all sections as JSON to this path. JSON keeps the full detail that the markdown leaves out. | Empty (no JSON). | `--out-json ./acme.json` |
| `--merge-claude-md` | Also write `.briar/knowledge/<company>.md` and put a short index of it into `CLAUDE.md`. | Off. | `--merge-claude-md` |
| `--claude-md-path` | Which `CLAUDE.md` to update. Used only with `--merge-claude-md`. | `./CLAUDE.md` | `--claude-md-path docs/CLAUDE.md` |
| `--advanced-help` | Print the per-extractor flags (table below) and exit. | Off. | `--advanced-help` |

### Common extractor parameters

These go to every extractor in `--include` that uses them. A
per-extractor flag (next section) wins when you give both.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--repo` | Repo to read, as `owner/repo`. Repeat for more repos. It also sets the project for GitHub Issues and Bitbucket Issues. | From `.briar.toml` (`repos` list or `[repo]`), else the git `origin` remote, else none. | `--repo acme-co/acme-app` |
| `--since-days` | How many days of history to read. | Per extractor: `code-hotspots` 30, `defect-hotspots` 90, `revert-signals` 90, `commit-message-quality` 90, `meeting-digest` 7. | `--since-days 60` |
| `--max` | Most items (PRs, commits, alerts, runs, releases, tickets, meetings) to read per repo or project. | Per extractor, 25 to 200. See the table below. | `--max 50` |
| `--top-n` | How many top results to show per repo. | Per extractor, 5 to 15. See the table below. | `--top-n 5` |
| `--sample` | How many recent PRs to sample per repo. Used by `reviewer-profile`, `review-nits` and `pr-hygiene`. | 20 or 30. See the table below. | `--sample 40` |
| `--authors-allow` | Keep only PRs by these authors. Repeat for more. Used by `pr-archaeology` and `active-work`. | No filter. | `--authors-allow alice` |
| `--authors-block` | Drop PRs by these authors. Repeat for more. Same extractors. | No filter. | `--authors-block 'dependabot[bot]'` |
| `--assignees-allow` | Keep only PRs assigned to these people. Same extractors. | No filter. | `--assignees-allow bob` |
| `--assignees-block` | Drop PRs assigned to these people. Same extractors. | No filter. | `--assignees-block carol` |

### Provider and extractor-specific parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--provider` | Code host for the repo extractors: `github` or `bitbucket`. | `github`, or `provider` under `[repo]` in `.briar.toml`. | `--provider bitbucket` |
| `--cloud` | Cloud for `aws-infra`: `aws`, `gcp` or `azure`. | `aws` | `--cloud gcp` |
| `--aws-extract-profile` | Local AWS profile name to use. | None. briar tries `AWS_ACME_*` env vars, then a profile named like the company, then your normal AWS login. | `--aws-extract-profile acme-prod` |
| `--aws-extract-region` | AWS region to read. | `us-east-1` | `--aws-extract-region eu-west-1` |
| `--aws-extract-service` | AWS part to read: `ecs`, `lambda`, `logs`, `rds`, `sqs` or `tagging-inventory`. Repeat for more. | All six. | `--aws-extract-service sqs` |
| `--tracker` | Issue tracker for the tracker extractors: `jira`, `github-issues`, `bitbucket-issues` or `linear`. | `jira`, or `tracker` in `.briar.toml`. | `--tracker linear` |
| `--meeting` | Meeting tool for `meeting-digest`. Only `fireflies` exists today. | `fireflies` | `--meeting fireflies` |
| `--meeting-attendee-allow` | Keep only meetings where one of these emails attended. Repeat for more. | No filter (every meeting you can see). | `--meeting-attendee-allow lead@acme.com` |
| `--prhygiene-large-loc` | Lines changed (added plus deleted) that make a PR "large" for `pr-hygiene`. | `400` | `--prhygiene-large-loc 800` |
| `--gov-branch` | Branch whose protection `repo-governance` checks. | The repo's default branch. | `--gov-branch release` |
| `--stale-days` | A PR open longer than this many days counts as stale for `stale-prs`. | `14` | `--stale-days 7` |

### Per-extractor override parameters (advanced)

`briar extract --advanced-help` lists these 69 flags. They are hidden
from `-h`. Each one sets a value for one extractor only. Use them when
two extractors in the same run need different values.

Most of them are old names for the common flags. When you use one,
briar prints `note: these flags are deprecated in favour of shared
canonical flags`. They still work. `--ticket-project` and
`--ticket-archaeology-project` print no note, because they are the
normal way to name a Jira or Linear project.

```bash
# reviewer-profile samples 10 PRs, review-nits samples 40
briar extract --company acme --repo acme-co/acme-app \
    --include reviewer-profile --include review-nits \
    --sample 40 --reviewer-pr-sample 10
```

You get both sections. Only `reviewer-profile` uses the smaller sample.

| Extractor | Parameter | What it changes | Default | Example |
|---|---|---|---|---|
| All repo extractors | `--provider` | Code host. Same as above. | `github` | `--provider bitbucket` |
| `aws-infra` | `--cloud` | Cloud. Same as above. | `aws` | `--cloud azure` |
| `aws-infra` | `--aws-extract-profile` | AWS profile. Same as above. | None. | `--aws-extract-profile acme-prod` |
| `aws-infra` | `--aws-extract-region` | AWS region. Same as above. | `us-east-1` | `--aws-extract-region us-west-2` |
| `aws-infra` | `--aws-extract-service` | AWS part. Same as above. | All six. | `--aws-extract-service lambda` |
| `active-tickets`, `ticket-archaeology` | `--tracker` | Issue tracker. Same as above. | `jira` | `--tracker linear` |
| `active-tickets` | `--ticket-project` | Project to list open tickets from. Repeat for more. | None. | `--ticket-project ACME` |
| `ticket-archaeology` | `--ticket-archaeology-project` | Project to study closed tickets in. Repeat for more. | None. | `--ticket-archaeology-project ACME` |
| `ticket-archaeology` | `--ticket-max` | Most closed tickets per project. Same as `--max`. | `100` | `--ticket-max 300` |
| `pr-archaeology` | `--pr-repo` | Repo to read. Same as `--repo`. | None. | `--pr-repo acme-co/acme-app` |
| `pr-archaeology` | `--pr-max` | Most merged PRs per repo. Same as `--max`. | `100` | `--pr-max 40` |
| `pr-archaeology` | `--pr-authors-allow` | Keep only these PR authors. | No filter. | `--pr-authors-allow alice` |
| `pr-archaeology` | `--pr-authors-block` | Drop these PR authors. | No filter. | `--pr-authors-block 'dependabot[bot]'` |
| `pr-archaeology` | `--pr-assignees-allow` | Keep only PRs with these assignees. | No filter. | `--pr-assignees-allow bob` |
| `pr-archaeology` | `--pr-assignees-block` | Drop PRs with these assignees. | No filter. | `--pr-assignees-block carol` |
| `active-work` | `--active-repo` | Repo to list open PRs from. Same as `--repo`. | None. | `--active-repo acme-co/acme-api` |
| `active-work` | `--active-authors-allow` | Keep only these PR authors. | No filter. | `--active-authors-allow alice` |
| `active-work` | `--active-authors-block` | Drop these PR authors. | No filter. | `--active-authors-block 'renovate[bot]'` |
| `active-work` | `--active-assignees-allow` | Keep only PRs with these assignees. | No filter. | `--active-assignees-allow bob` |
| `active-work` | `--active-assignees-block` | Drop PRs with these assignees. | No filter. | `--active-assignees-block carol` |
| `github-deployments` | `--deploy-repo` | Repo to read deployments from. | None. | `--deploy-repo acme-co/acme-app` |
| `codebase-conventions` | `--conventions-repo` | Repo to detect tools in. | None. | `--conventions-repo acme-co/acme-app` |
| `reviewer-profile` | `--reviewer-repo` | Repo to profile reviewers in. | None. | `--reviewer-repo acme-co/acme-app` |
| `reviewer-profile` | `--reviewer-pr-sample` | How many recent merged PRs to sample. Same as `--sample`. | `20` | `--reviewer-pr-sample 10` |
| `reviewer-profile` | `--reviewer-top-n` | How many top reviewers to profile. Same as `--top-n`. | `5` | `--reviewer-top-n 3` |
| `code-hotspots` | `--hotspots-repo` | Repo to read commits from. | None. | `--hotspots-repo acme-co/acme-app` |
| `code-hotspots` | `--hotspots-since-days` | Days of commits to read. | `30` | `--hotspots-since-days 14` |
| `code-hotspots` | `--hotspots-max-commits` | Most commits per repo. | `100` | `--hotspots-max-commits 300` |
| `code-hotspots` | `--hotspots-top-n` | How many hotspot files to show. | `10` | `--hotspots-top-n 20` |
| `meeting-digest` | `--meeting` | Meeting tool. Same as above. | `fireflies` | `--meeting fireflies` |
| `meeting-digest` | `--meeting-since-days` | Days of meetings to read. | `7` | `--meeting-since-days 30` |
| `meeting-digest` | `--meeting-max` | Most meetings in the digest. | `25` | `--meeting-max 10` |
| `meeting-digest` | `--meeting-attendee-allow` | Keep only meetings with these attendees. Same as above. | No filter. | `--meeting-attendee-allow lead@acme.com` |
| `defect-hotspots` | `--risk-repo` | Repo to rank files in. | None. | `--risk-repo acme-co/acme-app` |
| `defect-hotspots` | `--risk-since-days` | Days of commits to read. | `90` | `--risk-since-days 365` |
| `defect-hotspots` | `--risk-max-commits` | Most commits per repo. | `200` | `--risk-max-commits 500` |
| `defect-hotspots` | `--risk-top-n` | How many risky files to show. | `10` | `--risk-top-n 5` |
| `review-nits` | `--nits-repo` | Repo to read review comments from. | None. | `--nits-repo acme-co/acme-app` |
| `review-nits` | `--nits-pr-sample` | How many recent merged PRs to sample. | `30` | `--nits-pr-sample 60` |
| `review-nits` | `--nits-top-n` | How many comment groups to keep. | `15` | `--nits-top-n 5` |
| `pr-hygiene` | `--prhygiene-repo` | Repo to check. | None. | `--prhygiene-repo acme-co/acme-app` |
| `pr-hygiene` | `--prhygiene-max` | Most merged PRs for the "merged with zero comments" rate. | `100` | `--prhygiene-max 200` |
| `pr-hygiene` | `--prhygiene-diffstat-sample` | How many PRs to open one by one for size and first-review time. | `30` | `--prhygiene-diffstat-sample 10` |
| `pr-hygiene` | `--prhygiene-large-loc` | Lines changed that make a PR large. Same as above. | `400` | `--prhygiene-large-loc 1000` |
| `ci-health` | `--cihealth-repo` | Repo to read CI runs from. | None. | `--cihealth-repo acme-co/acme-app` |
| `ci-health` | `--cihealth-limit` | Most CI runs per repo. Same as `--max`. | `100` | `--cihealth-limit 30` |
| `dependency-health` | `--deps-repo` | Repo to read Dependabot alerts from. | None. | `--deps-repo acme-co/acme-app` |
| `dependency-health` | `--deps-max` | Most alerts per repo. | `200` | `--deps-max 50` |
| `code-scanning` | `--scan-repo` | Repo to read code scanning alerts from. | None. | `--scan-repo acme-co/acme-app` |
| `code-scanning` | `--scan-max` | Most alerts per repo. | `200` | `--scan-max 50` |
| `code-scanning` | `--scan-top-n` | How many rules to show. | `10` | `--scan-top-n 3` |
| `repo-governance` | `--gov-repo` | Repo to check. | None. | `--gov-repo acme-co/acme-app` |
| `repo-governance` | `--gov-branch` | Branch to check. Same as above. | The default branch. | `--gov-branch main` |
| `revert-signals` | `--revert-repo` | Repo to scan for reverts. | None. | `--revert-repo acme-co/acme-app` |
| `revert-signals` | `--revert-since-days` | Days of commits to read. | `90` | `--revert-since-days 30` |
| `revert-signals` | `--revert-max-commits` | Most commits per repo. | `200` | `--revert-max-commits 400` |
| `stale-prs` | `--stale-repo` | Repo to scan for stale PRs. | None. | `--stale-repo acme-co/acme-app` |
| `stale-prs` | `--stale-max` | Most open PRs per repo. | `100` | `--stale-max 50` |
| `stale-prs` | `--stale-days` | Days open before a PR is stale. Same as above. | `14` | `--stale-days 21` |
| `test-discipline` | `--testdisc-repo` | Repo to check. | None. | `--testdisc-repo acme-co/acme-app` |
| `test-discipline` | `--testdisc-top-n` | How many untested files to show. | `10` | `--testdisc-top-n 25` |
| `todo-density` | `--todo-repo` | Repo to search for markers. | None. | `--todo-repo acme-co/acme-app` |
| `todo-density` | `--todo-max` | Most code search matches per repo. | `200` | `--todo-max 100` |
| `todo-density` | `--todo-top-n` | How many files to show. | `10` | `--todo-top-n 5` |
| `release-cadence` | `--release-repo` | Repo to read releases from. | None. | `--release-repo acme-co/acme-app` |
| `release-cadence` | `--release-max` | Most releases per repo. | `100` | `--release-max 30` |
| `commit-message-quality` | `--msg-repo` | Repo to read commits from. | None. | `--msg-repo acme-co/acme-app` |
| `commit-message-quality` | `--msg-since-days` | Days of commits to read. | `90` | `--msg-since-days 30` |
| `commit-message-quality` | `--msg-max-commits` | Most commits per repo. | `200` | `--msg-max-commits 100` |

## What you see when it runs

Progress goes to stderr, so stdout stays empty for scripts:

```text
  running pr-archaeology ...
  skipped meeting-digest  (not available in this env)
  stale-prs: no data

wrote blob 'knowledge:acme' (18342 bytes, 5 sections) via store=file
```

- `skipped`: credentials or the repo list are missing for that extractor.
- `no data`: it ran but found nothing. This is not an error.
- Exit code `0`: the blob was written.
- Exit code `1` with the error `nothing extracted`: every extractor was skipped or empty, so nothing was written.

Check the result with `briar context get knowledge:acme`. Add
`--store postgres` to that command if you saved to Postgres, because
`context` reads files by default.

## Good to know

- Inside a repo with `.briar.toml` or a git `origin`, that repo is
  already in the `--repo` list. A `--repo` you type is added to it, not
  used instead. If you type the same repo, it is read twice. Leave
  `--repo` out in that case.
- With no `--include`, `aws-infra` always runs. Without AWS access it
  writes a section titled `AWS infrastructure (UNREACHABLE)`. Use
  `--include` to skip it.
- On Bitbucket, `dependency-health`, `code-scanning`, `release-cadence`,
  `todo-density` and `test-discipline` come back empty. `repo-governance`
  cannot read branch protection there.
- Heavy extractors make many API calls: `code-hotspots`,
  `defect-hotspots`, `revert-signals`. Lower `--max` or `--since-days`
  if you hit GitHub rate limits.
- `GetResources` (used by `tagging-inventory`) sees only tagged AWS
  resources. Untagged ones are not counted.
- The agent looks in every blob whose name starts with `knowledge:acme`,
  so a runbook can split the sections across several blobs. It keeps
  only the sections it uses (listed above), found by their `## ` heading.
  Other text in those blobs is not added to its prompt.
- A runbook can save a JSON "inventory" copy next to the blob on every
  scheduled run (`config: {inventory: "true"}` on its `knowledge:`
  block). See [runbook.md](runbook.md).
- Add `-v` to see why an extractor was skipped or failed.

| Problem | What to do |
|---|---|
| Every extractor says `skipped`. | Credentials or `--repo` are missing. Run `briar secrets doctor`, then pass `--repo`. |
| `403` or `404` from GitHub. | The token lacks access to the repo. A classic token needs `repo`, and `read:org` for org data. |
| Jira calls hang or fail. | Check `JIRA_ACME_URL`. It should look like `https://acme.atlassian.net`. |
| The run is slow. | Lower `--max` (for example `--max 20`) or use `--include` to run fewer extractors. |

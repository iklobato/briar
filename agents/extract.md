# `briar extract`

## Purpose
Mine the live state of GitHub / Bitbucket / AWS / Jira / Sentry /
meeting transcripts into a markdown knowledge blob. The blob is
named `knowledge:<company>` by default and becomes the source of
truth other commands splice into agent prompts.

## When to use
- First-time onboarding for a new company (no `knowledge:<company>` yet).
- Periodic cold rebuild — typically scheduled via `briar runbook serve`,
  but you may also want to run it ad-hoc when the world has drifted.
- Before building a plan with `--with-knowledge` so the synthesiser
  has fresh context.

Do NOT run this to update a plan's live knowledge — that's
`KnowledgeWriter`'s job, fired automatically by `briar plan run`
after each successful card.

## Prerequisites

| For these extractors | You need |
|---|---|
| `active-tickets`, `ticket-archaeology` | `JIRA_<COMPANY>_*` or `GITHUB_<COMPANY>_TOKEN` (per tracker) |
| `pr-archaeology`, `reviewer-profile`, `code-hotspots`, `codebase-conventions` | `GITHUB_<COMPANY>_TOKEN` |
| `pr-hygiene`, `defect-hotspots`, `review-nits`, `revert-signals`, `commit-message-quality`, `stale-prs`, `ci-health`, `repo-governance`, `test-discipline`, `release-cadence`, `todo-density` | `GITHUB_<COMPANY>_TOKEN` (the code-quality extractors — see below) |
| `dependency-health`, `code-scanning` | `GITHUB_<COMPANY>_TOKEN` with the **`security_events`** scope (Dependabot / code-scanning alert read) |
| `github-deployments` | `GITHUB_<COMPANY>_TOKEN` |
| `aws-infra` | `AWS_<COMPANY>_*` env or `--aws-extract-profile` (the `tagging-inventory` gatherer also needs the `tag:GetResources` IAM permission) |
| `meeting-digest` | `FIREFLIES_<COMPANY>_API_KEY` (`briar auth login fireflies --company <name>`) |

All code-quality extractors are provider-agnostic (`--provider github|bitbucket`).
The GitHub-native ones (`dependency-health`, `code-scanning`, `ci-health`,
`repo-governance`, `release-cadence`, `todo-density`) return an empty section
on a provider that lacks the underlying API rather than erroring.

Verify coverage with `briar secrets doctor --examples examples/` before
running (it audits every (company, extractor) pair in the runbook dir). If anything is missing the relevant extractor will skip
silently — you'll see `skipped <name>  (not available in this env)`.

## Commands

### Extract everything available for a company

```bash
briar extract --company <COMPANY>
```

Writes `knowledge:<COMPANY>` to `./knowledge/knowledge/<COMPANY>.md`.

### Run only specific extractors

```bash
briar extract --company <COMPANY> \
    --include pr-archaeology \
    --include reviewer-profile \
    --pr-repo <OWNER>/<REPO>
```

The available extractor names:
`active-tickets`, `active-work`, `aws-infra`, `ci-health`,
`code-hotspots`, `code-scanning`, `codebase-conventions`,
`commit-message-quality`, `defect-hotspots`, `dependency-health`,
`github-deployments`, `meeting-digest`, `pr-archaeology`, `pr-hygiene`,
`release-cadence`, `repo-governance`, `revert-signals`, `review-nits`,
`reviewer-profile`, `stale-prs`, `test-discipline`, `ticket-archaeology`,
`todo-density`.

### Code-quality extractors

Thirteen extractors mine git history + the repo-host API for code-health
signal (not just activity). All shape a terse `body` for the agent prompt
plus full structured detail in `data` (surfaced via `--out-json` or the
inventory companion).

| Extractor | What it surfaces | Key flags (defaults) |
|---|---|---|
| `defect-hotspots` | files most likely to break — churn × bug-fix density × size risk score | `--risk-repo`, `--risk-since-days` (90), `--risk-max-commits` (200), `--risk-top-n` (10) |
| `pr-hygiene` | PR-size distribution, large-PR rate, rubber-stamp (zero-comment-approval) rate, time-to-first-review | `--prhygiene-repo`, `--prhygiene-max` (100), `--prhygiene-diffstat-sample` (30), `--prhygiene-large-loc` (400) |
| `review-nits` | recurring reviewer asks clustered into categories — candidates to codify as lint rules | `--nits-repo`, `--nits-pr-sample` (30), `--nits-top-n` (15) |
| `revert-signals` | reverts + emergency fixes → fragile files the test/review net missed | `--revert-repo`, `--revert-since-days` (90), `--revert-max-commits` (200) |
| `commit-message-quality` | conventional-commits adherence + subject-line hygiene | `--msg-repo`, `--msg-since-days` (90), `--msg-max-commits` (200) |
| `stale-prs` | open PRs idle beyond a threshold (age measured from creation) | `--stale-repo`, `--stale-max` (100), `--stale-days` (14) |
| `ci-health` | pass rate, flaky workflows, run-duration trend | `--cihealth-repo`, `--cihealth-limit` (100) |
| `dependency-health` | open dependency vulnerabilities by severity (GitHub Dependabot) | `--deps-repo`, `--deps-max` (200) |
| `code-scanning` | open static-analysis findings grouped by rule/file (GitHub CodeQL) | `--scan-repo`, `--scan-max` (200), `--scan-top-n` (10) |
| `repo-governance` | branch protection + presence of CODEOWNERS / pre-commit / linter / editorconfig | `--gov-repo`, `--gov-branch` (default branch) |
| `test-discipline` | test-to-source file ratio + source files without an obvious test | `--testdisc-repo`, `--testdisc-top-n` (10) |
| `release-cadence` | shipping frequency — median gap between releases, recency | `--release-repo`, `--release-max` (100) |
| `todo-density` | TODO/FIXME/HACK marker count + the files carrying the most (single code-search page) | `--todo-repo`, `--todo-max` (200), `--todo-top-n` (10) |

### AWS resource inventory (every tagged resource)

`aws-infra` runs a registry of per-service gatherers
(`--aws-extract-service`): `ecs`, `lambda`, `logs`, `rds`, `sqs`, and
`tagging-inventory`. The first five describe one service each; the last
walks `resourcegroupstaggingapi:GetResources` to enumerate **every
tagged resource across every service** in the region.

```bash
# Just the account-wide inventory:
briar extract --company <COMPANY> --include aws-infra \
    --aws-extract-service tagging-inventory
```

In the knowledge markdown the inventory section stays terse — a
per-service **count** only, so it doesn't bloat agent prompts. The full
per-resource detail (ARN, type, region, tags) lives in the section's
structured `data`, surfaced via the JSON sidecar (`--out-json`) or the
inventory companion blob (below). Note: `GetResources` only sees
*tagged* resources; untagged ones are invisible to it.

### Persisting full detail (inventory companion)

When a runbook's `knowledge` binding sets `config: {inventory: "true"}`,
each scheduled run also writes a stable JSON **inventory companion** blob
(`knowledge:<company>` → `inventory:<company>`) carrying the full `data`
payloads the markdown drops. It's byte-stable (no timestamp), so
`put_if_changed` only rewrites it on real drift — the postgres history
table then doubles as a cloud/repo-estate change log. List them with
`briar context list --prefix inventory:`. See `agents/runbook.md`.

### Write to postgres instead of disk

```bash
briar extract --company <COMPANY> --storage postgres
```

Requires `BRIAR_DATABASE_URL`. The blob name is unchanged
(`knowledge:<COMPANY>`); only the backend differs.

### Custom blob name (rare)

```bash
briar extract --company <COMPANY> --blob-name knowledge:<COMPANY>.archive-2026q1
```

Use this for snapshots you don't want to clobber `knowledge:<COMPANY>`.

### Parallel JSON output (for piping into other tools)

```bash
briar extract --company <COMPANY> --out-json /tmp/<COMPANY>.json
```

Markdown still lands in the configured store; JSON is a sidecar.

## Verifying success

1. Exit code `0`.
2. `briar context get knowledge:<COMPANY>` returns non-empty markdown.
3. The byte count printed at the end is sensible (typically >2KB for
   a real company). An extractor that ran but produced an empty
   section prints `(no data)`; not an error, just nothing to write.

## Common failures

| Symptom | Fix |
|---|---|
| `nothing extracted — every enabled extractor returned empty` | Either credentials missing (run `briar secrets doctor`) or filters too tight (`--pr-authors-allow`, `--pr-max=0`). Re-run with `-v` to see which extractor skipped and why |
| Extractor `skipped (not available in this env)` | Missing env var for that extractor. `briar secrets doctor` will name it |
| `403` / `404` from GitHub | Token lacks scope or repo doesn't exist. PATs need `repo` (+ `read:org` for org-level metadata) |
| Jira call hangs | `JIRA_<COMPANY>_BASE_URL` set to the wrong host. Check `https://<workspace>.atlassian.net` |
| Slow runs | `--pr-max=20 --hotspots-max-commits=200 --ticket-max=50` cap the heavy extractors |

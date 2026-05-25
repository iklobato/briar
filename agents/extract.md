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
| `github-deployments` | `GITHUB_<COMPANY>_TOKEN` |
| `aws-infra` | `AWS_<COMPANY>_*` env or `--aws-extract-profile` |
| `meeting-digest` | `FIREFLIES_<COMPANY>_API_KEY` |

Verify coverage with `briar secrets doctor --company <name>` before
running. If anything is missing the relevant extractor will skip
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
`active-tickets`, `active-work`, `aws-infra`, `code-hotspots`,
`codebase-conventions`, `github-deployments`, `meeting-digest`,
`pr-archaeology`, `reviewer-profile`, `ticket-archaeology`.

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

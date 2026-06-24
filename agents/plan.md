# `briar plan`

## Purpose
Build, inspect, and execute LLM-driven implementation plans from a
tracker board. A plan is an ordered list of `PlanCard`s persisted as
`plan:<name>` in the chosen `KnowledgeStore`; the LLM `Selector`
picks what to do next at run time, and `KnowledgeWriter` keeps the
plan-scoped knowledge blob `knowledge:<company>.<plan>` current.

The dependency-graph picker (`topological_sort` / `apply_cascade` /
`next_pending`) is removed. `depends_on` is a hint, not a gate.

## Subcommands

| Op | Purpose |
|---|---|
| `build` | Fetch a board, synthesise cards, persist; seed `knowledge:<company>.<plan>` |
| `show` | Print the stored plan's markdown body |
| `status` | Group cards into done / in_progress / blocked / pending with journal artifacts |
| `next` | Ask the LLM selector for one decision (pick / replan / complete / blocked) |
| `advance` | Mark one specific card with a status |
| `run` | Iterate the LLM loop: pick → implement → writeback, with `replan` |
| `list` | Enumerate stored plans |
| `clear` | Delete a stored plan |

## When to use

| Trigger | Op |
|---|---|
| New board, never planned | `build` |
| Need to see what's pending vs done | `status` |
| Manual pacing — one card, decide, then next | `next` then `agent implement` then `advance` |
| End-to-end automated sweep | `run` |
| Operator just shipped a card outside the loop | `advance --card <key> --status done` |
| Board has drifted; want fresh ordering preserving status | Use `run` — selector will return `replan` when it senses drift, or call `build` again with the same `--name` (which overwrites) |

## Prerequisites

| For | Need |
|---|---|
| `build` | Tracker creds (`JIRA_<COMPANY>_*` or `GITHUB_<COMPANY>_TOKEN`). Optional `--llm` for richer synthesis |
| `next` / `run` | **Required**: `--llm <provider>` (no fallback selector). Anthropic / OpenAI / Gemini / Bedrock |
| `run` | Same as `agent implement` (repo + tracker creds + Anthropic) plus `--company` |
| `--with-knowledge` at build | `knowledge:<company>` blob from a prior `briar extract` |

## Supported board URLs

| Form | Example |
|---|---|
| Jira board URL | `https://<workspace>.atlassian.net/jira/software/projects/<KEY>/boards/<N>` |
| Jira short form | `jira:<KEY>` |
| GitHub Projects v2 (org) | `https://github.com/orgs/<ORG>/projects/<N>` |
| GitHub Projects v2 (user) | `https://github.com/users/<USER>/projects/<N>` |

Add a tracker = one file under `src/briar/plan/_boards/` + one
registry entry. The CLI has no per-vendor branching.

## Commands

### Build a plan + seed live knowledge

```bash
briar plan build <BOARD_URL_OR_SHORT_FORM> \
    --name <SLUG> --company <COMPANY> \
    --llm anthropic --with-knowledge
```

Worked examples:

```bash
# Jira, heuristic synthesis only
briar plan build jira:KAN --name acme-q3 --company acme

# GitHub Projects v2, LLM synthesis with company knowledge spliced in
briar plan build https://github.com/orgs/acme/projects/12 \
    --name acme-q3 --company acme \
    --llm anthropic --with-knowledge

# Persistent postgres-backed plan
briar plan build jira:ACME --name acme-impl --company acme \
    --store postgres

# Preview without persisting
briar plan build jira:ENG --name preview --dry-run
```

When `--company` is set, `build` also writes a seed body to
`knowledge:<company>.<plan>`.

### Show what's in a plan

```bash
briar plan show <NAME>                # markdown
briar plan list                       # all stored plans
```

### Visualise past / current / pending

```bash
briar plan status <NAME>              # table
briar plan status <NAME> --format json
```

The status renderer reads the plan blob AND the journal store, so
each `done` card carries its commit sha + PR URL (if present in the
journal artifacts), and each `blocked` card carries its
`last_attempt_summary`.

### Ask the selector for one decision

```bash
briar plan next <NAME> --llm anthropic --format json
```

Returns one of:

| `action` | Other fields | Meaning |
|---|---|---|
| `pick` | `key`, `why`, `branch_parent` (optional), card metadata | Run this card next |
| `replan` | `why` | Re-fetch the board and re-derive cards |
| `complete` | `why` | Plan is done |
| `blocked` | `why` | No forward progress without you |

### Run the full LLM loop end-to-end

```bash
briar plan run <NAME> \
    --company <COMPANY> --repo <OWNER>/<REPO> \
    --tracker <jira|github-issues|...> --provider <github|bitbucket> \
    --llm anthropic
```

`--repo` takes the `owner/repo` slug (or a bare name with `--owner`);
both are inferred from the git remote inside a checkout. The per-card
`agent implement` reuses the plan's `--root` as its knowledge-store root
(there is no separate `--knowledge` flag), and `--meeting-*` / `--slack-*`
enrichment flags mirror `briar agent`.

Per iteration: build `PlanContext` → `Selector.pick` → on PICK call
`run_implement` (same code path as `briar agent implement`) → on
rc=0 call `KnowledgeWriter.write` → loop. On REPLAN call `replan()`
(capped at `--max-replans`, default 3). On COMPLETE/BLOCKED stop.

| Flag | Default | Why |
|---|---|---|
| `--limit <N>` | 0 (∞) | Stop after N cards — useful for smoke runs |
| `--continue-on-failure` | off | Don't stop on a single blocked card |
| `--max-replans <N>` | 3 | Cap on selector REPLAN actions per invocation |
| `--dry-run` | off | Propagate `--dry-run` to every `agent implement` call |

### Advance one card

```bash
briar plan advance <NAME> --card <KEY> --status done
```

`--card` is required. Valid statuses: `pending`, `in_progress`,
`done`, `blocked`. Use this when an operator landed a card outside
the loop.

### Delete a plan

```bash
briar plan clear <NAME>           # confirms
briar plan clear <NAME> --yes     # no prompt
```

## Verifying success

After `build`:
- `briar plan list` includes your plan name.
- `briar context get knowledge:<company>.<plan>` returns non-empty markdown.

After `next`:
- Exit `0`. JSON output has `action` field matching one of the four kinds.

After `run`:
- Exit `0` (everything succeeded) or `1` (some cards blocked).
- `briar plan status <NAME>` shows the new state.
- `briar journal list --command plan.run` shows the session
  with `plan.next.decision` events, `plan.run.card.completed` /
  `plan.run.card.failed`, optional `plan.replan.requested`.

## Common failures

| Symptom | Fix |
|---|---|
| `--llm is required for briar plan {next,run}` | These ops have no deterministic fallback. Pass `--llm anthropic` (or another provider) |
| Selector returns `replan` repeatedly | The world has genuinely drifted, OR the prompt is starving on context. Check `knowledge:<company>.<plan>` (`briar context get`) — is it stale or empty? Cap with `--max-replans` |
| Selector picks an unknown key | The model hallucinated a key. The runner raises and journals `plan.next.invalid`. Re-running often recovers because the second prompt has the failure context |
| `replan` loop exits with `replan_cap` | Selector kept returning REPLAN. Either pass a higher `--max-replans` or investigate why the model thinks the board is stale |
| Card marked `blocked` with `implement rc=...` | The agent implement call returned non-zero. Drop into `--keep-worktree` mode on a manual `agent implement` to debug |
| `plan run` exits 1 with blocked cards | Expected — at least one card couldn't complete. `briar plan status` shows which, `last_attempt_summary` says why |
| Writeback didn't update the knowledge blob | The model returned unparseable JSON OR `put_if_changed` saw the same body. Best-effort by design; the loop never blocks on writeback |

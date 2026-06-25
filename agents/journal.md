# `briar journal`

## Purpose
Inspect the append-only decision journal every briar command writes
to. Each command opens a `Session` and records `DecisionEvent`s; the
session lands in `./journal/sessions/` (or postgres). Use `journal`
to read those sessions back.

## Subcommands

| Op | Purpose |
|---|---|
| `list` | Enumerate stored sessions (newest first) |
| `show` | Pretty-print one session as markdown |
| `export` | Write one session to a file |

## When to use
- Audit what an agent run actually did (every tool call, every decision).
- Recover the rationale for a card pick the LLM selector made.
- Debug a `plan run` that exited with blocked cards — the failure
  rationale is in the journal.
- Hand a stakeholder a markdown summary of a session.

## Prerequisites
- For `--store file` (default): `./journal/` (or `--root <path>`) exists.
- For postgres backend: not shipping yet for journal store; only `file`.

## Commands

### List recent sessions

```bash
briar journal list                          # newest first
briar journal list --limit 50               # cap
briar journal list --command plan.run       # filter by prefix
briar journal list --command agent.         # all agent sessions
```

**The same with Docker:**

```bash
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar journal list                          # newest first
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar journal list --limit 50               # cap
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar journal list --command plan.run       # filter by prefix
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar journal list --command agent.         # all agent sessions
```

Columns: `session_id`, `command`, `target`, `started_at`,
`ended_at`, `decision_count`.

### Pretty-print one session

```bash
briar journal show <SESSION_ID>

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar journal show <SESSION_ID>
```

Prints a markdown document with the session header + every decision
event (choice, value, rationale, alternatives, artifacts).

### Export to a file

```bash
briar journal export <SESSION_ID> --out /tmp/<NAME>.md

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar journal export <SESSION_ID> --out /tmp/<NAME>.md
```

### Find the session a specific plan run wrote

```bash
briar journal list --command plan.run | grep <PLAN_NAME>

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar journal list --command plan.run | grep <PLAN_NAME>
```

The `target` column is `<plan>@<owner>/<repo>` for `plan.run` and
`<owner>/<repo>#<pr>` for `agent.prfix`.

## Choice names you'll see

| Choice | Written by |
|---|---|
| `plan.run.start` | `briar plan run` loop entry |
| `plan.next.decision` | Every selector call (action + rationale) |
| `plan.run.card.start` | A card pick begins |
| `plan.run.card.completed` | Card succeeded (rc=0) |
| `plan.run.card.failed` | Card failed (rc≠0) |
| `plan.replan.requested` | Selector returned REPLAN |
| `plan.run.stopped` | Loop terminated (`limit_reached`, `first_failure`, `replan_cap`, `blocked`, `all_done`) |
| `agent.run.start` / `agent.run.completed` / `agent.run.failed` | `briar agent` lifecycle |
| `scaffold.*` | `briar scaffold` decisions |
| `runbook.*` | `briar runbook` scheduler entries |

## Verifying success

`list`:
1. Exit `0`.
2. Output has at least one row if you've run any command in this
   journal root.

`show`:
1. Exit `0`.
2. Output includes the session header and at least one decision.

`export`:
1. Exit `0`.
2. The file exists and is non-empty.

## Common failures

| Symptom | Fix |
|---|---|
| `journal list` is empty | Either no sessions yet, or you're pointing at the wrong root. Check `--root` |
| `--format json` for `journal export` doesn't work | Known CLI bug (xfailed in tests): the global `--format` and subcommand `--format` collide. Workaround: pipe `briar journal show <id> --format json` |
| Session shows up but `show` says "not found" | `--root` mismatch between the writer and reader. `briar journal list --root <X>` and `briar journal show --root <X> <id>` must agree |
| Decision artifacts truncated | The journal stores everything verbatim; rendering may truncate. Use `--format json` for raw |

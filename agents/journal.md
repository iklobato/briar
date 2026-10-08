# `briar journal`

## Purpose
Inspect the append-only decision journal. Today `briar scaffold` and
`briar plan run` open a `Session` and record `DecisionEvent`s; the
session lands in `./journal/sessions/<YYYY-MM-DD>/<id>.json` (`file` is
the only store). Use `journal` to read those sessions back.

## Subcommands

| Op | Purpose |
|---|---|
| `list` | Enumerate stored sessions (newest first) |
| `show` | Pretty-print one session as markdown |
| `export` | Write one session to a file |

## When to use
- Audit what a `plan run` did (every selector pick, every card result).
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
briar journal list --command scaffold.      # all scaffold sessions
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
    iklob1/briar journal list --command scaffold.      # all scaffold sessions
```

One line per session: `<session_id>  <command> target=<target>
decisions=<N>  started=<timestamp>`. `--limit` defaults to 50. Prints
`(no sessions)` when the root is empty.

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

The `target` is `<plan>@<owner>/<repo>` for `plan.run` and the
`--prefix` value for `scaffold.<template>`.

## Choice names you'll see

| Choice | Written by |
|---|---|
| `plan.run.start` | `briar plan run` loop entry |
| `plan.next.decision` | Every selector call (action + rationale) |
| `plan.run.card.start` | A card pick begins |
| `plan.run.card.completed` | Card succeeded (rc=0) |
| `plan.run.card.failed` | Card failed (rc≠0) |
| `plan.replan.requested` | Selector returned REPLAN |
| `plan.run.stopped` | Loop terminated (`limit_reached`, `selector_error`, `first_failure`, `replan_cap`, `blocked`) |
| `plan.run.completed` | Selector reported every card done (`all_done`) |
| `scaffold.*` | `briar scaffold` decisions (`scaffold.template`, `.sources`, `.archetype`, `.shape`, `.trigger`, `.tools.filtered`, `.output`) |

## Verifying success

`list`:
1. Exit `0`.
2. Output has at least one row if you've run `scaffold` or `plan run`
   with this journal root.

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
| Need JSON from `journal export` | Use `--as json` (not `--format json`, which is the global output flag): `briar journal export <id> --as json` |
| Session shows up but `show` says "not found" | `--root` mismatch between the writer and reader. `briar journal list --root <X>` and `briar journal show --root <X> <id>` must agree |
| Decision artifacts truncated | The journal stores everything verbatim; rendering may truncate. Use `briar journal export <id> --as json` for raw |

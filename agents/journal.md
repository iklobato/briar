# `briar journal`

`briar journal` reads the **decision journal**: a record of the choices
briar made during a run, and why. Each run is one **session**. A session
holds a list of **decisions**, each with a name (such as
`plan.run.card.completed`), a value, a reason, and sometimes the other
options briar looked at.

Only two commands write sessions:

- `briar plan run` (command name `plan.run`)
- `briar scaffold implementation` and `briar scaffold pr-fixes`
  (command names `scaffold.implementation` and `scaffold.pr-fixes`)

No other command writes a session. A plain `briar agent implement` or
`briar agent prfix` run does not, and nor do runbook tasks.

It has three subcommands:

| Subcommand | What it does |
|---|---|
| `list` | Lists sessions, newest first. |
| `show <session_id>` | Prints one session as markdown. |
| `export <session_id>` | Writes one session to a file, as markdown or JSON. |

## Where sessions are saved

When `plan run` or `scaffold` runs, briar saves the session as JSON in
`./journal/sessions/<YYYY-MM-DD>/<session_id>.json`, under the folder you
ran it from. It also writes a markdown copy to
`./journal/published/<session_id>.md`. A session ID is 12 letters and
digits, such as `3f9c2a7b1d04`.

These env vars change how sessions are written:

| Env var | What it does | Default |
|---|---|---|
| `BRIAR_JOURNAL` | Set to `off`, `0` or `no` to stop writing sessions. | on |
| `BRIAR_JOURNAL_ROOT` | The folder where new sessions are saved. | `./journal` |
| `BRIAR_JOURNAL_STORE` | The store for new sessions. Only `file` exists. | `file` |
| `BRIAR_JOURNAL_SINKS` | Extra outputs, comma separated. Only `file` (the markdown copy) exists. | `file` |

The `journal` commands read from `--root`. If you saved sessions with
`BRIAR_JOURNAL_ROOT`, pass the same folder in `--root`.

## `briar journal list`

### What it does

Prints one line per session, newest first:
`<session_id>  <command> target=<target>  decisions=<N>  started=<time>`.
The target is `<plan>@<owner>/<repo>` for `plan.run`, and the
`--prefix` value for `scaffold`. With no sessions, it prints `(no sessions)`.

### Use it when

- You want to find the session of a `plan run` that just ended.
- You want to see how many scaffold bundles were made, and when.

### Examples

```bash
# The 50 newest sessions.
briar journal list
```

You get up to 50 lines, newest first.

```bash
# Only plan run sessions, at most 10.
briar journal list --command plan.run --limit 10
```

You get the 10 newest `plan.run` sessions.

```bash
# Only scaffold sessions (the dot matches both templates).
briar journal list --command scaffold.
```

You get every `scaffold.implementation` and `scaffold.pr-fixes` session.

```bash
# Find the runs of plan q3.
briar journal list --command plan.run | grep "target=q3@"
```

You get the `plan.run` sessions whose target starts with `q3@`, such as `q3@acme-co/acme-app`.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--command` | Only show sessions whose command name starts with this text. | Empty (all sessions). | `--command plan.run` |
| `--limit` | The most sessions to show. | `50` | `--limit 10` |
| `--store` | The journal store. Only `file` exists. | `file` | `--store file` |
| `--root` | The journal folder to read. | `./journal` | `--root ./journal` |

## `briar journal show`

### What it does

Prints one session as markdown: a title with the command and target,
the session ID, start and end time, then one block per decision with
its value, reason, other options and time.

### Use it when

- A `plan run` stopped and you want to know why.
- You want to see which card the plan picked and its reason.

### Examples

```bash
# Read one session.
briar journal show 3f9c2a7b1d04
```

You get the session as markdown in your terminal.

```bash
# Read a session saved in another folder.
briar journal show 3f9c2a7b1d04 --root /srv/briar/journal
```

You get the same view, read from `/srv/briar/journal`.

```bash
# Show the newest plan run, without copying its ID by hand.
ID=$(briar journal list --command plan.run --limit 1 | cut -d' ' -f1)
briar journal show "$ID"
```

You get the newest `plan.run` session. If there is none, `list` prints `(no sessions)` and `show` fails.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `session_id` (positional) | The session to print. Required. | none | `3f9c2a7b1d04` |
| `--store` | The journal store. Only `file` exists. | `file` | `--store file` |
| `--root` | The journal folder to read. | `./journal` | `--root /srv/briar/journal` |

## `briar journal export`

### What it does

Writes one session to a file, or to stdout. Markdown is the same text as
`show`, where long values are cut at 80 characters and nested values
show as `{…}`. JSON is the full raw session, with nothing cut short.

### Use it when

- You want to paste a run summary into a PR or a ticket.
- A script needs the raw data of a session.

### Examples

```bash
# Save a session as markdown.
briar journal export 3f9c2a7b1d04 --out q3-run.md
```

You get the file `q3-run.md` and the line `wrote q3-run.md` on stderr.

```bash
# Print the raw JSON and read one field with jq.
briar journal export 3f9c2a7b1d04 --as json | jq '.decisions | length'
```

You get the number of decisions in the session.

```bash
# Save the raw JSON to a file.
briar journal export 3f9c2a7b1d04 --as json --out q3-run.json
```

You get `q3-run.json` with the whole session.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `session_id` (positional) | The session to export. Required. | none | `3f9c2a7b1d04` |
| `--as` | The format: `markdown` or `json`. | `markdown` | `--as json` |
| `--out` | The file to write. `-` means stdout. | `-` (stdout) | `--out q3-run.md` |
| `--store` | The journal store. Only `file` exists. | `file` | `--store file` |
| `--root` | The journal folder to read. | `./journal` | `--root ./journal` |

## Decision names you will see

| Decision | Written when |
|---|---|
| `plan.run.start` | `plan run` starts. |
| `plan.next.decision` | The plan picks its next step (with its reason). |
| `plan.run.card.start` | Work on a card begins. |
| `plan.run.card.completed` | A card's agent run ended with code 0. |
| `plan.run.card.failed` | A card's agent run ended with another code. |
| `plan.replan.requested` | The plan asked to be rebuilt. |
| `plan.run.stopped` | The loop stopped. The value says why: `limit_reached`, `selector_error`, `blocked`, `replan_cap` or `first_failure`. |
| `plan.run.completed` | Every card is done (value `all_done`). |
| `scaffold.template` | The template you picked. |
| `scaffold.sources` | The `--source` values. |
| `scaffold.archetype` | The agent role. |
| `scaffold.shape` | The workflow shape. |
| `scaffold.trigger` | The trigger kind. |
| `scaffold.tools.filtered` | How many tools the role kept and dropped. |
| `scaffold.output` | How many agents, tools and sources the bundle has. |

## Good to know

- `session '<id>' not found` (exit code 1) usually means `--root` points
  at the wrong folder. Sessions live under the folder where `plan run`
  or `scaffold` ran.
- `.briar.toml` can change these defaults by mistake. Its `root` key sets
  `--root` for `journal` too, and its `store` key sets `--store`. With
  `root = "./knowledge"`, `journal list` looks in the wrong folder. With
  `store = "postgres"`, it fails with `unknown journal store 'postgres'`.
  Pass `--root ./journal --store file` to fix both.
- Use `--as json`, not `--format json`. `--format` is the global output
  flag, and `export` does not use it.
- Sessions are only added, never changed. Do not edit the JSON files by hand.

# `briar runbook`

A **runbook** is a YAML file that says, per company, which extractors to
run, how often, and where to save the result. An **extractor** is one
data collector, such as `pr-archaeology` (past PRs) or `active-tickets`
(open tickets). Each one adds a section to the company's knowledge blob.
See [extract.md](extract.md) for the list of extractors.

`briar runbook` runs those files. It has two subcommands:

| Subcommand | What it does |
|---|---|
| `extract <file>` | Runs every task in one runbook file now, then exits. |
| `serve <folder>` | Stays running and starts each task on its own schedule (its `every:` value). It replaces cron. |

1.x also had `sweep <folder>` (run `extract` for every file in a folder)
and `serve --tick`. 2.0 removed them (decision C14): run `extract` once
per file (`for f in runbooks/*.yaml; do briar runbook extract "$f"; done`),
or `serve` the folder; `serve` checks the schedule every second.

## The runbook file

Here is a full runbook for the company `acme`. Save it as
`runbooks/acme.yaml`.

```yaml
version: 1
companies:
  acme:
    knowledge:
      store: file
      name: knowledge:acme
    schedules:
      - task: extractors
        every: "day at 03:17"
        extract:
          - name: pr-archaeology
            args:
              provider: github
              repo: [acme-co/acme-app]
              max: 50
          - name: codebase-conventions
            args:
              provider: github
              repo: [acme-co/acme-app]
      - task: prs
        every: "hour"
        extract:
          - name: active-work
            args:
              provider: github
              repo: [acme-co/acme-app]
              authors_block: ["dependabot[bot]"]
      - task: tickets
        every: "2 hours"
        extract:
          - name: active-tickets
            args:
              tracker: jira
              ticket_project: [ACME]
    messages:
      ops_chat:
        kind: slack-channel
```

This file gives you three tasks. Once a day, at 03:17 UTC, the
`extractors` task writes `knowledge:acme`. Every hour, `prs` writes
`knowledge:acme.prs`. Every two hours, `tickets` writes
`knowledge:acme.tickets`.

### The keys, one by one

| Key | What it means |
|---|---|
| `version` | Always `1`. |
| `companies` | One block per company. The block name (`acme`) is the company name. At least one is required. |
| `knowledge.store` | Only `file` (the default), so you can leave it out. `postgres` was removed in 2.0 and stops the file from loading with exit code 1. |
| `knowledge.name` | The blob to write, such as `knowledge:acme` or a path like `./knowledge/acme.md`. Agents read only blobs named `knowledge:<company>...`, so a path is for you, not for them. Default: `knowledge:<company>` in the `file` store (since 1.1.86; before, `./knowledge/<company>.md`, which agents never read). |
| `knowledge.root` | The folder for the `file` store. Default `./knowledge`. |
| `knowledge.config.inventory` | Set to `"true"` to also save a JSON copy of all the raw data (see below). |
| `schedules` | The list of tasks. |
| `schedules[].task` | The task name. You use it with `--task`. |
| `schedules[].every` | How often `serve` runs the task (see the next section). |
| `schedules[].extract` | The extractors this task runs. |
| `extract[].name` | The extractor name, such as `pr-archaeology`. A wrong name stops the file from loading. |
| `extract[].args` | Settings for that extractor, by the 1.x option names (`pr_max`, `stale_days`, `aws_extract_region`, ...; `briar extract` itself now takes short names in `.briar.toml`, see [extract.md](extract.md)). The shared names work too: `repo`, `since_days`, `max`, `top_n`, `sample`, `authors_allow`, `authors_block`, `assignees_allow`, `assignees_block`. Unknown names are not checked until spec 004. |
| `extract` (on the company) | Older form: a plain list of extractors with no `schedules`. briar treats it as a task named `extractors` that runs `"day at 03:17"`. |
| `messages`, `mcp`, `git_identity` | Removed in briar 2.0 (only `briar agent` read them). A runbook that still has one stops with exit code 1 and names the key: delete the block. |

### Where each task writes

- The task named `extractors` writes the blob in `knowledge.name`.
- Every other task writes `<name>.<task>`. For `knowledge:acme` and the
  task `prs`, that is `knowledge:acme.prs`. For `./knowledge/acme.md`, it
  is `./knowledge/acme.prs.md`.
- briar only writes when the text changed. Otherwise the status says
  `skipped (unchanged, ...)`.

### The inventory copy

With `config.inventory: "true"` (also `1`, `yes` or `on`), each task also
saves the full raw data of its extractors as JSON. The markdown blob
drops much of that detail. The copy for `knowledge:acme` is
`inventory:acme`. The copy for a path like `./knowledge/acme.md` is
`./knowledge/acme.inventory.json`; `inventory:acme` itself is the file
`./knowledge/inventory/acme.md`. It is only written when it changed, so
its file date shows when the data last changed.

## The `every:` format

`every:` uses a small format. Letters can be upper or lower case.

| Form | Meaning | Examples |
|---|---|---|
| `<unit>` | Once per unit. Units: `minute`, `hour`, `day`, `week`, or a weekday (`monday` to `sunday`). | `"hour"`, `"day"`, `"monday"` |
| `<n> <units>` | Every n units. Plural or not: `minutes`, `hours`, `days`, `weeks`. Not for weekdays. | `"10 minutes"`, `"4 hours"`, `"2 days"` |
| `<unit> at HH:MM` | For `day`, `n days` and weekdays: at that time, in UTC. Use two digits for the hour. | `"day at 03:17"`, `"monday at 09:00"`, `"2 days at 10:00"` |
| `hour at :MM` | At that minute of every hour. | `"hour at :15"` |
| `minute at :SS` | At that second of every minute. | `"minute at :30"` |

These do **not** work: `"daily"`, `"hourly"`, `"every day"`,
`"day at 3:17"` (one digit hour), `"day at 03:17:30"`, `"week at 10:00"`,
`"hour at 15"`, `"2 mondays"`.

`serve` spreads tasks out so they do not all start at once. A task with
no `at` time first runs after a fixed offset that is shorter than its
period. Then it runs once per period. For example, an hourly task might
first run 14 minutes after `serve` starts, then every hour. The offset
comes from the company and task names, so it stays the same after a
restart. Tasks with an `at` time run at exactly that time.

## `briar runbook extract`

### What it does

Loads one runbook file and runs every task in it now. It does not look
at `every:`. It prints a table with the columns `company`, `task`,
`status` and `output` (the blob name).

### Use it when

- You wrote or changed a runbook and want to test it once.
- You want fresh knowledge now, not at the next scheduled time.
- You want to run just one task with `--task`.

### Examples

```bash
# Run every task for every company in the file.
briar runbook extract runbooks/acme.yaml
```

You get one row per task, such as `acme  prs  wrote 4210 bytes via store=file  knowledge:acme.prs`.

```bash
# Run only the task named "prs".
briar runbook extract runbooks/acme.yaml --task prs
```

You get one row for `prs`. Companies with no `prs` task are skipped.

```bash
# The same result as JSON, for a script.
briar runbook extract runbooks/acme.yaml --format json
```

You get a JSON list of rows.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `file` (positional) | The runbook file. `.yaml` and `.yml` are read as YAML. Any other file is read as JSON. Required. | none | `runbooks/acme.yaml` |
| `--task` | Only run the task with this name. | Empty: run every task. | `--task tickets` |

### What the status column says

| Status | Meaning |
|---|---|
| `wrote <N> bytes via store=<store>` | The blob was written. |
| `skipped (unchanged, <N> bytes, hash=<8 chars>)` | The new text is the same as the stored one. Nothing was written. |
| `empty (no sections)` | No extractor returned data, or none had its repo or project list. Nothing was written. |
| `failed (...)` | The store could not be opened or written. See the log. |
| `skipped (no schedule)` | The company has no tasks. |
| `inventory wrote (<N> bytes)` | The inventory copy was written. |

Before any task runs, `extract` checks the logins of every company and
task it will run (after `--task`): each source says who the login is, and
briar reads each repo and project the tasks read. If one fails, no task
runs, there is no table, and the command exits with code 1 and lists every
failure:

```text
error: the login check failed; fix these, then run again:
  - fireflies (globex): no login saved (BRIAR_GLOBEX_FIREFLIES_API_KEY); run `briar auth login fireflies --company globex --runbook FILE`
```

## `briar runbook serve`

### What it does

Reads every `*.yaml` file in a folder once, at start. It registers one
job per company and task, then runs each job on its `every:` schedule.
It runs until you stop it (Ctrl-C) and logs to stderr.

### Use it when

- You want briar to keep each company's knowledge fresh on a server.
- You want one long-running process instead of many cron lines.

### Examples

```bash
# Run all schedules in the runbooks/ folder.
briar runbook serve runbooks/
```

You get log lines such as `registered company=acme task=prs every='hour'`,
then `scheduler starting: 3 job(s), tick=1.0s`. Later, at each run time,
you get `fire task=prs company=acme` and a `result ...` line.

```bash
# Keep the log in a file.
briar runbook serve runbooks/ >> /var/log/briar/scheduler.log 2>&1
```

You get the log in `/var/log/briar/scheduler.log`.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `directory` (positional) | The folder with the runbook files. `serve` takes a folder, not one file. Required. | none | `runbooks/` |

`serve` checks for due jobs every second; the 1.x `--tick` flag is gone.

### Good to know

- `serve` does not run any task at start. It checks the logins of every
  company and task first, and does not start (exit code 1) if one fails.
  Each task first runs at its next scheduled time. Run
  `briar runbook extract` once if you need data now.
- Tasks run one at a time. A slow task delays the next one.
- At each run, briar reads that task's file again. So changes to `args`
  apply at the next run. New tasks, removed tasks and new `every:`
  values need a restart.
- A bad file or a bad `every:` value stops `serve` at start. `extract`
  does not check `every:`, so test a new value with `serve`.
- Before each task, `serve` checks its logins again. If that check fails
  (a token expired, a network error), `serve` stops with exit code 1 for
  every company and prints the failure: fix it, then start it again. A
  process manager that restarts on failure starts it into the same check
  until the source answers.
- If a task fails while running for another reason, `serve` logs
  `FAILED task=...` and keeps going.
- `serve` never goes to the background on its own. Run it under a
  process manager such as systemd.

A systemd service for a server:

```ini
# /etc/systemd/system/briar-scheduler.service
[Service]
User=briar
WorkingDirectory=/srv/briar
EnvironmentFile=/etc/briar/secrets.env
ExecStart=/usr/local/bin/briar runbook serve /srv/briar/runbooks/
StandardError=append:/var/log/briar/scheduler.log
Restart=on-failure
```

Set `WorkingDirectory`: a `file` store path such as `./knowledge` is
relative to it. Use the path that `which briar` prints in `ExecStart`.

## Failure alerts

2.0 sends no alert when a task fails: the traceback goes to the log (stderr),
and the row says `failed (...)`. 1.x sent one to the channels in
`BRIAR_NOTIFY_SINKS`; that variable is no longer read. Alerts come back with
the ticket pipeline (spec 003).

## Good to know

- Check your keys before you schedule anything:
  `briar auth status <target> --company <name>` for each source (see
  [auth.md](auth.md)).
- `extract` exits with code 0 even when a task fails. Read the `status`
  column, or use `--format json` and check it in your script.
- An extractor with missing keys is skipped with a warning in the log.
  If every extractor in a task is skipped, the status is `empty (no sections)`.
- A wrong key at the top of the file, in a task, in an extractor entry
  or in a `messages:` entry stops the file from loading. A wrong key in
  a company block, in `knowledge:` or inside `args` is ignored without a
  warning, so check the spelling.
- `extract` and `serve` log progress at INFO level to stderr.
  The table goes to stdout.

## Run it with Docker

```bash
docker run --rm -v "$PWD":/work -w /work \
    --env-file "$HOME/.config/briar/secrets.env" \
    iklob1/briar runbook extract runbooks/acme.yaml
```

You get the same table. The blobs land in `./knowledge` in your current folder.

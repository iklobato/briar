# `briar runbook`

## Purpose
Drive `briar extract` from a YAML manifest, either once (`extract` /
`sweep`) or as a long-running scheduler loop (`serve`). One YAML
describes a company's extractors + cadences; the runbook walks it.

## When to use

| Op | When |
|---|---|
| `extract <yaml>` | Run every `extract:` / `schedules:` task in one YAML, then exit |
| `sweep <dir>` | Run `extract` for every `*.yaml` in a directory, then exit |
| `serve <dir>` | Stay alive, register every (company, task) in every `*.yaml` of the directory, run each on its `every:` cadence |

Reach for `serve` when you want briar to be the cron daemon. Use
`extract` for one-shot manual rebuilds; use `sweep` to refresh every
company you operate.

## Prerequisites

- A runbook YAML. Examples in `examples/` (e.g. `examples/all_features.yaml`).
- Every credential the YAML's extractors and `messages:` writers need,
  resolvable via env or `secrets.env`. Confirm with
  `briar secrets doctor --examples examples/`.
- For `serve`: a process supervisor (systemd, supervisord). The
  process never daemonises itself.

## Commands

### Run every task in one YAML

```bash
briar runbook extract <PATH>/<COMPANY>.yaml

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar runbook extract <PATH>/<COMPANY>.yaml
```

Equivalent to walking each `extract:` block and calling `briar
extract` with the YAML's flags filled in.

### Sweep every YAML in a directory

```bash
briar runbook sweep examples/

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar runbook sweep examples/
```

Useful before a release: refreshes every company's knowledge in one go.

### Serve the scheduler 24/7

```bash
briar runbook serve examples/

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar runbook serve examples/
```

Stays in foreground; logs to stderr at INFO. `serve` takes a
directory, not a single YAML. Pass `--tick <seconds>` to change how
often the loop checks for due jobs (default 1). Scheduled runs do not
write journal sessions.

Typical systemd wrapper (production):

```ini
# /etc/systemd/system/briar-scheduler.service
[Service]
ExecStart=/usr/local/bin/briar runbook serve /etc/briar/runbooks/
Restart=on-failure
User=briar
EnvironmentFile=/etc/briar/secrets.env
```

### Knowledge binding options

Each company's `knowledge:` block selects the store and blob name:

```yaml
    knowledge:
      store: postgres            # file | postgres
      name: knowledge:acme       # blob name (file backend: ./knowledge/...)
      # root: ./knowledge        # file backend only — override parent dir
      config:
        inventory: "true"        # also write the JSON inventory companion
```

With `config.inventory` truthy, every scheduled run additionally writes
a stable JSON companion blob (`knowledge:acme` → `inventory:acme`,
category `inventory`) holding the full structured `data` each extractor
produced — the detail the prompt-baked markdown drops (e.g. every tagged
AWS resource from `tagging-inventory`). It's byte-stable, so
`put_if_changed` only rewrites it on real drift; the postgres history
table becomes an estate change log. Inspect with `briar context get
inventory:acme` / `briar context list --prefix inventory:`. Off by
default — omit the block and behaviour is unchanged.

## Verifying success

For one-shot:
1. Exit code `0`.
2. The output table (`company`, `task`, `status`, `output`) shows
   `wrote <N> bytes via store=...` (or `skipped (unchanged, ...)` when
   the blob did not change) for each task.
3. `briar context list` shows the expected `knowledge:*` blobs.

For `serve`:
1. Process stays up. Check `systemctl status briar-scheduler`.
2. The log shows `registered company=<name> task=<name> every=...`
   for each job, then `scheduler starting: <N> job(s)`.
3. After the first scheduled fire, the log shows
   `fire task=... company=...` and `result task=... status=...`.

## Common failures

| Symptom | Fix |
|---|---|
| `every: cannot parse ...` | DSL grammar issue. See `EveryParser` in `briar/iac/runbook/scheduler.py`. Accepted shapes: `[N] minute(s)/hour(s)/day(s)/week(s)` or a weekday, with optional `at HH:MM` / `at :MM`, e.g. `"day at 03:17"`, `"4 hours"`, `"hour at :15"`, `"wednesday at 17:30"` |
| `secrets doctor` row `X ... MISSING: FOO_BAR` | The YAML uses an extractor or writer whose env var is not set. Either add it to `/etc/briar/secrets.env` or remove the task |
| `serve` exits immediately | A registration-time error (bad YAML, bad cron). Check the last log line; re-run `briar runbook extract <yaml>` for the same input to isolate |
| Task ran but no blob updated | The extractor returned empty (logged). Not an error; look at the YAML filters |

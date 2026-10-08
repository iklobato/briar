# `briar dashboard`

## Purpose
Serve a read-only HTML status page summarising the local briar
deployment. Aggregates the collectors in `dashboard/collectors.py`
into one page: scheduler state, recent cycles, GitHub API quota,
connectivity probes, git state, disk/memory/load, log tail.

## When to use
- A host has briar installed and you want a quick "is it healthy"
  view in a browser.
- You're debugging a `runbook serve` instance and want to see what
  it's actually running.
- One-off snapshot for an incident — use `--once` to write a
  static page and exit.

## Prerequisites
- Nothing else: runs in-process. The page is built from local files
  (scheduler log, runbook YAMLs, git checkout) plus TCP probes to
  `api.github.com`, `github.com` and `sts.amazonaws.com`.

## Commands

### Serve interactively

```bash
briar dashboard
# Opens on http://127.0.0.1:8080 (default --host / --port; loopback only)

# or with Docker (bind 0.0.0.0 so it's reachable from the host):
docker run --rm -p 8080:8080 -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar dashboard --host 0.0.0.0
```

### Bind to localhost only

```bash
briar dashboard --host 127.0.0.1 --port 8080

# or with Docker (publish to the host's loopback only; bind all
# interfaces *inside* the container so -p can reach it):
docker run --rm -p 127.0.0.1:8080:8080 -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar dashboard --host 0.0.0.0 --port 8080
```

### One-off snapshot

```bash
briar dashboard --once > /tmp/briar-status.html

# or with Docker (snapshot to stdout, no server, so no published port):
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar dashboard --once > /tmp/briar-status.html
```

Renders one page, writes to stdout, exits. Useful for cron / on-call
deliverables.

### Schedules view

```bash
briar dashboard --examples ./examples

# or with Docker (bind 0.0.0.0 so it's reachable from the host):
docker run --rm -p 8080:8080 -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar dashboard --host 0.0.0.0 --examples ./examples
```

Points the schedules card at a directory of runbook YAMLs (default `./examples`).

### Specific debug paths

| Flag | What it overrides |
|---|---|
| `--log-file <path>` | Which logfile to tail in the "recent activity" card (default `/var/log/briar/scheduler.log`) |
| `--repo-path <path>` | Where to read git state from (default `.`) |
| `--disk-path <path>` | Which mount to compute free space for (default `/`) |

## Verifying success

Interactive:
1. `http://<host>:<port>` returns HTTP 200 with HTML.
2. The "schedulers" card lists the companies and tasks from your
   `--examples` YAMLs.

One-off:
1. Exit `0`.
2. Output file parses as HTML.

## Common failures

| Symptom | Fix |
|---|---|
| `address already in use` on `:8080` | Another `briar dashboard` is running, or that port is taken. Pass `--port <N>` |
| One card shows "collector failed" | The collector raised, isolated correctly. Click the card / run `-v` to see which one. Often a missing optional dep |
| "recent activity" card empty | `--log-file` points nowhere. Pass an absolute path |
| Want to expose publicly | Don't bind `0.0.0.0` without a reverse proxy in front; the page has no auth |

# `briar dashboard`

## Purpose
Serve a read-only HTML status page summarising the local briar
deployment. Aggregates the collectors under `dashboard/collectors/`
into one page: scheduler state, recent extractions, git state,
disk, log tail, credential coverage.

## When to use
- A host has briar installed and you want a quick "is it healthy"
  view in a browser.
- You're debugging a `runbook serve` instance and want to see what
  it's actually running.
- One-off snapshot for an incident — use `--once` to write a
  static page and exit.

## Prerequisites
- The same env vars `briar` already reads at startup (the dashboard
  shows the credential-coverage section by reading them).
- Nothing else — runs in-process, no external dependencies.

## Commands

### Serve interactively

```bash
briar dashboard
# Opens on http://0.0.0.0:8080 (default --host / --port)
```

### Bind to localhost only

```bash
briar dashboard --host 127.0.0.1 --port 8080
```

### One-off snapshot

```bash
briar dashboard --once > /tmp/briar-status.html
```

Renders one page, writes to stdout, exits. Useful for cron / on-call
deliverables.

### Point at a non-default knowledge store

```bash
briar dashboard --knowledge-store postgres
briar dashboard --knowledge-store file --knowledge /var/lib/briar/knowledge
```

### Specific debug paths

| Flag | What it overrides |
|---|---|
| `--log-file <path>` | Which logfile to tail in the "logs" card |
| `--repo-path <path>` | Where to read git state from |
| `--secrets-file <path>` | Which `.env` file's coverage to report |
| `--disk-path <path>` | Which mount to compute free space for |
| `--du-path <path>` | Which path to `du` for the "storage" card |

## Verifying success

Interactive:
1. `http://<host>:<port>` returns HTTP 200 with HTML.
2. The "version" card matches `briar version`.
3. The "credential coverage" card lists the same lines `briar
   secrets doctor` would print.

One-off:
1. Exit `0`.
2. Output file parses as HTML.

## Common failures

| Symptom | Fix |
|---|---|
| `address already in use` on `:8080` | Another `briar dashboard` is running, or that port is taken. Pass `--port <N>` |
| One card shows "collector failed" | The collector raised, isolated correctly. Click the card / run `-v` to see which one. Often a missing optional dep |
| Logs card empty | `--log-file` points nowhere. Pass an absolute path |
| Want to expose publicly | Don't bind `0.0.0.0` without a reverse proxy in front; the page has no auth |

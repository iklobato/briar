# `briar version`

## Purpose
Print the client version string. The fastest signal that the
install is functional and on the version you expect.

## When to use
- Smoke-test that the `briar` CLI is reachable on a host.
- Confirm a deploy actually shipped the version it claims.
- Before opening a bug report — every report should name the version.

## Prerequisites
None. The command makes no network calls and reads no credentials.

## Commands

```bash
briar version
briar version --format json   # machine-readable
```

## Verifying success
Exit code `0`. Output is one line matching `\d+\.\d+\.\d+` (e.g. `1.1.11`).

## Common failures

| Symptom | Fix |
|---|---|
| `command not found: briar` | `pip install -e .` from the repo root, or use `.venv/bin/python -m briar version` |
| Wrong version printed | The on-PATH `briar` is from a different install. Resolve with `which briar` and reinstall in the active venv |

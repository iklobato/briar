# `briar version`

## Purpose
Print the client version string. The fastest signal that the
install is functional and on the version you expect.

## When to use
- Smoke-test that the `briar` CLI is reachable on a host.
- Confirm a deploy actually shipped the version it claims.
- Before opening a bug report — every report should name the version.

## Prerequisites
None. `briar --version` (or `-V`) prints the same line and exits before
any startup work. `briar version` runs the normal startup path
(credential bootstrap, telemetry event, update check), so use
`--version` when you need zero network calls.

## Commands

```bash
briar version
briar --version              # same output, no startup work
```

**The same with Docker:**

```bash
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar version
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar --version              # same output, no startup work
```

## Verifying success
Exit code `0`. Output is one line, `briar-cli <version>` (e.g. `briar-cli 1.1.56`).
`--format` has no effect on it.

## Common failures

| Symptom | Fix |
|---|---|
| `command not found: briar` | `pip install -e .` from the repo root, or use `.venv/bin/python -m briar version` |
| Wrong version printed | The on-PATH `briar` is from a different install. Resolve with `which briar` and reinstall in the active venv |

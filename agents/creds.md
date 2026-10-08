# `briar secrets`

## Purpose
Audit credential coverage and one-shot any registered bootstrap.
`doctor` answers "do I have what each (company, extractor) needs?".
`bootstrap` is the testing knob for credential-bootstrap targets
(e.g. envfile hydration) that normally fire on every CLI
startup.

## Subcommands

| Op | Purpose |
|---|---|
| `doctor` | Walk every (company, extractor) and `messages:` writer in the runbook YAMLs and report which env-vars are present / missing |
| `bootstrap` | Run one credential-bootstrap (e.g. `envfile`) manually |

## When to use

| Trigger | Op |
|---|---|
| Setting up a new host | `doctor` to see the gap |
| Something else failed with a missing-credential error | `doctor` to see which env var is missing |
| Wrote a new secret to `/etc/briar/secrets.env` | `doctor` to confirm it's picked up |
| Auto-startup bootstrap failed | `bootstrap --kind <kind>` to run it explicitly and read the error |

## Prerequisites
- For `doctor`: `--examples <dir>` (which runbook YAMLs to walk; default `./examples`, and if that dir is absent it reports "no runbooks to check" and exits 0).
- For `bootstrap`: the bootstrap target's prerequisites (e.g. a
  readable `secrets.env` for `envfile`).

## Commands

### Audit every company in the examples directory

```bash
briar secrets doctor --examples examples/

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar secrets doctor --examples examples/
```

Output is one `=== <company> (<file>.yaml) ===` header per company, then
one row per extractor or `messages.<handle>` writer:
`ok <extractor> (provider=<kind>)`, or `X  <extractor> (provider=<kind>)`
followed by `MISSING: <ENV_VAR>, ...`.
Exit `0` if every required env-var is present; `1` otherwise.

`doctor` reports every company it finds in the YAMLs; it has no
per-company or missing-only filter. Grep the output if you need a subset
(e.g. `briar secrets doctor --examples examples/ | grep MISSING`).

### Check coverage against a specific credential store

```bash
briar secrets doctor --examples examples/ --cred-store aws-secretsmanager

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar secrets doctor --examples examples/ --cred-store aws-secretsmanager
```

`--cred-store` picks the backend whose coverage is reported (default
`envfile`).

### Manually run a bootstrap

```bash
briar secrets bootstrap --kind envfile

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar secrets bootstrap --kind envfile
```

Useful when debugging why the auto-bootstrap at CLI startup failed —
this prints the same error in foreground.

## Verifying success

`doctor`:
1. Exit `0` if everything's covered.
2. Read the printed rows; every required env-var has `OK`.
3. Re-running an actual extractor (`briar extract --company <COMPANY>
   --include <extractor>`) no longer skips it for missing credentials.

`bootstrap`:
1. Exit `0`.
2. It prints `bootstrap <kind>: ... <N> env vars (preserved <M> already-set)`.
   Keys loaded into this process only; they do not persist after it exits.

## Common failures

| Symptom | Fix |
|---|---|
| `no examples dir at examples` | `--examples` defaults to `./examples`. Pass `--examples <dir>` pointing at your runbook YAMLs |
| Row says `MISSING` for a var you set | Wrong file. Check `BRIAR_SECRETS_FILE`, then `/etc/briar/secrets.env`, then `$XDG_CONFIG_HOME/briar/secrets.env`. The first one that exists wins |
| `doctor` says OK but extractor still fails | The env-var is present but invalid (expired token, wrong scope). `briar auth login <target>` to re-acquire |

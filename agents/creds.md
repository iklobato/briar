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
| `doctor` | Walk every (company, extractor) and report which env-vars are present / missing |
| `bootstrap` | Run one credential-bootstrap (e.g. `envfile`) manually |

## When to use

| Trigger | Op |
|---|---|
| Setting up a new host | `doctor` to see the gap |
| Something else exited 3 (`CREDENTIAL_ERROR`) | `doctor` to see which env var is missing |
| Wrote a new secret to `/etc/briar/secrets.env` | `doctor` to confirm it's picked up |
| Auto-startup bootstrap failed | `bootstrap <target>` to run it explicitly and read the error |

## Prerequisites
- For `doctor`: `--examples <dir>` (which company YAMLs to walk).
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

Output per row: `<company> · <extractor> · <env-var> · OK|MISSING`.
Exit `0` if every required env-var is present; non-zero otherwise.

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
   --include <extractor>`) doesn't exit 3.

`bootstrap`:
1. Exit `0`.
2. The secrets it fetches now appear via `briar auth list`.

## Common failures

| Symptom | Fix |
|---|---|
| `--examples` is required | Always pass it: `--examples examples/` (or wherever your company YAMLs live) |
| Row says `MISSING` for a var you set | Wrong file. Check `BRIAR_SECRETS_FILE`, then `/etc/briar/secrets.env`, then `$XDG_CONFIG_HOME/briar/secrets.env`. The first one that exists wins |
| `doctor` says OK but extractor still fails | The env-var is present but invalid (expired token, wrong scope). `briar auth login <target>` to re-acquire |

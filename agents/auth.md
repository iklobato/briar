# `briar auth`

## Purpose
Interactively acquire and persist credentials. Walks an OAuth /
device / SSO / paste flow for one target, then writes the
resulting tokens to a `CredentialStore` backend (envfile by
default).

This is the human-in-the-loop counterpart to `briar secrets doctor`:
`secrets doctor` tells you what's missing; `auth login <target>`
goes and gets it.

## Subcommands

| Op | Purpose |
|---|---|
| `login <target>` | Acquire credentials for `<target>` |
| `logout <target>` | Delete credentials this target's login would have written |
| `refresh <target>` | Renew a bundle without re-prompting (no target supports it yet, see below) |
| `list` | Show which credential env-var names are set (no values) |
| `status <target>` | One target's env-var names for a company, set or missing |

## When to use

- First-time setup on a new host (`briar secrets doctor` returns red).
- A token expired and needs replacing (re-run `login`).
- You're rotating credentials for a single target.

Do NOT use this in headless automation paths — every `login` flow
expects a terminal. For headless deploys, write the env vars
directly to `/etc/briar/secrets.env`.

## Targets (the registry)

| Target | What it does |
|---|---|
| `github-device` | OAuth device flow → writes `GITHUB_TOKEN` (not per-company) |
| `github-pat` | Paste a PAT → writes `GITHUB_TOKEN` (not per-company) |
| `bitbucket-app-password` | Paste an app password → writes `BITBUCKET_<COMPANY>_APP_PASSWORD` (+ username, workspace) |
| `aws-static` | Paste static keys → writes `AWS_<COMPANY>_*` |
| `aws-sso` | SSO browser flow → writes `AWS_<COMPANY>_*` |
| `jira-token` | Paste API token → writes `JIRA_<COMPANY>_*` |
| `jira-session` | Paste session cookie (workaround) → writes `JIRA_<COMPANY>_*` session vars |
| `linear-api-key` | Paste API key → writes `LINEAR_<COMPANY>_TOKEN` |
| `fireflies` | Paste API key → writes `FIREFLIES_<COMPANY>_API_KEY` (used by `meeting-digest` / `meeting-context`) |

## Prerequisites
- A terminal (these flows print to stdout and read stdin / paste / open a browser).
- `--company <name>` for every per-company target (the resulting env-var
  names are namespaced by it). The GitHub targets write the global
  `GITHUB_TOKEN` and ignore it.
- For `--cred-store aws-secretsmanager` etc.: the relevant
  backend already initialised with credentials.

## Commands

### Acquire a GitHub PAT for one company

```bash
briar auth login github-pat --company <COMPANY>
# Paste the token at the prompt

# or with Docker:
docker run --rm -it -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar auth login github-pat --company <COMPANY>
```

### Device-flow OAuth (opens a browser)

```bash
briar auth login github-device --company <COMPANY>
# Visits github.com/login/device with the printed user-code

# or with Docker:
docker run --rm -it -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar auth login github-device --company <COMPANY>
```

### AWS SSO

```bash
briar auth login aws-sso --company <COMPANY>
# Opens the SSO start URL in your browser

# or with Docker:
docker run --rm -it -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar auth login aws-sso --company <COMPANY>
```

### Persist into a non-default backend

```bash
briar auth login github-pat --company <COMPANY> --cred-store vault

# or with Docker:
docker run --rm -it -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar auth login github-pat --company <COMPANY> --cred-store vault
```

`--cred-store` options: `envfile` (default), `aws-secretsmanager`, `ssm`,
`vault`. (The old name `--store` still works but is deprecated; it is
named `--cred-store` so it does not clash with the knowledge-store
`--store` used by `extract` / `agent` / `plan` / `context`.)

### Refresh an existing bundle

No target implements refresh today: `aws-sso` and every other target
raise "cannot refresh" and tell you to re-run `login`. The command
shape is:

```bash
briar auth refresh github-device --company <COMPANY>

# or with Docker:
docker run --rm -it -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar auth refresh github-device --company <COMPANY>
```

Paste-based targets (PAT, app-password, Jira token) can never refresh:
re-run `login`.

### See what's been acquired

```bash
briar auth list                                   # every set name
briar auth list --company <COMPANY>               # one company
briar auth status <TARGET> --company <COMPANY>    # one target
```

**The same with Docker:**

```bash
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar auth list                          # every set name
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar auth status <TARGET> --company <COMPANY>    # one target
```

### Forget a credential

```bash
briar auth logout github-pat --company <COMPANY>

# or with Docker:
docker run --rm -it -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar auth logout github-pat --company <COMPANY>
```

## Verifying success

1. Exit `0`.
2. `briar auth status <TARGET> --company <COMPANY>` shows the target's
   env-var names as set.
3. `briar secrets doctor --examples examples/` shows the new env-var
   covered.
4. Whatever downstream command needed the cred now runs (e.g.
   `briar extract --company <COMPANY> --include pr-archaeology`).

## Common failures

| Symptom | Fix |
|---|---|
| `--company is required` | Pass `--company <name>` for any per-company target |
| OAuth device flow times out | Re-run; the device code expires after 15 min |
| `secrets.env` can't be read | briar writes it with mode `0600` (owner only). Run briar as the file's owner, or fix with `chmod 600 <path>` |
| Token works in browser, fails in CLI | PAT lacks required scopes. GitHub needs `repo`, `read:org`. Jira tokens are tied to the account that minted them |

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
| `refresh <target>` | Renew an OAuth / SSO bundle without re-prompting |
| `list` | Show what's been acquired |
| `status` | Per-target availability summary |

## When to use

- First-time setup on a new host (`briar secrets doctor` returns red).
- An OAuth token expired and needs refreshing.
- You're rotating credentials for a single target.

Do NOT use this in headless automation paths — every `login` flow
expects a terminal. For headless deploys, write the env vars
directly to `/etc/briar/secrets.env`.

## Targets (the registry)

| Target | What it does |
|---|---|
| `github-device` | OAuth device flow → writes `GITHUB_<COMPANY>_TOKEN` |
| `github-pat` | Paste a PAT → writes `GITHUB_<COMPANY>_TOKEN` |
| `bitbucket-app-password` | Paste an app password → writes `BITBUCKET_<COMPANY>_APP_PASSWORD` (+ username) |
| `aws-static` | Paste static keys → writes `AWS_<COMPANY>_*` |
| `aws-sso` | SSO browser flow → writes `AWS_<COMPANY>_*` |
| `jira-token` | Paste API token → writes `JIRA_<COMPANY>_*` |
| `jira-session` | Paste session cookie (workaround) |
| `linear-api-key` | Paste API key → writes `LINEAR_<COMPANY>_API_KEY` |
| `fireflies` | Paste API key → writes `FIREFLIES_<COMPANY>_API_KEY` (used by `meeting-digest` / `meeting-context`) |

## Prerequisites
- A terminal (these flows print to stdout and read stdin / paste / open a browser).
- `--company <name>` for every vendor target (the resulting env-var
  names are namespaced by it).
- For `--store aws-secretsmanager` etc.: the relevant
  backend already initialised with credentials.

## Commands

### Acquire a GitHub PAT for one company

```bash
briar auth login github-pat --company <COMPANY>
# Paste the token at the prompt
```

### Device-flow OAuth (opens a browser)

```bash
briar auth login github-device --company <COMPANY>
# Visits github.com/login/device with the printed user-code
```

### AWS SSO

```bash
briar auth login aws-sso --company <COMPANY>
# Opens the SSO start URL in your browser
```

### Persist into a non-default backend

```bash
briar auth login github-pat --company <COMPANY> --store vault
```

`--store` options: `envfile` (default), `aws-secretsmanager`, `ssm`,
`vault`.

### Refresh an existing bundle (OAuth/SSO only)

```bash
briar auth refresh github-device --company <COMPANY>
```

Paste-based targets (PAT, app-password, Jira token) can't refresh —
re-run `login`.

### See what's been acquired

```bash
briar auth list                          # all targets
briar auth status --company <COMPANY>    # one company
```

### Forget a credential

```bash
briar auth logout github-pat --company <COMPANY>
```

## Verifying success

1. Exit `0`.
2. `briar auth status --company <COMPANY>` shows green for the target.
3. `briar secrets doctor --company <COMPANY>` shows the new env-var
   covered.
4. Whatever downstream command needed the cred now runs (e.g.
   `briar extract --company <COMPANY> --include pr-archaeology`).

## Common failures

| Symptom | Fix |
|---|---|
| `--company is required` | Pass `--company <name>` for any vendor target |
| OAuth device flow times out | Re-run; the device code expires after 15 min |
| `secrets.env` has no perms | The env file requires `0600` owner-readable. Fix with `chmod 600 /etc/briar/secrets.env` |
| Token works in browser, fails in CLI | PAT lacks required scopes. GitHub needs `repo`, `read:org`. Jira tokens are tied to the account that minted them |

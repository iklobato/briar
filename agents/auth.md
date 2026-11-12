# `briar auth`

`briar auth` logs you in to the services briar reads from. It asks you
for a token (or walks you through a browser login), then saves the
result where other briar commands can find it.

It has two subcommands:

| Subcommand | What it does |
|---|---|
| `login <target>` | Get keys for one service and save them. |
| `status [target]` | With a target: which keys it needs, and which are set. Without one: the names of every key that is set. Never the values. |

1.x also had `logout`, `refresh` and `list`. 2.0 removed them (decision
C15): to remove a login, delete its lines from `secrets.env`; to renew
one, run `login` again; `status` with no target lists the names.

## How briar stores keys

briar keeps every key as an environment variable name, such as
`JIRA_ACME_TOKEN`. Most names carry the company name in capital
letters. A `-` in the company name becomes `_`. So `--company acme`
gives `ACME`, and `--company acme-co` gives `ACME_CO`.

A **credential store** is the place where the keys are saved. 2.0 has
one, `envfile`: a plain text file named `secrets.env`, one `NAME=value`
per line, file mode `0600` (only you can read it). It is the only store,
so the 1.x `--cred-store` flag is gone.

1.x also had `aws-secretsmanager`, `ssm` and `vault`. 2.0 removed them.
If `$BRIAR_DEFAULT_STORE` still names one, every command stops (exit
code 1; `--help` and `--version` still work) with this error:

```text
error: credential store 'vault' was removed in briar 2.0; unset BRIAR_DEFAULT_STORE or move the keys to the env file (briar auth login)
```

To move: unset `BRIAR_DEFAULT_STORE`, then run `briar auth login` for each
target again (it saves to the env file), or export the keys in your shell.

The `envfile` store uses the first of these paths that applies:

1. The path in `$BRIAR_SECRETS_FILE`, if you set it.
2. `/etc/briar/secrets.env`, if that file exists (the usual server setup).
3. `$XDG_CONFIG_HOME/briar/secrets.env`, which is `~/.config/briar/secrets.env` on most laptops.

Every briar command loads that file when it starts. A key you already
exported in your shell wins over the same key in the file.

## Targets

A **target** is the service and login style you want. Each target asks
for different things and saves different keys. `ACME` below stands for
your company name.

| Target | What it asks you for | Keys it saves |
|---|---|---|
| `github-device` | Nothing to paste. It shows a short code. You open `github.com/login/device`, type the code and approve. It asks for the scopes `repo` and `read:org`. Needs the env var `BRIAR_GITHUB_CLIENT_ID`: the client ID of a GitHub OAuth App with "Device flow" turned on. | `GITHUB_TOKEN` |
| `github-pat` | A GitHub personal access token (classic) with the scopes `repo` and `read:org`. | `GITHUB_TOKEN` |
| `bitbucket-app-password` | Your Bitbucket workspace slug, your username (not your email) and an app password with Repositories Read and Write, and Pull requests Read and Write. | `BITBUCKET_ACME_WORKSPACE`, `BITBUCKET_ACME_USERNAME`, `BITBUCKET_ACME_APP_PASSWORD` |
| `aws-static` | An IAM access key ID, its secret key, and a default region (press Enter for `us-east-1`). | `AWS_ACME_ACCESS_KEY_ID`, `AWS_ACME_SECRET_ACCESS_KEY`, `AWS_ACME_REGION` |
| `aws-sso` | Your SSO start URL and SSO region (default `us-east-1`). You approve in the browser. Then you pick an account and a role if you have more than one, and a default region. | `AWS_ACME_ACCESS_KEY_ID`, `AWS_ACME_SECRET_ACCESS_KEY`, `AWS_ACME_SESSION_TOKEN`, `AWS_ACME_REGION` |
| `jira-token` | Your Jira URL, your Atlassian account email and an Atlassian API token. | `JIRA_ACME_URL`, `JIRA_ACME_EMAIL`, `JIRA_ACME_TOKEN`, and `JIRA_ACME_AUTH_KIND=token` |
| `jira-session` | Your Jira URL and the value of the `tenant.session.token` browser cookie. Use it when your company does not let you make an API token. | `JIRA_ACME_URL`, `JIRA_ACME_TENANT_SESSION_TOKEN`, and `JIRA_ACME_AUTH_KIND=session` |
| `linear-api-key` | A Linear personal API key. | `LINEAR_ACME_TOKEN` |
| `fireflies` | A Fireflies API key (Settings, then the Developer Settings tab). | `FIREFLIES_ACME_API_KEY` |

The two GitHub targets save one `GITHUB_TOKEN` for the whole machine.
They ignore `--company`. Every other target needs `--company`.

## `briar auth login`

### What it does

Runs the login steps for one target, then saves the keys in the store
you pick. It prints the names it saved, never the values.

### Use it when

- You set up briar on a new machine.
- A token expired and a command now fails with an auth error.
- You rotate the key for one service.

### Examples

```bash
# Paste a GitHub token. One token serves every company.
briar auth login github-pat
```

You get `GITHUB_TOKEN` saved in your `secrets.env` file.

```bash
# Paste a Jira API token for the company "acme".
briar auth login jira-token --company acme
```

You get the four `JIRA_ACME_*` keys in `secrets.env`.

```bash
# Log in to AWS through SSO in the browser.
briar auth login aws-sso --company acme
```

You get short-lived AWS keys for `acme`. briar also prints when they expire.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `target` (positional) | The service and login style. One of the targets in the table above. Required. | none | `jira-token` |
| `--company` | Your company name. briar puts it in the key names. Required for every target except the two GitHub ones. | `$BRIAR_COMPANY`, else `company` in `.briar.toml`, else empty. | `--company acme` |

### Good to know

- Run `login` in a real terminal. It reads your answers from the
  keyboard and hides secret input. For servers with no terminal (CI,
  cron), write the keys into `secrets.env` yourself.
- briar tries to open your browser. Over SSH or in Docker it prints
  the link for you to open instead.
- A missing `--company` stops the login with `<target>: --company is required`.
- `github-device` stops at once if `BRIAR_GITHUB_CLIENT_ID` is not set.
- The device code (GitHub) and the SSO approval (AWS) expire after the
  time the service gives, usually 15 minutes for GitHub. Run `login` again
  if you were too slow.
- `aws-sso` keys are short-lived. Run `login` again when they expire.
- `jira-session` only saves what reads need. Writes (Jira comments and
  transitions) may also need `JIRA_ACME_XSRF_TOKEN`, which you set by hand.
- If some keys fail to save, `login` lists them as `FAIL` with the
  reason and exits with code 1.

## `briar auth status`

### What it does

With a target: shows the keys that target needs for one company, and
marks each one `ok` (set) or `MISS` (missing). With no target: prints the
names of every key that is set (in your shell or in `secrets.env`), never
the values, as 1.x `briar auth list` did.

### Use it when

- A command fails and you want to know which key is missing.
- You want to confirm a `login` worked.

### Examples

```bash
# Are the acme Jira keys all set?
briar auth status jira-token --company acme
```

You get one line per key, marked `ok` or `MISS`. The exit code is 0 when all are set and 1 when any is missing.

```bash
# Is the GitHub token set?
briar auth status github-pat
```

You get one line for `GITHUB_TOKEN`.

```bash
# Names of every key briar can see, then only the acme ones.
briar auth status
briar auth status --company acme
```

You get `store=envfile  N entry(ies):` and one name per line, or
`(no credentials in store=envfile)`.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `target` (positional) | The target to check. Leave it out to list every key name. | none | `fireflies` |
| `--company` | The company to check. Without a target, keeps only the names that carry that company. | `$BRIAR_COMPANY`, else `.briar.toml`, else empty. | `--company acme` |

### Good to know

- `status` only checks that a key is set. It does not test the key
  against the service. An expired token still shows `ok`.
- With no `--company` on a per-company target, briar prints
  `writes nothing` and exits with code 0.
- Without a target, inside a repo with `company = "acme"` in
  `.briar.toml`, the list is filtered to acme. Pass `--company ""` to see
  everything. `GITHUB_TOKEN` has no company in its name, so a company
  filter hides it.

## Run it with Docker

The image runs as the user `briar`. Mount your config folder so the
saved keys stay on your machine, and pass `-it` so you can type.

```bash
docker run --rm -it -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar auth login jira-token --company acme
```

You get the `JIRA_ACME_*` keys in `~/.config/briar/secrets.env` on your
machine. The container cannot open a browser, so briar prints the links.

## Related

- [setup.md](setup.md): `briar doctor` checks your whole setup.

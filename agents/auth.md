# `briar auth`

`briar auth` logs you in to the services briar reads from. It asks you
for a token (or walks you through a browser login), then saves the
result where other briar commands can find it.

It has five subcommands:

| Subcommand | What it does |
|---|---|
| `login <target>` | Get keys for one service and save them. |
| `logout <target>` | Delete the keys that `login` saves for that target. |
| `refresh <target>` | Renew keys without asking again. No target supports it yet. |
| `list` | Show the names of the keys that are set. Never the values. |
| `status <target>` | Show which keys one target needs, and which are set. |

## How briar stores keys

briar keeps every key as an environment variable name, such as
`JIRA_ACME_TOKEN`. Most names carry the company name in capital
letters. A `-` in the company name becomes `_`. So `--company acme`
gives `ACME`, and `--company acme-co` gives `ACME_CO`.

A **credential store** is the place where the keys are saved. You pick
it with `--cred-store`:

| Store | Where the keys go | What you need |
|---|---|---|
| `envfile` (default) | A plain text file named `secrets.env`, one `NAME=value` per line, file mode `0600` (only you can read it). | Nothing. |
| `aws-secretsmanager` | AWS Secrets Manager, one secret per key, named `briar/<NAME>`. | AWS access from the normal AWS chain (role, profile or env vars). |
| `ssm` | AWS SSM Parameter Store, one encrypted parameter per key, named `/briar/<NAME>`. | AWS access from the normal AWS chain. |
| `vault` | HashiCorp Vault, KV v2 mount `secret`, path `briar/<NAME>`, saved as `{value: ...}`. | `VAULT_ADDR` and `VAULT_TOKEN` set, and `pip install 'briar-cli[vault]'`. |

The `envfile` store uses the first of these paths that applies:

1. The path in `$BRIAR_SECRETS_FILE`, if you set it.
2. `/etc/briar/secrets.env`, if that file exists (the usual server setup).
3. `$XDG_CONFIG_HOME/briar/secrets.env`, which is `~/.config/briar/secrets.env` on most laptops.

Every briar command loads that file when it starts. A key you already
exported in your shell wins over the same key in the file.

Only the `envfile` store is loaded at start in 1.1.72. Keys you save to
`aws-secretsmanager`, `ssm` or `vault` are not loaded into other briar
commands by briar itself. You must put them in the environment yourself,
for example from your deploy tool.

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

```bash
# Save a Linear key in AWS SSM Parameter Store instead of the local file.
briar auth login linear-api-key --company acme --cred-store ssm
```

You get the parameter `/briar/LINEAR_ACME_TOKEN` in SSM.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `target` (positional) | The service and login style. One of the targets in the table above. Required. | none | `jira-token` |
| `--company` | Your company name. briar puts it in the key names. Required for every target except the two GitHub ones. | `$BRIAR_COMPANY`, else `company` in `.briar.toml`, else empty. | `--company acme` |
| `--cred-store` | Where to save the keys: `envfile`, `aws-secretsmanager`, `ssm` or `vault`. `--store` still works as an old name for this flag, but prints a note. | `$BRIAR_DEFAULT_STORE` if it names a store, else `envfile`. | `--cred-store vault` |

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

## `briar auth logout`

### What it does

Deletes every key that `login` saves for one target. With `envfile` it
removes the lines from `secrets.env`.

### Use it when

- You want briar to stop using an account.
- You want a clean start before a new `login`.

### Examples

```bash
# Remove the acme Jira keys. briar lists them and asks first.
briar auth logout jira-token --company acme
```

You get a list of the four `JIRA_ACME_*` names and a `proceed?` question.

```bash
# Same, with no question. Use this in scripts.
briar auth logout jira-token --company acme --yes
```

You get `auth-logout: removed 4/4 entries from store=envfile` when all four keys were there.

```bash
# Remove the acme AWS keys that were saved in SSM.
briar auth logout aws-static --company acme --cred-store ssm --yes
```

You get the three `/briar/AWS_ACME_*` parameters deleted from SSM.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `target` (positional) | The target whose keys to delete. Required. | none | `linear-api-key` |
| `--company` | The company whose keys to delete. | `$BRIAR_COMPANY`, else `.briar.toml`, else empty. | `--company acme` |
| `--cred-store` | The store to delete from. `--store` still works as an old name. | `$BRIAR_DEFAULT_STORE`, else `envfile`. | `--cred-store ssm` |
| `--yes` | Skip the `proceed?` question. | off | `--yes` |

### Good to know

- `logout github-pat` (or `github-device`) deletes `GITHUB_TOKEN`. That
  token is shared by every company on the machine.
- Without a terminal, the question counts as "no". briar prints
  `aborted` and exits with code 1. Pass `--yes`.
- With no `--company` on a per-company target, there is nothing to
  delete. briar says so and exits with code 0.

## `briar auth refresh`

### What it does

It is meant to renew keys without a new login. In 1.1.72 no target can
do this. Every target prints `credential expired: ...` with the `login`
command to run, and exits with code 2.

### Use it when

- Not yet. Run `briar auth login` again instead.

### Examples

```bash
# Exits 2 in 1.1.72 and tells you to run login again.
briar auth refresh aws-sso --company acme
```

You get the message `credential expired: aws-sso: refresh not implemented yet`, plus the `login` command to run.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `target` (positional) | The target to renew. Required. | none | `aws-sso` |
| `--company` | The company whose keys to renew. | `$BRIAR_COMPANY`, else `.briar.toml`, else empty. | `--company acme` |
| `--cred-store` | The store to read and write. `--store` still works as an old name. | `$BRIAR_DEFAULT_STORE`, else `envfile`. | `--cred-store envfile` |

## `briar auth list`

### What it does

Prints the names of the keys briar can see in a store. It never prints
values. With `envfile` it lists the known key names that are set in
the environment, from your shell or from `secrets.env`.

### Use it when

- You want to check what is set up on a machine.
- You want to see which companies have keys.

### Examples

```bash
# Names of every key briar knows about.
briar auth list
```

You get `store=envfile  N entry(ies):` and one name per line.

```bash
# Only the keys for acme.
briar auth list --company acme
```

You get only names that contain `_ACME_`, such as `JIRA_ACME_TOKEN`.

```bash
# Every company, even inside a repo whose .briar.toml sets company = "acme".
briar auth list --company ""
```

You get the full list with no company filter.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--company` | Only show names that contain this company (as `_ACME_`). | `$BRIAR_COMPANY`, else `.briar.toml`, else empty (no filter). | `--company acme` |
| `--cred-store` | The store to list. `--store` still works as an old name. | `$BRIAR_DEFAULT_STORE`, else `envfile`. | `--cred-store aws-secretsmanager` |

### Good to know

- Inside a repo with `company = "acme"` in `.briar.toml`, `list` filters
  to acme by default. Pass `--company ""` to see everything.
- `GITHUB_TOKEN` has no company in its name. A company filter hides it.

## `briar auth status`

### What it does

Shows the keys one target needs for one company, and marks each one
`ok` (set) or `MISS` (missing).

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
# Check the acme AWS SSO keys that were saved in Vault.
briar auth status aws-sso --company acme --cred-store vault
```

You get the four `AWS_ACME_*` names, each marked `ok` or `MISS` in Vault.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `target` (positional) | The target to check. Required. | none | `fireflies` |
| `--company` | The company to check. | `$BRIAR_COMPANY`, else `.briar.toml`, else empty. | `--company acme` |
| `--cred-store` | The store to read from. `--store` still works as an old name. | `$BRIAR_DEFAULT_STORE`, else `envfile`. | `--cred-store vault` |

### Good to know

- `status` only checks that a key is set. It does not test the key
  against the service. An expired token still shows `ok`.
- With no `--company` on a per-company target, briar prints
  `writes nothing` and exits with code 0.

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

- [creds.md](creds.md): `briar secrets doctor` shows which keys your runbooks still need.
- [setup.md](setup.md): `briar doctor` checks your whole setup.

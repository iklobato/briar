# `briar auth`

`briar auth` logs you in to the sources briar reads, one login per source
per company. It asks you for what the login needs (a token, an email, a
cookie) and saves it where every other briar command finds it.

It has two subcommands:

| Subcommand | What it does |
|---|---|
| `login <source>` | Ask for the values of one login, check them with the source, and save them. |
| `status [source]` | Check a login live: the source says who it is, and briar reads the repos and projects a run would read. Without a source: every configured login of the company. With no company either: the names of every value that is set. Never the values. |

1.x also had `logout`, `refresh` and `list`. 2.0 removed them (decision
C15): to remove a login, delete its lines from `secrets.env`; to renew
one, run `login` again; `status` with no source lists the names.

## Where things go

A login has two parts, kept apart on purpose:

- **The choices** (which method, which Jira site, which Bitbucket
  workspace) go in the config: `[auth.<source>]` in `.briar.toml`, or
  `companies.<company>.auth` in a runbook. briar reads them; it never
  writes them.
- **The secrets** (tokens, cookies) go in `secrets.env`. `briar auth
  login` writes them there.

```toml
# .briar.toml
company = "acme"

[auth.jira]
site = "acme.atlassian.net"
method = "token"          # the default; "cookie" is the other one

[auth.bitbucket]
workspace = "acme"
method = "access-token"   # "token" (the default), "access-token" or "cookie"
```

In a runbook the same keys go under the company:

```yaml
companies:
  acme:
    auth:
      jira:
        site: acme.atlassian.net
```

`briar extract` reads `[auth]` of `.briar.toml`. `briar runbook` reads only
the `auth:` block of its own file, never a `.briar.toml` next to it. A key
briar does not know stops the command with exit code 1 and names it:

```text
error: `auth.jira.metod` in /repo/.briar.toml: unknown key; allowed: method, site, user_agent
```

Every key of every source is checked each time, even a source the
command does not use.

## Sources

`ACME` below stands for your company name in capital letters, with `-`
as `_` (`--company acme-co` gives `ACME_CO`).

| Source | `method` | What `login` asks for | Saved as | Needs in the config |
|---|---|---|---|---|
| `github` | `token` | A fine-grained personal access token, read access. | `BRIAR_ACME_GITHUB_TOKEN` | nothing (`host` for GitHub Enterprise) |
| `jira` | `token` (default) | Your Atlassian account email and an API token. | `BRIAR_ACME_JIRA_EMAIL`, `BRIAR_ACME_JIRA_TOKEN` | `site` |
| `jira` | `cookie` | The `Cookie` request header of a logged-in browser tab on your Jira site. | `BRIAR_ACME_JIRA_COOKIE` | `site` (and `user_agent` if your Jira checks the browser) |
| `bitbucket` | `token` (default) | Your Atlassian account email and an API token with Bitbucket scopes. | `BRIAR_ACME_BITBUCKET_EMAIL`, `BRIAR_ACME_BITBUCKET_TOKEN` | `workspace` |
| `bitbucket` | `access-token` | A workspace or repository access token (not tied to a person). | `BRIAR_ACME_BITBUCKET_ACCESS_TOKEN` | `workspace` |
| `bitbucket` | `cookie` | The `Cookie` request header of a logged-in browser tab on bitbucket.org. Read only. | `BRIAR_ACME_BITBUCKET_COOKIE` | `workspace` |
| `linear` | `api-key` | A Linear personal API key. | `BRIAR_ACME_LINEAR_API_KEY` | nothing |
| `fireflies` | `api-key` | A Fireflies API key (Settings, then Developer Settings). | `BRIAR_ACME_FIREFLIES_API_KEY` | nothing |
| `aws` | (none) | Nothing: `login` runs `aws sso login --profile P` (or `aws configure --profile P` for a profile with no SSO). | nothing: the AWS files keep it | `profile` (the boto3 default chain when unset) |
| `gcp` | (none) | Nothing: `login` runs `CLOUDSDK_CONFIG=<config_dir> gcloud auth application-default login`. | nothing: the gcloud folder keeps it | `config_dir` (`~/.config/gcloud` when unset) and `project` |
| `azure` | (none) | Nothing: `login` runs `az login --tenant T`. | nothing: `az` keeps it | `tenant` (the default chain when unset) and `subscription` |

AWS, GCP and Azure log in through their own CLI, which keeps the login;
briar never copies a cloud key into `secrets.env`. A run reads them with
the cloud SDK: boto3 with the profile (it renews an SSO token by itself),
google-auth with the ADC file of the folder, azure-identity through `az`
(so `az` must be installed wherever an Azure extract runs). The CLIs are
needed for `login` only, except `az`.

```toml
[auth.aws]
profile = "acme"

[auth.gcp]
config_dir = "~/.config/gcloud-acme"
project = "acme-prod"

[auth.azure]
tenant = "acme.onmicrosoft.com"
subscription = "11111111-2222-3333-4444-555555555555"
```

The Bitbucket cookie method is tested only against a fake server so far:
it is not proven that the Bitbucket API takes a browser cookie.

## Coming from 1.x

2.0 does not read the 1.x names. When a 2.0 value is missing and its 1.x
name is set, the run stops with exit code 1 and says where the value goes
now:

```text
error: `JIRA_ACME_TOKEN` is a 1.x name: use `BRIAR_ACME_JIRA_TOKEN`. Or run `briar auth login jira --company acme`
```

| 1.x | 2.0 |
|---|---|
| `GITHUB_TOKEN` (one for the whole machine) | `BRIAR_ACME_GITHUB_TOKEN`, one per company |
| `JIRA_ACME_EMAIL`, `JIRA_ACME_TOKEN` | `BRIAR_ACME_JIRA_EMAIL`, `BRIAR_ACME_JIRA_TOKEN` |
| `JIRA_ACME_URL` | `site` under `[auth.jira]` |
| `JIRA_ACME_AUTH_KIND` | `method` under `[auth.jira]` |
| `JIRA_ACME_SESSION_TOKEN`, `JIRA_ACME_TENANT_SESSION_TOKEN`, `JIRA_ACME_XSRF_TOKEN` | the whole cookie in `BRIAR_ACME_JIRA_COOKIE`, with `method = "cookie"` |
| `JIRA_ACME_USER_AGENT` | `user_agent` under `[auth.jira]` |
| `BITBUCKET_ACME_USERNAME`, `BITBUCKET_ACME_APP_PASSWORD` | `BRIAR_ACME_BITBUCKET_EMAIL`, `BRIAR_ACME_BITBUCKET_TOKEN` (app passwords are gone: make an Atlassian API token) |
| `BITBUCKET_ACME_WORKSPACE` | `workspace` under `[auth.bitbucket]` |
| `LINEAR_ACME_TOKEN` | `BRIAR_ACME_LINEAR_API_KEY` |
| `FIREFLIES_ACME_API_KEY` | `BRIAR_ACME_FIREFLIES_API_KEY` |
| targets `github-pat`, `github-device` | `github` |
| targets `jira-token`, `jira-session` | `jira` (with `method = "cookie"` for the cookie) |
| target `bitbucket-app-password` | `bitbucket` |
| target `linear-api-key` | `linear` |
| targets `aws-static`, `aws-sso`; `AWS_ACME_ACCESS_KEY_ID`, `AWS_ACME_SECRET_ACCESS_KEY`, `AWS_ACME_SESSION_TOKEN` | an AWS profile named in `profile` under `[auth.aws]` (`briar auth login aws`) |
| `AWS_ACME_REGION` | `region` under `[extract.aws-infra]` |
| `[extract.aws-infra] profile` (the AWS profile, the GCP project or the Azure subscription) | `profile` under `[auth.aws]`, `project` under `[auth.gcp]`, `subscription` under `[auth.azure]` |

A 1.x target name is a usage error (exit code 2) that lists the sources.

## How briar stores the secrets

There is one store, `secrets.env`: a plain text file, one `NAME=value` per
line, file mode `0600` (only you can read it). briar uses the first of
these paths that applies:

1. The path in `$BRIAR_SECRETS_FILE`, if you set it.
2. `/etc/briar/secrets.env`, if that file exists (the usual server setup).
3. `$XDG_CONFIG_HOME/briar/secrets.env`, which is `~/.config/briar/secrets.env` on most laptops.

Every briar command loads that file when it starts. A value you already
exported in your shell wins over the same name in the file, so a server
or a CI job can set `BRIAR_ACME_GITHUB_TOKEN` as an env var instead.

1.x also had `aws-secretsmanager`, `ssm` and `vault` stores. If
`$BRIAR_DEFAULT_STORE` still names one, every command stops (exit code 1;
`--help` and `--version` still work):

```text
error: credential store 'vault' was removed in briar 2.0; unset BRIAR_DEFAULT_STORE or move the keys to the env file (briar auth login)
```

## `briar auth login`

### What it does

Reads the choices of one source from the config, asks for the values its
method needs, asks the source "who am I" with them, and saves them to
`secrets.env` only when the source answers. It prints the account and the
names it saved, never the values. For `aws`, `gcp` and `azure` the check
runs after their CLI login.

### Use it when

- You set up briar on a new machine or for a new company.
- A token expired and a command now fails with an auth error.
- You rotate the key for one source.

### Examples

```bash
# A Jira API token for the company "acme" (needs `site` under [auth.jira]).
briar auth login jira --company acme
```

You get `BRIAR_ACME_JIRA_EMAIL` and `BRIAR_ACME_JIRA_TOKEN` in `secrets.env`.

```bash
# A GitHub token for "acme" and another one for "globex".
briar auth login github --company acme
briar auth login github --company globex
```

Each company gets its own `BRIAR_<COMPANY>_GITHUB_TOKEN`.

```bash
# A server with a runbook and no .briar.toml.
briar auth login jira --company acme --runbook runbooks/acme.yaml
```

The site and the method come from `companies.acme.auth.jira` of that file.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `source` (positional) | One of `github`, `jira`, `bitbucket`, `linear`, `fireflies`, `aws`, `gcp`, `azure`. Required. | none | `jira` |
| `--company` | The company the login is for. briar puts it in the names. | `$BRIAR_COMPANY`, else `company` in `.briar.toml`. | `--company acme` |
| `--runbook` | Read the choices from `companies.<company>.auth` of this runbook instead of `.briar.toml`. | none | `--runbook runbooks/acme.yaml` |

### Good to know

- `aws`, `gcp` and `azure` need their value to log in (`profile`,
  `config_dir`, `tenant`); without it the login stops and says what to add.
  The CLI login runs in your terminal, and briar saves nothing for it.
- A missing `site` (Jira) or `workspace` (Bitbucket) stops the login
  before it asks anything: `error: set `site` under `auth.jira` in
  .briar.toml (briar jira login for acme)`. Add it to the config and run
  `login` again; briar never writes the config.
- A missing company stops it: `error: no company: pass --company or set
  `company` in .briar.toml`.
- Run `login` in a real terminal: it reads your answers from the keyboard
  and hides secret input. Piped answers on stdin work too. For a server
  with no terminal, set the `BRIAR_*` env vars instead.
- `login` checks before it saves: a value the source refuses, or a source
  briar cannot reach, saves nothing and exits with code 1:

  ```text
  error: the login check failed; fix these, then run again:
    - fireflies (acme): the login was refused (HTTP 401: HTTP Error 401: Unauthorized); run `briar auth login fireflies --company acme`
  ```

- The check of `login` is "who am I" only: it does not know which repos a
  run will read. `status` and every run read them.
- If some values fail to save, `login` lists them as `FAIL` with the
  reason and exits with code 1.

## `briar auth status`

### What it does

With a source: checks the login of one company live, the way a run checks
it. The source says who the login is, then briar reads each repo, Jira
project, Linear team, Bitbucket workspace, GCP project or Azure
subscription that `briar extract` would read with your `[extract]` (or
that the tasks of the company read, with `--runbook`). With no source:
the same for every configured login of the company, that is a source with
an `[auth.<source>]` block, a saved value, or a 1.x name. With no source
and no company: the name of every value that is set (in your shell or in
`secrets.env`), never the values.

### Examples

```bash
# Is the acme Jira login good, and can it read the projects of [extract]?
briar auth status jira --company acme
```

You get one line per source, `ok` with the account, `failed` with the fix,
or `unreachable` when briar could not reach the source:

```text
auth-status: company=acme sources=jira
  ok           jira (acme): bot@acme.example
```

The exit code is 1 when a line is `failed`, else 0 (a network error does
not fail it, as in `briar doctor`).

```bash
# Every configured login of acme, then the names of every value briar can see.
briar auth status --company acme
briar auth status --company ""
```

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `source` (positional) | The source to check. Leave it out to check every configured login. | none | `fireflies` |
| `--company` | The company to check. With no source and no company, `status` lists the names. | `$BRIAR_COMPANY`, else `.briar.toml`. | `--company acme` |
| `--runbook` | Read the choices from this runbook instead of `.briar.toml`. | none | `--runbook runbooks/acme.yaml` |

### Good to know

- The calls: GitHub `GET /user` and `GET /repos/<owner>/<repo>`; Jira
  `myself` and `project/<KEY>`; Bitbucket `GET /2.0/user` (not for an
  access token, which belongs to no user), then the workspace and each
  repository; Linear and Fireflies, the user of the key and each Linear
  team; AWS `sts get-caller-identity`; GCP a refresh of the ADC and the
  project; Azure a token for the tenant and the subscription. One call
  after another.
- For `gcp` and `azure` the SDK must be installed (`pip install
  'briar-cli[gcp]'`, `'briar-cli[azure]'`); without it the line says
  what to install.
- With no `[auth.azure] tenant`, Azure tries its whole default chain, and
  a machine where the managed identity address does not answer can wait
  about two minutes before it uses the `az` login.

## Run it with Docker

The image runs as the user `briar`. Mount your config folder so the saved
values stay on your machine, mount the repo so briar finds `.briar.toml`,
and pass `-it` so you can type.

```bash
docker run --rm -it -v "$HOME/.config/briar":/home/briar/.config/briar \
    -v "$PWD":/work -w /work iklob1/briar auth login jira --company acme
```

You get the `BRIAR_ACME_JIRA_*` values in `~/.config/briar/secrets.env` on
your machine.

The image has no `aws`, `gcloud` or `az`. Log in to AWS and GCP outside the
container, then mount `~/.aws` and the gcloud folder so the run reads
them. Azure in Docker is not supported, because the run needs `az`.

## Related

- [setup.md](setup.md): `briar doctor` checks your setup, including every
  configured login of your company, live.

# Logins

briar reads each source (GitHub, Jira, Bitbucket, Linear, Fireflies, AWS, GCP,
Azure) with one login per source per company. You write the logins yourself:
the choices in the config, the secrets in the environment or in `secrets.env`.
`briar doctor` checks them.

1.x had `briar auth login` and `briar auth status`; 2.0 removed them (spec 003
part 4): write the `BRIAR_*` values by hand, and use `briar doctor` (and
`briar doctor --runbooks DIR` for runbooks) to check them.

## Where things go

A login has two parts, kept apart on purpose:

- **The choices** (which method, which Jira site, which Bitbucket
  workspace) go in the config: `[auth.<source>]` in `.briar.toml`, or
  `companies.<company>.auth` in a runbook. briar reads them; it never
  writes them.
- **The secrets** (tokens, cookies) go in the environment or in
  `secrets.env`, under the names in the table below.

```toml
# .briar.toml
company = "acme"

[auth.jira]
site = "acme.atlassian.net"
method = "token"          # the default; "cookie" is the other one

[auth.bitbucket]
workspace = "acme"
method = "access-token"   # "token" (the default), "access-token" or "cookie"

[auth.gitlab]
url = "https://gitlab.acme.test"   # leave it out for gitlab.com
```

In a runbook the same keys go under the company:

```yaml
companies:
  acme:
    auth:
      jira:
        site: acme.atlassian.net
```

`briar extract` reads `[auth]` of `.briar.toml`. `briar serve` reads only the
`auth:` block of each runbook, never a `.briar.toml` next to it. A key briar
does not know stops the command with exit code 1 and names it:

```text
error: `auth.jira.metod` in /repo/.briar.toml: unknown key; allowed: method, site, user_agent
```

Every key of every source is checked each time, even a source the command
does not use.

## Sources

`ACME` below stands for your company name in capital letters, with `-` as `_`
(company `acme-co` gives `ACME_CO`).

| Source | `method` | What you write | Under the names | Needs in the config |
|---|---|---|---|---|
| `github` | `token` | A fine-grained personal access token: read access for `briar extract`; read and write on Contents, Pull requests and Issues (and read on Actions) for `briar serve`. | `BRIAR_ACME_GITHUB_TOKEN` | nothing (`host` for GitHub Enterprise) |
| `jira` | `token` (default) | Your Atlassian account email and an API token. | `BRIAR_ACME_JIRA_EMAIL`, `BRIAR_ACME_JIRA_TOKEN` | `site` |
| `jira` | `cookie` | The `Cookie` request header of a logged-in browser tab on your Jira site. | `BRIAR_ACME_JIRA_COOKIE` | `site` (and `user_agent` if your Jira checks the browser) |
| `bitbucket` | `token` (default) | Your Atlassian account email and an API token with Bitbucket scopes. | `BRIAR_ACME_BITBUCKET_EMAIL`, `BRIAR_ACME_BITBUCKET_TOKEN` | `workspace` |
| `bitbucket` | `access-token` | A workspace or repository access token (not tied to a person). | `BRIAR_ACME_BITBUCKET_ACCESS_TOKEN` | `workspace` |
| `bitbucket` | `cookie` | The `Cookie` request header of a logged-in browser tab on bitbucket.org. Read only. | `BRIAR_ACME_BITBUCKET_COOKIE` | `workspace` |
| `gitlab` | `token` (default) | A personal, group or project access token. `read_api` is enough for `briar extract`. | `BRIAR_ACME_GITLAB_TOKEN` | `url` (`https://gitlab.com` when unset; the origin of a self-hosted GitLab) |
| `linear` | `api-key` | A Linear personal API key. | `BRIAR_ACME_LINEAR_API_KEY` | nothing |
| `fireflies` | `api-key` | A Fireflies API key (Settings, then Developer Settings). | `BRIAR_ACME_FIREFLIES_API_KEY` | nothing |
| `slack` | `token` (default) | A user token (`xoxp-`) of a Slack app with the user scope `search:read`. Used by `briar serve` only. | `BRIAR_ACME_SLACK_TOKEN` | `workspace` (the `acme` of acme.slack.com) |
| `slack` | `session` | The `xoxc-` token of a logged-in browser tab and the value of its `d` cookie. It expires with the browser session. | `BRIAR_ACME_SLACK_TOKEN`, `BRIAR_ACME_SLACK_COOKIE` | `workspace` |
| `aws` | (none) | Nothing: run `aws sso login --profile P` (or `aws configure --profile P` for a profile with no SSO). | nothing: the AWS files keep it | `profile` (the boto3 default chain when unset) |
| `gcp` | (none) | Nothing: run `CLOUDSDK_CONFIG=<config_dir> gcloud auth application-default login`. | nothing: the gcloud folder keeps it | `config_dir` (`~/.config/gcloud` when unset) and `project` |
| `azure` | (none) | Nothing: run `az login --tenant T`. | nothing: `az` keeps it | `tenant` (the default chain when unset) and `subscription` |

AWS, GCP and Azure log in through their own CLI, which keeps the login; briar
never copies a cloud key into `secrets.env`. A run reads them with the cloud
SDK: boto3 with the profile (it renews an SSO token by itself), google-auth with
the ADC file of the folder, azure-identity through `az` (so `az` must be
installed wherever an Azure extract runs).

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

The Bitbucket cookie method is tested only against a fake server so far: it is
not proven that the Bitbucket API takes a browser cookie.

Slack takes no bot token (`xoxb-`): `briar serve` reads Slack through search,
which only a user or a session token can call. The check refuses a bot token
and a token of another workspace than `workspace`.

## The secrets file

`secrets.env` is a plain text file, one `NAME=value` per line. Make it readable
by you only (`chmod 600`). briar uses the first of these paths that applies:

1. The path in `$BRIAR_SECRETS_FILE`, if you set it.
2. `/etc/briar/secrets.env`, if that file exists (the usual server setup).
3. `$XDG_CONFIG_HOME/briar/secrets.env`, which is `~/.config/briar/secrets.env`
   on most laptops.

```bash
mkdir -p ~/.config/briar
printf 'BRIAR_ACME_GITHUB_TOKEN=%s\n' "<your token>" >> ~/.config/briar/secrets.env
chmod 600 ~/.config/briar/secrets.env
```

Every briar command loads that file when it starts. A value you already
exported in your shell wins over the same name in the file, so a server or a
CI job can set `BRIAR_ACME_GITHUB_TOKEN` as an env var instead.

1.x also had `aws-secretsmanager`, `ssm` and `vault` stores. If
`$BRIAR_DEFAULT_STORE` still names one, every command stops (exit code 1;
`--help` and `--version` still work):

```text
error: credential store 'vault' was removed in briar 2.0; unset BRIAR_DEFAULT_STORE or move the keys to secrets.env
```

## The check before a run

Every run checks the logins it needs before it reads anything: each source
says who the login is, then briar reads each repo, Jira project, Linear team,
Bitbucket workspace, GCP project or Azure subscription the run will read. One
failure stops the run with exit code 1 and lists every failure with its fix:

```text
error: the login check failed; fix these, then run again:
  - github (acme): no login saved (BRIAR_ACME_GITHUB_TOKEN); set BRIAR_ACME_GITHUB_TOKEN in the environment or in /home/me/.config/briar/secrets.env (Create a fine-grained token at https://github.com/settings/personal-access-tokens/new: read access, and read and write on Contents, Pull requests and Issues for `briar serve`)
```

To check without a run:

- `briar doctor`: every configured login of the company of `.briar.toml`.
- `briar doctor --runbooks DIR`: what `briar serve DIR` checks before its first
  cycle (the logins of the scheduled extracts, and the read and write logins of
  each company with a `serve:` block). See [serve.md](serve.md).

The calls: GitHub `GET /user` and `GET /repos/<owner>/<repo>`; Jira `myself` and
`project/<KEY>`; Bitbucket `GET /2.0/user` (not for an access token, which
belongs to no user), then the workspace and each repository; Linear and
Fireflies, the user of the key and each Linear team; AWS
`sts get-caller-identity`; GCP a refresh of the ADC and the project; Azure a
token for the tenant and the subscription. For `gcp` and `azure` the SDK must
be installed (`pip install 'briar-cli[gcp]'`, `'briar-cli[azure]'`).

## Coming from 1.x

2.0 does not read the 1.x names. When a 2.0 value is missing and its 1.x name is
set, the run stops with exit code 1 and says where the value goes now:

```text
error: `JIRA_ACME_TOKEN` is a 1.x name: use `BRIAR_ACME_JIRA_TOKEN`
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
| `AWS_ACME_ACCESS_KEY_ID`, `AWS_ACME_SECRET_ACCESS_KEY`, `AWS_ACME_SESSION_TOKEN` | an AWS profile named in `profile` under `[auth.aws]` |
| `AWS_ACME_REGION` | `region` under `[extract.aws-infra]` |
| `[extract.aws-infra] profile` (the AWS profile, the GCP project or the Azure subscription) | `profile` under `[auth.aws]`, `project` under `[auth.gcp]`, `subscription` under `[auth.azure]` |
| `briar auth login <source>` | write the names of the table above in `secrets.env` |
| `briar auth status` | `briar doctor`, or `briar doctor --runbooks DIR` |

## Run it with Docker

Pass the secrets file to the container, and mount the repo so briar finds
`.briar.toml`:

```bash
docker run --rm --env-file "$HOME/.config/briar/secrets.env" \
    -v "$PWD":/work -w /work iklob1/briar doctor
```

The image has no `aws`, `gcloud` or `az`. Log in to AWS and GCP outside the
container, then mount `~/.aws` and the gcloud folder so the run reads them.
Azure in Docker is not supported, because the run needs `az`.

## Related

- [setup.md](setup.md): `briar doctor` checks your setup, including every
  configured login of your company, live.
- [serve.md](serve.md) and [runbooks.md](runbooks.md): the logins of a runbook.

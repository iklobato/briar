# Reference: commands, settings and logins

Looking for a lesson? Start at the [guide](README.md).

## Commands

briar has 4 commands. Each one has a manual in
[`agents/`](../../agents/README.md).

| Command | What it does | Manual |
|---|---|---|
| `briar extract` | Runs extractors and saves what they find in one knowledge file. | [extract.md](../../agents/extract.md) |
| `briar serve` | Works your assigned tickets from the runbooks in a folder, and runs their scheduled extracts. | [serve.md](../../agents/serve.md), [runbooks.md](../../agents/runbooks.md) |
| `briar init` | Writes a starter `.briar.toml` and an example runbook in your repo. | [setup.md](../../agents/setup.md) |
| `briar doctor` | Shows each setting and where it came from, then checks your setup (and a folder of runbooks with `--runbooks`). | [setup.md](../../agents/setup.md) |

1.x also had `briar auth` and `briar runbook`. 2.0 removed them: write the
logins by hand (below), and use `briar serve DIR` for the schedules.

Recipes that chain these commands are in
[flows.md](../../agents/flows.md).

Three flags work with every command, anywhere on the line:

| Flag | What it does |
|---|---|
| `--format table\|json` | Output shape, for `doctor`. |
| `--verbose`, `-v` | Debug logs on stderr. |
| `--version`, `-V` | Print the version and exit. |

### Settings in `.briar.toml`

`briar extract` takes only three flags: `--company`, `--include` and
`--repo`. Everything else goes in `.briar.toml`, the settings file that
`briar init` writes at the top of your repo:

```toml
company = "acme"

[repo]
owner = "acme-co"
repo = "acme-app"
provider = "github"

# For every extractor
[extract]
max = 50
since_days = 30

# For one extractor
[extract.active-tickets]
tracker = "jira"
projects = ["ACME"]

# Login choices; the secrets go in secrets.env (see Logins below)
[auth.jira]
site = "acme.atlassian.net"
```

A value for one extractor wins over the value in `[extract]`. A key briar
does not know stops the run and says what to fix. `briar extract -h` lists
every key with its default, and `briar doctor` shows which settings briar
found and where.

### Logins

You write the secrets yourself, in the environment or in
`~/.config/briar/secrets.env` (`/etc/briar/secrets.env` on a server, or the
path in `$BRIAR_SECRETS_FILE`), one `NAME=value` per line, readable by you
only. `ACME` is your company name in capital letters:

| Source | Names | Also in the config |
|---|---|---|
| GitHub | `BRIAR_ACME_GITHUB_TOKEN` (a fine-grained token; `briar serve` needs read and write on Contents, Pull requests and Issues) | nothing |
| GitLab | `BRIAR_ACME_GITLAB_TOKEN` (`briar serve` needs the `api` scope) | nothing |
| Jira | `BRIAR_ACME_JIRA_EMAIL`, `BRIAR_ACME_JIRA_TOKEN` (or `BRIAR_ACME_JIRA_COOKIE`) | `site` under `[auth.jira]` |
| Bitbucket | `BRIAR_ACME_BITBUCKET_EMAIL`, `BRIAR_ACME_BITBUCKET_TOKEN` (or an access token, or a cookie) | `workspace` under `[auth.bitbucket]` |
| Linear | `BRIAR_ACME_LINEAR_API_KEY` | nothing |
| Fireflies | `BRIAR_ACME_FIREFLIES_API_KEY` | nothing |
| AWS, GCP, Azure | nothing: log in with their own CLI | `profile`, `config_dir` and `project`, `tenant` and `subscription` |
| The `briar serve` agent | `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` | the `serve:` block of the runbook |

`briar doctor` checks them. Every method and the 1.x names are in
[logins.md](../../agents/logins.md).

## Telemetry

briar sends nothing by default. To help fix bugs, turn on error reports
with `telemetry = "errors-only"` in `.briar.toml` (or
`BRIAR_TELEMETRY=errors-only`), or error reports and usage counts with
`full`. `DO_NOT_TRACK=1` turns it off whatever else says. `briar doctor`
shows the current choice and where it came from. Even when it is on, no
prompts, file contents, ticket keys, repo names, paths or secret values
leave your machine. 1.x sent by default; 2.0 ignores the choice 1.x saved,
so turn it on again if you want it.

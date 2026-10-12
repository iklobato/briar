# briar

briar is a command-line tool that runs on your own machine. It does two
things:

1. It reads what is going on in your team's tools (GitHub or Bitbucket,
   Jira or Linear, AWS, Fireflies) and saves it as markdown "knowledge".
2. It runs AI coding agents that use that knowledge. One agent takes a
   ticket and opens a draft PR. Another fixes the review comments on a PR.

There is no briar server and no account. briar talks only to the services
you set up: your code host, your tracker, your cloud, and your LLM
provider. Your credentials stay in a file on your machine.

## Install

```bash
pip install briar-cli
briar version
```

You get `briar-cli 1.1.75` (or newer). briar needs Python 3.10 or later.

The base install covers GitHub, Bitbucket, AWS, Jira, Linear, Fireflies,
Anthropic (Claude) and AWS Bedrock, with a file or Postgres store. It
also runs the coding agents. Extras add more:

```bash
pip install 'briar-cli[mcp]'      # briar mcp serve, briar chat, MCP tools for agents
pip install 'briar-cli[all]'      # everything below
```

The extras are `[openai]`, `[gemini]`, `[mcp]`, `[gcp]`, `[azure]` and
`[vault]`.

To run `briar agent` or `briar plan run` you also need `git` and the
GitHub CLI `gh`, because the agent runs them.

The `[mcp]` extra adds three things:

- `briar mcp serve` and `briar chat`. They do not start without it.
- The tool servers listed under `mcp:` in a runbook. An agent run with
  such a runbook stops without the extra.
- Two small helper tools for agents, "think" and "time". Without the
  extra, briar skips them, logs one warning and goes on.

Since 1.1.74 a plain install runs agents. Older versions stopped with
``MCP support requires the `mcp` package`` unless you passed
`--no-default-mcp`.

## Quickstart

```bash
# 1. Save a GitHub token and Jira login. They go to ~/.config/briar/secrets.env
briar auth login github-pat --company acme
briar auth login jira-token --company acme

# 2. Give briar a Claude key. It reads it from your shell.
export ANTHROPIC_API_KEY=sk-ant-your-key

# 3. Read the repo's recent PRs and its tools into the blob knowledge:acme
briar extract --company acme --repo acme-co/acme-app \
    --include pr-archaeology --include codebase-conventions --max 50

# 4. Read what was saved
briar context get knowledge:acme

# 5. Print the agent's prompt for ticket ACME-42. No cost, nothing pushed.
briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ACME-42 --tracker jira --dry-run

# 6. Run it for real: clone, branch, code, test, open a draft PR
briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ACME-42 --tracker jira
```

After step 3 you have `./knowledge/knowledge/acme.md`. After step 6 you
have a branch `chore/acme-42` and a draft PR on `acme-co/acme-app`.

## Less typing with `.briar.toml`

Every setting is looked up in this order: the flag you type, then an
environment variable, then `.briar.toml`, then the built-in default. The
repo owner and name can also come from the git `origin` remote.

Write a starter file in your repo:

```bash
briar init --company acme
```

You get a `.briar.toml` with the company, the store, and the repo taken
from git. The `[agent]` part comes commented out. Uncomment it to set
agent defaults:

```toml
company = "acme"
store   = "file"

[repo]
owner    = "acme-co"
repo     = "acme-app"
provider = "github"

[agent]
model          = "claude-sonnet-4-6"
git_user_name  = "Acme Bot"
git_user_email = "bot@acme.com"
```

Inside that repo the commands get short:

```bash
briar extract --include pr-archaeology
briar agent implement --ticket-key ACME-42
```

`briar config show` prints each setting and where it came from.
`briar doctor` checks the config, git, credentials and store.
`eval "$(briar completion bash)"` turns on tab completion (`zsh` works too).
See [setup.md](https://github.com/iklobato/briar/blob/main/agents/setup.md).

## Run with Docker

The image `iklob1/briar` has briar, the `[mcp]` extra, `git` and `gh`.
Nothing else to install:

```bash
docker run --rm iklob1/briar version
```

To work on a repo, mount it and your briar config folder:

```bash
docker run --rm \
    -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    -e ANTHROPIC_API_KEY \
    iklob1/briar extract --company acme --include pr-archaeology
```

- `-v "$PWD":/work -w /work` mounts your repo, so `.briar.toml` and
  `./knowledge` work as on your machine.
- `-v "$HOME/.config/briar":/home/briar/.config/briar` mounts the
  credentials that `briar auth login` saved.
- `-e ANTHROPIC_API_KEY` passes a variable from your shell. Repeat `-e`
  for others. Saved credentials need no `-e`.

Some commands need more:

| Command | Add |
|---|---|
| `auth login` | `-it`, because it asks you to paste a token. |
| `dashboard` | `-p 8080:8080`, and `--host 0.0.0.0` at the end. |
| `mcp serve` over stdio | `-i`, so the MCP client can talk to it. |

```bash
docker run --rm -it -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar auth login github-pat --company acme

docker run --rm -p 8080:8080 -v "$PWD":/work -w /work \
    iklob1/briar dashboard --host 0.0.0.0
```

### Run an agent in Docker

Since 1.1.75 the image can run `briar agent` and `briar plan run`. Add
two keys and your git name and email:

```bash
docker run --rm \
    -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    -e GITHUB_TOKEN -e ANTHROPIC_API_KEY \
    iklob1/briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ACME-42 --tracker jira \
    --git-user-name "Dana Lee" --git-user-email dana@acme.com
```

It runs the same steps as on your machine: a branch `chore/acme-42` and a
draft PR on `acme-co/acme-app`. Add `--dry-run` to see the prompt first.

- `--git-user-name` and `--git-user-email` give the commits your name and
  email. You can mount `~/.gitconfig` instead, read-only:
  `-v "$HOME/.gitconfig":/home/briar/.gitconfig:ro`.
- `-e GITHUB_TOKEN` lets `git push` and `gh` log in to GitHub. In the
  image, git asks `gh` for the login, and `gh` reads `GITHUB_TOKEN`.
- `-e ANTHROPIC_API_KEY` is the Claude key. `-e CLAUDE_CODE_OAUTH_TOKEN`
  works too.
- The Jira keys come from the mounted config folder.
- `--repo` can also come from `[repo]` in the mounted `.briar.toml`.

Good to know:

- A `~/.gitconfig` from a Mac works. `gh auth setup-git` writes the
  path of `gh` on your machine into it (`/usr/local/bin/gh` or
  `/opt/homebrew/bin/gh`). Since 1.1.79 the image has `gh` at both
  paths. Before that, the push failed with "gh: not found".
- briar can also read owner and repo from the mounted repo's git
  `origin` remote. Since 1.1.79 this works whoever owns the folder.
  Before that, on Linux, git refused a folder not owned by uid 1000
  ("detected dubious ownership"), and you had to pass `--repo`.
- The image sets up the git login for GitHub only. For Bitbucket, run
  agents from a local install.
- The image sets `BRIAR_NO_DEFAULT_MCP=1`, because it has no `npx` or
  `uvx` for the agent's helper tools. An image of your own that adds
  them can pass `-e BRIAR_NO_DEFAULT_MCP=0`.
- The image is built for `linux/amd64` only. On Apple Silicon, Docker
  runs it with emulation.
- The container runs as user `briar` (uid 1000). On Linux, the folders
  you mount must be writable by that uid.
- Tags are `latest` and each version, for example `iklob1/briar:1.1.75`.

## Commands

Each command has a manual in [`agents/`](https://github.com/iklobato/briar/blob/main/agents/README.md).
The same reference, with more examples, is at
[usebriar.com/docs](https://usebriar.com/docs).

| Command | What it does | Manual |
|---|---|---|
| `briar extract` | Reads your tools into a knowledge blob (23 extractors). | [extract.md](https://github.com/iklobato/briar/blob/main/agents/extract.md) |
| `briar agent implement` | Takes one ticket and opens a draft PR. | [agent.md](https://github.com/iklobato/briar/blob/main/agents/agent.md) |
| `briar agent prfix` | Fixes and answers the review comments on one PR. | [agent.md](https://github.com/iklobato/briar/blob/main/agents/agent.md) |
| `briar plan` | Turns a Jira or GitHub Projects board into a plan, then runs it card by card. | [plan.md](https://github.com/iklobato/briar/blob/main/agents/plan.md) |
| `briar runbook` | Runs extractors from a YAML file, once or on a schedule. | [runbook.md](https://github.com/iklobato/briar/blob/main/agents/runbook.md) |
| `briar context` | Reads, writes, lists and deletes knowledge blobs. | [context.md](https://github.com/iklobato/briar/blob/main/agents/context.md) |
| `briar scaffold` | Writes a JSON config bundle for an agent workflow. | [scaffold.md](https://github.com/iklobato/briar/blob/main/agents/scaffold.md) |
| `briar auth` | Saves credentials (`login`, `logout`, `list`, `status`, `refresh`). | [auth.md](https://github.com/iklobato/briar/blob/main/agents/auth.md) |
| `briar secrets` | Shows which credentials are missing (`doctor`, `bootstrap`). | [creds.md](https://github.com/iklobato/briar/blob/main/agents/creds.md) |
| `briar journal` | Shows the decisions that `plan run` and `scaffold` recorded. | [journal.md](https://github.com/iklobato/briar/blob/main/agents/journal.md) |
| `briar dashboard` | Serves a read-only status page. | [dashboard.md](https://github.com/iklobato/briar/blob/main/agents/dashboard.md) |
| `briar mcp serve`, `briar chat` | Serves briar as MCP tools, or drives them from a chat in your terminal. | [mcp.md](https://github.com/iklobato/briar/blob/main/agents/mcp.md) |
| `briar init`, `config show`, `doctor`, `completion` | Setup helpers. | [setup.md](https://github.com/iklobato/briar/blob/main/agents/setup.md) |
| `briar telemetry` | Shows or changes what usage data is sent. | [telemetry.md](https://github.com/iklobato/briar/blob/main/agents/telemetry.md) |
| `briar version` | Prints the version. | [version.md](https://github.com/iklobato/briar/blob/main/agents/version.md) |

Recipes that chain these commands are in
[flows.md](https://github.com/iklobato/briar/blob/main/agents/flows.md). A
runbook that uses every feature is
[examples/all_features.yaml](https://github.com/iklobato/briar/blob/main/examples/all_features.yaml).

Three flags work with every command, anywhere on the line:

| Flag | What it does |
|---|---|
| `--format table\|json\|yaml\|csv\|quiet` | Output shape. Lists default to `table`, single records to `json`. |
| `--verbose`, `-v` | Debug logs on stderr. `BRIAR_VERBOSE=1` does the same. |
| `--version`, `-V` | Print the version and exit. |

## Telemetry

briar sends error reports and usage counts by default. No prompts, file
contents, ticket keys, repo names, paths or secret values leave your
machine. Turn it off with `briar telemetry off`, `BRIAR_TELEMETRY=off`
or `DO_NOT_TRACK=1`.

## License

MIT. See [LICENSE](https://github.com/iklobato/briar/blob/main/LICENSE).

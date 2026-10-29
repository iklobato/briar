<div align="center">

# briar

**The local CLI that turns your team's tools into knowledge, then runs
coding agents that open draft PRs with it.**

[![PyPI](https://img.shields.io/pypi/v/briar-cli)](https://pypi.org/project/briar-cli/)
[![Python](https://img.shields.io/pypi/pyversions/briar-cli)](https://pypi.org/project/briar-cli/)
[![Downloads](https://img.shields.io/pypi/dm/briar-cli)](https://pypi.org/project/briar-cli/)
[![Docker pulls](https://img.shields.io/docker/pulls/iklob1/briar)](https://hub.docker.com/r/iklob1/briar)
[![Tests](https://github.com/iklobato/briar/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/iklobato/briar/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/github/license/iklobato/briar)](https://github.com/iklobato/briar/blob/main/LICENSE)

[Install](#install) · [Quickstart](#quickstart) · [Docs](https://usebriar.com/docs) · [Website](https://usebriar.com) · [Changelog](https://github.com/iklobato/briar/blob/main/CHANGELOG.md)

<img src="https://raw.githubusercontent.com/iklobato/briar/main/docs/demo/02-extract.gif" alt="briar extract reads the merged PRs and releases of iklobato/briar into one markdown blob, then briar context get prints it" width="800">

New to briar? [Learn it step by step](#learn-briar-step-by-step), from install to agents.

</div>

briar is a command-line tool that runs on your own machine. It does two
things:

1. It reads what is going on in your team's tools (GitHub or Bitbucket,
   Jira or Linear, AWS, Fireflies) and saves it as markdown "knowledge".
2. It runs AI coding agents that use that knowledge. One agent takes a
   ticket and opens a draft PR. Another fixes the review comments on a PR.

There is no briar server and no account. briar talks only to the services
you set up: your code host, your tracker, your cloud, and your LLM
provider. Your credentials stay in a file on your machine.

## What you get

- **Knowledge from your tools.** 23 extractors read PR history,
  reviewers, code hotspots, CI health, tickets, cloud resources and
  meeting notes from GitHub, Bitbucket, Jira, Linear, AWS, GCP, Azure and
  Fireflies. They write plain markdown to a folder or to Postgres, once or
  on a schedule.
- **Agents that read it first.** `briar agent implement` takes a ticket,
  reads the knowledge its job needs, codes in a clean git worktree, runs
  your tests and opens a draft PR. `briar agent prfix` answers each review
  comment with its own commit. It never force-pushes, amends or rebases.
- **A whole board, card by card.** `briar plan` turns a Jira board or a
  GitHub Project into a plan and runs it. When a card's dependency is done,
  the card starts from that card's branch, so the PRs stack.
- **You keep the merge.** Agents open draft PRs. briar has no merge
  command.
- **Works with your other tools.** `--merge-claude-md` puts the knowledge
  into `CLAUDE.md` for Claude Code, and `briar mcp serve` gives any MCP
  client 10 tools.
- **Local, no account.** Keys stay in a file on your machine, or in AWS
  Secrets Manager, SSM or Vault.

## Where the knowledge comes from, and where the work goes

![briar reads code, tickets, cloud, meetings and Slack into a local knowledge store; agents read that knowledge plus live ticket context, work in a clean git worktree, and open draft PRs that you review and merge.](https://raw.githubusercontent.com/iklobato/briar/main/docs/diagrams/under-the-hood.svg)

Everything in the dashed box runs on your machine and reads from your
tools. Agents push branches, open draft PRs and reply on reviews; their
`send_message` tool only reaches the channels you list in the runbook.

1. Extractors read your tools, on demand or on a schedule, and write
   markdown knowledge to a local folder or to Postgres.
2. You point an agent at a ticket or a PR. It fetches the ticket, the PR's
   review threads, and matching meeting notes and Slack messages live.
3. In a clean git worktree it first reads the knowledge sections that fit
   its job (conventions, hotspots, reviewers, PR history), writes the code
   on a new branch and runs your tests. Coding agents run on Anthropic with
   your own key.
4. It pushes and opens a draft PR. If you allow those channels in the
   runbook, it can also comment on the ticket or post in Slack or Telegram.
5. You review. `briar agent prfix` answers the comments with one commit per
   thread. You merge; briar never does.
6. A plan run writes what each card taught into the plan's knowledge, which
   its selector reads before the next pick. All knowledge also feeds Claude
   Code (`CLAUDE.md`) and any MCP client.

More diagrams are in [See it run](#see-it-run).

## Install

```bash
pip install briar-cli
briar version
```

briar needs Python 3.10 or later. To run `briar agent` or `briar plan run`
you also need `git` and the GitHub CLI `gh`, because the agent runs them.

The base install covers GitHub, Bitbucket, AWS, Jira, Linear, Fireflies,
Anthropic (Claude) and AWS Bedrock, with a file or Postgres store, and
runs the coding agents. Extras add more:

```bash
pip install 'briar-cli[mcp]'      # briar mcp serve, briar chat, MCP tools for agents
pip install 'briar-cli[all]'      # openai, gemini, mcp, gcp, azure, vault
```

Or skip the install and use the Docker image, which also has `git` and
`gh`:

```bash
docker run --rm iklob1/briar version
```

Mounts, keys and running agents in Docker:
[usebriar.com/docs/install#docker](https://usebriar.com/docs/install#docker).
What each extra adds:
[usebriar.com/docs/install#extras](https://usebriar.com/docs/install#extras).

## Quickstart

The short version, for a GitHub repo with Jira tickets. New to briar?
[Learn it step by step](#learn-briar-step-by-step) instead.

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

## Learn briar step by step

Eight steps, from nothing to agents and a scheduler. Each step says the
problem it solves, gives the commands to copy, shows a real run, and says
what you get. The runs were recorded in a clone of this repo on briar
1.1.99: the commands really ran; only the typing is paced, and a long
output is shown one screen at a time so you can read it.

**Before you start** (once, about 2 minutes):

```bash
pip install briar-cli                  # needs Python 3.10 or later
cd path/to/your-repo                   # any GitHub repo you can read
export GITHUB_TOKEN=$(gh auth token)   # or: briar auth login github-pat --company myteam
```

That GitHub token is all the steps below need. Nothing is sent to an AI
model, so they cost nothing. Only when you drop `--dry-run` in steps 5
and 7, so the agent really works, do you also need a Claude key
(`export ANTHROPIC_API_KEY=...`).

### 1. Set up your repo once

**The problem:** every briar command needs to know your team and your
repo, and you want to know your keys work before anything runs.

```bash
briar init --company myteam   # writes .briar.toml: your team name + the repo from your git remote
briar doctor                  # checks Python, the config, the git remote, your keys and the store
briar config show             # shows each setting and where it came from
```

![briar init writes .briar.toml, briar doctor shows every check as ok, briar config show lists each setting and its source](https://raw.githubusercontent.com/iklobato/briar/main/docs/demo/01-setup.gif)

**You get:** a `.briar.toml` file. From now on no command needs
`--company` or `--repo`; a flag you type still wins over the file. Every
key is listed in
[the configuration docs](https://usebriar.com/docs/configuration#project-config).

### 2. Teach briar how your repo works

**The problem:** an AI agent, or a new teammate, starts with no idea how
your team works: how fast PRs get merged, who writes them, how often you
ship.

```bash
briar extract --include pr-archaeology --include release-cadence
briar context get knowledge:myteam
```

![briar extract reads the merged PRs and releases into one blob, briar context get prints it](https://raw.githubusercontent.com/iklobato/briar/main/docs/demo/02-extract.gif)

**You get:** one markdown file of facts, called a blob, named
`knowledge:myteam` and kept under `./knowledge/`. Every briar agent reads
it before it works. Each `--include` picks one extractor. Good first
picks:

| Extractor | What it finds |
|---|---|
| `pr-archaeology` | Merged PRs: how long they take to merge, who writes them. |
| `release-cadence` | How often you release, and the latest release. |
| `ci-health` | CI pass rate, flaky workflows, how long runs take. |
| `code-hotspots` | The files that change most, and the files that change with them. |
| `codebase-conventions` | Language, test runner, linter and formatter. |
| `stale-prs` | Open PRs nobody touched for a while. |

`briar extract -h` lists all 23. `briar extract` with no `--include` runs
every one of them; on this repo that took about 4 minutes.

### 3. Add more facts later

**The problem:** you want one more fact without reading everything again.

```bash
briar extract --include ci-health
briar context get knowledge:myteam
```

![briar extract --include ci-health, then the blob shows PR archaeology, release cadence and CI health](https://raw.githubusercontent.com/iklobato/briar/main/docs/demo/03-add-knowledge.gif)

**You get:** a CI section in the same blob. The two sections from step 2
stay: a run only replaces the sections it ran.

### 4. Give Claude Code the same knowledge

**The problem:** Claude Code starts every session knowing nothing about
your team.

```bash
briar extract --include codebase-conventions --merge-claude-md
```

![briar extract --merge-claude-md, then CLAUDE.md shows the briar-knowledge block](https://raw.githubusercontent.com/iklobato/briar/main/docs/demo/04-claude-md.gif)

**You get:** a short block in `CLAUDE.md`, between two markers, that
tells Claude Code to read `.briar/knowledge/myteam.md`, where the full
text is. Claude Code reads `CLAUDE.md` when it starts. Your own text in
`CLAUDE.md` outside the markers stays. The block lists only the topics of
this run, so put every extractor you want Claude Code to see in the same
command.

### 5. Turn a ticket into a draft PR

**The problem:** before an agent can fix a ticket, someone has to collect
the ticket, the repo's rules and the team's habits into one prompt, and
make sure the agent cannot push anything risky.

```bash
briar agent implement --ticket-key 38 --tracker github-issues --dry-run
```

![briar agent implement --dry-run prints the steps, the strict rules, the tools and the ticket context for issue #38](https://raw.githubusercontent.com/iklobato/briar/main/docs/demo/05-agent-dry-run.gif)

**You get:** with `--dry-run`, the whole prompt briar would send: the
agent's steps (new branch, run the tests, open a draft PR), its rules
(never force-push, amend or rebase), your saved knowledge, the issue
text, and the tools it may use. No clone, no model call, nothing pushed.
Drop `--dry-run` and the agent writes the code on a new branch, runs your
tests and opens a draft PR. You review and merge; briar never merges.
Jira and Linear tickets work the same way (`--tracker jira`,
`--tracker linear`).

### 6. Put your extractors in one file

**The problem:** the extract command gets long, and you type it again
every day.

Save this as `runbook.yaml`:

```yaml
version: 1
companies:
  myteam:
    schedules:
      - task: extractors
        every: "day at 03:17"
        extract:
          - name: pr-archaeology
            args: {pr_repo: [iklobato/briar]}
          - name: ci-health
            args: {cihealth_repo: [iklobato/briar]}
          - name: release-cadence
            args: {release_repo: [iklobato/briar]}
```

```bash
briar runbook extract runbook.yaml
```

![briar runbook extract runbook.yaml runs three extractors and prints a summary table](https://raw.githubusercontent.com/iklobato/briar/main/docs/demo/06-runbook.gif)

**You get:** every extractor in the file runs, with one log line per
step, then a table of what was written. A runbook writes its whole blob
each time, so sections written by other commands are replaced.

### 7. Answer review comments

**The problem:** review comments pile up, and fixing each one, committing
and replying on the thread takes time.

```bash
briar agent prfix --pr 75 --branch docs/readme-hero --dry-run
```

![briar agent prfix --dry-run prints the review comment, the fix steps, the strict rules and the tools](https://raw.githubusercontent.com/iklobato/briar/main/docs/demo/07-prfix.gif)

**You get:** with `--dry-run`, the prompt for the agent that fixes PRs:
the PR, its description and every open review comment (here one, under
"Inline review comments (1)"), plus its rules: one small commit per
comment, a reply on the thread with the commit SHA, never a force-push,
amend or rebase. Nothing changes on the PR. Without `--dry-run` the agent
does that work on the PR's branch.

### 8. Keep knowledge fresh on a schedule

**The problem:** facts from last month are wrong today, and you have more
than one team.

Save this as `runbooks/teams.yaml`:

```yaml
version: 1
companies:
  briar:
    schedules:
      - task: pr-history
        every: "minute"
        extract:
          - name: pr-archaeology
            args: {pr_repo: [iklobato/briar]}
  flask:
    schedules:
      - task: pr-history
        every: "minute"
        extract:
          - name: pr-archaeology
            args: {pr_repo: [pallets/flask]}
```

```bash
briar runbook serve runbooks
```

![briar runbook serve registers two teams, runs each one a few seconds apart, writes two blobs, then stops on Ctrl+C](https://raw.githubusercontent.com/iklobato/briar/main/docs/demo/08-scheduler.gif)

**You get:** one blob per team (`knowledge:briar.pr-history` and
`knowledge:flask.pr-history`), refreshed at each `every:` time. The first
runs start a few seconds apart on purpose, so many teams do not call
GitHub at the same moment. It keeps running until you press Ctrl+C; on a
server you leave it on, with times like `"day at 03:17"`.

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

## See it run

The same four diagrams as on [usebriar.com](https://usebriar.com/#diagrams). Click one to open it.

<details>
<summary><b>Which command?</b></summary>

![A decision tree from what you need right now (understand, change code, check the setup) to the exact briar command for eleven common situations.](https://raw.githubusercontent.com/iklobato/briar/main/docs/diagrams/which-command.svg)

1. Want to know something about the code, the team or production? Read it
   once with `briar extract`, every day with `runbook serve`, from Claude
   Code or any MCP client, or ask `briar chat`.
2. Want code changed? One ticket is `agent implement`, an open PR with
   review comments is `agent prfix`, and a whole board is `plan build` once,
   then `plan run`.
3. Setting up or checking? `briar init` and `briar doctor` first,
   `secrets doctor` when a credential is missing, and `journal list` to read
   back what a plan run decided.

</details>

<details>
<summary><b>Inside an agent run</b></summary>

![One agent run: briar assembles a system prompt from persona, the knowledge sections that persona reads, task context and procedure, then loops between the Anthropic model and tools that run in a temporary git worktree, for at most 30 turns.](https://raw.githubusercontent.com/iklobato/briar/main/docs/diagrams/agent-run.svg)

1. briar builds one system prompt: the agent's persona, the knowledge
   sections that persona reads (for implement: codebase conventions, code
   hotspots, reviewer profiles, active work, PR history and the meeting
   digest), the ticket or PR context, and the procedure to follow.
2. The Anthropic model answers with tool calls. briar runs them inside a
   temporary git worktree and sends back the results. This repeats for at
   most 30 turns (`--max-iter`).
3. The tools are bash and file read, write and edit. `send_message` and MCP
   tools are added only when the runbook lists channels or servers.
4. When the model finishes, briar prints its final message (ending with the
   PR URL) and deletes the worktree. On an error or the turn ceiling the
   worktree is kept so you can look.
5. `--dry-run` prints the prompt and the tool list and stops there: no
   clone, no model call.

</details>

<details>
<summary><b>Answering a review</b></summary>

![How briar agent prfix answers a review: skip a PR that is already approved and green, otherwise handle each open thread with a fix commit, a reply, or a note saying why, then push fast-forward and reply with the commit SHA.](https://raw.githubusercontent.com/iklobato/briar/main/docs/diagrams/review.svg)

1. prfix reads the PR with `gh pr view`: review decision, branch and
   checks.
2. If it is approved, every required check is green and every open thread
   is positive, it reports "skipped" and stops.
3. Otherwise it lists every open review thread and PR comment and handles
   them one at a time.
4. A requested code change gets the smallest fix and its own commit citing
   the comment. A question or opinion gets a reply, no commit. A fix that
   isn't safe gets a comment saying why.
5. Then it pushes fast-forward to the PR branch and replies on each thread
   it addressed with the commit SHA.
6. It never force-pushes, amends, rebases or squashes, and never commits as
   a bot identity.

</details>

<details>
<summary><b>Working a board</b></summary>

![How briar plan run works a board: a selector picks the next card, replans, completes or stops; picked cards go to agent implement, and each success writes a lesson into the plan's knowledge that the selector reads next time.](https://raw.githubusercontent.com/iklobato/briar/main/docs/diagrams/board.svg)

1. `plan build` reads a Jira board or GitHub Project and turns it into an
   ordered card list (with `--with-knowledge`, using your knowledge too). It
   also seeds a `knowledge:acme.q3` blob for the plan.
2. `plan run` asks the selector (an LLM) what to do next. It reads the
   plan, the knowledge and the journal of earlier decisions.
3. Pick: the card's ticket goes to `agent implement`, on the card's branch
   (`chore/kan-2` when built without an LLM), from the card's parent
   branch. Success marks it done and writes a lesson into
   `knowledge:acme.q3`, which the selector reads before its next pick.
4. A failed card is marked blocked. The run stops there, unless
   `--continue-on-failure`.
5. Replan: briar re-reads the board, keeps the status of cards it already
   knew, and adds new ones as pending. At most 3 times per run
   (`--max-replans`).
6. Complete ends the run. Blocked stops it until you step in. `--limit N`
   stops after N cards. Every decision lands in the journal.

</details>


## Contributing

Bug reports and PRs are welcome: open an
[issue](https://github.com/iklobato/briar/issues) or read
[CONTRIBUTING.md](https://github.com/iklobato/briar/blob/main/CONTRIBUTING.md)
first. Every merge to `main` is a release on PyPI and Docker Hub, and
[CHANGELOG.md](https://github.com/iklobato/briar/blob/main/CHANGELOG.md)
lists what each version changed. If briar helps you, a star on GitHub
helps other people find it.

## Telemetry

briar sends error reports and usage counts by default. No prompts, file
contents, ticket keys, repo names, paths or secret values leave your
machine. Turn it off with `briar telemetry off`, `BRIAR_TELEMETRY=off`
or `DO_NOT_TRACK=1`.

## License

MIT. See [LICENSE](https://github.com/iklobato/briar/blob/main/LICENSE).

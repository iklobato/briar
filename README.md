<div align="center">

# briar

**The local CLI that reads your team tools and writes what it learns into
plain markdown files, for you and for your AI coding agents.**

[![PyPI](https://img.shields.io/pypi/v/briar-cli)](https://pypi.org/project/briar-cli/)
[![Python](https://img.shields.io/pypi/pyversions/briar-cli)](https://pypi.org/project/briar-cli/)
[![Downloads](https://img.shields.io/pypi/dm/briar-cli)](https://pypi.org/project/briar-cli/)
[![Docker pulls](https://img.shields.io/docker/pulls/iklob1/briar)](https://hub.docker.com/r/iklob1/briar)
[![Tests](https://github.com/iklobato/briar/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/iklobato/briar/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/github/license/iklobato/briar)](https://github.com/iklobato/briar/blob/main/LICENSE)

[Install](#install) · [Quickstart](#quickstart) · [Commands](#commands) · [Manuals](https://github.com/iklobato/briar/blob/main/agents/README.md) · [Changelog](https://github.com/iklobato/briar/blob/main/CHANGELOG.md)

</div>

## What briar is

briar is a command-line tool (you run it in your terminal) that collects
facts about how your team works, so an AI coding assistant does not have
to guess them.

**The problem:** an AI assistant like Claude can write code, but it does
not know your team. It does not know which tests you run, how fast PRs get
merged, who reviews what, how often you release or how often CI passes.
So it guesses, and you fix its guesses.

**What briar does about it:** it reads your tools (GitHub or Bitbucket,
Jira or Linear, AWS, GCP or Azure, Fireflies meeting notes) and writes what
it learns into plain markdown files on your computer. briar calls these
facts "knowledge". Any AI tool that reads files can use them, and so can
you.

- **23 extractors.** An extractor is a small reader for one kind of fact,
  for example "how long PRs take to merge" or "which files change the
  most".
- **Once or on a schedule.** Run them by hand with `briar extract`, or list
  them in a YAML runbook and let `briar runbook serve` keep the facts fresh.
- **Runs on your computer.** There is no briar server and no account.
  briar only talks to the services you set up, and your tokens stay in a
  file on your machine.

**What comes next:** pipelines. A pipeline will take a ticket, read the
knowledge, and have AI agents write the code, run your tests and open a
draft PR that you review and merge. This is planned for briar 2.0 and not
built yet. briar 1.x had a first version of it (`briar agent`,
`briar plan`); to use that today, install `pip install 'briar-cli<2'`.

## Install

```bash
pip install briar-cli
briar --version
```

briar needs Python 3.10 or later. The base install covers GitHub,
Bitbucket, AWS, Jira, Linear and Fireflies. For GCP and Azure, add the
extras:

```bash
pip install 'briar-cli[all]'      # or [gcp], or [azure]
```

Or skip the install and use the Docker image:

```bash
docker run --rm iklob1/briar --version
```

## Quickstart

The short version, for a GitHub repo with Jira tickets. New to briar?
[Learn it in 2 examples](#learn-briar-in-2-examples) instead.

```bash
# 1. Save a GitHub token and a Jira login. They go to ~/.config/briar/secrets.env
briar auth login github-pat --company acme
briar auth login jira-token --company acme

# 2. Read the recent PRs and the tools of the repo into the knowledge file of acme
briar extract --company acme --repo acme-co/acme-app \
    --include pr-archaeology --include codebase-conventions

# 3. Read what was saved
cat ./knowledge/knowledge/acme.md
```

## Commands

briar has 5 commands. Each one has a manual in
[`agents/`](https://github.com/iklobato/briar/blob/main/agents/README.md).

| Command | What it does | Manual |
|---|---|---|
| `briar extract` | Runs extractors and saves what they find in one knowledge file. | [extract.md](https://github.com/iklobato/briar/blob/main/agents/extract.md) |
| `briar runbook` | Runs extractors from a YAML file: once (`extract`) or on a schedule (`serve`). | [runbook.md](https://github.com/iklobato/briar/blob/main/agents/runbook.md) |
| `briar auth` | Saves a login (`login`) and shows which ones are set (`status`). | [auth.md](https://github.com/iklobato/briar/blob/main/agents/auth.md) |
| `briar init` | Writes a starter `.briar.toml` in your repo. | [setup.md](https://github.com/iklobato/briar/blob/main/agents/setup.md) |
| `briar doctor` | Shows each setting and where it came from, then checks your setup. | [setup.md](https://github.com/iklobato/briar/blob/main/agents/setup.md) |

Recipes that chain these commands are in
[flows.md](https://github.com/iklobato/briar/blob/main/agents/flows.md).

Three flags work with every command, anywhere on the line:

| Flag | What it does |
|---|---|
| `--format table\|json` | Output shape, for `doctor` and `runbook extract`. |
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
```

A value for one extractor wins over the value in `[extract]`. A key briar
does not know stops the run and says what to fix. `briar extract -h` lists
every key with its default, and `briar doctor` shows which settings briar
found and where.

## Learn briar in 2 examples

Written for someone who has never used briar. Each example says the
problem it solves, what each command does, what happens behind the scenes,
and what you get.

**Before you start** (once, about 2 minutes):

```bash
pip install briar-cli                  # installs the briar command (needs Python 3.10 or later)
cd path/to/your-repo                   # go into any GitHub repo you can read
export GITHUB_TOKEN=$(gh auth token)   # gives briar your GitHub login, so it can read the repo
```

If you do not use the GitHub CLI `gh`, run
`briar auth login github-pat --company myteam` and paste a GitHub token
instead. That token is all both examples need. Nothing is sent to an AI,
so they cost nothing.

**Words you will see:**

| Word | What it means |
|---|---|
| extractor | A reader for one kind of fact, like "merged PRs" or "CI runs". |
| knowledge | The facts briar saved. One markdown file per team, named like `knowledge:myteam`. |
| runbook | A YAML file that lists which extractors to run, for which team, and how often. |

### Example 1: Set up briar and collect facts

**The problem:** an AI that writes code for you does not know your repo or
how your team works: how fast PRs get merged, who writes them, how often
you release, how often CI passes.

```bash
briar init --company myteam    # creates .briar.toml, a small settings file in your repo
briar doctor                   # shows your settings and checks that briar has what it needs
briar extract --include pr-archaeology --include release-cadence --include ci-health
                               # collects three kinds of facts
cat ./knowledge/knowledge/myteam.md  # prints the facts briar saved
```

**What happens:**

- `init` runs `git remote get-url origin` to find the GitHub owner and repo
  name, and writes them into `.briar.toml` with the team name you chose
  (`myteam` is just a label). After this, no command needs `--company` or
  `--repo`.
- `doctor` prints two tables. The first lists each setting (`company`,
  `owner`, `repo`, `telemetry` and more) and where it came from. The
  second checks Python, the settings file, the git remote and your GitHub
  token: `ok` means ready, `warn` tells you what is missing and what needs
  it.
- `extract` runs the extractors you name with `--include`. They ask the
  GitHub API for your merged PRs (`pr-archaeology`), your releases
  (`release-cadence`) and your last 100 CI runs (`ci-health`), and sum
  them up. The result is saved as one markdown file under `./knowledge/`,
  called `knowledge:myteam`.
- Each extractor has defaults for its limits (how many PRs, how many
  days). To change one, add it to `.briar.toml`, for example
  `[extract.pr-archaeology]` then `max = 50`.
- `cat` prints the saved file. It is a plain markdown file on your disk.

**You get:** the facts of your repo in one text file, ready for any AI.

`briar extract -h` lists all 23 extractors. Good ones to add later:
`code-hotspots` (the files that change the most),
`codebase-conventions` (your test runner, linter and formatter) and
`stale-prs` (open PRs nobody touched for a while). Running `extract` again
only replaces the sections of the extractors that ran, so the facts you
already have stay. `briar extract` with no `--include` runs all 23; on the
briar repo that takes a few minutes.

### Example 2: Keep the facts fresh with a runbook

**The problem:** facts from last month are wrong today, and you do not want
to type the extract command every day.

Save this as `runbooks/myteam.yaml`. It lists the same three extractors as
example 1:

```yaml
version: 1
companies:
  myteam:
    schedules:
      - task: extractors
        every: "minute"          # only for the demo; use "day at 03:17" for real
        extract:
          - name: pr-archaeology
            args: {pr_repo: [iklobato/briar]}
          - name: release-cadence
            args: {release_repo: [iklobato/briar]}
          - name: ci-health
            args: {cihealth_repo: [iklobato/briar]}
```

```bash
briar runbook serve runbooks   # start a scheduler for every runbook in the folder
```

**What happens:**

- `serve` is a scheduler: a program that keeps running and starts each job
  on time. Every second it checks which job is due.
- When the job is due, it does the same work as `briar extract` and saves
  `knowledge:myteam` again. The log lines show each step.
- It runs until you press Ctrl+C. On a server you leave it running.
- To run the file once, right now, use
  `briar runbook extract runbooks/myteam.yaml`.
- More teams go under `companies:`. Their first runs start a few seconds
  apart, so they do not all call GitHub at the same moment.
- A runbook writes its whole file each time, so list in it every extractor
  you want to keep.

**You get:** facts that refresh themselves, without anyone typing a
command.

## Telemetry

briar sends nothing by default. To help fix bugs, turn on error reports
with `telemetry = "errors-only"` in `.briar.toml` (or
`BRIAR_TELEMETRY=errors-only`), or error reports and usage counts with
`full`. `DO_NOT_TRACK=1` turns it off whatever else says. `briar doctor`
shows the current choice and where it came from. Even when it is on, no
prompts, file contents, ticket keys, repo names, paths or secret values
leave your machine. 1.x sent by default; 2.0 ignores the choice 1.x saved,
so turn it on again if you want it.

## Contributing

Bug reports and PRs are welcome: open an
[issue](https://github.com/iklobato/briar/issues) or read
[CONTRIBUTING.md](https://github.com/iklobato/briar/blob/main/CONTRIBUTING.md)
first. [CHANGELOG.md](https://github.com/iklobato/briar/blob/main/CHANGELOG.md)
lists what each version changed, including everything 2.0 removed. If
briar helps you, a star on GitHub helps other people find it.

## License

MIT. See [LICENSE](https://github.com/iklobato/briar/blob/main/LICENSE).

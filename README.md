<div align="center">

# briar

**A command-line tool that reads your team tools and writes what it learns
into plain files, and can also work your tickets for you: plan, code, open
the pull request, and answer the review.**

[![PyPI](https://img.shields.io/pypi/v/briar-cli)](https://pypi.org/project/briar-cli/)
[![Python](https://img.shields.io/pypi/pyversions/briar-cli)](https://pypi.org/project/briar-cli/)
[![Downloads](https://img.shields.io/pypi/dm/briar-cli)](https://pypi.org/project/briar-cli/)
[![Docker pulls](https://img.shields.io/docker/pulls/iklob1/briar)](https://hub.docker.com/r/iklob1/briar)
[![Tests](https://github.com/iklobato/briar/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/iklobato/briar/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/github/license/iklobato/briar)](https://github.com/iklobato/briar/blob/main/LICENSE)

[What it does](#what-briar-does) · [Install](#install) · [First steps](#first-steps-your-first-10-minutes) · [Let briar work a ticket](#let-briar-work-a-ticket) · [Commands](#commands) · [If something goes wrong](#if-something-goes-wrong) · [Manuals](https://github.com/iklobato/briar/blob/main/agents/README.md) · [Changelog](https://github.com/iklobato/briar/blob/main/CHANGELOG.md)

</div>

## What briar does

briar is a program you run in your terminal. It has two jobs, and you can
use one without the other.

**Job 1: learn how your team works.** An AI assistant can write code, but
it does not know your team: which tests you run, how fast pull requests (PRs)
get merged, who reviews what, how often CI passes. So it guesses, and you fix
its guesses. `briar extract` reads your tools (GitHub, GitLab or Bitbucket,
Jira or Linear, AWS, GCP or Azure, Fireflies meeting notes) and writes the
facts into a plain markdown file on your computer. Any AI tool that reads
files can use it, and so can you. briar calls these facts "knowledge".

**Job 2: work your tickets.** `briar serve` watches the tickets assigned to
you. For each one it writes a plan, has a Claude agent write the code in its
own copy of your repo, runs your checks, opens a draft PR, waits for CI, asks
the usual reviewers, and answers their comments. It never merges: a person
does that.

| I want to... | Use | It costs |
|---|---|---|
| See facts about my repo and team in one file | `briar extract` | nothing (no AI is used) |
| Have briar do a ticket for me | `briar serve` | money for the AI model, within a limit you set |

briar runs on your computer. There is no briar server and no account. It
talks only to the services you set up, and your tokens stay in a file on your
machine.

## What you need

- A terminal and **Python 3.10 or later**.
- A **GitHub token** (or a login for the tool you use). Both jobs need one.
  [Logins](#logins) says how to get each.
- For `briar serve` only: a **Claude login** (`ANTHROPIC_API_KEY`, or
  `CLAUDE_CODE_OAUTH_TOKEN`), and either macOS, or Linux with `bubblewrap` and
  `socat` installed. The agent runs inside a sandbox, and briar will not start
  without one.

## Install

```bash
pip install briar-cli
briar --version
```

> **Heads up:** version 2.0 is not on PyPI yet, and `pip install briar-cli`
> gives the older 1.x, which has different commands. Until 2.0 is published,
> install it from GitHub (the Docker image below is also 1.x until then):
> `pip install "git+https://github.com/iklobato/briar@v2"`

The base install covers GitHub, GitLab, Bitbucket, AWS, Jira, Linear and
Fireflies. For GCP and Azure, add the extras:

```bash
pip install 'briar-cli[all]'      # or [gcp], or [azure]
```

Or skip the install and use the Docker image:

```bash
docker run --rm iklob1/briar --version
```

## First steps: your first 10 minutes

This path needs only a GitHub token and costs nothing. It ends with a file of
facts about your repo.

**1. Get a GitHub token and give it to briar.** If you use the GitHub CLI
`gh`, this one line is enough for now. `myteam` is just a label you pick:

```bash
export BRIAR_MYTEAM_GITHUB_TOKEN=$(gh auth token)
```

No `gh`? Create a token at
<https://github.com/settings/personal-access-tokens/new> (read access is
enough for this part) and put it in a file only you can read:

```bash
mkdir -p ~/.config/briar && chmod 700 ~/.config/briar
printf 'BRIAR_MYTEAM_GITHUB_TOKEN=%s\n' "<your token>" >> ~/.config/briar/secrets.env
chmod 600 ~/.config/briar/secrets.env
```

**2. Go into a repo you can read, and let briar set itself up.**

```bash
cd path/to/your-repo
briar init --company myteam
```

`init` finds your GitHub owner and repo from `git remote get-url origin` and
writes two files: `.briar.toml` (your settings) and `runbooks/myteam.yaml`
(an example runbook, used later). After this, no command needs `--repo`.

**3. Check that everything is ready.**

```bash
briar doctor
```

It prints your settings and where each came from, then checks Python, the
settings file, the git remote and every login you set up, live. `ok` means
ready. `fail` says exactly what to fix. `warn` means briar could not reach
the source.

**4. Collect three kinds of facts.**

```bash
briar extract --include pr-archaeology --include release-cadence --include ci-health
```

`extract` checks the logins first, then reads your merged PRs
(`pr-archaeology`), your releases (`release-cadence`) and your last 100 CI
runs (`ci-health`), and sums them up.

**5. Read what it saved.**

```bash
cat ./knowledge/knowledge/myteam.md
```

That is a plain markdown file on your disk: the facts of your repo in one
place, ready to give to any AI tool.

`briar extract -h` lists all 23 extractors. Good ones to add next:
`code-hotspots` (the files that change the most), `codebase-conventions` (your
test runner, linter and formatter) and `stale-prs` (open PRs nobody touched
for a while). Running `extract` again only replaces the sections of the
extractors that ran, so the facts you already have stay. `briar extract` with
no `--include` runs all 23, which takes a few minutes.

### With Jira too

Say where your Jira site is, and add a Jira login next to the GitHub token:

```bash
printf 'company = "acme"\n[auth.jira]\nsite = "acme.atlassian.net"\n[extract.active-tickets]\ntracker = "jira"\nprojects = ["ACME"]\n' >> .briar.toml
printf 'BRIAR_ACME_JIRA_EMAIL=%s\nBRIAR_ACME_JIRA_TOKEN=%s\n' \
    "<Atlassian email>" "<Atlassian API token>" >> ~/.config/briar/secrets.env
briar doctor          # checks both logins, live
briar extract --company acme --repo acme-co/acme-app --include active-tickets
```

## Let briar work a ticket

This is job 2. Start small and safe: one GitHub issue, one repo, a low limit.
It uses the AI model, so it costs money. In our own tests a small ticket cost
between US$ 0.08 and US$ 0.25.

**1. Give briar a token that can write, and a Claude login.** The GitHub token
needs read and write on Contents, Pull requests and Issues.

```bash
printf 'BRIAR_MYTEAM_GITHUB_TOKEN=%s\nANTHROPIC_API_KEY=%s\n' \
    "<GitHub token with write access>" "<Claude API key>" >> ~/.config/briar/secrets.env
```

**2. Make a small issue and assign it to yourself.** `serve` only takes
tickets assigned to the owner of the token. Pick something tiny, like a
sentence in a doc:

```bash
gh issue create --assignee @me --title "Fix the typo in the README" --body "The word receive is spelled recieve in the install section."
```

Note the issue number it prints (say, `12`).

**3. Write a small runbook.** Replace `runbooks/myteam.yaml` with this, and use
your own repo and issue number:

```yaml
version: 1
companies:
  myteam:
    serve:
      repos: [acme-co/acme-app]          # where briar may work
      only: [acme-co/acme-app#12]        # first run: touch this one issue only
      model: claude-sonnet-5-5
      max_usd: 5                         # most it may spend on one ticket
      max_loops: 3                       # most fix attempts on one ticket
      ci_wait_minutes: 30                # how long it waits for your CI
      max_review_rounds: 3               # most review rounds on one PR
      max_new_per_day: 1                 # new tickets it may start per day
      verify: ["pytest -q"]              # the check it runs before it opens a PR
```

`verify` is the command that tells briar the change works. Use your own test
command. The `init` runbook has a `serve:` block with `model` and the limits
commented out on purpose: briar will not guess how much money to spend.

**4. Check, then start.**

```bash
briar doctor --runbooks runbooks     # checks the runbook, the logins and the sandbox, without starting
briar serve runbooks                 # starts watching
```

**What you will see** (the log, one line per step):

```text
serve: myteam: picked acme-co/acme-app#12 for acme-co/acme-app: <why it picked it>
serve: issue-12 is planned
serve: issue-12 is pr-open https://github.com/acme-co/acme-app/pull/13
serve: issue-12 is done https://github.com/acme-co/acme-app/pull/13 US$ 0.15 of 5.0, 0 of 3 fix loops
```

**What happens behind the scenes:**

- briar asks the agent for a plan and posts it as a comment on the issue.
- It makes a new branch `briar/issue-12` in its own copy of your repo (your own
  files are never touched), has the agent write the change, and runs `verify`.
  If a check fails, the agent fixes it, up to `max_loops` times.
- It pushes the branch and opens a **draft** pull request that says
  `Closes #12`, then waits for your CI. If CI fails, the agent gets the failed
  log and fixes it.
- When CI passes it marks the PR **ready** and asks the two people who usually
  review that repo.
- When someone comments on the PR, briar reads the comment, changes the code or
  answers why not, and replies in the thread.

**You get:** a PR ready for a person to review and merge. Check it like any
other PR.

**What briar never does:** it never merges, never force pushes, and never
spends more than `max_usd` on one ticket. Nothing leaves its sandbox except
what the runbook allows. If it cannot finish, it stops, writes a report of why on the ticket (and on the
draft PR, when it already made one), and never picks that ticket up again by
itself.

`serve` keeps running until you press Ctrl+C. It checks for new work every 5
minutes by default (`poll_minutes`), so a quiet log is normal. Run only one
`serve` per runbook folder.

When this works, remove `only` and let it choose among all the tickets
assigned to you. [serve.md](https://github.com/iklobato/briar/blob/main/agents/serve.md)
covers Jira tickets, GitLab, the usual reviewers, limits per day and how it
chooses.

### Keep the facts fresh

The same `serve` command can also refresh your knowledge file on a schedule,
so nobody has to type `extract` every day. A runbook with no `serve:` block
runs only its schedules:

```yaml
version: 1
companies:
  myteam:
    schedules:
      - task: extractors
        every: "day at 03:17"
        extract:
          - name: pr-archaeology
            args: {pr_repo: [acme-co/acme-app]}
          - name: release-cadence
            args: {release_repo: [acme-co/acme-app]}
          - name: ci-health
            args: {cihealth_repo: [acme-co/acme-app]}
```

```bash
briar doctor --runbooks runbooks   # checks the runbook and its logins, without starting
briar serve runbooks               # runs until you press Ctrl+C
```

A schedule writes its whole file each time, so list in it every extractor you
want to keep. For a quick test use `every: "minute"`, and set it back after.

## Commands

briar has 4 commands. Each one has a manual in
[`agents/`](https://github.com/iklobato/briar/blob/main/agents/README.md).

| Command | What it does | Manual |
|---|---|---|
| `briar extract` | Runs extractors and saves what they find in one knowledge file. | [extract.md](https://github.com/iklobato/briar/blob/main/agents/extract.md) |
| `briar serve` | Works your assigned tickets from the runbooks in a folder, and runs their scheduled extracts. | [serve.md](https://github.com/iklobato/briar/blob/main/agents/serve.md), [runbooks.md](https://github.com/iklobato/briar/blob/main/agents/runbooks.md) |
| `briar init` | Writes a starter `.briar.toml` and an example runbook in your repo. | [setup.md](https://github.com/iklobato/briar/blob/main/agents/setup.md) |
| `briar doctor` | Shows each setting and where it came from, then checks your setup (and a folder of runbooks with `--runbooks`). | [setup.md](https://github.com/iklobato/briar/blob/main/agents/setup.md) |

1.x also had `briar auth` and `briar runbook`. 2.0 removed them: write the
logins by hand (below), and use `briar serve DIR` for the schedules.

Recipes that chain these commands are in
[flows.md](https://github.com/iklobato/briar/blob/main/agents/flows.md).

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
[logins.md](https://github.com/iklobato/briar/blob/main/agents/logins.md).

## If something goes wrong

Start with `briar doctor` (or `briar doctor --runbooks runbooks` for `serve`):
it names what is wrong and how to fix it.

| What you see | What it means | What to do |
|---|---|---|
| `the login check failed` and a list | A login is missing or refused. Each line names the variable. | Set that variable in `secrets.env`, then run `briar doctor` again. |
| `HTTP 401` or `Bad credentials` | The token is wrong, expired, or for another account. | Create a new token and replace it. |
| `Field required` when reading a runbook | The `serve:` block needs `model` and the limits, and has no default. | Fill in `model`, `max_usd`, `max_loops`, `ci_wait_minutes`, `max_review_rounds`. |
| `no agent sandbox: install ...` | `serve` found no sandbox. | macOS has one built in. On Linux, install `bubblewrap` and `socat`. |
| `no model login` | `serve` has no Claude login. | Set `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. |
| `another briar serve runs on the company` | A second `serve` uses the same folder. | Stop the other one first. |
| A ticket ends `blocked`, and the issue gets a comment starting `briar stopped:` | briar hit a limit or a failure and stopped on purpose. The comment says why. | Read the reason. The common ones: a limit was reached (raise it or make the ticket smaller), or your `verify` command fails even without briar (fix that first). |
| `command not found` in a `blocked` report | A program your `verify` command runs is not on the PATH of the process. | Add its folder to the `PATH` of the shell or service that runs `serve`. |
| `serve` prints nothing for minutes | It is waiting for the next check, or has nothing to do. | Normal. It checks every 5 minutes by default. |

## Words you will see

| Word | What it means |
|---|---|
| extractor | A reader for one kind of fact, like "merged PRs" or "CI runs". |
| knowledge | The facts briar saved. One markdown file per team, like `knowledge:myteam`. |
| runbook | A YAML file that says which extractors to run and how often, and how `serve` may work tickets. |
| ticket | A task in Jira, or an issue on GitHub. |
| PR | Pull request: a proposed change to a repo. On GitLab it is called a merge request (MR). |
| CI | The checks that run on every PR (tests, lint). |
| sandbox | A locked box the agent runs in, so it can only touch its own copy of the repo. |

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

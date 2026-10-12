# `briar agent`

## What it does

`briar agent` runs an AI coding agent on a fresh copy of your repo.
There are two commands:

- `briar agent implement` takes one ticket, writes the code, and opens a
  **draft** PR.
- `briar agent prfix` takes one PR, fixes what reviewers asked for, pushes
  one commit per comment, and replies to each comment.

Both always use Anthropic's Claude models. There is no `--llm` flag here.
(`briar plan` and `briar chat` are the commands that take `--llm`.)

The agent works in a worktree: a separate temporary copy of the repo that
briar clones for this run only. Your own checkout is never touched.

## Use it when

- A ticket is small and clear, and you want a draft PR to review.
- A reviewer left comments on a PR, and you want each one fixed and answered.
- You want to see the exact prompt first, for free: add `--dry-run`.
- You have a whole board of tickets: use `briar plan run` instead. It calls
  `agent implement` once per card (see [plan.md](plan.md)).

## Before you start

| You need | Why | How to get it |
|---|---|---|
| Claude credentials. | The agent is Claude. | `CLAUDE_CODE_OAUTH_TOKEN` (tried first) or `ANTHROPIC_API_KEY`. |
| A repo token. | briar clones the repo with it. | GitHub: `GITHUB_TOKEN`. Bitbucket: `BITBUCKET_ACME_USERNAME` and `BITBUCKET_ACME_APP_PASSWORD`. |
| Tracker credentials (`implement` only). | briar reads the ticket. | Jira: `JIRA_ACME_URL`, `JIRA_ACME_EMAIL`, `JIRA_ACME_TOKEN`. Linear: `LINEAR_ACME_TOKEN`. GitHub Issues: `GITHUB_TOKEN`. |
| `git` and `gh` on your `PATH`. | The agent runs them to commit, push, and open or read the PR. `gh` uses `GITHUB_TOKEN`. | Install both. The Docker image has both since 1.1.75 (see [Run an agent in Docker](../README.md#run-an-agent-in-docker)). |
| Push access over HTTPS. | After the clone, briar removes the token from the `origin` URL. The push then uses your normal git login for the host. | For GitHub, `gh auth setup-git` is one way. The Docker image sets this up for GitHub already. |
| A git identity for commits. | Commits must have a human name and email. | `--git-user-name` and `--git-user-email`, or `git_identity` in the runbook, or your own `git config` (not used when `CI` is set). |

A plain `pip install briar-cli` is enough. The `[mcp]` extra is
optional here. Since 1.1.74 a run without it goes on; older versions
stopped unless you passed `--no-default-mcp`.

Optional, but they make the agent better:

- A knowledge blob from `briar extract --company acme` (see [extract.md](extract.md)).
  `implement` uses these sections: `codebase-conventions`, `code-hotspots`,
  `reviewer-profile`, `active-work`, `pr-archaeology` and `meeting-digest`.
  `prfix` uses the same ones except `pr-archaeology`. Other sections are
  not added to the prompt.
- `FIREFLIES_ACME_API_KEY`, so the agent reads meetings about the work.
- `SLACK_ACME_TOKEN` and `SLACK_ACME_COOKIE_D`, so it reads Slack threads about the work.
- A runbook YAML (`--runbook`), so it can post messages through set channels.
- The `[mcp]` extra (`pip install 'briar-cli[mcp]'`). It gives the agent
  two small helper tools: "think" (needs `npx`) and "time" (needs
  `uvx`). A tool whose program is missing is skipped. Without the
  extra, briar skips both and logs one warning, then the run goes on:
  ``mcp: the `mcp` package is not installed, so the built-in think/time servers are skipped; install 'briar-cli[mcp]' to enable them``.
  `--no-default-mcp` hides that warning. The extra is required only
  when the runbook lists servers under `mcp:`.

## How `implement` works

1. briar checks `--company` and the repo, and opens the knowledge store.
2. It makes a temp folder named `briar-agent-implement-*` and clones the
   default branch into it (the last 50 commits).
3. It reads the ticket: description, acceptance criteria, comments and
   status history. If that fails, the agent goes on with the key alone.
4. It looks up Fireflies meetings and Slack threads that mention the
   ticket key. This step is skipped when those credentials are missing.
5. It builds the prompt: the agent's role, the knowledge sections it
   uses (from every blob named `knowledge:acme...`), the ticket, the
   meetings and the Slack threads.
6. The agent works for up to 30 turns. A turn is one call to Claude.
7. The agent's instructions are: create branch `chore/acme-42` (built from
   the ticket key), make the change, and run the test command found by
   `codebase-conventions`. It pushes only when the tests pass, with
   `git push -u origin HEAD`. Then it opens a draft PR and prints its URL.
8. If an acceptance criterion is unclear, the agent stops and posts a
   question on the ticket instead of guessing.
9. briar prints the agent's final message and the commit SHAs. It deletes
   the temp folder, unless the run failed or you passed `--keep-worktree`.

On GitHub the PR is opened with `gh pr create --draft`.

## How `prfix` works

1. Same checks as above. The temp folder is `briar-agent-prfix-*`, and
   briar clones the PR branch you name with `--branch`.
2. briar reads the PR: title, branches, every comment thread, and failing
   CI steps with a log tail.
3. It looks up meetings and Slack threads that mention
   `acme-co/acme-app#482` (or your query).
4. The agent skips the PR when it is approved, every required check is
   green, and all open comments are positive. It reports `skipped`.
5. Otherwise it reads every open review thread and PR comment. For each
   request it makes the smallest fix and one commit. The commit body
   names the comment.
6. It pushes with `git push origin HEAD:<branch>`. It never force-pushes,
   amends, rebases or squashes.
7. It replies to each thread with one sentence that cites the commit SHA.
   For a question like "did you consider X?" it replies without a commit.
   If it cannot fix a thread safely, it says why and moves on.

`prfix` works on review threads and PR comments only. It does not set out
to fix failing CI. Its built-in steps use the `gh` CLI, so it is made for
GitHub.

## Examples: `implement`

Check the prompt first. This costs nothing:

```bash
briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ACME-42 --tracker jira --dry-run
```

You get the full system prompt, the first message, the list of tools,
and the size of each context section. There is no clone and no Claude
call. briar still reads the ticket, meetings and Slack. With the `[mcp]`
extra it also starts the helper tools.

Run it for real:

```bash
briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ACME-42 --tracker jira
```

You get a pushed branch `chore/acme-42` and a draft PR. The PR URL is the
last line of the agent's final message.

Inside a checkout with `.briar.toml`, most flags come from config:

```bash
briar agent implement --ticket-key ACME-42
```

Here `--company` and the repo come from `.briar.toml` or the git
`origin` remote. `--tracker` is `jira` unless `.briar.toml` sets
`tracker`. `--ticket-project` is taken from the key (`ACME`).

Implement a GitHub issue, and keep the folder to look at afterwards:

```bash
briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key '#42' --tracker github-issues --keep-worktree -v
```

Quote `'#42'`, or the shell treats it as a comment. With `-v` the log
prints `worktree kept at <path>`.

Implement a Linear ticket with more context and a bigger turn limit:

```bash
briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ENG-7 --tracker linear \
    --meeting-query "checkout redesign" --slack-query "checkout redesign" \
    --max-iter 50 --model claude-sonnet-4-5
```

You get the same flow. The agent also reads meetings and Slack threads
about "checkout redesign", and may use up to 50 turns.

Run on a host without `npx` or `uvx`, with a fixed bot identity and a
runbook:

```bash
briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ACME-42 --no-default-mcp \
    --git-user-name "Acme Bot" --git-user-email bot@acme.com \
    --runbook runbooks/acme.yaml
```

The helper tools are skipped. Commits use the given name and email. The
agent gets a `send_message` tool for the channels in the runbook's
`messages:` block.

## Examples: `prfix`

Check the prompt first:

```bash
briar agent prfix --company acme --repo acme-co/acme-app \
    --pr 482 --branch fix/login --dry-run
```

You get the prompt with the PR's comments and failing CI steps. Nothing
is cloned or pushed.

Fix the review comments on PR 482:

```bash
briar agent prfix --company acme --repo acme-co/acme-app \
    --pr 482 --branch fix/login
```

You get new commits on `fix/login`, one per addressed comment, and a reply
on each thread.

Use one specific meeting as context:

```bash
briar agent prfix --company acme --repo acme-co/acme-app \
    --pr 482 --branch fix/login \
    --meeting-key 01JB7Q4ZK3M8 --meeting-max-bytes 8000
```

The agent reads the Fireflies meeting `01JB7Q4ZK3M8`, cut to 8000 bytes.
`--meeting-key` wins over `--meeting-query`.

Search Slack for the reviewer's name instead of the PR number:

```bash
briar agent prfix --company acme --repo acme-co/acme-app \
    --pr 482 --branch fix/login --slack-query "alice login" --slack-top-k 5
```

The agent reads up to 5 Slack threads that match "alice login".

## All parameters

### Shared by `implement` and `prfix`

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--company` | Company key. It picks per-company credentials, the knowledge blobs to read, and the company block in `--runbook`. briar stops with an error when it is empty. | `BRIAR_COMPANY`, or `company` in `.briar.toml`. | `--company acme` |
| `--owner` | Repo owner (GitHub) or workspace (Bitbucket). Use it with a bare `--repo` name. | From `.briar.toml` `[repo]`, else the git `origin` remote. | `--owner acme-co` |
| `--repo` | Repo as `owner/repo`, or a bare name with `--owner`. The `owner/repo` form wins over `--owner`. | From `.briar.toml` `[repo]`, else the git `origin` remote. | `--repo acme-co/acme-app` |
| `--provider` | Code host: `github` or `bitbucket`. | `github`, or `provider` under `[repo]` in `.briar.toml`. | `--provider bitbucket` |
| `--store` | Knowledge store to read blobs from: `file` or `postgres`. | `postgres` if `BRIAR_DATABASE_URL` is set, else `file`. `BRIAR_STORE` or `store` in `.briar.toml` change it. | `--store postgres` |
| `--root` | Folder of the file store. Ignored for Postgres. `--knowledge` still works as an old name for this. It prints a note. | `./knowledge`, or `root` in `.briar.toml`. | `--root ./kb` |
| `--model` | Claude model name to use. | `claude-sonnet-4-5`, or `model` under `[agent]` in `.briar.toml`. | `--model claude-sonnet-4-6` |
| `--max-iter` | Most turns (calls to Claude) before the run stops with an error. | `30` (`0` also means 30). | `--max-iter 50` |
| `--git-user-name` | `user.name` for commits in the worktree. | The runbook's `git_identity.name`, then your `git config user.name` (skipped when `CI` is set). | `--git-user-name "Acme Bot"` |
| `--git-user-email` | `user.email` for commits in the worktree. | The runbook's `git_identity.email`, then your `git config user.email` (skipped when `CI` is set). | `--git-user-email bot@acme.com` |
| `--keep-worktree` | Keep the temp folder after a successful run. A failed run always keeps it. | Off. | `--keep-worktree` |
| `--no-default-mcp` | Do not start the two built-in helper tools ("think" and "time"). Same as `BRIAR_NO_DEFAULT_MCP=1`. Use it on hosts without `npx` or `uvx`. Without the `mcp` extra the helpers are skipped anyway, and this flag only hides the warning. | Off (helpers start when the `mcp` extra is installed). | `--no-default-mcp` |
| `--dry-run` | Print the prompt, the first message and the tool list, then stop. No clone, no Claude call, no Claude credentials needed. | Off. | `--dry-run` |
| `--runbook` | Runbook YAML to read this company's block from. Its `messages:` gives the agent a `send_message` tool. Its `mcp:` adds tool servers. Its `git_identity` sets the commit identity. | None. | `--runbook runbooks/acme.yaml` |
| `--meeting-key` | One Fireflies meeting ID to add to the prompt. Wins over `--meeting-query`. | None. | `--meeting-key 01JB7Q4ZK3M8` |
| `--meeting-query` | Words to search recent meetings for. | `implement`: the ticket key. `prfix`: `owner/repo#PR`. | `--meeting-query "login retry"` |
| `--slack-query` | Words to search Slack for. Slack search syntax works. | `implement`: the ticket key. `prfix`: `owner/repo#PR`. | `--slack-query "ACME-42"` |

### Only for `implement`

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--ticket-key` | The ticket to implement. Jira `ACME-42`, Linear `ENG-7`, GitHub or Bitbucket Issues `'#42'`. Required. | None. | `--ticket-key ACME-42` |
| `--tracker` | Where the ticket lives: `jira`, `github-issues`, `bitbucket-issues` or `linear`. | `jira`, or `tracker` in `.briar.toml`. | `--tracker linear` |
| `--ticket-project` | Tracker project. Jira project key, Linear team, or `owner/repo` for GitHub and Bitbucket Issues. | Jira and Linear: the part of the key before the last dash (`ACME-42` gives `ACME`). Issues: `owner/repo`. | `--ticket-project ACME` |

### Only for `prfix`

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--pr` | PR number to work on. Required. | None. | `--pr 482` |
| `--branch` | The PR's head branch. briar clones it and the agent pushes to it. Required. | None. | `--branch fix/login` |

### Hidden parameters

These work on both commands but do not show in `-h`.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--knowledge` | Old name for `--root`. Prints `note: --knowledge is deprecated; use --root instead.` | `./knowledge` | `--knowledge ./kb` |
| `--meeting` | Meeting tool to search. Only `fireflies` exists. | `fireflies` | `--meeting fireflies` |
| `--meeting-top-k` | How many matching meetings to read when searching. Values below 1 count as 1. | `3` | `--meeting-top-k 5` |
| `--meeting-max-bytes` | Byte limit for each meeting transcript. Values below 1024 count as 1024. | `50000` | `--meeting-max-bytes 8000` |
| `--chat` | Chat tool to search. Only `slack` exists. | `slack` | `--chat slack` |
| `--slack-top-k` | How many matching Slack threads to read. Values below 1 count as 1. | `3` | `--slack-top-k 5` |
| `--slack-max-bytes` | Byte limit for all Slack thread text together. Values below 1024 count as 1024. | `30000` | `--slack-max-bytes 10000` |

## What you see when it runs

Output on stdout:

```text
--- agent final text ---
Opened draft PR: https://github.com/acme-co/acme-app/pull/483
--- commits: 4f2a9c1, 9b03e7d ---
```

The `commits` line appears only when the agent made commits. Add `-v` to
see each turn, the token count, and a cost estimate in the log.

| Exit code | Meaning |
|---|---|
| `0` | The agent finished on its own. Read the final text: it may say `skipped`, or that it asked a question on the ticket. |
| `1` | The run failed. Examples: missing Claude credentials, clone failed, no git identity, a Claude call failed, or the turn limit was hit. |
| `2` | Bad flags, an unknown provider, or an unexpected crash. |

A runbook with an `mcp:` block on an install without the `mcp` extra fails
with an error telling you to run `pip install 'briar-cli[mcp]'`.
| `130` | You pressed Ctrl-C. |

`briar agent` does not write a journal session. Only `plan run` and
`scaffold` do (see [journal.md](journal.md)).

## Good to know

- The agent's `bash` tool runs only commands that start with one of:
  `git`, `gh`, `ls`, `cat`, `head`, `tail`, `grep`, `rg`, `find`, `wc`,
  `pwd`, `echo`, `cd`, `python`, `python3`, `pytest`, `ruff`, `black`,
  `mypy`, `npm`, `node`, `make`, `tee`, `sort`, `uniq`. A test command like
  `uv run pytest`, `go test` or `cargo test` is refused.
- The tool also refuses any command that contains `--force`, `--amend`,
  `rebase`, `squash`, `reset --hard`, `rm -rf`, `sudo`, `curl` or `wget`,
  among others. Each command stops after 60 seconds by default, and after
  300 seconds at most.
- The file tools can read and write only inside the worktree.
- On Bitbucket, briar tells the agent to open the PR with `curl`, but the
  tool refuses `curl`. Expect a pushed branch and open the PR yourself.
- Comments the agent posts through `send_message` on a PR, in Jira or in Slack start with `[AI] `.
- `--model` must be a Claude model name. Each turn may use up to 8000
  output tokens.

| Problem | What to do |
|---|---|
| `--company is required for briar agent`. | Pass `--company acme`, or set `company` in `.briar.toml`. |
| `repository target is required`. | Pass `--repo acme-co/acme-app`, or run inside a checkout with an `origin` remote. |
| `git identity not configured`. | Pass `--git-user-name` and `--git-user-email`, or set `git_identity` in the runbook, or run `git config user.name`. |
| `LLM (anthropic) credentials missing`. | Set `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. |
| `anthropic 429 rate-limited`. | briar does not retry a 429. Wait for your quota to reset (about 1 hour for an API key, up to 5 hours for OAuth), then run again. |
| Clone fails. | The token cannot read the repo, or the branch name is wrong. Check `gh auth status` and `--branch`. |
| The ticket section is empty in `--dry-run`. | Wrong `--tracker`, wrong key, or missing tracker credentials. |
| `hit iteration ceiling (30)`. | The task is too big for 30 turns. Split the ticket, or raise `--max-iter`. |

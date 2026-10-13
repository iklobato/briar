# `briar plan`

## What it does

`briar plan` turns a tracker board into a plan, then works through it.

- A **plan** is a saved list of cards. A **card** is one ticket from the
  board, with a status, a short summary, its scope, risks, and a branch name.
- `plan build` reads the board and saves the plan.
- `plan run` repeats three steps until the work is done. First an LLM
  (the "selector") picks the next card. Then `briar agent implement`
  does that card. Then briar writes what it learned into the plan's
  knowledge blob.

The other subcommands let you look at a plan and change it by hand.

| Subcommand | What it does |
|---|---|
| `build` | Read a board and save a plan. |
| `show` | Print a plan as markdown. |
| `status` | Group the cards by status: done, in progress, blocked, pending. |
| `next` | Ask the selector for its next decision. Changes nothing. |
| `advance` | Set one card's status by hand. |
| `run` | Pick, implement and learn, card after card. |
| `list` | List saved plans. |
| `clear` | Delete a saved plan. |

## Where things are saved

| What | Name | Where (file store) |
|---|---|---|
| The plan. | `plan:q3` | `./knowledge/plan/q3.md` (markdown with a JSON block inside). |
| What the plan has learned. | `knowledge:acme.q3` | `./knowledge/knowledge/acme.q3.md` |
| The log of each `plan run`. | A journal session (`plan.run`). | `./journal/` (see [journal.md](journal.md)). |

`knowledge:acme.q3` starts as a summary of all cards. After each card
that finishes, the LLM rewrites it with new notes. The selector reads it
before each pick. Since 1.1.93 `plan run` also gives it to the coding
agent of each card, as a "Plan notes" section of its prompt, read again
before every card. Before 1.1.93 the agent did not see it.

When the selector names no parent, a card starts from the branch of its
first finished dependency (since 1.1.96), so it builds on that code. A
card counts as done only when its agent made a commit (since 1.1.96).

## Card statuses

| Status | Meaning |
|---|---|
| `pending` | Not started. Only pending cards can be picked. `build` sets every card to pending. |
| `in_progress` | `run` sets this right before it calls the agent. If you stop the run, the card stays here. |
| `done` | The agent exited with code 0 and made at least one commit (since 1.1.96; before, a run with no commit counted too). It does not prove a PR exists, so check it. |
| `blocked` | The agent failed. The selector never picks it again until you set it back to `pending`. |

## Selector decisions

Each time, the selector answers with one of four actions:

| Action | Fields | What `plan run` does |
|---|---|---|
| `pick` | `key`, `why`, `branch_parent` (optional) | Runs `agent implement` on that card. The key must be a pending card, or briar stops with an error. |
| `replan` | `why` | Reads the board again. Cards it already knew keep their status. New cards start as pending. Cards gone from the board are dropped. |
| `complete` | `why` | Stops. The plan is finished. |
| `blocked` | `why` | Stops with exit code 1. A person has to act. |

When no card is pending, the answer is `complete` without asking the LLM.
`depends_on` on a card is a hint for the selector, not a hard rule.

## Use it when

- You have a board of small tickets and want draft PRs for them, one by one.
- You want to see what an LLM would pick next before you spend money (`plan next`).
- You finished a card by hand and want the plan to know (`plan advance`).
- You want a list of done, blocked and pending cards (`plan status`).

## Before you start

| For | You need |
|---|---|
| A Jira board. | `--company acme` plus `JIRA_ACME_URL`, `JIRA_ACME_EMAIL` and `JIRA_ACME_TOKEN` (or Jira session cookies). |
| A GitHub Projects board. | `GITHUB_TOKEN` that can read the project. |
| `--llm anthropic` | `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. |
| `--llm openai` | `OPENAI_API_KEY` and the `[openai]` extra. |
| `--llm gemini` | `GEMINI_API_KEY` and the `[gemini]` extra. |
| `--llm bedrock` | AWS credentials from your normal AWS login. |
| `plan run` | Everything `briar agent implement` needs (see [agent.md](agent.md)). The coding agent always uses Claude, whatever `--llm` you pick for the selector. |

Boards briar can read:

| Board | Example |
|---|---|
| Jira board URL. | `https://acme.atlassian.net/jira/software/projects/ACME/boards/12` |
| Jira board URL, company-managed style. | `https://acme.atlassian.net/jira/software/c/projects/ACME/boards/12` |
| Jira short form. | `jira:ACME` |
| GitHub Projects (org). | `https://github.com/orgs/acme-co/projects/3` |
| GitHub Projects (user). | `https://github.com/users/alice/projects/3` |

From Jira, briar reads the open tickets of the project in the URL. The
board number itself is not used. Card keys look like `ACME-42`. From
GitHub Projects, keys look like `acme-co/acme-app#42`. Draft items get
keys like `draft:<title>`.

## A full example

```bash
# 1. Build the plan from the Jira project ACME
briar plan build jira:ACME --name q3 --company acme --llm anthropic

# 2. Look at it, and ask what would run first
briar plan status q3
briar plan next q3 --company acme --llm anthropic

# 3. Run one card only
briar plan run q3 --company acme --repo acme-co/acme-app \
    --tracker jira --llm anthropic --limit 1

# 4. Run the rest, and keep going past a failed card
briar plan run q3 --company acme --repo acme-co/acme-app \
    --tracker jira --llm anthropic --continue-on-failure
```

You get one draft PR per finished card, and a status table that shows
what is done, blocked and pending.

## `plan build`

Reads the board and saves the plan. With `--company`, it also writes the
starting `knowledge:<company>.<plan>` blob.

Without `--llm`, briar fills each card from the ticket text. The summary
is the first paragraph. Scope, out-of-scope and risks come from bullet
lists under headings like "Acceptance criteria", "Out of scope" or
"Risks". Dependencies come from lines like "depends on ACME-41" or
"blocked by ACME-40". The branch is `chore/<key>`, for example
`chore/acme-42`.

With `--llm`, the LLM writes a summary (up to 240 characters), scope,
risks, dependencies (only keys that are on the board), and a branch name
like `fix/login-retry`. The text rules above still fill any gaps.

`plan run` uses each card's branch name and parent: the agent clones the
parent, pushes the card's branch, and opens the PR against the parent.
Before 1.1.85 every card started from the default branch and pushed
`chore/<key>`, whatever the plan said.

```bash
# Text rules only, no LLM
briar plan build jira:ACME --name q3 --company acme
```

You get JSON like `{"plan": "q3", "blob": "plan:q3", "knowledge_seed": "knowledge:acme.q3", "cards": 12, ...}`.

```bash
# LLM summaries, with the company knowledge added to each card's prompt
briar plan build https://github.com/orgs/acme-co/projects/3 \
    --name q3 --company acme --llm anthropic --with-knowledge --print
```

You get the same JSON, then the full plan as markdown.

```bash
# Try it without saving anything
briar plan build jira:ACME --name q3-preview --company acme --dry-run
```

You get the plan as markdown on stdout. Nothing is saved.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `board` | The board to read: a Jira board URL, `jira:<KEY>`, or a GitHub Projects URL. Required. | None. | `jira:ACME` |
| `--name` | Plan name. Other subcommands use it. | Made from the last two parts of the URL. `jira:ACME` gives `jiraacme`, and `.../boards/12` gives `boards-12`. Set it yourself. | `--name q3` |
| `--default-branch` | Branch each card starts from. The selector may choose another per card (for example the branch of the card it builds on). If this branch does not exist in the repo, the card starts from the repo's real default branch, with a warning. | `main` | `--default-branch develop` |
| `--max-cards` | Most cards to read from the board. | `50` | `--max-cards 20` |
| `--with-knowledge` | Add `knowledge:<company>`, `active-tickets:<company>` and `active-work:<company>` to each card's prompt. Needs `--company`. Has an effect only with `--llm`. | Off. | `--with-knowledge` |
| `--print` | After saving, also print the plan as markdown. | Off. | `--print` |
| `--dry-run` | Build the plan and print it, but save nothing. | Off. | `--dry-run` |
| `--llm` | LLM for card summaries: `anthropic`, `openai`, `gemini` or `bedrock`. Empty means text rules only. If its credentials are missing, briar warns and uses the text rules. | Empty. | `--llm anthropic` |
| `--model` | Model name for `--llm`. | The provider's default: `claude-sonnet-4-5`, `gpt-4o`, `gemini-2.5-pro`, or `anthropic.claude-sonnet-4-20250514-v1:0` on Bedrock. | `--model gpt-4o` |

`build` also takes `--store`, `--root` and `--company` (see "Shared
parameters" below).

Building again with the same `--name` replaces the plan. Every card is
`pending` again, and `knowledge:<company>.<plan>` goes back to a fresh
summary, so the learned notes are lost.

## `plan show`

Prints a saved plan as markdown: the board, then each card with its
status, summary, scope, risks and dependencies.

```bash
briar plan show q3
```

You get the plan as markdown on stdout.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `name` | Plan name, as given to `build --name`. Required. | None. | `q3` |

Also takes `--store`, `--root` and `--company`.

## `plan status`

Shows the cards grouped as DONE, IN PROGRESS, BLOCKED and PENDING. It
reads the plan, and the `plan run` journal for start times and failure
reasons.

```bash
briar plan status q3
briar plan status q3 --format json
```

The first prints a table. The second prints JSON with `counts` and one
list per status. Blocked cards show the last error.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `name` | Plan name. Required. | None. | `q3` |
| `--journal-store` | Where the journal is. Only `file` exists. | `file` | `--journal-store file` |
| `--journal-root` | Folder of the journal. | `./journal` | `--journal-root /var/lib/briar/journal` |

Also takes `--store`, `--root` and `--company`.

The DONE rows have room for a commit and a PR link. In 1.1.75 `plan run`
does not record them, so those columns are usually empty.

## `plan next`

Asks the selector what to do next and prints the answer. It changes
nothing, so it is safe to run any time. It does cost one LLM call.

```bash
briar plan next q3 --company acme --llm anthropic
```

You get JSON like `{"plan": "q3", "action": "pick", "key": "ACME-42", "why": "...", "branch_parent": "", "title": "...", "branch_name": "fix/login-retry", "summary": "..."}`.
The last three fields appear only for `pick`.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `name` | Plan name. Required. | None. | `q3` |
| `--llm` | LLM for the selector: `anthropic`, `openai`, `gemini` or `bedrock`. Required. | None. | `--llm openai` |
| `--model` | Model name for `--llm`. | The provider's default. | `--model gpt-4o` |
| `--journal-store` | Where the journal is. Only `file`. | `file` | `--journal-store file` |
| `--journal-root` | Folder of the journal. | `./journal` | `--journal-root ./journal` |

Also takes `--store`, `--root` and `--company`. The selector sees past
results from the journal, the pending cards, and the first 8000
characters of `knowledge:<company>.<plan>` and of `knowledge:<company>`.
The company here is the one saved in the plan at build time.

## `plan advance`

Sets one card's status by hand. Use it after you finish a card yourself,
or to retry a blocked card.

```bash
# You merged ACME-42 by hand
briar plan advance q3 --card ACME-42

# Let the selector pick ACME-43 again
briar plan advance q3 --card ACME-43 --status pending
```

You get the updated card as JSON.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `name` | Plan name. Required. | None. | `q3` |
| `--card` | Key of the card to change. Required. | None. | `--card ACME-42` |
| `--status` | New status: `pending`, `in_progress`, `done` or `blocked`. | `done` | `--status blocked` |

Also takes `--store`, `--root` and `--company`. A key that is not in
the plan stops with `card 'ACME-99' not in plan 'q3'` and exit code 1.

## `plan run`

Runs the loop: the selector picks a card, `agent implement` does it,
and briar saves the result. After each finished card, the LLM rewrites
`knowledge:<company>.<plan>`. It goes on until the selector says
`complete` or `blocked`, or a limit is hit.

```bash
# One card only, to check the setup
briar plan run q3 --company acme --repo acme-co/acme-app \
    --tracker jira --llm anthropic --limit 1
```

You get one card done or blocked, then a JSON summary.

```bash
# Check every prompt without calling Claude for code
briar plan run q3 --company acme --repo acme-co/acme-app \
    --tracker jira --llm anthropic --dry-run --limit 3
```

Each picked card runs `agent implement --dry-run`. The selector still
calls the LLM, so you see which card it would pick next. Since 1.1.81 a
dry run changes nothing: the saved plan keeps its card statuses, the
notes step is skipped, and the session is journaled as `plan.dry-run`,
which the next real run and `plan status` do not read. Before 1.1.81 a
dry run marked each picked card `done`; set them back with
`plan advance --status pending`.

```bash
# The whole board, past failures, with a bot identity and a runbook
briar plan run q3 --company acme --repo acme-co/acme-app \
    --tracker jira --llm anthropic --continue-on-failure --max-replans 1 \
    --git-user-name "Acme Bot" --git-user-email bot@acme.com \
    --runbook runbooks/acme.yaml
```

You get a draft PR per finished card. Failed cards are marked `blocked`
and the loop goes on.

```bash
# Selector on OpenAI. The coding agent still uses Claude, with its default model.
briar plan run q3 --company acme --repo acme-co/acme-app \
    --tracker jira --llm openai --model gpt-4o
```

The summary looks like this:

```text
{"plan": "q3", "done": 3, "blocked": 1, "replans": 0, "stopped_early": false, "remaining_pending": 8}
```

### Loop parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `name` | Plan name. Required. | None. | `q3` |
| `--limit` | Stop after this many picked cards. `0` means no limit. | `0` | `--limit 1` |
| `--continue-on-failure` | When a card fails, mark it `blocked` and go on. Without it, the run stops at the first failure. | Off. | `--continue-on-failure` |
| `--max-replans` | Most `replan` answers in one run. One more stops the run with exit code 1. | `3` | `--max-replans 1` |
| `--llm` | LLM for the selector and for the notes written after each card: `anthropic`, `openai`, `gemini` or `bedrock`. Required. | None. | `--llm anthropic` |
| `--model` | Model for `--llm`. It reaches the coding agent only with `--llm anthropic`. With another `--llm`, the agent uses Claude's default model. | The provider's default. | `--model claude-sonnet-4-6` |

### Parameters passed to `agent implement` for each card

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--owner` | Repo owner or Bitbucket workspace. | From `.briar.toml` `[repo]`, else the git `origin` remote. | `--owner acme-co` |
| `--repo` | Repo as `owner/repo`, or a bare name with `--owner`. | From `.briar.toml` `[repo]`, else the git `origin` remote. | `--repo acme-co/acme-app` |
| `--tracker` | Where the card tickets live: `jira`, `github-issues`, `bitbucket-issues` or `linear`. Set it to match the board. | `github-issues`, or `tracker` in `.briar.toml`. | `--tracker jira` |
| `--tracker-project` | Tracker project passed to each card. | `owner/repo`. | `--tracker-project ACME` |
| `--provider` | Code host: `github` or `bitbucket`. | `github`, or `provider` under `[repo]` in `.briar.toml`. | `--provider bitbucket` |
| `--max-iter` | Most agent turns per card. | `0`, which means 30. | `--max-iter 40` |
| `--git-user-name` | Commit name in each worktree. | The runbook's `git_identity.name`, then your `git config` (not when `CI` is set). | `--git-user-name "Acme Bot"` |
| `--git-user-email` | Commit email in each worktree. | The runbook's `git_identity.email`, then your `git config` (not when `CI` is set). | `--git-user-email bot@acme.com` |
| `--keep-worktree` | Keep each card's temp folder. Failed cards always keep it. | Off. | `--keep-worktree` |
| `--dry-run` | Run `agent implement --dry-run` for each card, and save nothing: no card status, no notes, no `plan.run` history. | Off. | `--dry-run` |
| `--runbook` | Runbook YAML. The company's `messages:`, `mcp:` and `git_identity` blocks are used. | None. | `--runbook runbooks/acme.yaml` |
| `--meeting-key` | One Fireflies meeting ID to add to every card's prompt. | None. | `--meeting-key 01JB7Q4ZK3M8` |
| `--meeting-query` | Words to search meetings for. | The card key. | `--meeting-query "q3 planning"` |
| `--slack-query` | Words to search Slack for. | The card key. | `--slack-query "q3 launch"` |

The hidden agent flags work here too: `--meeting`, `--meeting-top-k`,
`--meeting-max-bytes`, `--chat`, `--slack-top-k` and `--slack-max-bytes`
(see [agent.md](agent.md)). There is no `--no-default-mcp` here. To
skip the helper tools, or to hide their warning on a plain install, set
`BRIAR_NO_DEFAULT_MCP=1`. Each card reads knowledge from the
plan's own `--store` and `--root`.

`run` also takes `--store`, `--root`, `--company`, `--journal-store` and
`--journal-root`. `--company` is required here.

### Exit codes for `plan run`

| Code | When |
|---|---|
| `0` | The selector said `complete`, or `--limit` was reached, and no card failed in this run. |
| `1` | The selector said `blocked`, the replan limit was hit, the selector answered with bad JSON or an unknown key, or a card failed while `--continue-on-failure` was on. Also when `--company` or the LLM credentials are missing. |
| Same as the agent | A card failed and `--continue-on-failure` was off. The run stops at that card. |

Every step goes to the journal: `plan.run.start`, `plan.next.decision`,
`plan.run.card.start`, `plan.run.card.completed` or
`plan.run.card.failed`, `plan.replan.requested`, and finally
`plan.run.completed` or `plan.run.stopped`. The `stopped` value says why:
`limit_reached`, `selector_error`, `blocked`, `replan_cap` or
`first_failure`. Read it with `briar journal list --command plan.run`.

## `plan list`

Lists saved plans.

```bash
briar plan list
```

You get one row per plan, with names like `plan:q3`.

Takes only `--store`, `--root` and `--company`.

## `plan clear`

Deletes a saved plan. It asks first.

```bash
briar plan clear q3
briar plan clear q3 --yes
```

The first asks `Delete plan 'q3'? [y/N]`. The second deletes without asking.
`knowledge:acme.q3` is not deleted. Remove it with
`briar context delete knowledge:acme.q3` if you want.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `name` | Plan name. Required. | None. | `q3` |
| `--yes` | Do not ask before deleting. | Off (asks). | `--yes` |

Also takes `--store`, `--root` and `--company`. Exit code 1 when you
answer no, or when the plan does not exist.

## Shared parameters

Every subcommand takes these three.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--store` | Knowledge store for the plan: `file` or `postgres`. | `postgres` if `BRIAR_DATABASE_URL` is set, else `file`. `BRIAR_STORE` or `store` in `.briar.toml` change it. | `--store postgres` |
| `--root` | Folder of the file store. Ignored for Postgres. | `./knowledge`, or `root` in `.briar.toml`. | `--root ./kb` |
| `--company` | Company key. It picks per-company credentials (Jira, Postgres) and names `knowledge:<company>.<plan>`. Without it, `build` writes no plan knowledge and `run` refuses to start. | `BRIAR_COMPANY`, or `company` in `.briar.toml`. | `--company acme` |

Use the same `--store` and `--root` for every subcommand of one plan.
Otherwise briar looks in another place and reports `plan not found: plan:q3`.

## Good to know

- `plan run` uses `--tracker github-issues` by default, even for a plan
  built from Jira. Pass `--tracker jira` (or set `tracker = "jira"` in
  `.briar.toml`). With the wrong tracker the agent gets no ticket text.
- A card left `in_progress` after a crash or Ctrl-C is never picked
  again. Set it back with `plan advance --status pending`.
- After each finished card, the notes LLM sees only the card's title and
  summary. In 1.1.75 briar does not pass it the code diff. A failed
  write is logged and never stops the loop.
- `plan run` stops on the first failed card unless you pass
  `--continue-on-failure`.
- Try a new board with `--limit 1` first. Each card can use up to 30
  agent turns.

| Problem | What to do |
|---|---|
| `the following arguments are required: --llm` (exit code 2). | `next` and `run` have no fallback without an LLM. Pass `--llm anthropic` or another provider. |
| `--llm=anthropic is not available (missing credentials?)`. | Set the provider's key (see "Before you start"). |
| `jira tracker is not available`. | Pass `--company acme` and set the `JIRA_ACME_*` variables. |
| `GITHUB_TOKEN not set`. | Export `GITHUB_TOKEN` before building from a GitHub Projects board. |
| `plan not found: plan:q3`. | Wrong name, `--store` or `--root`. Run `briar plan list` with the same store flags. |
| The selector keeps answering `replan`. | The board changed, or the plan knowledge is thin. Check `briar context get knowledge:acme.q3`. Lower `--max-replans` to stop sooner. |
| `selector picked key 'ACME-9', not in pending [...]`. | The LLM named a card that is not pending. Run again, or use `plan advance` to fix statuses. |

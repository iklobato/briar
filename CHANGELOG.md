# Changelog

All notable changes to `briar-cli` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases are
cut automatically on merge to `main` (patch bump + PyPI + Docker).

## [1.1.100] - 2026-10-29

### Docs

- README: a header with badges and quick links, a demo GIF (a real
  `extract` and `agent implement --dry-run` on this repo), and a "What you
  get" list. Install, `.briar.toml` and Docker are short now and link to
  usebriar.com/docs; the four "See it run" diagrams fold open on click.
  Removed the old version notes ("You get 1.1.75", "Since 1.1.7x").

## [1.1.99] - 2026-10-29

### Docs

- README shows the five diagrams from usebriar.com: where the knowledge
  comes from and where the work goes, which command to use, inside an
  agent run, answering a review, and working a board. The SVG files are
  in `docs/diagrams/`.

## [1.1.98] - 2026-10-13

### Docs

- `agents/plan.md` and `agents/agent.md` describe 1.1.95 to 1.1.97: what
  the bash tool accepts, that `implement` exits 1 without a commit, and
  when a card counts as done. A real `plan run` on a test board opened
  two stacked draft PRs on 1.1.97.

## [1.1.97] - 2026-10-13

### Fixed

- **A card put back to pending is picked again.** The selector read
  done and failed cards from the journal only, so after `plan advance
  --status pending` the card still showed as completed and `plan run`
  ended with "no remaining work" (seen in a real run). The card's current
  status now wins; history of cards no longer on the plan is kept.

## [1.1.96] - 2026-10-13

### Fixed

Found in a real `plan run` on a test board:

- **A card stacks on a finished dependency.** When the selector names no
  `branch_parent`, `plan run` now starts a card from the branch of its
  first dependency that is done. Before, card #2 started from `main`,
  did not find card #1's code and wrote nothing. If that branch is gone
  (deleted after merge) the card starts from the repo's default branch.
- **A card is only done when the agent made a commit.** `agent
  implement` (not a dry run) that ends without a commit now exits 1, so
  `plan run` marks the card blocked. Before, an agent that only left a
  comment counted as done. `prfix` is unchanged: it may rightly commit
  nothing.

## [1.1.95] - 2026-10-13

### Fixed

- **The agent's bash tool reads quotes before it splits commands.** It
  split on `;`, `|` and `&&` first, so a quoted PR body, commit message
  or `python3 -c "..."` with one of those characters was rejected ("No
  closing quotation"). In a real `plan run` the agent spent all 30 turns
  on this and never opened the PR. Two gaps closed at the same time: a
  command after a single `&` and a command on a new line were run without
  checking their verb against the allowlist.
- The bash tool's description tells the model that a `VAR=value` prefix
  is not allowed (the real run lost turns on `PYTHONPATH=... python3`).

## [1.1.94] - 2026-10-13

### Fixed

- **The MCP tool `extract_run` can read Jira and Linear.** It had no way
  to name a tracker or a project key (`repo` feeds only GitHub and
  Bitbucket Issues), so `active-tickets` and `ticket-archaeology` could
  not run over MCP for Jira or Linear. It now takes `tracker` and
  `ticket_project`.

## [1.1.93] - 2026-10-13

### Fixed

- **`plan run` gives the plan's notes to each card's agent.** The plan
  notes blob `knowledge:<company>.<plan>` (seeded by `plan build`,
  rewritten after each finished card) reached only the selector: the
  agent's knowledge loader keeps only extractor sections, so it dropped
  the blob, though the writeback code said agents saw it. `plan run` now
  reads the blob before each card and `agent implement` adds it to the
  prompt as a "Plan notes" section.

## [1.1.92] - 2026-10-13

### Fixed

- **`.briar.toml` `store = "postgres"` no longer breaks `briar journal`.**
  The journal's `--store` and `--root` shared their names with the
  knowledge store's, so the project config set them too: every `briar
  journal` command failed with "unknown journal store", and `root` moved
  the journal into the knowledge folder. The journal flags have their own
  names inside briar now; the flags you type are the same.
- **Keys saved to a remote store reach other commands.** `briar auth
  login --cred-store ssm|aws-secretsmanager|vault` saved keys that no
  other command loaded (only the envfile was read at start). When
  `$BRIAR_DEFAULT_STORE` names one of those stores, every command now
  loads its keys at start, after the envfile; the shell and the envfile
  still win, and names like `LD_PRELOAD` or `PATH` are never loaded.
- `briar auth refresh -h` no longer says it renews OAuth / SSO keys. No
  target supports refresh yet; the help now says so.

## [1.1.91] - 2026-10-13

### Fixed

- **`briar extract --repo` replaces the config repo.** The default repo
  from `.briar.toml` or the git remote was kept, and `--repo` was added to
  it, so `briar extract --repo other/x` inside a checkout also mined the
  checkout's repo. The first `--repo` now replaces the default; repeating
  `--repo` still collects every value.

## [1.1.90] - 2026-10-13

### Fixed

- **`runbook serve` runs each company's task once.** The scheduler makes
  one job per (company, task), but each job ran that task for every
  company that had it. With N companies sharing a task name, each one ran
  N times per period, and the per-company time offset was lost.
  `extract_runbook` takes a `company` now and each job passes its own.

## [1.1.89] - 2026-10-13

### Fixed

- **`ci-health` works on Bitbucket.** The Bitbucket provider passed raw
  pipeline names through (`COMPLETED`, `SUCCESSFUL`, `FAILED`), named each
  run by its unique build number and set no end time, while `ci-health`
  compares against GitHub's `success`/`failure`. So on Bitbucket the pass
  rate was always 0, no pipeline was ever flagged flaky, and there was no
  run duration. `list_ci_runs` now maps states and results to the GitHub
  words, names a run by its pipeline selector (for example
  `branches: main`), and sets `updated_at` from `completed_on`.

## [1.1.88] - 2026-10-13

### Fixed

- **The agent can open a Bitbucket PR.** The Bitbucket PR recipe told the
  agent to use `curl`, which the agent's shell refuses, so Bitbucket runs
  ended with a pushed branch and no PR. The recipe now gives the agent a
  short Python script to save under `.git/` (never committed) and run
  with `python3`. It uses the same auth as briar's own Bitbucket calls
  (Bearer for workspace tokens, Basic for app passwords) and sets
  `destination` when there is a base branch. The bash tool's description
  no longer lists `curl` as allowed.

## [1.1.87] - 2026-10-13

### Fixed

- **`agent implement` no longer gets the PR-review procedure.** The
  agent's first message always started with prfix's steps ("the PR branch
  you should fix", `gh pr view`, reply to review threads), and the
  implement steps came after as "additional instructions", so an
  implement run read two procedures that disagree. Only `prfix` gets that
  procedure now; `implement` gets a short intro and its own steps.

## [1.1.86] - 2026-10-13

### Fixed

- **A runbook with no `knowledge.name` now feeds the agents.** The default
  blob was the path `./knowledge/<company>.md`, but coding agents read only
  blobs named `knowledge:<company>...`, so scheduled extracts never reached
  them. The default is now `knowledge:<company>` (file
  `./knowledge/knowledge/<company>.md`), the same name `briar extract`
  writes. An explicit `knowledge.name` or `knowledge_file` is unchanged.
  An old `./knowledge/<company>.md` is left in place; delete it by hand.

## [1.1.85] - 2026-10-13

### Fixed

- **`plan run` uses each card's branch name and parent.** The plan stored
  a branch name and a parent per card (from the LLM, or the selector's
  `branch_parent` pick), but `agent implement` always cloned the default
  branch and pushed `chore/<key>`, so cards meant to stack on each other
  did not. `agent implement` now takes `--branch-name` and
  `--base-branch`: it clones the base, the agent pushes the branch, and
  the draft PR targets the base (`gh pr create --base`, Bitbucket
  `destination.branch.name`). `plan run` passes the card's fields. A
  parent equal to the plan's `--default-branch` (default `main`) falls
  back to the repo's real default branch with a warning, so repos whose
  default is `master` keep working; any other missing parent fails the
  card.

## [1.1.84] - 2026-10-13

### Fixed

- **The MCP tool `extract_run` can mine repos.** It had no way to name a
  repo, so every repo and tracker extractor was skipped over MCP (only
  `aws-infra` could run). It now takes `repo`, `since_days` and
  `max_items`, which work like `briar extract --repo/--since-days/--max`.
  The service behind it (`service.extract.run_extract`) now fans the
  canonical keys out to each extractor, as the CLI and runbook YAML
  already did; a per-extractor key still wins.

## [1.1.83] - 2026-10-13

### Fixed

- **The MCP tool `extract_run` keeps the rest of the knowledge blob.**
  1.1.82 fixed this for `briar extract` only: the MCP tool had its own
  copy of the write and still replaced the whole blob. Both now go
  through one function (`service.extract.write_knowledge_blob`), so they
  merge the same way: sections of the extractors that ran are replaced,
  all other sections stay.

## [1.1.82] - 2026-10-12

### Fixed

- **`extract --include x` keeps the rest of the knowledge blob.** Each run
  replaced the whole blob, so refreshing one extractor wiped every other
  section the agents read. Now only the sections of the extractors that
  ran are replaced: one that ran and found nothing loses its old section,
  one that is not set up (or not included) keeps it. The section parser
  the agents use (`KnowledgeSplicer`) moved to
  `KnowledgeComposer.split_sections`, so both sides split the blob the
  same way. To start from an empty blob, delete it first with
  `briar context delete`.

## [1.1.81] - 2026-10-12

### Fixed

- **`plan run --dry-run` changes nothing.** Each card's implement ran dry
  and exited 0, so the loop marked the card `done`, saved the plan and
  asked the LLM to update the plan notes. The `plan.run` history it
  recorded was read back by the next real run and by `plan status`, so a
  preview made the board look finished. A dry run now walks the cards in
  memory only: the saved plan, the notes blob and the `plan.run` history
  stay as they were, and the session is journaled as `plan.dry-run`. The
  selector still calls the LLM.

### Changed

- `FEATURES.md`: `agent prfix` addresses review comments; failing CI logs
  are context only (its procedure has no CI step).

## [1.1.80] - 2026-10-12

### Fixed

- **Tests no longer depend on the machine's telemetry state.** The test
  `cli` fixture left telemetry on, so on a fresh runner the first
  in-process `briar` call printed the first-run banner, and whichever
  exact-stderr test ran first failed at random (it blocked the first try
  of this release). Tests also wrote `~/.config/briar/telemetry.json` into
  the real home and ran at the default `full` tier with the real Sentry
  DSN. The fixture now points `XDG_CONFIG_HOME` at a temp dir with
  telemetry off. Tests only; the CLI is unchanged.

### Changed

- **CHANGELOG** entries for 1.1.72 to 1.1.79, which were released without
  one (or under "Unreleased"). `FEATURES.md` sections 3 and 9 to 11
  rechecked against 1.1.79 (13 flag and env-var fixes) plus a Docker image
  note in section 12.
  `CONTRIBUTING.md`: the release also makes the GitHub Release, and a PR
  writes its CHANGELOG entry under the version it will get. Docs only.

## [1.1.79] - 2026-10-12

### Fixed

- **Docker: a mounted repo works whoever owns it.** On Linux the folder you
  mount at `/work` belongs to your host user, rarely uid 1000, and git in
  the image refused it ("dubious ownership"). The image now trusts every
  folder (`safe.directory '*'`): it has one user and you pick what to mount.
- **Docker: a Mac `~/.gitconfig` no longer breaks the push.** `gh auth
  setup-git` writes `/usr/local/bin/gh` or `/opt/homebrew/bin/gh` into the
  file; the image now links both paths to its own `gh` (PR #53).

## [1.1.78] - 2026-10-12

### Added

- **A GitHub Release for every version.** The release workflow now ends by
  creating the GitHub Release for the new tag, with notes generated from
  the merged PRs. It skips a tag that already has one (PR #52).

## [1.1.77] - 2026-10-12

### Fixed

- **MCP setup failures print one `error:` line.** A runbook that asks for
  MCP servers on an install without the `mcp` package (or a server that
  never connects) used to end in a traceback and exit 2. `McpError` is now
  a `CliError`, so briar prints `error: MCP support requires the mcp
  package...` and exits 1, like every other user-facing failure (PR #51).

## [1.1.76] - 2026-10-12

### Changed

- **README and `agents/*.md` rewritten in plain language**, with every
  parameter: what it does, default, allowed values and an example. New
  pages `agents/mcp.md` and `agents/setup.md`. Docs only (PR #48).

## [1.1.75] - 2026-10-11

### Fixed

- **`briar agent` and `plan run` work in the Docker image.** `iklob1/briar`
  had neither `git` nor `gh`, so every run failed at the clone ("Bad git
  executable"). The image now ships both, and `gh` is git's login helper
  for github.com, so pushes use `GITHUB_TOKEN`. It also sets
  `BRIAR_NO_DEFAULT_MCP=1`, because it has no `npx` or `uvx` for the
  built-in think/time MCP servers; `-e BRIAR_NO_DEFAULT_MCP=0` turns them
  back on in an image that adds them (PR #50).

## [1.1.74] - 2026-10-11

### Fixed

- **Agents run on a plain `pip install briar-cli`.** Every `briar agent`
  run and every `plan run` card, even `--dry-run`, failed with `McpError:
  MCP support requires the mcp package`, because the built-in think/time
  MCP servers need the `mcp` package from the `[mcp]` extra. Without it
  they are now skipped with one warning, like `--no-default-mcp`. Servers a
  runbook declares still fail loudly when `mcp` is missing (PR #49).

## [1.1.73] - 2026-10-11

### Changed

- **PyPI describes the CLI.** The package description and keywords still
  described the archived Briar SaaS client; they now describe the CLI.
- **The Docker image carries `io.modelcontextprotocol.server.name`**, so
  the official MCP registry can list `docker.io/iklob1/briar` as
  `io.github.iklobato/briar` (PR #47).

## [1.1.72] - 2026-10-08

### Removed

- Unused code: `CANONICAL_LIST` and `CANONICAL_SCALAR` in
  `extract/canonical.py`, and `CollectorRegistry.for_paths` in
  `dashboard/collectors.py`. No behavior change (PR #46).

## [1.1.71] - 2026-10-08

### Changed

- **CHANGELOG** entries for 1.1.60 to 1.1.70, which were released without
  one. Docs only.

## [1.1.70] - 2026-10-08

### Fixed

- **`briar agent` rejects typos in `--provider`, `--tracker` and
  `--meeting`.** They had no `choices`, so `--tracker jiraa` parsed, failed
  only after the clone, and the agent ran with no ticket context. Now argparse
  exits 2 with "invalid choice", using the provider, tracker and meeting
  registries (#35, PR #44). `plan run` gets the same `--meeting` check.

## [1.1.69] - 2026-10-08

### Fixed

- **MCP `serverInfo.version` is briar's version.** `briar mcp serve`
  advertised the `mcp` library version (e.g. 1.27.2) on `initialize`
  (#37, PR #43).

## [1.1.68] - 2026-10-08

### Fixed

- **`briar secrets doctor` works outside a repo checkout.** With no
  `--examples` and no `./examples` dir (the wheel ships none) it now prints
  "no runbooks to check" and exits 0. An explicit `--examples <dir>` that
  does not exist still exits 1 (#36, PR #42).

## [1.1.67] - 2026-10-08

### Fixed

- **`briar scaffold pr-fixes -h` shows the real defaults** (`pr-fixer`,
  `one-shot`) instead of hard-coded `engineer` / `plan-approve-act`
  (#34, PR #41).

## [1.1.66] - 2026-10-08

### Changed

- **Removed 11 unused imports and fixed stale comments** (`AgentRunner`
  docstring, a stale `noqa`). No behavior change (#38, PR #40).

## [1.1.65] - 2026-10-08

### Fixed

- **`briar scaffold pr-fixes` defaults to an hourly cron trigger.** It
  inherited the `github_webhook` trigger, which fires on `issues.opened` /
  `issues.labeled`, so a PR fixer built with defaults never reacted to
  reviews. `--trigger-kind github_webhook` still works, and the webhook
  flags now need it on pr-fixes (#33, PR #39).

## [1.1.64] - 2026-10-08

### Changed

- **The knowledge store env var is now `BRIAR_STORE`.** `BRIAR_DEFAULT_STORE`
  was read both as the `auth --cred-store` default and as the knowledge
  `--store` default, so `BRIAR_DEFAULT_STORE=vault` broke `extract` and
  `mcp serve` with "unknown knowledge store 'vault'". `BRIAR_DEFAULT_STORE`
  is now credentials only. If you used it to pick the knowledge store, set
  `BRIAR_STORE` (or `store` in `.briar.toml`) instead (PR #32).

## [1.1.63] - 2026-10-08

### Fixed

- **`plan run --model` no longer reaches the coding agent unless
  `--llm anthropic`.** `--llm openai --model gpt-4o` sent `gpt-4o` to the
  Anthropic-only agent and every card failed (PR #31).

## [1.1.62] - 2026-10-08

### Fixed

- **`extract --assignees-allow/-block` really filter PRs.** They were parsed
  and then ignored. `PullRequest` now carries GitHub assignees; Bitbucket PRs
  have none, so an allow-list drops them (PR #30).

## [1.1.61] - 2026-10-08

### Fixed

- **AWS falls back to the default credential chain.** With no
  `AWS_<COMPANY>_*` keys and no `~/.aws` profile named after the company,
  boto3 raised `ProfileNotFound` before trying env vars, SSO or the instance
  role, and the AWS extractors returned nothing (PR #29).

## [1.1.60] - 2026-10-08

### Fixed

- **`extract --repo owner/repo` is no longer sent to Jira as a project key.**
  It crashed `active-tickets` with `jira project key must match ...`. `--repo`
  now feeds the tracker project only for GitHub Issues and Bitbucket Issues
  (PR #28).

## [1.1.59] - 2026-10-08

### Changed

- **Docs synced with the code.** README, FEATURES, ARCHITECTURE*, DEPLOY_EC2,
  CONTRIBUTING and `agents/*.md` were checked against v1.1.56: command examples
  that no longer parsed (`auth status` without a target, `plan next` without
  `--llm`, `dashboard --knowledge`), stale versions, missing commands (`mcp`,
  `chat`, `init`, `config`, `doctor`, `completion`) and wrong env var names.
  Docs only, no code change.

## [1.1.58] - 2026-10-08

### Fixed

- **`briar auth login` with no input no longer crashes.** With no terminal and
  an empty stdin (CI, scripts, agents) it printed a `termios.error` /
  `EOFError` traceback and exited 2. It now prints "no input to read: run this
  in an interactive terminal, or pipe the answer on stdin" and exits 1. Piping
  the token on stdin still works.
- **Refresh hint names a real command.** When a credential can't be refreshed,
  the message said `briar auth login --provider <kind> ...`; `--provider` does
  not exist. It now says `briar auth login <kind> --company <company>`.

## [1.1.57] - 2026-10-08

### Fixed

- **Python 3.10 works again.** `config.py` imported `tomllib` (3.11+), so every
  command that loads the config failed on 3.10 with `No module named
  'tomllib'`, although `requires-python` is `>=3.10`. It now falls back to the
  `tomli` backport, a new dependency only on Python < 3.11.
- **CI really tests 3.10 and 3.11.** The `pytest (py3.10)` and `py3.11` jobs
  ran on the runner's system Python 3.12; the job now sets `UV_PYTHON` from the
  matrix.

## [1.1.56] - 2026-06-24

### Fixed

- **Docker image can now serve MCP.** The image installed the base wheel with
  no extras, so `docker run iklob1/briar mcp serve` crashed with
  `ModuleNotFoundError: No module named 'mcp'`. The Dockerfile now installs the
  wheel with the `[mcp]` extra (FastMCP), so the documented
  `docker run iklob1/briar mcp serve` works. README wording updated: the image
  carries the base providers + the MCP server (not the optional LLM/cloud
  extras).

## [1.1.55] - 2026-06-24

briar can now be driven BY agents, not just run them.

### Added

- **`briar mcp serve`** exposes briar over the Model Context Protocol
  (FastMCP) so any MCP host (Claude Code, Claude Desktop, Cursor, the new
  `briar chat`) can call it. `stdio` transport by default; `http` (Streamable
  HTTP) with bearer auth and a no-public-bind-without-token guard. Reads
  (`knowledge_list/get/categories`, `runbook_get/validate`, `version`) are
  ungated; mutations (`knowledge_put/delete`, `extract_run`,
  `mcp_server_set_enabled`) preview by default and act only with
  `confirm=true`. The server pins the store/root, so the model never passes a
  backend.
- **`briar chat`** an interactive assistant that drives briar's own MCP server
  with a human-in-the-loop approval gate the model cannot bypass.
- **`briar.service` seam** a presentation-free, gated core (extract, knowledge,
  runbook), plus a runbook writer. Only the MCP server calls it today; the CLI
  commands and the dashboard do not.

## [1.1.54] - 2026-06-24

### Added

- **Flow 15 in `agents/flows.md`**: one end-to-end example that uses every
  provider and extractor, ships a Jira card with `agent implement`, then
  auto-fixes the PR with `agent prfix`. Docs only.

## [1.1.53] - 2026-06-24

### Fixed

- **Slack auth errors name the fix.** On an auth error the read-only Slack
  provider's message now tells you to refresh the `SLACK_*` creds instead of
  a bare `invalid_auth`.

## [1.1.52] - 2026-06-24

### Fixed

- **Release image push.** The release workflow pushed to `iklobato/briar`;
  it now pushes to `iklob1/briar`, the image the docs use. The PyPI publish
  step sets `attestations: false` to stop a warning on every run.

## [1.1.51] - 2026-06-24

Documentation: a leaner README and Docker usage everywhere. No code or
behavior changes.

### Changed

- **Simplified the README** to the common flow (install, auth, extract, act),
  moving the deep-dive material to the `agents/` guides and the docs site.
- **Every command example now shows a Docker form** next to the native
  `briar ...` line, using the published `iklob1/briar` image. The README
  documents the canonical `docker run` invocation (repo + credential mounts,
  LLM key) and its variants for `dashboard` (published port) and
  `agent` / `plan run` (SSH + git-identity mounts).

## [1.1.50] - 2026-06-24

Documentation fixes: align the command guides with the actual CLI surface.
No code or behavior changes.

### Fixed

- **`briar dashboard` docs** listed flags the command no longer has
  (`--knowledge-store`, `--knowledge`, `--secrets-file`, `--du-path`, dropped
  when the dashboard was slimmed to a monitoring view) and showed the wrong
  `--host` default (`0.0.0.0`; the real default is `127.0.0.1`, loopback only).
- **`briar journal list --command-prefix`** in the agent/plan/flows/runbook
  guides: the flag is `--command` (it already matches on prefix).
- **`briar secrets doctor`** examples used `--company` and `--only-missing`,
  which it does not accept; `doctor` reports every company and has only
  `--examples` / `--cred-store`.

## [1.1.49] - 2026-06-24

Cross-command flag unification and common-path simplification. Every old
spelling still works (deprecated aliases print a one-line note), so this is
backward compatible.

### Added

- **`--repo owner/repo` slug on `agent` and `plan run`**, matching `extract`.
  `--owner`/`--repo` still work and are no longer required (both infer from
  the git `origin` remote in a checkout).
- **Derived `--store` default**: `postgres` when `BRIAR_DATABASE_URL` is set,
  else `file`, on `agent`, `plan`, and `extract`. A laptop with no database no
  longer fails on a postgres connection.
- **Derived `--ticket-project`** for `agent implement`: taken from the ticket
  key for Jira/Linear (`ACME-42` becomes `ACME`) or from owner/repo for
  GitHub/Bitbucket Issues, so the smallest call is `--ticket-key <KEY>`.
- **Ambient git identity**: when neither `--git-user-name`/`--git-user-email`
  nor the runbook's `git_identity` is set, `agent` falls back to your local
  `git config user.name`/`user.email` (suppressed under `$CI`).
- **`context --store`/`--root` accepted after the sub-op** (e.g.
  `briar context get x --store postgres`), not only before it.
- **Slack enrichment flags on `plan run`** (`--slack-query`, ...), matching
  `briar agent`.

### Changed

- **Knowledge-store root is `--root` everywhere.** `agent`'s `--knowledge` is
  now a hidden deprecated alias of `--root`.
- **Credential store is `--cred-store`** on `auth` and `secrets` (deprecated
  alias `--store`), so it no longer clashes with the knowledge-store `--store`.
  This also stops a project-config `store` value from leaking into the
  credential flag.
- **`extract --storage` is hidden and deprecated** in favour of `--store`.
- **`plan run` has one knowledge root**: the per-card `agent implement` reuses
  `--root` (the separate `--knowledge` flag was removed).
- **Agent enrichment sizing knobs hidden from `-h`** (`--meeting-top-k`,
  `--meeting-max-bytes`, `--slack-top-k`, `--slack-max-bytes`, `--meeting`,
  `--chat`); they still work.
- **`scaffold implementation -h`** groups flags by source (github / jira / aws
  / sentry / bitbucket) and trigger, instead of one flat list.

## [1.1.48] - 2026-06-22

### Added

- **Slack read source** (`extract/_chats/slack.py`). A fourth source family
  alongside repos, trackers and meetings: a read-only `ChatProvider` that
  searches Slack and hydrates threads using the browser web-session
  credentials (an `xoxc-` token plus the shared `d`/`xoxd-` cookie), the same
  session-auth shape as the `JIRA_*` family. Read-only is enforced at a single
  chokepoint: any non-read method is refused before the request leaves the
  machine, so it can never post, edit or delete. Set per company via
  `SLACK_<COMPANY>_TOKEN` and `SLACK_<COMPANY>_COOKIE_D`. (This is the read
  counterpart to the existing webhook-based Slack *write* sinks, which are
  unchanged.)
- **`slack-context` task-scoped extractor.** `briar agent implement` and
  `briar agent prfix` now splice the top Slack threads matching the ticket key
  or PR identifier into the agent's prompt, the same way `meeting-context`
  splices transcripts. New flags: `--chat`, `--slack-query`, `--slack-top-k`,
  `--slack-max-bytes` (all optional; the query defaults to the ticket/PR).

## [1.1.47] - 2026-06-19

### Added

- **`briar --version` / `-V`**, **`briar completion bash|zsh`**, **`briar init`**
  (starter `.briar.toml`), **`briar config show`** (each setting's value and
  source) and **`briar doctor`** (offline environment check).
- **Update notice** on stderr when a newer `briar-cli` is on PyPI (once a day;
  opt out with `BRIAR_NO_UPDATE_CHECK`, `DO_NOT_TRACK` or `BRIAR_TELEMETRY=off`).
- `CONTRIBUTING.md`, plus `fmt` / `lint` / `typecheck` / `pytest` / `check`
  Makefile targets. The release workflow now runs the unit suite before the
  version bump.

### Changed

- **Logs are quiet by default (WARNING) and go to stderr.** `--verbose` /
  `BRIAR_VERBOSE` gives DEBUG, `BRIAR_LOG_LEVEL` overrides, and `runbook serve`
  / `runbook extract` still log at INFO. Progress and "wrote <path>" messages
  moved to stderr so stdout stays clean for `--format json`.

### Fixed

- **Release run failed on a stale `uv.lock`.** The lock is regenerated and the
  workflow discards lock churn before pushing.

## [1.1.46] - 2026-06-19

Aggressive CLI parameter simplification: ~60% smaller visible flag surface
with zero capability lost. Fully backward compatible.

### Added

- **Canonical extract flags.** One shared knob per concept —
  `--repo`, `--since-days`, `--max`, `--top-n`, `--sample`,
  `--authors-allow`/`--authors-block`, `--assignees-allow`/`--assignees-block`
  — fans out to every extractor selected with `--include`. Replaces ~50
  per-extractor flags (`--pr-repo`, `--risk-since-days`, `--reviewer-top-n`, …).
  Works on the CLI and in runbook YAML `args:`.
- **Project config** via `.briar.toml` (or `[tool.briar]` in `pyproject.toml`),
  searched upward from the working directory. Resolution precedence:
  `CLI flag > env var > project config > built-in default`. Config can satisfy
  otherwise-required flags (`--company`, `agent --owner`/`--repo`).
- **Git inference** of `--owner`/`--repo` from the `origin` remote when neither
  flag nor config supplies them.
- **Shared scaffold filters** — `--authors-allow`/`--authors-block` and
  `--assignees-allow`/`--assignees-block` apply to every `--source`, replacing
  the per-source `--github-*` / `--bitbucket-*` / `--jira-*` filter trios.
- `briar extract --advanced-help` lists the full per-extractor override flags.
- `--store` as the canonical name for extract's backend flag.

### Changed

- `briar extract -h` now shows the canonical + genuinely-extractor-specific
  flags only (29, down from 77); the per-extractor overrides are hidden.
- Using a legacy per-extractor / per-source flag prints a one-line note
  pointing at its canonical replacement.
- Docs (README, FEATURES, `agents/*`) rewritten around the canonical flags,
  the config file, and inference.

### Deprecated

- Per-extractor flags (`--pr-repo`, `--risk-since-days`, …) and per-source
  scaffold filters (`--jira-authors-allow`, …) are hidden from `-h`. They still
  parse and **override** the canonical value for the rare case where two
  extractors in one invocation need different values for the same concept.

### Notes

- `--storage` remains accepted as an alias of `--store`.
- No breaking changes: every previously valid command still works.

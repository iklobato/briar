# briar 2.0: work status

**State on 2026-11-13:** every 2.0 PR is merged into `v2` (#79 to #110, as merge
commits; `v2` at `f7bc267`, checked with `git ls-remote briar v2 main`), and no 2.0 PR is
open. `v2` runs `briar extract` (GitHub, GitLab, Bitbucket, Jira, Linear, AWS, Azure,
GCP, Fireflies with a company filter) and `briar serve` (GitHub or GitLab, Jira or
GitHub Issues, the ticket choice with Slack, the open PR or MR rule). `main` is still the
1.x line (`ec6d313`, release 1.1.101); 2.0 ships when `v2` merges into `main`. A push to
`v2` runs no CI (`tests.yml` runs on pushes to `main` and on PRs into `main` or `v2`):
each PR was green on its merge ref. Sections 1 to 8 below keep the history of how the
work got here.

**Re-scoped on 2026-11-11:** the core of 2.0 is `briar extract` and `briar serve`, the
agentic development pipeline (watch the tickets assigned to briar and the review comments
on its PRs; plan, build, open the PR, answer every comment; in parallel, with the Claude
Agent SDK; GitHub and Jira only). The decisions are
`docs/decisions/2026-11-11-briar-2-serve-pipeline.md` (S1 to S35), which also list the
earlier decisions they change. Spec 003 builds it in 4 PRs; part 1 is #100, part 2 is #101, part 3 is #102, part 4 is #103.

**Validated for real on 2026-11-11 and 2026-11-12:** the first real `serve` runs (a small
test repo and a private copy of `pallets/click` with real CI) and real `extract` runs found
17 bugs; 15 are fixed in https://github.com/iklobato/briar/pull/104 (on top of #103). What
ran, what it showed, what is fixed and what is open: `docs/briar-2-validation.md`. The
decisions: `docs/decisions/2026-11-11-briar-2-serve-knowledge-draft.md` (W1 to W9, built) and
`docs/decisions/2026-11-11-briar-2-extract-sources.md` (E1 to E27, the plan of spec 007 for
AWS, Azure, GCP, Fireflies, Bitbucket and Linear, not built).

The handoff file for briar 2.0. Read it before you touch the `v2` branch or
any 2.0 PR. It says where the work is, what is done, how the code works
now, what was found on the way, what is left and what to watch.

- Scope: `docs/decisions/2026-11-10-briar-2-pipeline.md` (D1 to D36, plus
  D29a, D29b, D32a, D34), `docs/decisions/2026-11-10-briar-2-source-logins.md`
  (L1 to L21) and `docs/decisions/2026-11-10-briar-2-core-cleanup.md` (C1 to
  C22, which cut 2.0 to 5 commands and change D5, D18, D21, D28, D29, D30,
  D32a, D35) and `docs/decisions/2026-11-10-briar-2-ticket-pipeline.md` (P1
  to P22 and A1 to A15: the ticket pipeline's context, stages and loops, the 4
  agent roles, and the pipeline YAML) and `docs/decisions/2026-11-10-briar-2-knowledge.md`
  (K1 to K20: one file per extractor with front-matter and an `index.md` under
  `~/.briar/knowledge/<company>/`, pipeline notes, history, freshness), and
  for spec 005 (logins) `docs/decisions/2026-11-10-briar-2-logins-spec.md`
  (G1 to G26, which replace L15 and parts of L2 to L5) with the per-PR files
  `2026-11-10-briar-2-logins-pr1.md` (H1 to H10),
  `2026-11-11-briar-2-logins-pr2.md` (I1 to I10) and
  `2026-11-11-briar-2-logins-pr3.md` (J1 to J19), and for spec 003
  (`briar serve`) `2026-11-11-briar-2-serve-pipeline.md` (S1 to S35),
  `2026-11-11-briar-2-review-routine.md` (V1 to V16) and
  `2026-11-11-briar-2-cleanup-pr4.md` (X1 to X6). Those files decide; this
  file reports.
- Facts below were checked on 2026-11-10 and 2026-11-11 (UTC) with the
  command shown. When this file and the code disagree, the code wins: fix
  this file in the same PR.
- The spec-kit working files (`specs/`, `.specify/`) are not committed. What
  an agent needs from them is in this file.

## 1. Where the work is

- **Branch `v2`** takes every 2.0 PR (D27). One 2.0 release ships when `v2`
  merges to `main`. Until then `main` gets 1.x fixes only.
- `v2` holds every 2.0 PR, #79 to #110, merged in order as merge commits (2026-11-13,
  `f7bc267`). `main` is still `ec6d313` (release 1.1.101).
- The PRs are **stacked**: each one is based on the branch of the one
  before it, and each targets `v2`. A PR's diff shows the commits of the
  PRs under it until those merge. Merge them **in order, #79 first, as
  merge commits**.
- CI runs on every PR into `main` or `v2` (`.github/workflows/tests.yml`).
  Every PR of the stack had green CI on its last commit when this was last
  checked (2026-11-11 17:14 UTC, `gh pr checks <n> -R iklobato/briar`): #97 run
  42945883590, #98 run 43011048825, #99 run 43023399944, #100 run 43034311508,
  #101 run 43043290376, #102 run 43052772918, #103 run 43064825959. Nobody has
  reviewed them (the maintainer is the only reviewer; 0 reviews and 0
  comments on #99 to #103).
- The branch of #97 was rewritten and force-pushed twice on 2026-11-11 to
  take a test constant out of its history (section 4, GitGuardian). #98 was
  built on the rewritten branch. Nothing else in the stack was rewritten.

| PR | Branch | What it does |
|---|---|---|
| https://github.com/iklobato/briar/pull/79 | `cleanup/01-scaffold-dead-code` | Removes `briar scaffold`, dead code and the old SaaS docs; adds the byte-for-byte extract snapshot test (`tests/fixtures/extract_snapshot/`, regenerate with `UPDATE_GOLDEN=1`) |
| https://github.com/iklobato/briar/pull/80 | `cleanup/02-dashboard-chat-mcp-server` | Removes `briar dashboard`, `briar chat`, the MCP server and `service/`; `CommandExtract._write_blob` is the one write path of `briar extract` |
| https://github.com/iklobato/briar/pull/81 | `cleanup/03-default-mcp-servers` | Removes the built-in `think` and `time` MCP servers |
| https://github.com/iklobato/briar/pull/82 | `cleanup/04-telemetry-opt-in` | Telemetry is opt-in (`telemetry.v2.json`; the 1.x `telemetry.json` is ignored); event fields go to Sentry as `extra` so its `EventScrubber` runs |
| https://github.com/iklobato/briar/pull/83 | `cleanup/05-remote-credential-stores` | Removes the SSM, Secrets Manager and Vault stores; `credentials/_removed_stores.py` stops a run whose `BRIAR_DEFAULT_STORE` names one |
| https://github.com/iklobato/briar/pull/84 | `cli/01-characterisation-tests` | The 1.x CLI contract tests (`tests/unit/cli/`), the decision notes D29a/D32a/D34 and the source-logins file, and a fix: briar now closes the urllib `HTTPError` responses it catches |
| https://github.com/iklobato/briar/pull/85 | `cli/02-typer-app` | Adds Typer, `cli_app.py`, `settings.py`; moves `version`, `init`, `config`, `doctor`, `telemetry` |
| https://github.com/iklobato/briar/pull/86 | `cli/03-typer-four-commands` | Moves `context`, `journal`, `secrets`, `runbook`; removes `secrets doctor --store`; builds only the named command's options |
| https://github.com/iklobato/briar/pull/87 | `cli/04-typer-auth-agent` | Moves `auth`, `agent`; removes `--knowledge` (agent) and `--store` (auth) |
| https://github.com/iklobato/briar/pull/88 | `cli/05-typer-plan` | Moves `plan` |
| https://github.com/iklobato/briar/pull/89 | `cli/06-removals-docs` | Removes `extract --storage`, `briar completion` and its templates; Typer completion; docs; the spec 002 success checks |
| https://github.com/iklobato/briar/pull/90 | `docs/v2-status` | This file, the four 2026-11-10 decision files, the CLAUDE.md and CONTRIBUTING updates (docs only) |
| https://github.com/iklobato/briar/pull/91 | `core/01-remove-plan-agent` | Spec 006 part 1: removes `plan`, `agent` and the code only they used; moves the personas and rules to `src/briar/prompts/` |
| https://github.com/iklobato/briar/pull/92 | `core/02-small-commands` | Spec 006 part 2: removes `context`, `secrets`, `journal`, `telemetry`, `version`, `messaging`, `notify`; `--format table|json`; `-v` only; telemetry tier from env or `.briar.toml` |
| https://github.com/iklobato/briar/pull/93 | `core/03-file-store-only` | Spec 006 part 3: the file store is the only knowledge store; removes Postgres, `--store`, the `store` setting, `SQLAlchemy`, `psycopg` |
| https://github.com/iklobato/briar/pull/94 | `core/04-extract-config` | Spec 006 part 4: `extract` keeps `--company`, `--include`, `--repo`; every other value moves to `[extract]` / `[extract.<name>]` with short names; the output modes go |
| https://github.com/iklobato/briar/pull/95 | `core/05-final-trims` | Spec 006 part 5: `auth` login and status, `runbook` extract and serve, `briar config` folded into `doctor`, README rewritten around the 5 commands, the success checks |
| https://github.com/iklobato/briar/pull/96 | `fix/doctor-git-remote-hint` | With no git origin, `doctor` says to run `briar init --owner OWNER --repo REPO` or set `[repo]` in `.briar.toml` (F10); no em dash in the `doctor` hints |
| https://github.com/iklobato/briar/pull/97 | `logins/01-config-pasted` | Spec 005 part 1: `[auth]` config and the runbook `auth:` block, `BRIAR_*` names with the 1.x name guard, source targets for `auth login|status` with `--runbook`, the pasted logins (GitHub PAT, Jira token and cookie, Bitbucket token, access token and cookie, Linear key, Fireflies key), the flat credential store |
| https://github.com/iklobato/briar/pull/98 | `logins/02-clouds` | Spec 005 part 2: AWS, GCP and Azure log in through their CLI (`[auth.aws] profile`, `[auth.gcp] config_dir` and `project`, `[auth.azure] tenant` and `subscription`); `profile` leaves `[extract.aws-infra]`; the acquirers and `CredEnv` go |
| https://github.com/iklobato/briar/pull/99 | `logins/03-check` | Spec 005 part 3: the login check before every run ("who am I" plus a read of each resource), in `extract`, `runbook extract` and `serve`, `auth login` and `status`, `doctor` |
| https://github.com/iklobato/briar/pull/100 | `serve/01-runbook-serve` | Spec 003 part 1: the serve decisions (S1 to S35), the runbook `serve:` block, `briar serve DIR` (model, read and write checks; one asyncio loop with `max_parallel` slots per company; the finder; the `work:<company>` state), `doctor` model login |
| https://github.com/iklobato/briar/pull/101 | `serve/02-ticket-routine` | Spec 003 part 2: the ticket routine (Claude Agent SDK in a worktree per ticket; plan, build, verify and self-review loops, PR, CI wait, limits with a draft PR and a report) |
| https://github.com/iklobato/briar/pull/102 | `serve/03-review-routine` | Spec 003 part 3: the review routine (writers and listed bots, marker, edits, resolved threads, base merge, replies, `max_review_rounds`), the agent sandbox and read limit, `ServeRoutine` |
| https://github.com/iklobato/briar/pull/103 | `serve/04-cleanup` | Spec 003 part 4: `briar auth` and `briar runbook` leave; `serve` runs extract-only folders, `doctor --runbooks`, `init` writes an example runbook; the hints, manuals, README and CHANGELOG |
| https://github.com/iklobato/briar/pull/104 | `serve/05-knowledge-draft` | The fixes from the first real runs: absolute knowledge path, PR as a draft until CI passes, verify on a clean copy, CI pending while Actions runs are queued, scheduled tasks into the company blob, no ticket done twice, a failed poll retried, the clone lock, conflicts GitHub has not computed, the mutation tool; `docs/briar-2-validation.md` |
| https://github.com/iklobato/briar/pull/105 | `select/01-selector` | Spec 008 part 1: `briar serve` chooses which tickets to start (not workable, blockers, fixed scores, one model pick with the repo and a reason) |
| https://github.com/iklobato/briar/pull/106 | `select/02-chat` | Spec 008 part 2: `briar serve` reads Slack for the ticket choice (asks of other people, the threads as data) |
| https://github.com/iklobato/briar/pull/107 | `gitlab/3a-extract` | Spec 008 part 3a: GitLab as a code host for `briar extract` (`[auth.gitlab]`, `python-gitlab`) |
| https://github.com/iklobato/briar/pull/108 | `fix/real-run-bugs` | Fixes from real runs: the Fireflies query, the cloud region, the agent bash guard (every line, heredocs, relative programs, builtins, word tokens), the tools of each agent step, the tool cache of the sandbox |
| https://github.com/iklobato/briar/pull/109 | `serve/3b-gitlab` | Spec 008 part 3b: `briar serve` on GitLab, `serve.only`, the open PR or MR rule (N6), the selector with no tool, and the fixes of two real GitLab runs |
| https://github.com/iklobato/briar/pull/110 | `meetings/company-filter-v2` | `meeting-digest` keeps only the meetings of the company (title words, emails and domains, member speakers); a step whose source fails keeps its last section |

### A local test build (not a release)

To try the top of the stack on a machine without publishing anything (a PyPI
version can never be reused, and the 2.0 release is not decided, section 5):

```bash
git -C <repo> worktree add --detach /tmp/briar-build <top commit>
# in /tmp/briar-build/pyproject.toml only: version = "2.0.0.dev0+g<short sha>"
(cd /tmp/briar-build && uv build --wheel -o <out dir>)
uv venv --python 3.13 <out dir>/briar-2.0.0.dev0
uv pip install --python <out dir>/briar-2.0.0.dev0/bin/python <out dir>/briar_cli-2.0.0.dev0+g<sha>-py3-none-any.whl
<out dir>/briar-2.0.0.dev0/bin/briar --version     # briar-cli 2.0.0.dev0+g<sha>
git -C <repo> worktree remove --force /tmp/briar-build
```

- `briar --version` reads the installed package metadata
  (`importlib.metadata`), so the version comes from `pyproject.toml` at build
  time. Never commit the dev version: `release.yml` bumps the patch of what is
  in `pyproject.toml`.
- A separate venv keeps an older `briar` on the `PATH` untouched; run the new
  one by its full path or an alias.
- `uv build -o <dir>` also writes a `.gitignore` (`*`) into `<dir>`.
- The venv is about 409 MB, mostly the Claude Code CLI inside
  `claude-agent-sdk`.
- Built and checked on 2026-11-11 from `748d1d2` (PR #103): `--version`, the 4
  commands in `--help`, `claude-agent-sdk` 0.2.180, `init` writing both files,
  `doctor --runbooks` stopping on `Field required` for the commented model and
  limits.
- The real `serve` runs of 2026-11-11 and 2026-11-12 used this build first, then the
  editable install of the PR #104 branch (`.venv/bin/briar` of its worktree; it still prints
  1.1.101). See `docs/briar-2-validation.md` section 1.

## 2. What is done

Each part below records what that PR did when it was written. Later PRs
changed some of it: `briar auth` and `briar runbook`, which several parts
describe, left in #103 (spec 003 part 4). Section 3 is how the code works now.

### Spec 001: cleanup (PRs #79 to #83)

- Removed: `scaffold`, `dashboard`, `chat`, `mcp serve` and `service/`,
  the default `think`/`time` MCP servers, the SSM/Secrets Manager/Vault
  credential stores (with the `vault` extra and the `moto` extras they
  needed), the first-run telemetry banner, the old SaaS design docs.
- Kept on purpose: the env-file credential store, runbook `mcp:` servers
  (the `[mcp]` extra), the extractors (unchanged output, pinned by the
  snapshot test).
- Numbers: code in `src` went from 30,217 lines to 25,599 (goal: at least
  4,000 fewer); `briar --help` listed 14 commands; mypy 87 errors to 73.

### Spec 002: the command line on Typer (PRs #84 to #89)

- Every command except `extract` is parsed by Typer 0.29 (D29a). `extract`
  keeps its argparse parser until spec 004 (D32a).
- Every command's `run()` still gets the same `argparse.Namespace` as in
  1.x. Proof: `tests/unit/cli/flags_1x.json` (generated once from the 1.x
  parser; generator in commit `14869ca`) drives 257 flag cases, 27 values
  that start with `-`, and 39 bare-command cases, all passing unchanged.
- Settings use a plain pydantic model, not pydantic-settings (D29b).
- Removed: the hidden 1.x aliases `--storage` (extract), `--knowledge`
  (agent), `--store` (auth, secrets); `briar completion` and its 6
  templates. New: `briar --install-completion`, `briar --show-completion`.
- `briar --help` lists 13 commands: extract, runbook, context, agent,
  auth, plan, secrets, journal, telemetry, version, init, config, doctor.
- Success checks (PR #89 body has the commands and output): the
  `ArgumentParser(` calls left (`cli.py`, `extract/canonical.py`,
  `iac/runbook/executor.py`) all serve `extract`; `config.py` touches
  `_actions` only in the extract shim; the extract snapshot goldens are
  untouched since `f01164f`; CI green on Python 3.10, 3.11, 3.12.
- State at the top of the stack (PR #89): `pytest -n auto -m "not
  integration"` 2994 passed, 2 skipped; `pytest -m integration` 93 passed;
  `make smoke` ok for all 13 commands; mypy 73 errors; 25,969 lines in `src`.

### Spec 006: core cleanup, part 1 of 5 (PR #91)

- Moved first, in its own commit (`73b4dff`): the 1.x persona text
  (engineer, pr-fixer, CI fixer, conflict resolver) and the 8 shared rules
  to `src/briar/prompts/` (`roles/engineer.md`, `tasks/*.md`, `rules/*.md`),
  for the spec 003 pipeline (C21, A1). The triager persona was not moved
  (no 2.0 role takes it). Nothing reads these files at run time yet;
  `tests/unit/test_prompts.py` checks they ship.
- Removed: the `agent` and `plan` commands; the packages `agent/` (loop,
  tools, LLM providers), `plan/` (board readers, selector, store), `mcp/`
  (the client behind a runbook `mcp:` block), `iac/scaffold/` (archetypes,
  knowledge splicer); the task-scoped extractors and `extract/_chat.py`,
  `extract/_chats/`; the dependencies `anthropic` and `GitPython`; the
  `mcp`, `openai`, `gemini` extras; the `[agent]` settings (`model`,
  `git_user_name`, `git_user_email`); the `llm key` check of `doctor`; the
  LLM and Slack session env names in `env_vars.py`.
- A runbook company with `mcp:` or `git_identity:` now fails to load with
  exit 1 and the message `` `mcp` was removed in briar 2.0; delete it from
  the runbook `` (`CompanyEntry.REMOVED_KEYS`, test in
  `tests/unit/cli/test_exit_codes.py`).
- Kept on purpose for spec 003: the providers' clone and PR seam
  (`resolve_token`, `clone_url`, `authed_clone_url`, `pr_creation_recipe`)
  and their per-PR verbs (`get_pull`, `list_pr_comments`, ...); `briar
  agent` and `pr-review-context` were their only callers, but the pipeline
  clones, opens PRs and reviews them.
- Numbers on the branch (2026-11-10): `src` 25,969 lines to 18,423
  (`find src -name '*.py' | xargs cat | wc -l`); `briar --help` lists 11
  commands; mypy 73 errors to 39; `pytest -n auto -m "not integration"`
  2136 passed; `pytest -m integration` 78 passed; `make smoke` ok for all
  11. Startup, CPU median of 7 against the 1.x code on the same machine:
  `import briar.cli` 736 ms (1.x) vs 741 ms (822 ms before this PR);
  `main(['--help'])` 39 ms vs 17 ms.

#### Where the deleted code is (FR-013)

Spec 003 reuses some of it. Every path below is in commit `73b4dff` (the
parent of the delete commit in PR #91, with the spec 002 changes) and,
as 1.x shipped it, in tag `v1.1.101`. Read one with
`git show 73b4dff:<path>`; list a folder with
`git ls-tree -r --name-only 73b4dff <folder>`.

| What | Paths | Reused by |
|---|---|---|
| Plan selector (LLM picks the next card) | `src/briar/plan/_selector.py` | spec 003 `board` pipeline |
| Board readers (Jira board, GitHub Projects) | `src/briar/plan/_board.py`, `src/briar/plan/_boards/` | spec 003 `board` pipeline |
| Bash allowlist and forbidden tokens | `src/briar/agent/tools.py` | spec 003 agent tools (A12) |
| Agent loop and LLM providers | `src/briar/agent/runner.py`, `src/briar/agent/_llms/` | spec 003 (LangGraph replaces the loop) |
| MCP client manager | `src/briar/mcp/_manager.py` (and the rest of `src/briar/mcp/`) | not planned |
| Task-scoped extractors | `src/briar/extract/{ticket_context,pr_review_context,meeting_context,slack_context}.py`, `src/briar/extract/_chat.py`, `src/briar/extract/_chats/` | spec 003 context (P1, P3) |
| Archetypes and knowledge splicer | `src/briar/iac/scaffold/` | already moved to `src/briar/prompts/` |

### Spec 006: core cleanup, part 2 of 5 (PR #92)

- Removed: the commands `context`, `secrets`, `journal`, `telemetry`,
  `version` (each now gets `No such command`, exit 2, pinned in
  `tests/unit/cli/test_exit_codes.py`); the packages `journal/`,
  `messaging/`, `notify/` and the default-journal install in `cli.py`;
  the formatters `yaml`, `csv`, `quiet`; the bootstrap factory only
  `secrets bootstrap` used; `InheritingOptions` (only `context` had a
  group option); the `BRIAR_VERBOSE` and `BRIAR_LOG_LEVEL` reads; the
  notify env names; `_notify_failure` in the runbook executor (a failed
  task is only logged now, C20).
- A runbook company with `messages:` stops like `mcp:` and
  `git_identity:` (`CompanyEntry.REMOVED_KEYS`).
- Telemetry (C7): `resolve()` reads `DO_NOT_TRACK`, `BRIAR_TELEMETRY`,
  then `telemetry` in the project config, then `off`; no state file is
  read or written. `main()` now reads the project config before it
  installs telemetry and passes it in (`telemetry.install(project_config)`).
  The `briar init` template has a commented `telemetry` line (and lost the
  `[agent]` block part 1 missed).
- `agents/creds.md` was deleted, not updated: it only described `briar
  secrets`. Its replacement in the docs is `briar auth status <target>`.
- Numbers on the branch (2026-11-10): `src` 18,423 lines to 15,557;
  `briar --help` lists 6 commands; mypy 39 errors to 31;
  `pytest -n auto -m "not integration"` 1631 passed; `pytest -m
  integration` 37 passed; `make smoke` ok for all 6. Startup, CPU median
  of 7 (load average 12): `import briar.cli` 759 ms on 1.x, 728 ms here;
  `main(['--help'])` 40 ms vs 15 ms.

### Spec 006: core cleanup, part 3 of 5 (PR #93)

- Removed: `storage/postgres.py`, `storage/_models.py`,
  `default_store_kind`, `--store` on `extract` and `init`,
  `KnowledgeStoreChoice`, the `store` setting and `BRIAR_STORE`, the
  `store` check of `doctor`, the `BRIAR_DATABASE_URL` env names, and the
  `SQLAlchemy` and `psycopg` dependencies (`uv lock` also dropped
  `tzdata`). The knowledge store registry keeps one entry, `file`.
- `store` in the project config stops every command with exit 1 naming
  the file (`settings.RemovedSettingGuard`, called next to the
  removed-credential-store guard in `cli.main`). A runbook with
  `knowledge.store: postgres` stops with exit 1 (`KnowledgeBinding.REMOVED_STORES`).
- The example runbooks now use the file store. Not changed:
  `examples/multi_company.env.example` still lists `TELEGRAM_*`,
  `SLACK_*_WEBHOOK_URL` and maybe Postgres placeholders that 2.0 no longer
  reads (reading it was blocked as an env file; check it by hand).
- D21 in the pipeline decisions file now notes C11: checkpoints use
  SQLite only.
- Numbers on the branch (2026-11-10): `src` 15,557 lines to 15,039; mypy
  31 errors to 30; `pytest -n auto -m "not integration"` 1613 passed;
  `pytest -m integration` 37 passed; `make smoke` ok for all 6. Startup,
  CPU median of 7 (load average 8): `import briar.cli` 794 ms on 1.x,
  446 ms here; `main(['--help'])` 42 ms vs 16 ms. The heaviest import
  left is `briar.commands.runbook` (253 ms cumulative in
  `python -X importtime`, mostly the runbook models and executor).

### Spec 006: core cleanup, part 4 of 5 (PR #94)

- Settled first in a grill session:
  `docs/decisions/2026-11-10-briar-2-extract-config.md` (E1 to E16).
- The `extract` parser has `--company`, `--include` and `--repo` only.
  `ExtractConfig` (`commands/extract.py`) builds each included
  extractor's namespace: its own defaults, then the `tracker` and
  `[repo] provider` settings, then `[extract]` (shared concepts and source
  kinds, fanned out by `apply_canonical`), then `[extract.<name>]` (short
  names from `ExtractorKeys`: the concept name, `projects` for a tracker
  project list, and seven niche names such as `region` and `days`). It
  stops with exit 1 on an unknown key, an unknown extractor, a 1.x key
  name (naming the short one) or a value outside a fixed list. Types are
  not checked (spec 004). The root is the `root` setting.
- `briar extract -h` and `agents/extract.md` list every key from one
  renderer (`ExtractKeyTable`); `tests/unit/commands/test_extract_config.py`
  fails when the page and the code differ, and when an option has no key.
- Removed: `--out-json`, `--merge-claude-md`, `--claude-md-path`,
  `--blob-name`, `--root`, `--advanced-help`, `extract/claude_md.py`, the
  JSON render, `register_canonical_flags`, `hide_canonicalised_flags`,
  the legacy-flag notice in `cli.py`, and the 3 goldens of those modes.
  `blob.golden.md` is unchanged with `root` set in `.briar.toml`.
- Not changed: a runbook's `args:` keeps the 1.x names and no key check
  (E12, until spec 004 merges the two paths).

### Spec 006: core cleanup, part 5 of 5 (PR #95)

- Settled first in a grill session:
  `docs/decisions/2026-11-10-briar-2-final-trims.md` (F1 to F9).
- `auth` has `login` and `status`. `status` with no target lists the name
  of every credential that is set, as 1.x `auth list` did. `logout`,
  `refresh`, `list`, `--cred-store`, `_effective_store_kind` and
  `DestinationPolicy` left; the `CredentialStore` interface and its
  one-entry registry stay for spec 005 (F1).
- `runbook` has `extract` and `serve`. `sweep` left (use `serve DIR`, or
  `extract` once per file, F9); `serve` ticks every second
  (`TICK_SECONDS` in `commands/runbook.py`), `--tick` left.
- `briar config` left. `briar doctor` prints its settings table first, with
  a `telemetry` row (tier and source, from `telemetry.active_config()`,
  so no install id is written), then the checks. `--format json` prints
  `{"settings": [...], "checks": [...]}` (F2, F3). The table renderer adds
  cells as `rich.text.Text`, which fixes the `[repo]` that rich read as
  markup in the `git remote` hint.
- `briar init` writes a commented `[extract]` example (F8). `make smoke`
  checks the 5 commands.
- README rewritten around the 5 commands, with no GIF or diagram (F4 to
  F6); the files stay in `docs/demo/` and `docs/diagrams/` for the site.
  `pyproject.toml` description and keywords match 2.0 (F7).
- Success checks, run 2026-11-10 on Python 3.13.2: `briar --help` lists
  extract, runbook, auth, init, doctor (SC-001); `briar extract --help`
  lists `--company`, `--include`, `--repo`, `-h` (SC-002); `blob.golden.md`
  has no change since `f01164f`, the commit that added it (SC-003); 1511
  unit and 35 integration tests pass, and the kept `flags_1x.json` cases
  only lost the keys of removed options (`tick`, `cred_store`) (SC-004);
  `src` 14,967 lines before this PR and 14,772 after, and `import
  briar.cli` 679 ms against 1,130 ms on the 1.x code (CPU time, median of
  7, load average 23, so only the ratio means something) (SC-005); no
  module imports `anthropic`, `git`, `sqlalchemy`, `psycopg`, `mcp`,
  `openai` or `google.generativeai`, and `pyproject.toml` names none of
  them (SC-006). mypy: 29 errors before, 28 after.

### Fix: the `doctor` git remote hint (PR #96)

- With no git `origin`, the `git remote` check of `briar doctor` says: no
  git origin: run `briar init --owner OWNER --repo REPO`, or set `[repo]`
  in `.briar.toml`. It used to name a command that 2.0 removed. Decision F10
  in `docs/decisions/2026-11-10-briar-2-final-trims.md`.
- The `doctor` hints have no em dash any more.

### Spec 005: source logins, part 1 of 4 (PR #97)

- Settled first in two grill sessions:
  `docs/decisions/2026-11-10-briar-2-logins-spec.md` (G1 to G26, the
  whole spec) and `docs/decisions/2026-11-10-briar-2-logins-pr1.md` (H1
  to H10, this PR). The spec-kit files are in the local
  `specs/005-source-logins/`.
- `briar/auth/config.py`: the `[auth.<source>]` pydantic models, used for
  `.briar.toml` (`AuthTable`, which checks the whole block, H6) and for the
  runbook `CompanyEntry.auth`. The Jira `site` keeps the scheme and the
  host only (H10).
- `briar/auth/logins.py`: `CompanyLogins` gives each adapter the login of
  its source. A `SourceLogin` knows its secret names
  (`BRIAR_<COMPANY>_<SOURCE>_<FIELD>`, G7), what `briar auth login` asks
  for each method, its required config values (H1), and its 1.x names:
  when the 2.0 login is missing and a 1.x name is set, `ready()` stops the
  run (G8, G25). `briar extract` builds it from `[auth]` and puts it on
  each extractor namespace (`args.logins`); the runbook executor builds it
  from the company `auth:` block (G3).
- Adapters (Jira, Bitbucket provider and tracker, Linear, Fireflies,
  GitHub provider and issues) take `logins` instead of reading env vars.
  `GithubApi` holds the company token (`GithubApi(token).get_json(...)`);
  briar no longer reads `GITHUB_TOKEN`. Jira auth is one strategy per
  method; the cookie method sends the pasted `Cookie` header with the
  1.x browser headers and `[auth.jira] user_agent` (H5).
- `briar auth`: source targets plus the AWS acquirers (`LoginTargets`),
  `--runbook FILE` (G6). The pasted 1.x acquirers and `github-device`
  left; until PR 4 GitHub takes `method = "token"` only (H8) and Linear
  `api-key` only.
- Flat store (G16): `CredentialStore`, its registry, `delete`,
  `fingerprint` and `expires_at` left; `EnvFileStore.list` matches the
  `BRIAR_*` names. `CredEnv` keeps only the AWS names (PR 2).
- `briar doctor`: `github login` replaces `github token` (PR 3 checks every
  source live).
- In a runbook, the G8 stop fails the task (a `failed` row, the message in
  the log; exit code 0, as for any failed task) until PR 3 adds the check
  before each task.
- Numbers, 2026-11-10, Python 3.13.2: 1469 unit and 31 integration tests
  pass; `make smoke` ok; mypy 30 errors before, 28 after; `src` 14,772
  lines before, 14,377 after. The Bitbucket cookie is tested with fakes
  only (H3).

### Spec 005: source logins, part 2 of 4 (PR #98)

- Settled first in a grill session:
  `docs/decisions/2026-11-11-briar-2-logins-pr2.md` (I1 to I10).
- `briar/auth/logins.py`: `CliLogin` (AWS, GCP, Azure) runs the CLI login
  through `PromptIO.run_login` (`os.system`, no `subprocess`) and saves
  nothing (L3). `AwsLogin` tells an SSO profile from a static one by
  reading the AWS config with botocore (I5); `GcpLogin` gives the ADC file
  of `config_dir` (G26); `AzureLogin` the tenant. A login needs its value;
  a run does not (I6). `status_lines` is polymorphic: secret names for the
  pasted sources, the CLI on `PATH` and the local login state for the
  clouds (I7).
- Cloud adapters take `logins`: boto3 `Session(profile_name=...)`,
  google-auth `load_credentials_from_file` of the folder (or the default
  chain) passed to every google client, `AzureCliCredential(tenant_id=...)`
  (or `DefaultAzureCredential` with no tenant). The project and the
  subscription come from `[auth.gcp]` and `[auth.azure]` (I4).
- `profile` leaves `[extract.aws-infra]` (`ExtractConfig.MOVED` names the
  new place) and the runbook args `aws_extract_profile` and `cloud_profile`
  stop the load. The examples moved their cloud scope to `auth:` blocks,
  and `examples/all_features.yaml` now lists the `BRIAR_*` names.
- Gone: the acquirer package, `briar.env_vars` (`CredEnv`), the AWS
  `required_env_vars`, and the 1.x AWS fallback to a profile named like
  the company.
- Numbers, 2026-11-11, Python 3.13.2: 1428 unit and 31 integration tests
  pass; `make smoke` ok; mypy 28 errors before, 25 after; `src` 14,377
  lines before, 14,104 after.

### Spec 005: source logins, part 3 of 4 (PR #99)

- Settled first in a grill session:
  `docs/decisions/2026-11-11-briar-2-logins-pr3.md` (J1 to J19). The end of
  that file lists the choices made while coding, for the review.
- `briar/auth/check.py`: `LoginCheck` groups the targets of a run by source
  (`TargetGroups`: the first adapter of each source, each resource once)
  and runs one `SourceCheck` per source: the login `problem()` first (no
  call), then `who_am_i()`, then `read()` of each resource, one call after
  another (J6). States `ok`, `failed`, `unreachable`. `require()` raises
  `LoginCheckFailed` (exit 1) on any line that is not `ok`, network
  included (J3). `LoginRejected` and `SourceUnreachable` are the only
  errors an adapter raises to it.
- Every adapter has `login_source`, `who_am_i()` and `read()` (defaults in
  the 4 adapter base classes raise `LoginRejected`); `CloudProvider.scope()`
  gives the GCP project or the Azure subscription.
  `extract/_login_failures.py` turns the errors of requests (atlassian),
  PyGithub, urllib (Linear, Fireflies), botocore, google and azure into
  those two errors, through the same client the run uses (J4).
- The login left `is_available` (J15). An extractor gives its
  `login_target(args)`: the adapter and the repos or projects of its gate
  argument. `ExtractPlan` (`commands/extract.py`) builds the steps of a
  `briar extract`, checks the targets of the steps that run (J2), then
  runs them. `RunbookLoginCheck` (`iac/runbook/executor.py`) does the same
  for every company and task of a runbook run before any task (J7);
  `RunbookScheduler.check_logins()` runs it at `serve` start, and a job
  whose check fails stops `serve` (`run_forever` raises it, J8).
- `briar auth login` checks with `CompanyLogins.with_entries(entries)`
  before saving (J9). `briar auth status` and `briar doctor` check the
  configured sources (`CompanyLogins.configured()`: a block, a secret or a
  1.x name), with the run targets of the project or the runbook and
  `SourceProbes` (`extract/login_probes.py`) for the rest (J10, J11, J13).
- Gone: `SourceLogin.ready()`, `status_lines`, `StatusLine`, the offline
  CLI lines of I7, and the `github login` doctor check of PR 1.
- Numbers, 2026-11-11, Python 3.13.2: 1501 unit and 32 integration tests
  pass; `make smoke` ok; mypy 25 errors before, 25 after; `src` 14,104
  lines before, 14,947 after.

### Spec 003: `briar serve`, part 1 of 4 (PR #100)

- Settled first in a grill session (the re-scope of 2.0):
  `docs/decisions/2026-11-11-briar-2-serve-pipeline.md` (S1 to S35). The
  local plan and tasks: `specs/003-serve-pipeline/plan.md`, `tasks.md`.
- `iac/runbook/models.py`: `ServeSettings` (`companies.<c>.serve`), with
  `model`, `max_usd`, `max_loops`, `ci_wait_minutes` required (S5, S24).
- `briar/serve/`: `state.py` (`WorkBook` of `WorkEntry`, `WorkState` on
  `work:<company>` under an `asyncio.Lock`), `work.py` (`WorkFinder`: reviews
  first, then Jira `assignee = currentUser()` and GitHub issues assigned to
  the PAT owner, by `PriorityRank`; `WriteCheck`; `ModelLogin`;
  `ServeCheck`; `ServeCompanies` loads the folder), `loop.py` (`TaskSlots`:
  a semaphore of `max_parallel`, one task per key; `CompanyWatch` polls in a
  thread; `ServeLoop` gathers the watches and the extract ticks of
  `RunbookScheduler.tick()`; `FoundRoutine` records a found ticket until
  part 2).
- `commands/serve.py`: `briar serve DIR` (the method is `serve`, with
  `run = serve` for the `Command` interface, since the rules hook flags a new
  method named `run`).
- Adapter verbs: `JiraTracker.assigned`, `JiraTracker.can_write`
  (`mypermissions`), `GithubIssuesTracker.assigned`,
  `GithubProvider.can_write` (`permissions.push`).
- Real run (read only): the checks and one cycle against the private test
  repo `iklobato/briar-plan-test`; 0 issues assigned, so no ticket found.
- Numbers, 2026-11-11, Python 3.13.2: 1539 unit and 33 integration tests
  pass; `make smoke` ok; mypy 25 before, 25 after; `src` 14,947 lines
  before, 15,585 after.
- The GitHub device flow written as spec 005 PR 4 was dropped (S2: GitHub by
  PAT); `git branch device-flow cf7bcb4` brings it back while the reflog
  keeps it. The rest of spec 005 PR 4 (Linear OAuth, the GitHub App) is not
  planned.

### Spec 003: `briar serve`, part 2 of 4 (PR #101)

- The ticket routine (S18 to S27): `serve/ticket.py` (`TicketRoutine` per
  company, `TicketRun` per ticket: plan, comment and `status.started`,
  build, verify loop, self-review loop, push, PR ready, comment and
  `status.review`, CI wait with fixes; `blocked()` on any `CliError`, which
  pushes a draft PR with the report and comments it, and blocks the ticket
  even when posting fails).
- `serve/agent.py`: `ClaudeAgent` on `claude_agent_sdk.query` (structured
  output for the plan and the review, `max_budget_usd` per call, no setting
  sources), `BashGuard` (1.x verbs and forbidden tokens, plus `git push` and
  `gh `; edits only in the worktree; reads anywhere) behind `ToolGate`
  (`can_use_tool`; read-only steps cannot edit). The SDK yields an error
  `ResultMessage` and then raises `ResultError`: both become one failed
  `AgentAnswer`, which stops the task.
- `serve/git.py`: `Shell` (`os.system` with the output in a file, no
  `subprocess`), `RepoCheckout` (clone in `$XDG_CACHE_HOME/briar`, one
  worktree per task, commits as `<login>@users.noreply.github.com`, push
  with a credential helper that reads a 0600 token file).
- `serve/task.py`: `TaskPrep` (the context of a task: clone, worktree,
  branch and title formats, ticket text), `Budget` (S23), `Plan`, `Review`,
  `Prompts` (`src/briar/prompts/serve/*.md`), `AgentSteps`.
- Adapter verbs: `GithubProvider.open_pull`, `comment`, `make_draft`,
  `ci_state` (wrapped in `GithubFailures`, so a GitHub refusal is a
  `CliError`), `JiraTracker.comment` and `move`.
- State: `found`, `planned`, `pr-open`, `done`, `blocked`. The finder takes
  again a ticket left `found` or `planned` (a crash before the PR), never
  one `pr-open`, `done` or `blocked`.
- Numbers, 2026-11-11, Python 3.13.2: 1593 unit and 33 integration tests
  pass; `make smoke` ok; mypy 25 before, 25 after; `src` 15,585 lines
  before, 16,462 after.
- Not run for real: a full ticket (needs model money and a ticket briar may
  write to). The SDK was called once with a fake key: a failed answer, cost
  0.

### Spec 003: `briar serve`, part 3 of 4 (PR #102)

- Decisions from a grill session: `docs/decisions/2026-11-11-briar-2-review-routine.md`
  (V1 to V16). They change S22 to S25.
- `serve/work.py`: the finder reads each watched PR (`pr-open` or `done`): `pull_state`
  (a merged or closed PR becomes a `merged` or `closed` item, recorded and left),
  `review_comments` and `resolved_comment_ids` (GraphQL); `ReviewFilter` keeps the new
  comments of writers and `review_bots` (V1), skips the `<!-- briar -->` marker
  (`Marker`, V2), answered and unedited ones (V4) and resolved threads (V5); a
  `dirty` PR alone starts a round (V6). `AgentSandbox` checks `sandbox-exec`, or
  `bwrap` and `socat` (V14).
- `serve/review.py`: `ReviewRun` (round count and `max_review_rounds`, base merge with
  conflict resolution and abort, one `AgentSteps.answer` call, verify, self-review,
  push, CI wait) and `ReviewReplies` (thread replies plus one quoting comment).
  `serve/routine.py`: `ServeRoutine` picks the routine per item. `serve/flow.py`:
  `TaskFlow`, what both routines share; `record` now keeps the entry already stored.
- `serve/agent.py`: the SDK `sandbox` (no `autoAllowBashIfSandboxed`, no unsandboxed
  commands, `allowedDomains` from `allowed_domains`); Read, Glob and Grep stay in the
  worktree and the knowledge root.
- State: `answered` (comment key to `updated_at`) and `rounds` replace
  `handled_comments`; statuses `merged` and `closed`.
- Real, read only, 2026-11-11: `briar doctor` on this Mac says `agent sandbox ok
  sandbox-exec found`; the bundled CLI starts with the sandbox settings and stops at
  the login with a fake key (cost 0); `pull_state`, `review_comments` and
  `resolved_comment_ids` on https://github.com/iklobato/briar/pull/75 (the GraphQL
  `databaseId` equals the REST id of the inline comment).
- Not run for real: bash inside the sandbox, a full round with the model, Linux with
  `bwrap` (CI only runs the tests).
- Numbers, 2026-11-11, Python 3.13.2: 1637 unit and 33 integration tests pass (the
  serve tests on 3.11 too); `make smoke` ok; mypy 27 before, 27 after, same list, with
  the same venv (the 25 of part 2 was another install of the dev extras); `src`
  16,462 lines before, 17,051 after.

### Spec 003: `briar serve`, part 4 of 4 (PR #103)

- Decisions from a grill session: `docs/decisions/2026-11-11-briar-2-cleanup-pr4.md`
  (X1 to X6).
- `briar auth` and `briar runbook` left (`commands/auth.py`, `commands/runbook.py`,
  their tests and the `flags_1x.json` entries). The old names give `No such
  command` (X5).
- Gone with them, each proved unused with grep: `briar.auth._prompt`
  (`PromptIO`), `briar.credentials.envfile` (`EnvFileStore`, the writer),
  `SourceLogin.login`, `CliLogin.login`, `CompanyLogins.with_entries`,
  `LoginScope.runbook` and `login_command`, `SecretField.prompt` and `hidden`,
  `RegistryChoice`. The startup bootstrap still reads `secrets.env`.
- `SourceLogin.fix()`: the hint of a missing or refused login names the
  `BRIAR_*` variables, the `secrets.env` path (`secrets_path()`) and the
  `HELP` text; `CliLogin.fix()` names the CLI command.
- `briar serve DIR` with no `serve:` block runs only the scheduled extracts
  (X1); with neither block nor schedule it stops.
- `briar doctor --runbooks DIR` (`doctor.RunbookChecks`, X2): the load, then
  `RunbookScheduler.login_results()` and `ServeCheck.lines()`, a login both
  check shown once.
- `briar init` writes `runbooks/<company>.yaml` from
  `init_templates/runbook.yaml.tmpl` (X3).
- Manuals: `agents/auth.md` became `agents/logins.md` and `agents/runbook.md`
  became `agents/runbooks.md`, without the commands (the decision said the two
  go; their content on logins and the runbook file stays true, so they were
  renamed). README: 4 commands, a logins table, the pipeline paragraph.
  CHANGELOG: the 2.0 entries that described the two commands now describe the
  final state.
- Real, local, 2026-11-11: `briar init --company acme-labs` in an empty folder
  wrote both files; `briar doctor --runbooks runbooks` failed on
  `companies.acme-labs.serve.model  Field required` (exit 1), then, with the
  keys uncommented, gave `runbooks ok` and one `github login fail` line with
  the new hint; `briar auth login github` gives `No such command 'auth'`.
- Numbers, 2026-11-11, Python 3.13.2: 1530 unit and 25 integration tests pass
  (fewer: the tests of the removed commands and of `EnvFileStore` left);
  `make smoke` ok on 4 commands; mypy 27 before, 25 after (the two of the
  removed files); `src` 17,051 lines before, 16,321 after.

### The first real runs and their fixes (PR #104)

- Decisions: `docs/decisions/2026-11-11-briar-2-serve-knowledge-draft.md` (W1 to W9).
  Detail, evidence and how to run it again: `docs/briar-2-validation.md`.
- The first real ticket (2026-11-11, `iklobato/briar-plan-test` issue #1, PR #5) worked end to
  end, but the agent never read the knowledge (relative path) and verify passed only in the
  task folder (a `pytest.ini` the global gitignore kept out of the commit).
- 16 commits: `9ab6dc3` absolute knowledge path; `b1e8d18` PR as a draft, ready after CI;
  `0d25930` verify on a clean copy; `9d599b6` atomic file store write; `7220294` CI pending
  while Actions runs are queued; `3a04aee` every scheduled task into the company blob (K9);
  `6988bb5` a failed poll is logged and retried; `5a6c05b` answered comments saved before the
  CI wait; `fe4b292` no ticket done twice (PR of the branch recorded first, any failure
  blocks, PR number saved at once, plan posted once, a draft left by a stop is finished);
  `06e802a` self-review accepts a change that makes verify pass; `09cea4b` the CI fix prompt
  names the PR; `a723896` one lock per clone; `6887b6a` `BaseDrift` finds a conflict GitHub
  has not computed; `2b11d3a` the mutation tool targets `auth/logins.py`; plus the docs
  commits.
- Real runs: 17 serve cases on the small test repo and 11 flows on a private copy of
  `pallets/click` with real CI (bug fixes, a feature, review rounds, a CI failure fixed by
  the agent, a conflict plus a reviewer commit, parallel tasks, scheduled extracts); extract
  on GitHub, AWS and Azure checked against `gh`, `aws` and `az`.
- Numbers, 2026-11-12, Python 3.13.2: 1549 unit and 25 integration tests pass (1530 and 25 on
  #103); each commit passes alone; mypy 25 (same); `python tools/mutation_test.py` 22 of 22
  killed; CI green on #104.

### Spec 008: work selection, part 1 of 4 (PR #105)

- Decisions: `docs/decisions/2026-11-12-briar-2-work-selection.md` (N1 to N17) and
  `docs/decisions/2026-11-12-briar-2-work-selection-pr1.md` (Q1 to Q4).
- `serve` no longer starts every assigned ticket: `briar.serve.select` drops the
  not-workable ones, scores the rest with the fixed points of Q1, and one read-only model
  call picks among the 5 best for the free slots, with the repo and a reason. A ticket
  briar already started goes on in its repo without the choice. The reason is in the log
  and the state entry; the call cost starts the task budget.
- `serve.jira` is now `{PROJ: {repos: [...], not_workable: [...]}}`; the 1.x form stops the
  load with the new form in the message (Q4). The Jira tracker reads `issuelinks`,
  `duedate` and `parent`; GitHub issues read `issue_dependencies_summary` (Q3).
- `extract --include active-tickets` says "first 25 of 881 open ticket(s)" when it cuts the
  list (N17).
- Real run, 2026-11-12, read only: the selector (`Choice`) on the 5 open issues of the private
  copy of `pallets/click`, `claude-sonnet-5-5`, 2 free slots. The first run kept nothing: the
  model answered the key `1` for `iklobato/briar-oss-click#1`, and the picks were dropped with
  no log line. Fixed: the prompt says to copy the whole key, and a dropped pick is logged.
  Then: 2 picks (#1 and #3) with a reason each, US$ 0.037 for the call, and the second poll
  with the same shortlist made no call. No task was started; nothing was written.
- Numbers, 2026-11-12, Python 3.13.2: 1595 tests pass (unit and integration); mypy 25
  (same); ruff 5 (the same old ones). Not run yet: a full `serve` poll that starts a picked
  ticket, and a Jira project with dependencies and due dates.

### Spec 008: work selection, part 2 of 4 (branch `select/02-chat`, https://github.com/iklobato/briar/pull/106)

- Decisions: `docs/decisions/2026-11-12-briar-2-work-selection-pr2.md` (C1 to C16).
- `[auth.slack]` login (user token, or session token and cookie; no bot token);
  `serve.chat.slack.channels`; `briar.serve.chat` (`ChatReader`, `Citations`, `ChatAsks`,
  `TitleWords`) and `briar.serve.slack` (`SlackApi` with retries, `SlackSource`).
- Real run 1, 2026-11-12, read only, on a real company Slack and its Jira project (39
  tickets assigned to the owner, 19 workable): the login check passed (`auth.test`,
  `conversations.info`); the key searches took 2 s and found 12 assigned tickets cited by a
  person; one ticket moved into the top 5 because of chat. For the 5 best, 38 threads (113,095
  characters, 20 s); the selector call cost US$ 0.26 (0.037 without chat in PR 1). Nothing was
  written anywhere.
- Real run 2, 2026-11-12, read only, on a test workspace: one message in a new test
  channel, sent by hand by the token owner, citing one issue of the private test repo. It
  gave 0 points (C3) and the order did not change, but the model picked that issue and wrote
  "a teammate explicitly asked", and the title search of another issue brought two unrelated
  DMs. Fixed by C15 (owner lines marked `(owner)`) and C16 (a title hit needs two title
  words); the re-run says "The owner asked for it first" and the cost went from US$ 0.16 to
  0.038. Not run: a message by a second person (the +25 path), by choice of the
  maintainer; unit tests cover it.
- Numbers, 2026-11-12, Python 3.13.2: 1376 unit tests pass; mypy no new error; ruff 5 (the
  same old ones).

### Spec 005: real runs on the accounts of the maintainer (G20)

Read only, on the machine of the maintainer, 2026-11-11, with
`.venv/bin/briar` of the branch in an empty folder under the job scratch dir.

| Source | What ran | Result |
|---|---|---|
| GitHub | `BRIAR_REALTEST_GITHUB_TOKEN="$(gh auth token)" env -u GITHUB_TOKEN -u GH_TOKEN briar extract --company realtest --include ...` | ok: the blob was written (516 bytes) with the token of company `realtest` and no `GITHUB_TOKEN` |
| AWS, SSO profile | `[auth.aws] profile` set to an SSO profile, `briar auth status aws`, `briar extract --include aws-infra` | ok: the AWS section names the account and region `us-east-1`, exit 0 |
| AWS, static-key profile | the same with the `default` profile | AWS itself rejects the key (`InvalidClientTokenId`, the same as `aws sts get-caller-identity`), so the section says UNREACHABLE; not a briar bug |
| GCP, Azure | `briar auth status gcp`, `briar auth status azure` | ok: found the CLIs and their local logins (PR 2, offline lines) |
| GitHub, PR 3 | `briar extract --include stale-prs --repo iklobato/briar`, then a repo that does not exist, then a fake token | ok: `login github (realtest): iklobato`; the missing repo stops with the 404 line; the fake token stops with `HTTP 401: ... Bad credentials` |
| AWS, PR 3 | `briar auth status aws` with the SSO profile | ok: the assumed-role ARN |
| GCP, Azure, PR 3 | `briar auth status gcp` and `azure`, first without the SDKs, then in a separate env with `--extra gcp --extra azure` | without: a crash, fixed (the line says what to install); with: GCP refreshed the ADC (`application default credentials`), Azure got a token through `az` (`the default tenant`), after 114 s on the managed identity address |
| doctor, PR 3 | `briar doctor` with GitHub and AWS configured | ok: one `github login` and one `aws login` line, each with the account |

Not run for real yet: Jira, Bitbucket, Linear, Fireflies (each needs the
maintainer to write its `BRIAR_*` secrets first; `briar auth`, used in the
runs above, left in #103), a GCP or
Azure extract and the project or subscription read (no project or
subscription set up for it), the Bitbucket cookie (fakes only, H3) and the
Bitbucket access token path. Keep account ids and emails out of the repo
and the PRs.

### Spec 008: work selection, part 3a of 4 (branch `gitlab/3a-extract`)

- Decisions: `docs/decisions/2026-11-12-briar-2-work-selection-pr3.md` (G1 to G20).
  Base `v2`; parts 1 and 2 (selector, Slack) are separate PRs.
- `[auth.gitlab]` login, `python-gitlab` 8.6 (LGPL-3.0-or-later), `GitlabProvider`
  (`src/briar/extract/_providers/gitlab.py`) for every repo extractor; `provider = "gitlab"`.
- Real run, 2026-11-12, read only, a new `read_api` token, two projects of a real company
  GitLab (5 levels deep), all 19 repo extractors: the first run found three gaps (the
  python-gitlab error class, a missing file logged as an error 28 times, no `OR` in blob
  search), fixed in `39d0cc0`. The second run: exit 0, no error, 16 of 19 sections (no code
  scanning or dependency alerts on GitLab, no releases in those projects), 393 s. Most of
  the time is in the four commit extractors (41 to 68 s each) and `pr-hygiene` (61 s): each
  reads the same commits and up to 50 commit diffs per repo again, as on GitHub.
- Not run: a self-hosted GitLab, a group or project access token.

## 3. How the command line works now

```
briar.cli.main(argv)
  1. pre-parse: take --format, -v/--verbose, -V/--version out of argv (anywhere)
  2. --version: print and return, no other work
  3. "metadata only" (help, no command, --show/--install-completion, a TAB):
     skip credential bootstrap, telemetry, config, git
  4. else: bootstrap env file, read the project config once, install telemetry with it
  5. CommandLine.parse(...)   (cli_app.py)  ->  ParseOutcome(args or None, exit code)
  6. telemetry span + removed-store guard + commands[name].run(args)
     CliError -> 1 with "error: <msg>", KeyboardInterrupt -> 130, crash -> 2
```

- **`cli_app.py`**: builds the Typer app. Only the command the line names
  gets its real options (Typer reads every registered command's options
  on each run); the others are registered by name and help. Shell
  completion (env `_BRIAR_COMPLETE`) gets every command. The app does not
  run the command: its result callback keeps the namespace and `main()`
  runs it. Help and usage errors are plain text (`rich_markup_mode=None`).
- **`CommandOptions`** (`commands/base.py`): a command's options are a
  frozen dataclass, one field per 1.x dest with a `typer.Option` default,
  in the command's own module, listed in `cli_app.ROUTES`. Typer reads the
  dataclass like a function. This keeps every function within the
  maintainer's code rules (at most 3 parameters).
  - `UNSET_AS`: a None default turned into the 1.x value at run time
    (`secrets bootstrap --kind` keeps `""`).
  - Fixed choices are `str` Enums in `commands/_enums.py`, each with a test
    that keeps it equal to its registry (`tests/unit/cli/test_typer_routes.py`).
    `RegistryChoice` (the `auth` targets) left with `auth` in spec 003 part 4.
- **`settings.py`**: env (`BRIAR_COMPANY`, empty counts as
  unset) > project config file > git `origin` (owner, repo) > the option's
  default. One file is read, never a merge: the nearest `.briar.toml`, or
  `pyproject.toml` with `[tool.briar]`, found upward from the cwd; in one
  folder `.briar.toml` hides `pyproject.toml`. It feeds the Typer default
  map and the settings table of `doctor` (labels `env (<VAR>)`,
  `config (<file>) [<table>]`, `inferred (git origin)`, `-`).
- **`config.py`**: now only the argparse shim for `extract`
  (`apply_config_defaults`); `infer.py` fills `extract --repo` from git.

### Logins (spec 005, PRs #97 to #99)

```
.briar.toml [auth.<source>]           runbook companies.<c>.auth
        |  AuthTable(config, origin).block()   |  CompanyEntry.auth (pydantic)
        v                                      v
     AuthBlock  (auth/config.py: one model per source, extra keys forbidden)
        |
        v
CompanyLogins(LoginScope(company, origin, table), block, os.environ)   (auth/logins.py)
        |  .github() .jira() .bitbucket() .linear() .fireflies() .aws() .gcp() .azure()
        v
   SourceLogin: names(), problem(), fix()
        |
        +-- briar extract: ExtractConfig.logins(company) -> args.logins on every namespace
        +-- runbook executor: RunbookLogins(runbook).company(name)
        +-- briar serve / doctor --runbooks: ServeCompanies(DIR) -> CompanyLogins per company
        v
adapters (_providers/, _trackers/, _meetings/, _clouds/) call self._login()

The check before a run (PR #99, auth/check.py):

ExtractPlan.steps() / RunbookLoginCheck   (the steps that run: is_available, J2)
        |  extractor.login_target(namespace) -> LoginTarget(adapter, repos or projects)
        v
LoginCheck(logins).require(targets)
        |  TargetGroups: first adapter per source, each resource once
        v
SourceCheck: login.problem()  ->  adapter.who_am_i()  ->  adapter.read(resource) ...
        |  adapter errors -> LoginRejected | SourceUnreachable (extract/_login_failures.py)
        v
CheckResult lines (ok / failed / unreachable); any not ok -> LoginCheckFailed, exit 1
```

- **Secrets** are env names `BRIAR_<COMPANY>_<SOURCE>_<FIELD>`: the company
  in capitals with `-` as `_` (`acme-co` gives `BRIAR_ACME_CO_JIRA_TOKEN`).
  The `method` key of `[auth.<source>]` picks the fields (Jira `token`:
  `EMAIL`, `TOKEN`; Jira `cookie`: `COOKIE`; Bitbucket `access-token`:
  `ACCESS_TOKEN`). The startup bootstrap loads `secrets.env` into
  `os.environ`, so a secret can come from either. The person writes
  `secrets.env` (`$BRIAR_SECRETS_FILE`, else `/etc/briar/secrets.env` when it
  exists, else `~/.config/briar/secrets.env`; `credentials/_paths.py`
  `secrets_path()`). Since #103 nothing in briar writes it (`EnvFileStore`
  and `briar auth login` left); a missing secret names its variable and that
  path (`SourceLogin.fix()`).
- **Config values** (Jira `site`, Bitbucket `workspace`, cloud scope) live
  in `[auth]`, never in `secrets.env`. briar never writes the config: a
  missing one stops with "set `site` under `auth.jira` in .briar.toml (...)"
  (H1).
- **`problem()`**: "" when every secret and config value is there, else
  the reason with its fix: a 1.x name set while the 2.0 login is missing
  (`JIRA_<C>_TOKEN`, `GITHUB_TOKEN`, `AWS_<C>_REGION`, ...; G8, G25, I10),
  no secret saved, or a missing config value (H1). The check shows it as a
  `failed` line without calling the source. With the 2.0 login set, the
  1.x name is ignored. For the clouds only a 1.x name is a problem (I6).
- **Pasted sources** (GitHub token, Jira, Bitbucket, Linear key, Fireflies
  key): `login` asks each field (secrets hidden) and returns the entries;
  `commands/auth.py` checks them with `CompanyLogins.with_entries` and
  saves them only when the source answers (J9).
- **Cloud sources** (`CliLogin`: AWS, GCP, Azure): `login` runs the cloud
  CLI in the terminal through `PromptIO.run_login` (`os.system`) and saves
  nothing. The run reads the CLI login with the cloud SDK: boto3
  `Session(profile_name=...)`, google-auth `load_credentials_from_file` on
  `<config_dir>/application_default_credentials.json`,
  `AzureCliCredential(tenant_id=...)`. With no value set, the run uses the
  default chain of the cloud (`DefaultAzureCredential` for Azure).
- **Tests** build logins with `tests/unit/fake_logins.py` (`FakeLogins(auth,
  environ, company)`), not with env vars.

### `briar serve` (spec 003, PRs #100 to #103)

The agentic pipeline. Decisions: `docs/decisions/2026-11-11-briar-2-serve-pipeline.md`
(S1 to S35), `2026-11-11-briar-2-review-routine.md` (V1 to V16),
`2026-11-11-briar-2-cleanup-pr4.md` (X1 to X6). User manual: `agents/serve.md`,
`agents/runbooks.md`, `agents/logins.md`.

```
briar serve DIR                                   (commands/serve.py)
  ServeCompanies(DIR).load()        companies with a `serve:` block (serve/work.py)
  RunbookScheduler(DIR).register_all()   the scheduled extracts of every runbook
  none of both -> exit 1; no `serve:` block -> extracts only (X1)
  scheduler.check_logins()          login check of every extract job (J8)
  ServeCheck.require()              model login, AgentSandbox, read + write logins (S29, V14)
  ServeLoops(...).loop().serve_forever()        (serve/loop.py, one asyncio loop)
     |-- _extracts(): RunbookScheduler.tick() every 1 s (the 1.x `runbook serve`)
     +-- CompanyWatch per company, every `poll_minutes` (default 5):
           WorkFinder.find()  (in a thread)
             1. book.watched(): each briar PR in `pr-open` or `done`
                  pull_state() -> merged/closed: WorkKind.MERGED / CLOSED item
                  review_comments() + resolved_comment_ids() (GraphQL)
                  ReviewFilter: writers (OWNER, MEMBER, COLLABORATOR) or `review_bots`,
                    no `<!-- briar -->`, never answered or edited since, thread not resolved
                  new comments or mergeable_state `dirty` -> WorkKind.REVIEW item
             2. tickets: Jira `assignee = currentUser()` per project of `jira:`,
                GitHub issues assigned to the PAT owner per repo of `repos:`,
                minus the keys already taken; PRs first, then PriorityRank, then oldest
           TaskSlots.dispatch(): asyncio.Semaphore(max_parallel), never two tasks on one key
             ServeRoutine.work_on(item)                    (serve/routine.py)
               MERGED/CLOSED -> record the status, nothing else
               TICKET -> TaskPrep.context()        clone + worktree from origin/<default branch>
               REVIEW -> TaskPrep.review_context() worktree of the PR branch + fast forward
               Budget(max_usd, max_loops); AgentSteps(ClaudeAgent); TaskFlow
               TicketRun.work() or ReviewRun.work(); any CliError -> TaskFlow.blocked()
```

- **The ticket routine** (`serve/ticket.py`, S18 to S27, W1 to W7): a PR of the task
  branch that already exists is recorded and nothing is built (`branch_pull`) -> plan
  (structured output, read only) -> comment the plan on the ticket once, move it to
  `status.started` -> build (edits) -> `TaskFlow.verified` (commit, run `verify` on a clean
  detached worktree of HEAD, fix until green) -> `TaskFlow.reviewed` (read-only self-review
  against the plan, fix until approved) -> push -> open the PR **as a draft** (`Closes #n`
  for an issue), save `pr-open` with its number -> comment the link, move to
  `status.review` -> `TaskFlow.ci` (poll every 30 s; check runs, statuses and Actions
  workflow runs; fix a failed run with the PR number in the prompt; stop after
  `ci_wait_minutes`) -> mark the PR ready -> `done`. A draft left in `pr-open` by a stop is
  found again (`WorkKind.READY`) and finished. Changes of PR #104: `docs/briar-2-validation.md`
  section 2.
- **A review round** (`serve/review.py`, V6 to V11): stop at `max_review_rounds`,
  else count the round -> merge `origin/<base>` when `dirty`, or when `BaseDrift` finds a
  conflict GitHub has not computed (the agent resolves the conflict; a merge it cannot
  finish is aborted) -> one `AgentSteps.answer` call over every new comment
  (`{"answers": [{id, changed, reply}]}`) -> commit; if the head moved: verify,
  self-review against the comments, push -> `ReviewReplies`: a reply in each inline thread
  (`create_review_comment_reply` on the first comment of the thread) and one comment
  quoting the others -> record `answered` (before the CI wait) -> CI wait if the head
  moved.
- **A stop** (`TaskFlow.blocked`, S23): a limit (`LimitHit`), any git, GitHub or Jira
  failure (`CliError`; `GithubFailures` turns PyGithub errors into one), or an `OSError`,
  `RuntimeError`, `ValueError` or `LookupError` (`ServeRoutine.TASK_FAILURES`). briar
  commits "work so far", pushes, turns the PR into a draft (or opens one) with the
  report, comments the report on the ticket, and records `blocked`. Posting failures
  join the report; the ticket is blocked anyway, so no poll pays for it again.
- **The agent** (`serve/agent.py`): `ClaudeAgent` runs `claude_agent_sdk.query` with
  the `model` of the company, `max_budget_usd` = money left in the task,
  `setting_sources=[]`, `output_format` for a structured answer, and `sandbox`
  (`enabled`, `autoAllowBashIfSandboxed: false`, `allowUnsandboxedCommands: false`,
  `network.allowedDomains` = `allowed_domains`). `ToolGate` is `can_use_tool`:
  `BashGuard` allows bash programs of `bash:` (or the 1.x default verbs) and refuses
  the forbidden tokens (`git push`, `gh `, `curl`, `rm -rf`, `sudo`, ...); edits only
  inside the worktree; Read, Glob and Grep only inside the worktree and the knowledge
  root; a read-only step cannot edit. The prompts are `src/briar/prompts/serve/*.md`
  (`system`, `plan`, `build`, `fix`, `review`, `comments`).
- **Git** (`serve/git.py`): `Shell` runs `{ cd X && (cmd); } > file 2>&1` through
  `os.system`. Clones live in `$XDG_CACHE_HOME/briar/repos/<owner>__<repo>`, worktrees
  in `.../worktrees/<company>/<branch with / as __>`. The token reaches git through a
  credential helper that `cat`s a 0600 file in a temp folder; never in a URL, the
  config or an error. Commits are `<login> <login@users.noreply.github.com>`.
- **The state** (`serve/state.py`): `work:<company>` in the file store, a JSON
  `WorkBook` at `<knowledge root>/work/<company>.md`, written under an `asyncio.Lock`.
  Each `WorkEntry`: `key`, `source`, `repo`, `title`, `status`, `branch`, `pr`,
  `plan`, `report`, `answered` (comment key -> `updated_at`), `rounds`. Statuses:
  `planned`, `pr-open`, `done`, `blocked`, `merged`, `closed` (`found` exists but no
  routine writes it). Taken again: a ticket left `found` or `planned` (a crash before
  the PR). Read each poll: PRs in `pr-open` or `done`. `TaskFlow.record` keeps what
  the entry already holds. The file store writes a temp file and renames it, so a poll
  never reads half a state. The state is not the only guard any more: a PR of the task
  branch on GitHub is recorded before any build.
- **The poll** (`CompanyWatch.cycle`): a failed find (GitHub or Jira error) is logged and
  tried again next poll; a failed login check of a scheduled extract still stops `serve`
  (J8). The tasks of one repo share its clone under one lock (`RepoCheckout.CLONE_LOCKS`).
- **The checks** (`ServeCheck`, `AgentSandbox`, `WriteCheck`): `ANTHROPIC_API_KEY` or
  `CLAUDE_CODE_OAUTH_TOKEN`; `sandbox-exec` on macOS, `bwrap` and `socat` on Linux;
  the read check of spec 005 per company, then GitHub `permissions.push` per repo and
  Jira `mypermissions` (`ADD_COMMENTS`, plus `TRANSITION_ISSUES` when `status` names
  are set). `briar doctor --runbooks DIR` (`doctor.RunbookChecks`) shows the same
  lines without serving.
- **The `serve:` block** (`ServeSettings`): required `model`, `max_usd`, `max_loops`,
  `ci_wait_minutes`, `max_review_rounds`; optional `repos`, `jira` (project -> repo),
  `priority_labels`, `max_parallel` (3), `poll_minutes` (5), `verify`, `bash`,
  `review_bots` (logins ending in `[bot]`), `allowed_domains`, `status` (`started`,
  `review`), `branch` (`briar/{key}`), `title` (`{key}: {title}`).
- **Calls per poll**, per company: one Jira search per project; with `repos:`,
  `GET /user` and one issue list per repo; per watched PR 5 calls (`GET /pulls/{n}`,
  three comment lists, one GraphQL). A task adds its own calls (clone or fetch,
  the ticket text, the PR, comments, a CI poll every 30 s).
- **Tests**: `tests/unit/serve/` (`fakes.py`: `FakeGithub`, `FakeJira`, `Companies`;
  `ticket_fakes.py`: `LocalOrigin` (a bare git repo), `ScriptedAgent`,
  `FakeGithubSide`, `Setups`; `conftest.py`: the `setups` fixture). `test_ticket.py`
  and `test_review.py` run the routines with real git, including a real merge
  conflict. `tests/unit/fake_logins.py` `Fixes` builds the expected login hints.

## 4. Found on the way

Each item was reproduced in this work; the PR named holds the evidence.

- **Typer resolves an inherited dataclass field's type in the subclass's
  module.** A shared option class in `base.py` raised `NameError: name
  'MeetingChoice' is not defined` in `agent.py`. `base.py` therefore has no
  `from __future__ import annotations` (comment at its top). PR #87.
- **Click's default map mixes option names and subcommand names.** A root
  option named `version` read the `version` command's entry ("Input should
  be a valid boolean"). Root option fields are `output_format`,
  `debug_logging`, `print_version`. PR #85.
- **Typer styles help and errors when `GITHUB_ACTIONS`, `FORCE_COLOR` or
  `PY_COLORS` is set, even into a pipe**, and the escape codes split tokens
  like `--nope`. Fixed with `rich_markup_mode=None`. PR #85.
- **pydantic-settings did not fit** (2 env vars, one config file, git as a
  source; its only way to put env above the file is a 5-parameter method).
  Recorded as D29b. PR #85.
- **The integration CI job runs on Python 3.14** (it does not pin a
  version), while the unit jobs run 3.10 to 3.12. Python 3.14 warns when an
  `HTTPError` is only closed by garbage collection, and the warning failed
  whichever test ran then (`--randomly-seed=4235835875`). briar now closes
  them in `swallow_errors`, the retry helper, the GitHub device flow and
  the GitHub Projects reader. PR #84.
- **Old credential-store tests made 2 real AWS calls** (boto3 found the
  machine's AWS config). They went with the stores. PR #83.
- **1.x quirks the tests now pin**: `--format` with no value exits 1, not 2
  (the pre-parse raises before argparse runs); `args.verbose` is always
  False in `run()` because the pre-parse removes `-v`, so `plan run`'s
  `impl.verbose = getattr(args, "verbose", False)` never turned on (`plan`
  is gone since spec 006).
- **Startup** (CPU time, median of 7, busy machine, against the 1.x code):
  `import briar.cli` 734 ms (1.x) vs 804 ms; `main(['version'])` after
  import 45 ms vs 37 ms. About 60 ms slower overall: Typer and the option
  classes load at import. Parsing itself is faster. PR #89.

Found in spec 005 (2026-11-10 and 2026-11-11):

- **GitGuardian flags an email next to a token, even a fake one.** A
  module-level pair of test constants (an `@example.test` email and a
  placeholder token) failed the PR check as an "Authentication Tuple". Only
  rewriting the branch history cleared it (PR #97, force-pushed twice). Put
  fake emails and tokens inside each test or a local dict, never as
  module-level constants side by side.
- **gcloud keeps one ADC file per config folder, not per named
  configuration.** google-auth reads `$CLOUDSDK_CONFIG` (or
  `~/.config/gcloud`) `/application_default_credentials.json`
  (`google/auth/_cloud_sdk.py`, `get_config_path`). A named `gcloud config
  configurations` entry changes the account of `gcloud` commands only. So
  one GCP login per company is one folder: `[auth.gcp] config_dir` (G26).
- **`AzureCliCredential` calls `az` at run time** to get each token, so
  `az` must be installed wherever the run happens, not only at login (I3).
  This is why Azure in the Docker image is unsupported (I8).
- **AWS SSO and static profiles log in differently.** An SSO profile
  (`sso_session` or `sso_start_url` in `~/.aws/config`) logs in with `aws
  sso login --profile P` and boto3 renews the token by itself; a static
  profile only has `aws configure --profile P`. `AwsLogin` reads the config
  with botocore to pick one (I5).
- **GitHub for many companies** (research before G10, G22): the gh CLI
  holds several accounts since 2.40.0, but `gh auth token` returns
  `GH_TOKEN`/`GITHUB_TOKEN` when set, while `--user` ignores them; its
  keyring entries (`gh:<host>`) are gh internals. No Python library reads
  the gh login (`ghtoken` only runs `gh auth token`). PyGithub (2.9.1, in
  `uv.lock`) has no device flow; githubkit (0.17.1) has one but is a new
  dependency. GitHub App user tokens from the device flow last 8 hours,
  their refresh token 6 months, and the refresh needs no `client_secret`.
  So 2.0 uses a briar GitHub App with its own device flow (httpx) plus a
  pasted token method, and PyGithub for the API (G10, G11, G22).
- **`os.system` can hand the output of a command back** through `os.pipe` (the
  child writes to the pipe fd, briar reads it), with no `subprocess` and
  nothing on disk. Tested on 2026-11-10; not used, since G10 dropped
  reading `gh auth token`.
- **Each PR of spec 005 cut tests with the code they pinned**: unit tests
  went 1511 (PR #95) to 1469 (PR #97) to 1428 (PR #98). The acquirer,
  `env_vars` and store-interface tests left with their code; the adapter
  tests were rewritten to take `FakeLogins`. PR #99 added the check tests
  (1501) and dropped the ones that pinned the old login skip.
- **A test of a command now meets the login check** (PR #99). Unit tests of
  what a run does after the check use the `logins_pass` fixture
  (`tests/conftest.py`); tests of `auth` and `doctor` use `fake_sources`
  (`FakeProbe` adapters); the integration wire fake answers `GET /user`,
  `/repos/<o>/<r>`, Jira `myself` and `project/<KEY>` (`add_pattern` in
  `tests/integration/conftest.py`), and `graphql_at` points Linear and
  Fireflies at it.
- **With no `[auth.azure] tenant`, `DefaultAzureCredential` waited 114 s**
  on the managed identity address (`169.254.169.254` answered HTML on
  this network) before it used the `az` login (`-v` log, 2026-11-11).
  The run and `auth status` pay that each time.
- **An optional SDK that is missing must not crash the check.** The error
  translators import their library only when a call fails; `auth status
  gcp` crashed with `ModuleNotFoundError: No module named 'httplib2'`
  until they returned nothing to catch in that case, and Azure raised a
  `RuntimeError` that was not a `CliError`. Found by the real run of PR #99.
- **There is no `[extract] include` key.** G12 and J18 named one; the docs
  say `--include`.

Found in spec 003 (`briar serve`, 2026-11-11):

- **PyGithub raises `GithubException`, not a `CliError`.** A refused
  `create_pull` (422 "A pull request already exists") crashed the task and left
  the ticket `planned`, so every poll would plan, build and pay again. Every write
  and CI read of `serve` on GitHub now goes through `GithubFailures`
  (`answer()` for calls that return a value), and `TaskFlow.blocked` blocks the
  ticket even when posting the report fails. PR #101.
- **The Agent SDK yields an error `ResultMessage`, then raises `ResultError`.**
  `ClaudeAgent` keeps the last result and catches `ClaudeSDKError`; with a refused
  key the reason is the text `Claude Code returned an error result: Invalid API key
  · Fix external API key (exit code: 1)` with subtype `success`, cost 0, about 4 s
  (checked with a fake key, 2026-11-11). A budget stop has subtype
  `error_max_budget_usd`. PR #101.
- **`/bin/sh` on the Linux CI runners is dash**: a missing folder reads `can't cd
  to`, not `No such file or directory`. Never assert the text of the shell itself;
  assert the path or the git output. PR #101 (first CI run failed on it).
- **`opentelemetry`, pulled in by `claude-agent-sdk`, warns on Python 3.10 and
  3.11** (`SelectableGroups dict interface is deprecated`), and pytest turns
  warnings into errors. `pyproject.toml` ignores that one message from that one
  module, like the botocore filter. PR #101.
- **The SDK sandbox is bash only.** `ClaudeAgentOptions.sandbox` (0.2.180) passes
  `{"sandbox": {...}}` to the bundled CLI through `--settings`; it limits writes
  to the working folder and the network to `allowedDomains`. Reads are limited by
  permission rules, not by the sandbox, so briar limits Read, Glob and Grep in
  `ToolGate`; `cat` in bash still reads any file of the user. Linux needs
  `bwrap` and `socat`; what the SDK does without them was not tested, so briar
  checks for them itself. The CLI starts with these settings (fake key, cost 0);
  a command running inside the sandbox was not seen. PR #102.
- **The GraphQL `databaseId` of a review thread comment equals its REST id**
  (`4413950074` on https://github.com/iklobato/briar/pull/75, 2026-11-11), so
  the resolved-thread skip can match REST comments by id. REST ids of the three
  comment kinds are separate sequences: the state keys them as
  `inline:<id>`, `conversation:<id>`, `review:<id>` (`ReviewComment.key()`).
  PR #102.
- **A reply to a reply is not allowed** by the GitHub review comment API: briar
  replies to the first comment of the thread (`in_reply_to_id`, else the id).
  PR #102.
- **A merge that leaves conflict markers would be pushed by the S23 stop**, since
  `blocked()` commits "work so far". `ReviewRun` aborts the merge before it
  re-raises; a test with a real conflict proves both paths. PR #102.
- **A GitHub suggestion committed on the PR moves the branch**: the review
  worktree must fast forward to `origin/<branch>` first, or the push is refused.
  PR #102.
- **`mergeable_state` is computed late by GitHub**: a new PR can read `unknown`
  once; `dirty` means a conflict. It comes in the same `GET /pulls/{n}` the
  finder makes anyway. PR #102.
- **The mypy count depends on the uv extras installed.** `uv run --extra test`
  drops the dev extras (the yaml stubs), so the same tree read 25 or 27 errors.
  Compare before and after with one venv: a `git worktree add --detach` of the
  base, and `<worktree>/.venv/bin/mypy` in both. PRs #101 and #102.
- **A `doctor --runbooks` line can come twice**: a scheduled GitHub extract and
  the pipeline check the same login. `RunbookChecks` drops the copy (found by
  the real run of PR #103).
- **`save_runbook_file` (`iac/runbook/writer.py`) has no caller in `src`**,
  before and after PR #103. `WorkItem.entry()` (status `found`) is used only by
  tests since part 2.

## 5. Next steps, in order

1. **Review and merge #79 to #104 into `v2`**, in order, as merge commits.
   Re-check `gh pr checks <n>` before each merge; after the last one, check
   that the `v2` CI run is green.
2. **Decide how 2.0 is released before `v2` merges to `main`.**
   `release.yml` runs on every push to `main` and bumps the patch only
   (`major.minor.patch+1`); `pyproject.toml` on `v2` says `1.1.101`, so the
   merge would publish 1.1.102, not 2.0.0. A PyPI version can never be
   reused. Not covered by any spec yet.
3. **The core cleanup spec** (C1 to C22, decided 2026-11-10): before specs
   003, 004 and 005, cut 2.0 to `extract`, `runbook` (extract, serve), `auth`
   (login, status), `init` and `doctor`; remove `plan`, `agent`, `context`,
   `secrets`, `journal`, `telemetry`, `version` as commands, the Postgres
   store, the task-scoped extractors, `messaging`, `notify`, `mcp`; move the
   stable `extract` flags to config. Planned (spec-kit, not committed) as 5
   stacked PRs on top of #90: (1) `plan`, `agent` and the code only they use,
   personas moved to `src/briar/prompts/*.md` first (done: PR #91, section
   2); (2) `context`,
   `secrets`, `journal`, `telemetry`, `version`, `messaging`, `notify`,
   `--format table|json`, `-v` only (done: PR #92); (3) file store only
   (done: PR #93); (4) `extract` with three flags and the rest from
   `[extract]` / `[extract.<name>]` (done: PR #94);
   (5) `auth`, `runbook`, `doctor`/`init` trims, docs and the success checks
   (done: PR #95).
   A 1.x key that left (`store`, runbook `messages:`/`mcp:`, an unknown
   `[extract]` key) stops the run with exit 1. The speed PR below is likely moot: after part 3,
   `import briar.cli` is about 350 ms faster than 1.x (section 2).
4. **Optional speed PR** (the user deferred it): import a command's option
   classes only when that command runs (about 25 ms), and lazy-import the
   heavy modules (before spec 006, `python -X importtime -c "import
   briar.cli"` showed `briar.commands.agent` 486 ms, now gone, and
   `briar.commands.runbook` 389 ms cumulative, mostly SQLAlchemy and
   storage, which part 3 removes).
5. **Write the next specs** (spec-kit, from the decisions files):
   - spec 003: `briar serve` (S1 to S35; it replaces the LangGraph engine
     of D9 and the YAML pipelines of D11). Part 1 is #100, part 2 (the ticket
     routine) is #101, part 3 (the review routine) is #102, part 4 (`auth`
     and `runbook` leave) is #103. Spec 003 is complete;
   - spec 004: extractors cleaned inside with the same text per extractor,
     `extract` on Typer, the extractor flags (D32, D32a, C12), and the new
     knowledge layout (K1 to K14, K16, K20);
   - spec 005: source logins, in progress (section 2). PR 1 is #97, PR 2
     (AWS, gcloud and Azure) is #98, PR 3 (the check) is #99. Left (G19),
     grilled before code (the maintainer asks for a grill at the start of
     every PR):
     - PR 4 (device flow, Linear OAuth, refresh lock) is dropped by the
       re-scope (S2: GitHub by PAT); see the spec 003 part 1 section.
     - Real runs still to do (G20): Jira, Bitbucket, Linear, Fireflies. The
       maintainer writes the `BRIAR_*` secrets in `secrets.env` by hand
       (`agents/logins.md`; `briar auth` left in #103), runs `briar doctor`,
       then asks for the run.
     - Local spec-kit state: `specs/005-source-logins/tasks.md` has T001 to
       T017 done (PRs 1 to 3; T017a, the real runs, open) and T018 to T023
       left.
6. **The first real runs of `briar serve`: done** (2026-11-11 and 2026-11-12, on
   `iklobato/briar-plan-test` and `iklobato/briar-oss-click`; cost US$ 0.19 to 1.03 per
   ticket). Their fixes are PR #104; what is left of them: `docs/briar-2-validation.md`
   sections 5 to 7. In short: spec 007 for the extract sources (decisions E1 to E27 in
   `docs/decisions/2026-11-11-briar-2-extract-sources.md`, a grill before each PR), B13
   (`doctor --runbooks` accepts a model id that does not exist), B11 proven live, Jira
   through `serve`, Linux and Docker.
7. **The review questions on #100 to #103**, none answered yet: the 152 MB
   wheel of `claude-agent-sdk` as a required dependency (#100); a failed poll
   stopping `serve` (#100); bash reads outside the worktree and a failure
   before the task starts retried every poll (#101, #102; V12 and V15 decided
   them, and the PR text still asks); `save_runbook_file` with no caller
   (#103).
8. **Site and images**: usebriar.com `/docs` and the landing page (private
   repo `iklobato/usebriar-landing`) still describe removed features
   (pipeline R7). `docs/diagrams/which-command.svg` still shows `briar
   chat`, `mcp serve`, `agent` and `plan`; `docs/diagrams/under-the-hood.svg`
   shows `mcp serve` and agents. Both come from the site, so change them
   there and export again. `agent-run.svg`, `review.svg`, `board.svg` and
   the demo GIFs `03-agent-ticket.gif`, `04-agent-review.gif` are no longer
   in the README; the site may still load them from `main`. Since PR #95
   the README shows no GIF and no diagram (F6): record
   `01-setup-and-extract.gif` and `02-runbook.gif` again on 2.0 and redraw
   the diagrams before putting any back. Nobody owns that yet.
9. **1.x issues still open on `main`**:
   https://github.com/iklobato/briar/issues/76 and
   https://github.com/iklobato/briar/issues/77.

## 6. What is missing or open

- The version on `v2` is `2.0.0.dev0` (2026-11-13), so an install from `v2` says what it
  is. The release workflow on `main` (`.github/workflows/release.yml`, "Bump patch
  version") reads a plain `X.Y.Z` and publishes the next patch: with `2.0.0.dev0` it stops
  at `int("dev0")`. Before `v2` merges into `main`, decide how 2.0.0 is published (set the
  version the bump turns into 2.0.0, or let the workflow publish a version as written).
  The update notice compares plain `X.Y.Z` only: a `dev` version gets no notice, so `v2`
  users are not told to "upgrade" to the next 1.x.
- `extract` is still argparse: its 3 flags do not tab-complete. Its
  `[extract]` values are not type-checked and a runbook still uses the
  1.x option names, until spec 004 (D32).
- Startup: spec 002 left it about 60 ms slower than 1.x (section 4); after
  spec 006, `import briar.cli` is faster than 1.x (679 ms against 1,130 ms,
  section 2, part 5).
- The README has no GIF or diagram until they are redone for 2.0
  (section 5, item 6).
- `briar doctor --format json` changed shape (F2): a script that read the
  1.x list of checks breaks.
- The scheduled extracts of `briar serve` have no failure alert until Apprise (C20, K4).
- Unit tests in `tests/unit/test_http_retry.py` and `tests/test_abstractions.py`
  still leak their own `HTTPError` objects on Python 3.14. CI does not run
  unit tests on 3.14, so nothing fails today.
- Typer 0.29.0 came out on 2026-11-09 and is pre-1.0; the pin is
  `typer>=0.29,<0.30`.
- Not checked: `briar --install-completion` writing into a real shell
  config; Python 3.10 only runs in CI.
- The open risks in both decisions files (pipeline R1 to R12, logins R1 to R7).
- Spec 003, open after PR #100:
  - The Linux wheel of `claude-agent-sdk` is 152 MB (it carries Claude
    Code); S4 makes it required. Review question on #100.
  - A failed poll (Jira or GitHub down) stops `serve` (J8 strictness);
    a non-briar error crashes with exit 2. Review question on #100.
  - Nothing is cut yet (S8): Bitbucket, Linear, Fireflies and the clouds
    stay as extractors.
- Spec 003, open after PR #103:
  - The site (usebriar.com `/docs`) still teaches `briar auth` and `briar
    runbook`; it changes after the 2.0 release (X6).
  - A 1.x script that calls `briar runbook serve` gets a plain `No such
    command` (X5).
  - `iac/runbook/writer.py` (`save_runbook_file`) has no caller in `src`; it
    was unused before this PR and is left for a later cleanup.
  - Bash of the agent can still read any file of the user (`cat`); Read, Glob
    and Grep are limited (V12). A secret read that way can only leave in a file
    of the PR diff (no network in the sandbox).
  - Not tested: `bwrap` in the Docker image (user namespaces), Linux. Bash inside the
    macOS sandbox and full tickets and review rounds with the model ran for real on
    2026-11-11 and 2026-11-12 (`docs/briar-2-validation.md`).
  - A failure before a task starts is logged and tried again on the next poll
    (V15), so a branch that cannot fast forward logs every cycle.
  - `allowed_domains` is a way out for data (V13).
  - The example model of the `init` runbook template and `agents/serve.md` is
    `claude-sonnet-4-5`, an older id than the current Claude models; the real runs used
    `claude-sonnet-5-5`. `doctor --runbooks` does not check the id (B13).
- Open after PR #104 (detail: `docs/briar-2-validation.md` sections 4 and 6):
  - B13 `doctor` accepts any model id; B14 the Azure title shows an AWS region; B11 (the
    self-review against a verify fix) changed in the prompt only, not proven live.
  - Every extract source but GitHub has a known bug (D1 to D15, G, L, BB items); the plan
    is E1 to E27 (spec 007), nothing built.
  - `serve` takes every open issue assigned to the token owner (S9): a repo where that
    person has other assigned issues needs a label filter, which does not exist.
  - Jira through `serve`, a CI failure that needs a code change, and a network drop during
    a poll did not run for real.
  - After a stop in the second between the PR and the ticket comment, the "briar opened"
    comment is missing.
- Spec 005, open after PRs #97 to #99:
  - `briar serve` (the scheduled extracts, as `runbook serve` before #103)
    stops for every company when one check fails, a short network drop
    included (J8 with J3, chosen on purpose); under a supervisor that
    restarts on failure it loops until the source answers. Review question
    on #99.
  - A scheduled extract (`extract_runbook`) runs nothing when its check
    fails (J7).
  - A bare `briar extract` checks every source of the extractors that have
    their repo or project list: with the git origin as the repo, that is
    GitHub, AWS and Fireflies at least. Use `--include`.
  - The Bitbucket access token path skips `GET /2.0/user` (it belongs to no
    user); not checked on a real token. The GCP project read and the Azure
    subscription read were not run for real.
  - GitHub takes `method = "token"` only and Linear `api-key` only, until
    PR 4 (H8). Review question on #97.
  - With no `[auth.azure] tenant`, a run uses `DefaultAzureCredential`,
    which waited 114 s here (section 4). Review question on #98: keep it,
    or `AzureCliCredential` with the default tenant only.
  - The Docker image has no `aws`, `gcloud` or `az` (I8): log in outside
    the container and mount `~/.aws` and the gcloud folder. Azure in Docker
    is not supported until someone asks.
  - `examples/multi_company.env.example` still lists 1.x names; reading it
    is blocked for agents (env file), so check it by hand.
  - mypy on the top of the stack (`serve/04-cleanup`): 25 errors with the dev
    and test extras installed (2026-11-11), none in the files of spec 003.

## 7. What to watch

- **The stacked PRs**: a change to a lower PR needs the PRs above it
  rebased or merged up. Check each PR's CI after any push.
- **Release**: when `v2` merges to `main`, the release workflow publishes
  at once (section 5, item 2). Then check PyPI, Docker Hub and the GitHub
  Release as `CLAUDE.md` section 4 says.
- **The integration job on Python 3.14**: a random failure there after an
  unrelated change is usually an object left open (section 4). Reproduce
  with `uv run --python 3.14 --extra test pytest -m integration -p randomly
  --randomly-seed=<seed from the CI log>` in a separate environment
  (`UV_PROJECT_ENVIRONMENT=<some dir>`).
- **Typer upgrades**: bump the pin only with the CLI tests green
  (`pytest tests/unit/cli`), since Typer changes its API between minor releases.
- **The CLI contract**: `tests/unit/cli/flags_1x.json` is the 1.x contract.
  Change it only when a decision removes or renames a flag, in the same PR.
- **`v2` drifting from `main`** (pipeline R8): 1.x fixes on `main` need a
  merge into `v2`.
- **GitGuardian on every PR** (section 4): a fake email next to a fake
  token as module-level constants fails the check, and only a history
  rewrite clears it. Use `bot@example.test` and `PLACEHOLDER-not-a-secret`
  inside the test.
- **The cloud CLIs change under briar**: `aws sso login`, `gcloud auth
  application-default login` and `az login` are run as typed, and the run
  reads their files (`~/.aws/config`, the ADC file, the `az` login). A
  change in where a CLI keeps its login breaks `briar doctor` and the
  run. When one breaks, check the CLI version first.
- **1.x users moving to 2.0**: every 1.x env name (`GITHUB_TOKEN`,
  `JIRA_<C>_*`, `AWS_<C>_*`, ...) stops a run until the user moves it. The
  stop message names the new place; keep those messages in step with
  `[auth]` when a key moves.
- **The calls of the login check** (PR #99): one per source plus one per
  resource before every run, and before every `serve` job. A job every
  minute with 10 GitHub repos makes 11 calls a minute, 660 an hour, against
  the GitHub REST limit of 5,000 an hour for a user token (J16, other
  sources not measured). Watch the rate limit when a company has many repos
  and a short `every:`.
- **`briar serve` spends model money** (S23, S24): at most `max_usd` per task,
  and a PR can take `max_review_rounds` rounds of `max_usd` each. There is no
  measured cost of one ticket yet (section 5, item 6). The cost of each agent
  call is in `AgentAnswer.cost_usd`; the stop report says the total.
- **`briar serve` acts as the owner of the PAT** (S2, S9): every issue assigned to
  that person becomes work, and the commits, PRs and replies carry that name.
  Only comments of writers and `review_bots` are answered (V1); on a public repo
  a stranger can still open an issue, but only an assignee makes it work.
- **GitHub calls of `serve`**: 5 per watched PR per poll (section 3), plus the
  login check of each extract job. 20 open briar PRs at the default 5 minutes
  make about 1,200 calls an hour, against 5,000 an hour for a user token (not
  measured). The GraphQL query reads the first 100 threads of a PR and 100
  comments each (a `ponytail:` note in `_providers/github.py`).
- **The agent sandbox on Linux and in Docker**: `bwrap` needs user namespaces;
  a container that blocks them fails the sandbox (not tested). `serve` and
  `doctor` only check that the programs are there.
- **`claude-agent-sdk` upgrades**: the pin is `>=0.2.180,<0.3`, and the SDK
  bundles its own Claude Code CLI, whose version follows the SDK (S1 risk).
  `ClaudeAgent` reads `ResultMessage` fields and `ResultError.subtype`; re-run
  `tests/unit/serve/test_agent.py` and one real call after an upgrade.
- **What the validation run of PR #104 says to watch** (`docs/briar-2-validation.md`
  section 7): the cost of `BaseDrift` (one fetch and `merge-tree` per watched PR per poll
  once the base moves), GitHub keeping `mergeable_state` stale, private repo Actions runs
  that stay `queued` (now a `blocked` task after `ci_wait_minutes`, not a ready PR), a
  machine whose Python fails pytest in a clean folder (verify runs on a clean copy now),
  issues assigned to the token owner that are not for briar, and model spend (US$ 0.19 to
  1.03 per ticket so far).
- **One `serve` per knowledge folder**: two processes write the same
  `work:<company>` file (the lock is per process).
- **A new extractor or adapter must join the check**: an extractor gives
  `login_target`, an adapter `login_source`, `who_am_i` and `read`, and its
  library errors go through a class of `extract/_login_failures.py`. One
  that does not is never checked, and its bad login shows up only as an
  empty section.

## 8. The asks of the maintainer and where they stand

What the maintainer asked in their own words (not the answers to questions)
in the session of 2026-11-10 and 2026-11-11, merged into one list, and where
each stands on 2026-11-11. "Done" means built in an open PR with green CI:
none is merged or released (section 1).

| # | Ask | Status |
|---|---|---|
| 1 | Analyse briar and plan how to make it as organized as possible | Done: the decision files of `docs/decisions/` and specs 001, 002, 003, 005, 006 |
| 2 | Use ready libraries (CrewAI was named) instead of manual code | Done with other libraries: the Claude Agent SDK for the agent (S1 replaced LangGraph, D9), PyGithub, atlassian-python-api, the cloud SDKs |
| 3 | Access and logins with existing tools, not manual code | Done: spec 005 (#97 to #99), GitHub by PAT (S2) |
| 4 | Programmatic in the code rather than client tools | Partial: GitHub and Jira through libraries; `serve` still runs `git` through `os.system`; the agent is the Claude Code CLI inside the SDK; the clouds log in through their own CLI |
| 5 | Keep extracting the sources as today | Done: `briar extract`, 23 extractors, the byte-for-byte snapshot test (#79) |
| 6 | Understand the extractor and its features | Decided (K1 to K20); not built (spec 004, not written) |
| 7 | Store the data so other agents can use it | Decided (K1 to K20); not built: still one blob `knowledge:<company>` |
| 8 | Always the simplest way | Applied in every PR |
| 9 | Logins for every source briar reads | Built (#97 to #99); run for real: GitHub, AWS, GCP and Azure offline lines; not run for real: Jira, Bitbucket, Linear, Fireflies (G20) |
| 10 | A pasted cookie string instead of a browser login | Built for Jira and Bitbucket (`method = "cookie"`); not run for real; the Bitbucket cookie only against a fake server |
| 11 | `briar extract` and `briar serve` as the core of 2.0 | Done: 4 commands (#103) |
| 12 | A pipeline with inner loops up to the PR | Done: verify, self-review and CI loops (#101) |
| 13 | Look at sources like GitHub and Fireflies in the ticket pipeline | Partial: the pipeline talks to GitHub and Jira only (S8); other sources reach the agent only through the knowledge blob |
| 14 | Watch for a newly assigned ticket | Done (#100) |
| 15 | Write a plan of action | Done: structured plan, commented on the ticket (#101) |
| 16 | Read the knowledge of the sources | Done: scheduled extracts plus the blob path given to the agent (#100, #101) |
| 17 | Build on a new branch and open the PR | Done (#101) |
| 18 | Watch the PR and fix or justify each review comment | Done (#102) |
| 19 | Parallel, not only sequential | Done: asyncio, `max_parallel` per company (#100) |
| 20 | Focus on GitHub and Jira only | Partial: true for `serve`; Bitbucket, Linear, Fireflies and the clouds stay as extractors (S8) |
| 21 | Cut and clean up as much as possible | Partial: 15 commands of 1.x left (`scaffold`, `dashboard`, `chat`, `mcp`, `agent`, `plan`, `context`, `secrets`, `journal`, `telemetry`, `version`, `config`, `completion`, `auth`, `runbook`) and the Postgres store too; still there: the extra sources, the unused prompts `roles/`, `tasks/`, `rules/`, `save_runbook_file`, `extract` on argparse |
| 22 | A simplest PR 4 for `serve`, no grill | Built (the GitHub device flow, `cf7bcb4`), then dropped by the re-scope (S30) |
| 23 | A grill before each decision | Done for every spec and PR except ask 22 |
| 24 | Status of the running task | Given at the time |
| 25 to 28 | Keep this file and `CLAUDE.md` current, readable by any agent, in the repo, for the session only | Done (PR #90, a commit on #98, `748d1d2` and this commit on #103) |
| 29 | List every ask of the session | Given in the session |
| 30 | Plan tests of every feature; say what to remove or improve so `extract` and `serve` reach their goal | Done: `docs/briar-2-validation.md` |
| 31 | Analyse the deep consequences, not only the output; connect Fireflies, AWS, Azure and GitHub | Done for real on GitHub, AWS and Azure; Fireflies broken for everyone (D7); GCP not readable with the login of the machine |
| 32 | A fix plan to make AWS, Azure, GCP, Fireflies, Bitbucket and Linear work, decided interactively | Decided (E1 to E27), not built: spec 007 |
| 33 | Fix `serve` to read the knowledge, open a draft, fix the PR and justify on review comments | Done: PR #104 (W1 to W4), proven on real PRs |
| 34 | Validate `extract` and `serve` relentlessly in complex flows with the logins at hand | Done: 17 bugs found, 15 fixed in PR #104 (section 2, "The first real runs and their fixes") |
| 35 | Do not use the infra of a client company for the tests | Done: the validation used private test repos of the maintainer only |
| 36 | Fix the mutation tool before `v2` merges to `main` | Done: `2b11d3a`, mutation 22 of 22 |

Left before any of it counts as live: the review and merge of #79 to #104 into `v2`, the
answers to the review questions on #100 to #103, the 2.0 release decision (S32), then the
site (X6).

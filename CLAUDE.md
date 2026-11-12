# briar CLI: notes for agents

Read this before you change anything in this repo. It says what the repo
is, how a change becomes a release, which facts are easy to get wrong, what
is open, and what to watch. Facts carry the date they were last checked.
When a fact here and the code disagree, the code wins: fix this file in the
same PR.

The website (usebriar.com, the `/docs` pages) lives in a separate private
repo, `iklobato/usebriar-landing`, branch `nextjs-app`, with its own
`CLAUDE.md`.

## 1. What briar is

A command-line tool, package `briar-cli`, command `briar`. It runs on the
user's machine and does two things:

1. **Collects facts** ("knowledge") from a team's tools with extractors
   and writes them as markdown blobs to a file store (`./knowledge/`); 1.x
   could also write to Postgres.
2. **Runs AI coding agents** that read those facts. In 1.x (`main`) that
   is `agent implement` (ticket to draft PR), `agent prfix` (answers review
   comments on a PR) and `plan` (works a whole board card by card). 2.0
   (`v2`) removed all three (spec 006); the ticket pipeline of spec 003,
   not built yet, replaces them.

No server, no account. There was a SaaS version; it is archived and lives
only in the private site repo.

## 2. Quick facts (checked 2026-11-10)

| Thing | Value |
|---|---|
| Latest version | 1.1.101 (PyPI and GitHub Release `v1.1.101` = latest) |
| Python | 3.10, 3.11, 3.12 (CI unit jobs); the integration job runs on whatever `uv` picks (3.14 in CI) |
| Release trigger | every push to `main`, except `chore(release):` commits; it bumps the patch only |
| 2.0 | in progress on branch `v2` (still equal to `main`); 18 stacked PRs open, #79 to #96. Status and handoff: `docs/briar-2-status.md` |
| MCP registry | `io.github.iklobato/briar`, latest 1.1.94; stays there, 2.0 has no MCP server |
| Open issues | #76, #77 (section 8) |
| Open PRs | #79 to #96, all into `v2`, not reviewed |
| Stars / forks | 3 / 0 |

## 3. Repo layout

| Path | What is there |
|---|---|
| `src/briar/cli.py` | Entry point `main()`: global flags (`--format`, `-v`, `--version` anywhere), startup work (skipped for help, completion and `--version`), then runs the parsed command with exit codes 0/1/2/130. On `main` (1.x) it still builds the whole argparse parser |
| `src/briar/cli_app.py` | The Typer app (2.0, spec 002). Each command declares its options as a `CommandOptions` dataclass in its own module; only `extract` still goes through `PassThrough` to its argparse parser (until spec 004), and `src/briar/config.py` keeps the argparse shim it needs. Since spec 006 part 4 that parser has only `--company`, `--include`, `--repo`; every other extractor value comes from `[extract]` / `[extract.<name>]` (`ExtractConfig` in `commands/extract.py`, decisions in `docs/decisions/2026-11-10-briar-2-extract-config.md`) |
| `src/briar/settings.py` | Where a setting comes from: env > `.briar.toml` / `[tool.briar]` > git origin > default; feeds the settings table of `briar doctor` and the Typer default map |
| `src/briar/commands/` | One module per top-level command (`extract`, `runbook`, `auth`, `init`, `doctor`) |
| `tests/unit/cli/` | The CLI contract from 1.x (`flags_1x.json`, precedence, global flags, exit codes); must pass unchanged through the Typer move |
| `src/briar/extract/` | The extractors: the 23 in `EXTRACTORS` (`extract/__init__.py`). The 4 task-scoped ones of 1.x left in spec 006. Providers in `_providers/`, `_trackers/`, `_clouds/`, `_meetings/` |
| `src/briar/extract/composer.py` | `KnowledgeComposer`: writes `## <heading> - <detail>` sections, `split_sections` parses them, `merge_markdown` merges a run into a stored blob (called from `CommandExtract._write_blob`, the one write path of `briar extract`) |
| `src/briar/prompts/` | The agent prompt text for the spec 003 pipeline: `roles/`, `tasks/` (moved from the 1.x personas) and the 8 shared `rules/`. Nothing reads them at run time yet. The 1.x `agent/`, `plan/` and `mcp/` packages are gone; `docs/briar-2-status.md` says where to find them in git |
| `src/briar/iac/runbook/` | Runbook loader, executor, `scheduler.py` (`runbook serve`) |
| `src/briar/credentials/` | The env-file credential store (2.0 removed AWS Secrets Manager, SSM and Vault) and `_removed_stores.py`, which stops a run whose `BRIAR_DEFAULT_STORE` names a removed one |
| `agents/*.md` | The user manual, one file per command (linked from the README) |
| `docs/decisions/` | The agreed 2.0 scope: `2026-11-10-briar-2-pipeline.md` (D1-D36, plus D29a, D29b, D32a), `2026-11-10-briar-2-source-logins.md` (L1-L21), `2026-11-10-briar-2-core-cleanup.md` (C1-C22: 2.0 keeps 5 commands), `2026-11-10-briar-2-ticket-pipeline.md` (P1-P22, A1-A15: ticket pipeline, agent roles, pipeline YAML) and `2026-11-10-briar-2-knowledge.md` (K1-K20: knowledge layout for agents). The reference for every 2.0 PR |
| `docs/briar-2-status.md` | 2.0 handoff: the PR stack, what is done, how the 2.0 CLI works, findings, next steps, what to watch |
| `docs/diagrams/`, `docs/demo/` | 1.x README images, still loaded by the site; the 2.0 README shows none until they are redone (section 9) |
| `examples/` | Runbook YAML examples |

## 4. How to work in this repo

- **Branch from `briar/main`** for 1.x work. In the maintainer's local
  checkout the remote is named `briar`, not `origin`, and the checked-out
  branch is an old feature branch. Always `git fetch briar` and branch from
  `briar/main`.
- **2.0 work goes to `v2`, not `main`** (decision D27). The open 2.0 PRs are
  stacked: branch a new 2.0 PR from the top of the stack (now
  `fix/doctor-git-remote-hint`, PR #96) and target `v2`. Read
  `docs/briar-2-status.md` first.
- **A merge to `main` is a public release.** `.github/workflows/release.yml`
  runs the unit tests (`uv run pytest -n auto -m "not integration"`),
  bumps the patch version, builds, pushes the tag, uploads to PyPI, pushes
  `iklob1/briar:<version>` to Docker Hub and creates the GitHub Release. A
  PyPI version can never be reused. If the test gate fails, nothing is
  published: fix and merge again.
- **One release at a time.** Wait for the release run of one merge to
  finish before merging the next, and rebase each branch on the newest
  `main` first. CHANGELOG versions must follow merge order.
- **CHANGELOG.** The workflow never edits `CHANGELOG.md`. Every PR writes
  its own entry under the version it will get (current + 1).
- **Tests.** `make check` is the gate. For a bug fix, add a test that fails
  on `main` and passes on the fix; prove the fail by applying only the
  new test to `main`. Some old tests pinned wrong behavior; a fix must
  update them too. `CONTRIBUTING.md` lists the test gotchas (hermetic
  `cli` fixture, `-p no:tldr`, `-p no:randomly`).
- **Lint.** black (line length 160, from `pyproject.toml`) and ruff (its
  default rules; no `[tool.ruff]` section). The repo is not clean repo-wide
  under black, ruff and mypy (39 mypy errors on `v2` on 2026-11-10); keep your
  changed files clean and compare counts before and after.
- **Fake secrets in docs must look fake.** GitGuardian blocks PRs on
  realistic ones (`ghp_...` with a real-looking body, `sk-ant-api03-...`,
  `postgresql://user:pass@`). Use `<placeholder>` or `sk-ant-...`.
- **Grep every caller before calling a fix done.** The extract loop is still
  copied in `iac/runbook/executor.py` (2.0 decision D32 removes the copy), so
  a bug can live in two places. In 1.x the MCP server's `service/` layer was
  a third copy (the extract merge was fixed in the CLI in 1.1.82 and in MCP
  in 1.1.83).
- **After a release, verify it live**, not from the run log:
  - PyPI: `curl -s https://pypi.org/pypi/briar-cli/<version>/json`
    (the plain `/pypi/briar-cli/json` lags a few minutes).
  - Docker Hub: `https://hub.docker.com/v2/repositories/iklob1/briar/tags/<version>`.
  - GitHub: `gh api repos/iklobato/briar/releases/latest -q .tag_name`.
- **`gh pr edit` fails** on this repo ("Projects (classic) is being
  deprecated") and changes nothing. Edit a PR body with
  `gh api -X PATCH repos/iklobato/briar/pulls/<n> -F body=@file`.
- **The MCP registry entry pins 1.1.94.** 2.0 removed `briar mcp serve`, so
  do not re-publish it from 2.0 (in 1.x: `mcp-publisher login github` gives
  a device code that expires in about 15 minutes, then
  `mcp-publisher publish`).

## 5. Behavior that is easy to get wrong (checked in code)

- **`briar extract` merges** into the stored blob (since 1.1.82, MCP
  `extract_run` since 1.1.83): only the sections of the extractors that
  ran are replaced or dropped. **`runbook extract` and `runbook serve`
  write the whole blob**, so sections from other runs are replaced.
- **Blob names**: `briar extract` writes `knowledge:<company>`. A runbook
  task named `extractors` writes `knowledge:<company>`; any other task
  name writes `knowledge:<company>.<task>`. No check that task names are
  unique.
- **`runbook serve`**: minimum cadence is one minute. The first run of each
  (company, task) is shifted by `sha1("company:task") % cadence` seconds
  so teams do not call GitHub together; jobs with `at` (for example
  `"day at 03:17"`) are not shifted. Jobs run one after another in one
  thread. Ctrl+C prints `interrupted` and exits 130.
- **Logging**: quiet by default (WARNING), `-v` for DEBUG; on `v2` no
  env var changes the level (`BRIAR_VERBOSE`, `BRIAR_LOG_LEVEL` left).
  `runbook extract` and `runbook serve` call `daemon_logging()` and always
  print INFO progress.
- **`briar init`** reads `git remote get-url origin` for owner and repo.
  Settings resolve as flag > env var > `.briar.toml` (or `[tool.briar]` in
  `pyproject.toml`) > git origin (owner and repo) > default; only one config
  file is read, never a merge (`briar doctor` prints the source of
  each). Pinned by `tests/unit/cli/test_settings_precedence.py` on `v2`.
- **Telemetry tier** on `v2`: `DO_NOT_TRACK=1` > `BRIAR_TELEMETRY` >
  `telemetry` in the project config > `off`. `main()` reads the project
  config before `telemetry.install(project_config)`; no state file.
- **`runbook` failure alerts**: none on `v2` (`notify/` and
  `BRIAR_NOTIFY_SINKS` left, C20); a failed task is only logged.
- **A runbook with `messages:`, `mcp:` or `git_identity:`** stops with
  exit 1 and names the block (`CompanyEntry.REMOVED_KEYS` in
  `iac/runbook/models.py`).
- **Docker image** (`iklob1/briar`): has `git`, `gh`,
  `safe.directory '*'`, `gh` also linked at `/usr/local/bin/gh` and
  `/opt/homebrew/bin/gh` (Mac gitconfigs), `linux/amd64` only, runs as uid 1000. The git login is set up for GitHub
  only. The `[mcp]` extra is gone on `v2`.

## 6. What was done (summary)

Full detail per version is in `CHANGELOG.md`. In short:

- **1.1.56 to 1.1.72**: Python 3.10 support, auth and extract fixes, every
  bug from a command audit fixed (one PR each), dead code removed.
- **1.1.73 to 1.1.80**: PyPI description and MCP registry label; agents run
  on a plain install (1.1.74) and in Docker (1.1.75, 1.1.79); MCP setup
  errors print one line (1.1.77); release workflow creates the GitHub
  Release (1.1.78); test fixture keeps telemetry off (1.1.80).
- **1.1.81 to 1.1.94**: one PR and release per known bug (dry-run saves
  nothing, extract merge in CLI and MCP, MCP `extract_run` params, card
  branch names and parents, runbook default blob, implement prompt,
  Bitbucket PR recipe and CI normalising, `runbook serve` per company,
  `--repo` replaces config, journal stores, remote default cred store,
  plan notes reach each card).
- **1.1.95 to 1.1.97**: fixes found by the first real `plan run` (bash
  tool quote parsing, stacked dependent cards, "done needs a commit",
  card status over journal history). That run, on a private test board,
  opened two stacked draft PRs.
- **1.1.98 to 1.1.100** (docs only): `agents/plan.md` and `agent.md`;
  README diagrams copied from usebriar.com (1.1.99); README rewritten for
  newcomers with badges, glossary and "Learn briar in 3 examples" with
  GIFs (1.1.100).
- **2.0, on `v2`, unreleased (2026-11-10)**: spec 001 cleanup (PRs #79 to
  #83: scaffold, dashboard, chat, MCP server, default MCP servers, remote
  credential stores removed; telemetry opt-in) and spec 002 (PRs #84 to #89:
  every command but `extract` parsed by Typer with the same namespace in
  `run()`, the 1.x aliases and `briar completion` removed, Typer completion
  added), then spec 006 (core cleanup) PR #91: `agent`, `plan` and the code
  only they used removed, the personas and rules moved to
  `src/briar/prompts/`; PR #92: `context`, `secrets`, `journal`,
  `telemetry`, `version`, `messaging`, `notify` removed, `--format
  table|json`, `-v` only, telemetry tier from env or `.briar.toml`; PR #93:
  the Postgres store, `--store` and the `store` setting removed; PR #94:
  `extract` keeps 3 flags, the rest moved to `[extract]` in `.briar.toml`,
  the output modes removed; PR #95: `auth` keeps `login` and `status`,
  `runbook` keeps `extract` and `serve`, `briar config` folded into
  `briar doctor` (settings table, telemetry row, JSON object), README
  rewritten around the 5 commands with no images; PR #96: the `doctor`
  git remote hint names `briar init --owner`.
  Details and numbers: `docs/briar-2-status.md`.

## 7. Next steps (in order)

0. **2.0**: review and merge PRs #79 to #96 into `v2` in order; decide how
   2.0 is released before `v2` merges to `main` (the workflow would publish
   1.1.102); then specs 003, 004, 005. Order and detail:
   `docs/briar-2-status.md` section 5.
1. Fix #77 (small): treat GitHub's `404 no analysis found` as "no data"
   without a traceback; keep other errors logged. Then check the same
   `@swallow_errors` pattern for other expected 404s (for example a
   missing ticket in `agent implement --dry-run`, which logs a full
   traceback and carries on).
2. Fix #76 on `main` (1.x only; `--merge-claude-md` is gone on `v2`):
   build the `CLAUDE.md` index and detail file from the merged blob, not
   from the run's sections.
3. The plan-run findings in section 8, branch-name slug first (1.x only:
   2.0 has no `plan`).
4. The untested paths in section 8 that need real credentials (Bitbucket,
   Jira or Linear over MCP, Postgres; 1.x only).
5. The MCP registry stays at 1.1.94; 2.0 has no MCP server.

## 8. What is still open

**2.0**: see `docs/briar-2-status.md` section 6 (extract still on argparse;
site docs, GIFs and diagrams still show removed commands, so the README
shows none until they are redone; release mechanics for 2.0 not decided).

**Filed issues (2026-10-29, both reproduced on 1.1.99, acceptance
criteria in each):**

- #76 `extract --merge-claude-md` writes `CLAUDE.md` and
  `.briar/knowledge/<company>.md` from this run's sections only
  (`commands/extract.py`, `_merge_claude_md(args, sections)`). The
  `wrote blob (N bytes, M sections)` line mixes blob bytes with this run's
  section count.
- #77 `code-scanning` logs ERROR plus a full traceback on GitHub's normal
  `404 no analysis found` (`@swallow_errors` uses `log.exception`).

**Found, not filed yet:**

- From the real `plan run` (2026-10-13, not re-checked since; 1.x only,
  `plan` is gone on `v2`): branch
  names for GitHub Project cards come out as
  `chore/<owner>-<repo>issue-1` (missing dash, whole repo in the name);
  `briar plan show --format json` prints markdown; a plan built from a
  GitHub Project stores `repo: ""`, so `plan run` needs `--owner/--repo`;
  the bash tool's forbidden-token check matches inside quotes (a PR body
  that says "no rebase" would be refused; not seen in a run); the selector
  trusts the plan notes blob over card status.
- `briar context list` prints `updated_at` as a raw epoch float
  (`1793282842.3368032`), from `commands/context.py` line 109 (2026-10-29;
  1.x only, `context` is gone on `v2`).
- `briar extract` with no `--include` ran all 23 extractors in about 4
  minutes on this repo and wrote an "AWS infrastructure (UNREACHABLE)"
  section on a machine with an AWS default chain whose
  `caller_identity` failed. Whether that section should be written is not
  decided; the cause on that machine was not checked (2026-10-29).
- `auth refresh` (1.x only; gone on `v2`) supports no target: a real
  GitHub refresh needs a GitHub App and its client secret.

**Never run for real (unit tests only), all 1.x:** Postgres for the extract
merge, a successful Bitbucket PR, Jira and Linear over MCP, and a full MCP
client run that writes data.

## 9. What to watch

- **2.0 stack and release**: `docs/briar-2-status.md` section 7 (stacked PRs,
  the release that a `v2` to `main` merge triggers, the integration job on
  Python 3.14, Typer upgrades, the 1.x CLI contract file).
- **Release runs.** A random test failure has blocked a release before
  (telemetry banner, fixed in 1.1.80; one other failure on 2026-06-25,
  cause not checked). After every merge, check that the run is green and
  that PyPI, Docker Hub and the GitHub Release all show the new version.
- **Docs drift.** The website's `/docs` pages and the landing page state
  CLI behavior and some versions ("since 1.1.75"). When CLI behavior
  changes, update `agents/*.md` here and the site docs in the site repo.
  The site was last checked against 1.1.98.
- **README images.** The 2.0 README shows none (decision F6 of
  `docs/decisions/2026-11-10-briar-2-final-trims.md`): the 1.x ones show
  removed commands. Record and draw them again before putting them back.
  `docs/diagrams/*.svg` are copies of the diagrams on
  usebriar.com (exported from the live page with styles inlined). If the
  site diagrams change, export them again. `docs/demo/*.gif` are real
  runs of briar 1.1.99 in a clone of this repo (asciinema 2.4.0 + agg
  1.9.0; a small zsh player typed each command and printed "What it
  does / Behind the scenes / What you see" lines; long output was paged
  one screen at a time by changing only timestamps). Re-record them when
  the output of those commands changes. The recorder scripts are not in
  git. The images load from `raw.githubusercontent.com/.../main/...` so
  PyPI shows them too; a PR preview shows them only after merge.
- **MCP registry** stays at 1.1.94, the last release with `briar mcp serve`.
- **Merged branches** pile up on GitHub (50 remote branches on
  2026-10-29). Delete them only after checking each one is merged.

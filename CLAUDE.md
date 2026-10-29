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
   and writes them as markdown blobs to a file store (`./knowledge/`) or
   Postgres.
2. **Runs AI coding agents** that read those facts: `agent implement`
   (ticket to draft PR), `agent prfix` (answers review comments on a PR),
   `plan` (works a whole board card by card).

No server, no account. There was a SaaS version; it is archived and lives
only in the private site repo.

## 2. Quick facts (checked 2026-10-29)

| Thing | Value |
|---|---|
| Latest version | 1.1.100 (PyPI, Docker Hub `iklob1/briar:1.1.100`, GitHub Release `v1.1.100` = latest) |
| Python | 3.10, 3.11, 3.12 (CI runs all three) |
| Release trigger | every push to `main`, except `chore(release):` commits |
| MCP registry | `io.github.iklobato/briar`, latest 1.1.94 (no MCP tool changed since) |
| Open issues | #76, #77 (section 8) |
| Open PRs | none |
| Stars / forks | 2 / 0 |

## 3. Repo layout

| Path | What is there |
|---|---|
| `src/briar/cli.py` | Entry point: parser, config defaults, git inference |
| `src/briar/commands/` | One module per top-level command (`extract`, `agent`, `plan`, `runbook`, `context`, `auth`, ...) |
| `src/briar/extract/` | The extractors. 23 scheduled ones in `EXTRACTORS`, 4 task-scoped ones (ticket, PR review, meeting, Slack context) in `TASK_SCOPED_EXTRACTORS`, both in `extract/__init__.py`. Providers in `_providers/`, `_trackers/`, `_clouds/`, `_meetings/` |
| `src/briar/extract/composer.py` | `KnowledgeComposer`: writes `## <heading> - <detail>` sections, `split_sections` parses them, `merge_markdown` merges a run into a stored blob |
| `src/briar/service/` | Shared logic for the CLI and the MCP server. `service/extract.py::write_knowledge_blob` is the one write path for extract |
| `src/briar/agent/` | `runner.py` (the tool loop), `tools.py` (bash allowlist and forbidden tokens), persona and prompt building |
| `src/briar/plan/` | Board readers (`_boards/`), selector, store, run loop |
| `src/briar/iac/runbook/` | Runbook loader, executor, `scheduler.py` (`runbook serve`) |
| `src/briar/mcpserver/` | `briar mcp serve`; tools in `_tools.py` (10 `@mcp.tool()`) |
| `src/briar/credentials/` | Credential stores: env file, AWS Secrets Manager, SSM, Vault |
| `agents/*.md` | The user manual, one file per command (linked from the README) |
| `docs/diagrams/`, `docs/demo/` | README images (section 9) |
| `examples/` | Runbook YAML examples |

## 4. How to work in this repo

- **Branch from `briar/main`.** In the maintainer's local checkout the
  remote is named `briar`, not `origin`, and the checked-out branch is an
  old feature branch. Always `git fetch briar` and branch from
  `briar/main`.
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
- **Lint.** black and ruff (line length 100). The repo is not clean
  repo-wide under ruff and mypy; keep your changed files clean and compare
  counts before and after.
- **Fake secrets in docs must look fake.** GitGuardian blocks PRs on
  realistic ones (`ghp_...` with a real-looking body, `sk-ant-api03-...`,
  `postgresql://user:pass@`). Use `<placeholder>` or `sk-ant-...`.
- **Grep every caller before calling a fix done.** `service/` holds copies
  of command logic for the MCP server, so a bug often lives in two places
  (it happened with the extract merge: CLI fixed in 1.1.82, MCP in 1.1.83).
- **After a release, verify it live**, not from the run log:
  - PyPI: `curl -s https://pypi.org/pypi/briar-cli/<version>/json`
    (the plain `/pypi/briar-cli/json` lags a few minutes).
  - Docker Hub: `https://hub.docker.com/v2/repositories/iklob1/briar/tags/<version>`.
  - GitHub: `gh api repos/iklobato/briar/releases/latest -q .tag_name`.
- **`gh pr edit` fails** on this repo ("Projects (classic) is being
  deprecated") and changes nothing. Edit a PR body with
  `gh api -X PATCH repos/iklobato/briar/pulls/<n> -F body=@file`.
- **The MCP registry entry pins a version.** Re-publish only after a
  release that changes an MCP tool or the image's MCP behavior
  (`mcp-publisher login github` gives a device code that expires in about
  15 minutes, then `mcp-publisher publish`).

## 5. Behavior that is easy to get wrong (checked in code)

- Coding agents (`agent implement`, `agent prfix`) always run on
  **Anthropic**: `AgentRunner` is built without `llm_kind`. Only `plan`
  (the selector) and `chat` take `--llm` (anthropic, openai, gemini,
  bedrock). An OpenRouter key works through `ANTHROPIC_BASE_URL` +
  `ANTHROPIC_API_KEY`; if `CLAUDE_CODE_OAUTH_TOKEN` is set, briar prefers it.
- **`--dry-run` on agents**: builds and prints the prompt and the tool
  list, makes a temp dir, but does not clone (`_prepare_agent_workdir`:
  "clone + set git identity unless `--dry-run`") and makes no model call.
  It still fetches the ticket or PR, so it needs the GitHub token.
- **`plan run --dry-run`** saves nothing (since 1.1.81) but still calls the
  selector LLM, so it costs tokens.
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
- **Logging**: quiet by default (WARNING). `runbook extract` and `runbook
  serve` call `daemon_logging()` and always print INFO progress;
  `BRIAR_LOG_LEVEL=WARNING` does not quiet them.
- **`briar init`** reads `git remote get-url origin` for owner and repo.
  Settings resolve as flag > env var > `.briar.toml` > default
  (`briar config show` prints the source of each).
- **`context` commands** default to the file store even when
  `BRIAR_DATABASE_URL` is set; `BRIAR_STORE=postgres` switches them.
- **`briar journal`** records only `plan run` and `scaffold` sessions, not
  standalone agent runs.
- **`agent prfix`** sees failing CI logs in its context but its procedure
  covers review threads and PR comments only; `pr-ci-fixer` (a scaffold
  archetype) is the CI bot.
- **Message channels** (`messages:` in a runbook) become a `send_message`
  tool for agents run with `--runbook`; failure alerts are
  `BRIAR_NOTIFY_SINKS`. Slack is read live only (task context); cloud only
  by extractors.
- **The bash tool** parses quotes before splitting on `;`, `|`, `&&`
  (since 1.1.95); its forbidden-token check (`--force`, `--amend`,
  `rebase`, `squash`, `reset --hard`, ...) still matches inside quoted text
  (section 8).
- **Docker image** (`iklob1/briar`): has `git`, `gh`, the `[mcp]` extra,
  `safe.directory '*'`, `gh` also linked at `/usr/local/bin/gh` and
  `/opt/homebrew/bin/gh` (Mac gitconfigs), `BRIAR_NO_DEFAULT_MCP=1`,
  `linux/amd64` only, runs as uid 1000. The git login is set up for GitHub
  only.
- **`[mcp]` extra**: without it, agents skip the default think/time servers
  with one warning (since 1.1.74); `mcp serve` and `chat` need it.

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

## 7. Next steps (in order)

1. Fix #77 (small): treat GitHub's `404 no analysis found` as "no data"
   without a traceback; keep other errors logged. Then check the same
   `@swallow_errors` pattern for other expected 404s (for example a
   missing ticket in `agent implement --dry-run`, which logs a full
   traceback and carries on).
2. Fix #76: build the `CLAUDE.md` index and detail file from the merged
   blob, not from the run's sections.
3. The plan-run findings in section 8, branch-name slug first.
4. The untested paths in section 8 that need real credentials (Bitbucket,
   Jira or Linear over MCP, remote credential stores, Postgres).
5. Bump the MCP registry only after an MCP-facing change.

## 8. What is still open

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

- From the real `plan run` (2026-10-13, not re-checked since): branch
  names for GitHub Project cards come out as
  `chore/<owner>-<repo>issue-1` (missing dash, whole repo in the name);
  `briar plan show --format json` prints markdown; a plan built from a
  GitHub Project stores `repo: ""`, so `plan run` needs `--owner/--repo`;
  the bash tool's forbidden-token check matches inside quotes (a PR body
  that says "no rebase" would be refused; not seen in a run); the selector
  trusts the plan notes blob over card status.
- `briar context list` prints `updated_at` as a raw epoch float
  (`1793282842.3368032`), from `commands/context.py` line 109 (2026-10-29).
- `briar extract` with no `--include` ran all 23 extractors in about 4
  minutes on this repo and wrote an "AWS infrastructure (UNREACHABLE)"
  section on a machine with an AWS default chain whose
  `caller_identity` failed. Whether that section should be written is not
  decided; the cause on that machine was not checked (2026-10-29).
- `auth refresh` supports no target: a real GitHub refresh needs a GitHub
  App and its client secret. Help text and docs say it is not supported.
- The Docker MCP catalog (`docker/mcp-registry`) builds from the repo
  `Dockerfile`, which copies a `dist/` only CI makes, so briar cannot be
  listed there until the Dockerfile builds from source.

**Never run for real (unit tests only):** a successful Bitbucket PR, the
remote default credential store against real SSM, Secrets Manager or
Vault, Jira and Linear over MCP, Postgres for the extract merge, a full
MCP client run that writes data.

## 9. What to watch

- **Release runs.** A random test failure has blocked a release before
  (telemetry banner, fixed in 1.1.80; one other failure on 2026-06-25,
  cause not checked). After every merge, check that the run is green and
  that PyPI, Docker Hub and the GitHub Release all show the new version.
- **Docs drift.** The website's `/docs` pages and the landing page state
  CLI behavior and some versions ("since 1.1.75"). When CLI behavior
  changes, update `agents/*.md` here and the site docs in the site repo.
  The site was last checked against 1.1.98.
- **README images.** `docs/diagrams/*.svg` are copies of the diagrams on
  usebriar.com (exported from the live page with styles inlined). If the
  site diagrams change, export them again. `docs/demo/*.gif` are real
  runs of briar 1.1.99 in a clone of this repo (asciinema 2.4.0 + agg
  1.9.0; a small zsh player typed each command and printed "What it
  does / Behind the scenes / What you see" lines; long output was paged
  one screen at a time by changing only timestamps). Re-record them when
  the output of those commands changes. The recorder scripts are not in
  git. The images load from `raw.githubusercontent.com/.../main/...` so
  PyPI shows them too; a PR preview shows them only after merge.
- **MCP registry** stays at the last MCP-facing release (1.1.94 now).
- **Merged branches** pile up on GitHub (50 remote branches on
  2026-10-29). Delete them only after checking each one is merged.

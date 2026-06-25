# Changelog

All notable changes to `briar-cli` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases are
cut automatically on merge to `main` (patch bump + PyPI + Docker).

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
  runbook) shared by the CLI, MCP server, and dashboard, plus a runbook writer.

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

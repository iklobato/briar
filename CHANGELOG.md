# Changelog

All notable changes to `briar-cli` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases are
cut automatically on merge to `main` (patch bump + PyPI + Docker).

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

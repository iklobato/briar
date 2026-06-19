# Contributing to briar-cli

Thanks for hacking on briar. This file captures the setup and the non-obvious
gotchas so you don't have to rediscover them.

## Setup

```bash
make venv          # creates .venv/ and `pip install -e '.[test,dev]'`
source .venv/bin/activate
briar --help
```

The editable install (`-e`) puts the `briar` console script on your PATH and
makes `import briar` work without `PYTHONPATH` games.

## The pre-push gate

```bash
make check         # pytest — the gate the release CI enforces
```

`make check` runs the unit suite, which is what blocks a release. `make fmt`
(black), `make lint` (ruff), and `make typecheck` (mypy) are also available:
the repo is **not yet clean repo-wide** under ruff/mypy (there's pre-existing
debt being paid down), so keep *your* changed files clean (the commit hook
checks them) rather than expecting a green whole-repo lint today. Once the debt
is cleared, `lint` + `typecheck` fold into `check` and the release gate.
`make smoke` help-parses every subcommand.

Add or update tests for behavior you change. For a bug fix, add a regression
test that fails before the fix and passes after.

## Gotchas (these will bite you)

- **Running tests without the editable install needs `PYTHONPATH=src`.** If you
  invoke `python -m pytest` against a non-installed checkout, prefix it:
  `PYTHONPATH=src python -m pytest`. `make pytest` / the `.venv` avoid this.
- **A globally-installed `pytest-tldr` plugin breaks collection.** If you see odd
  collection output or import errors from a `tldr` plugin, add `-p no:tldr`.
  Clean virtualenvs (and CI) don't have it, so `make pytest` is unaffected.
- **Tests must be hermetic against ambient git + config.** `briar.cli.main`
  reads the git `origin` remote (for `--owner`/`--repo` inference) and any
  `.briar.toml`. The shared `cli` test fixture (`tests/conftest.py`) neutralises
  both, plus the update check. If you add a code path that reads ambient state,
  neutralise it in the fixture or your test will pass locally and fail in CI
  (CI checkouts have an `origin`; many dev checkouts don't, or name it
  differently — this exact gap has bitten a release).
- **The repo's `logging.py` shadows stdlib `logging` under some import orders.**
  If a randomised test run throws `ImportError: cannot import name 'LogRecord'`,
  it's a collection-order artifact, not your change; re-run with `-p no:randomly`.
- **Logs go to stderr and are quiet by default (WARNING).** Use `--verbose` /
  `BRIAR_VERBOSE=1` for DEBUG, or `BRIAR_LOG_LEVEL=INFO`. stdout is reserved for
  machine-readable output (`--format json`).

## Conventions

- Two CLI dispatch styles coexist on purpose (Op-class `SubcommandCommand` vs the
  `_ACTIONS` string map). Both are test-pinned; don't unify them.
- Command output: data on stdout (via `briar.formatting.render`, honouring
  `--format`), human progress/status on stderr.
- New non-`.py` assets (templates, etc.) ship in the wheel because hatchling
  packages everything under `src/briar/`; verify with a `make build` + unzip if
  unsure.

## Releases

Pushing to `main` triggers `.github/workflows/release.yml`, which runs the
quality gate, bumps the patch version, tags, and publishes to PyPI (+ Docker).
Don't bump the version by hand. Open a PR; merging it ships the release.

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
`make smoke` help-parses the top-level commands it lists in the Makefile.

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
- **The integration CI job runs on Python 3.14** (it pins no version; the unit
  jobs run 3.10 to 3.12). Python 3.14 warns when a urllib `HTTPError` is only
  closed by garbage collection, and pytest turns that into a failure in
  whichever test runs then. Close an `HTTPError` you catch (`exc.close()` or
  `with exc:`). To reproduce a CI seed: `UV_PROJECT_ENVIRONMENT=<dir> uv run
  --python 3.14 --extra test pytest -m integration -p randomly
  --randomly-seed=<seed>`.
- **Logs go to stderr and are quiet by default (WARNING).** Use `-v` /
  `--verbose` for DEBUG; 2.0 reads no log-level env var. stdout is reserved
  for machine-readable output (`--format json`).

## Conventions

- Two CLI dispatch styles coexist on purpose (Op-class `SubcommandCommand` vs the
  `_ACTIONS` string map). Both are test-pinned; don't unify them.
- **CLI options on the `v2` branch (2.0)** are Typer options declared as fields
  of a frozen `CommandOptions` dataclass in the command's module (for example
  `InitOptions` in `commands/init.py`), listed in `cli_app.ROUTES`. Name the
  field after the namespace attribute `run()` reads. A fixed list of choices
  is a `str` Enum in `commands/_enums.py` with a test that keeps it equal to
  its registry. Only `extract` still uses `add_arguments` (argparse) until
  spec 004. `tests/unit/cli/` holds the 1.x contract (`flags_1x.json`):
  change it only when a decision removes or renames a flag. The traps found
  while building this are in `docs/briar-2-status.md` section 4.
- Command output: data on stdout (via `briar.formatting.render`, honouring
  `--format`), human progress/status on stderr.
- New non-`.py` assets (templates, etc.) ship in the wheel because hatchling
  packages everything under `src/briar/`; verify with a `make build` + unzip if
  unsure.

## Releases

Pushing to `main` triggers `.github/workflows/release.yml`, which runs the
quality gate, bumps the patch version, tags, publishes to PyPI (+ Docker),
and creates the GitHub Release with notes generated from the merged PRs.
Don't bump the version by hand. Open a PR; merging it ships the release.
The workflow does not touch `CHANGELOG.md`. Every merge bumps the patch
version once, so write your entry in the same PR under the next number
(the current version in `pyproject.toml` plus one patch), and check that
number again right before you merge.

# briar 2.0: work status

The handoff file for briar 2.0. Read it before you touch the `v2` branch or
any 2.0 PR. It says where the work is, what is done, how the code works
now, what was found on the way, what is left and what to watch.

- Scope: `docs/decisions/2026-11-10-briar-2-pipeline.md` (D1 to D36, plus
  D29a, D29b, D32a, D34) and `docs/decisions/2026-11-10-briar-2-source-logins.md`
  (L1 to L21). Those files decide; this file reports.
- Facts below were checked on 2026-11-10 (UTC) with the command shown. When
  this file and the code disagree, the code wins: fix this file in the same PR.
- The spec-kit working files (`specs/`, `.specify/`) are not committed. What
  an agent needs from them is in this file.

## 1. Where the work is

- **Branch `v2`** takes every 2.0 PR (D27). One 2.0 release ships when `v2`
  merges to `main`. Until then `main` gets 1.x fixes only.
- `v2` on GitHub still equals `main` (`ec6d313`, release 1.1.101): no 2.0
  PR is merged yet. Checked with `git ls-remote briar v2 main`.
- The PRs are **stacked**: each one is based on the branch of the one
  before it, and each targets `v2`. A PR's diff shows the commits of the
  PRs under it until those merge. Merge them **in order, #79 first, as
  merge commits**.
- CI runs on every PR into `main` or `v2` (`.github/workflows/tests.yml`).
  All 11 PRs had green CI on their last commit. Nobody has reviewed them
  (the maintainer is the only reviewer).

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

## 2. What is done

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

## 3. How the command line works now

```
briar.cli.main(argv)
  1. pre-parse: take --format, -v/--verbose, -V/--version out of argv (anywhere)
  2. --version: print and return, no other work
  3. "metadata only" (help, no command, --show/--install-completion, a TAB):
     skip credential bootstrap, journal, telemetry, config, git
  4. else: bootstrap env file, journal, telemetry, read the project config once
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
    (`agent`/`plan` `--store` reads `BRIAR_DATABASE_URL` when the command
    runs; `secrets bootstrap --kind` and `plan build --llm` keep `""`).
  - `InheritingOptions`: `context --store x get y` and `context get y --store x`
    both work; a setting fills only the group, so it never beats a group flag.
  - `SETTING_NAMES`: `agent --root` keeps its 1.x dest `knowledge`, filled by the `root` setting.
  - `RegistryChoice`: `auth` targets are checked against the registry when
    the command runs (tests add fake targets). Other choices are `str`
    Enums in `commands/_enums.py`, each with a test that keeps it equal to
    its registry (`tests/unit/cli/test_typer_routes.py`).
  - `EnrichmentOptions`: the meeting and Slack options shared by `agent` and `plan run`.
- **`settings.py`**: env (`BRIAR_COMPANY`, `BRIAR_STORE`, empty counts as
  unset) > project config file > git `origin` (owner, repo) > the option's
  default. One file is read, never a merge: the nearest `.briar.toml`, or
  `pyproject.toml` with `[tool.briar]`, found upward from the cwd; in one
  folder `.briar.toml` hides `pyproject.toml`. It feeds the Typer default
  map, `config show` (labels `env (<VAR>)`, `config (<file>) [<table>]`,
  `inferred (git origin)`, `-`) and `doctor`.
- **`config.py`**: now only the argparse shim for `extract`
  (`apply_config_defaults`); `infer.py` fills `extract --repo` from git.

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
  `impl.verbose = getattr(args, "verbose", False)` never turns on (not
  fixed).
- **Startup** (CPU time, median of 7, busy machine, against the 1.x code):
  `import briar.cli` 734 ms (1.x) vs 804 ms; `main(['version'])` after
  import 45 ms vs 37 ms. About 60 ms slower overall: Typer and the option
  classes load at import. Parsing itself is faster. PR #89.

## 5. Next steps, in order

1. **Review and merge #79 to #89 into `v2`**, in order, as merge commits.
   Re-check `gh pr checks <n>` before each merge; after the last one, check
   that the `v2` CI run is green.
2. **Decide how 2.0 is released before `v2` merges to `main`.**
   `release.yml` runs on every push to `main` and bumps the patch only
   (`major.minor.patch+1`); `pyproject.toml` on `v2` says `1.1.101`, so the
   merge would publish 1.1.102, not 2.0.0. A PyPI version can never be
   reused. Not covered by any spec yet.
3. **Optional speed PR** (the user deferred it): import a command's option
   classes only when that command runs (about 25 ms), and lazy-import the
   heavy modules (`python -X importtime -c "import briar.cli"`:
   `briar.commands.agent` 486 ms and `briar.commands.runbook` 389 ms
   cumulative, mostly SQLAlchemy and storage).
4. **Write the next specs** (spec-kit, from the decisions files):
   - spec 003: the pipeline engine (LangGraph, `briar run/resume/runs`,
     built-in pipelines, loops, checkpoints; D7 to D26, D35, D36);
   - spec 004: extractors cleaned inside with the same output, `extract`
     on Typer, the 69 extractor flags (D32, D32a);
   - spec 005: source logins (L1 to L21), after spec 002.
5. **Site and images**: usebriar.com `/docs` and the landing page (private
   repo `iklobato/usebriar-landing`) still describe removed features
   (pipeline R7). `docs/diagrams/which-command.svg` still shows `briar
   chat` and `mcp serve`; `docs/diagrams/under-the-hood.svg` shows `mcp
   serve`. Both come from the site, so change them there and export again.
6. **1.x issues still open on `main`**:
   https://github.com/iklobato/briar/issues/76 and
   https://github.com/iklobato/briar/issues/77.

## 6. What is missing or open

- `extract` is still argparse: its flags do not tab-complete (1.x
  completed them), `--advanced-help` and the legacy per-extractor flags
  stay until spec 004.
- Startup is about 60 ms slower than 1.x (section 4); the plan's goal was
  "no slower".
- Unit tests in `tests/unit/test_http_retry.py` and `tests/test_abstractions.py`
  still leak their own `HTTPError` objects on Python 3.14. CI does not run
  unit tests on 3.14, so nothing fails today.
- Typer 0.29.0 came out on 2026-11-09 and is pre-1.0; the pin is
  `typer>=0.29,<0.30`.
- Not checked: `briar --install-completion` writing into a real shell
  config; Python 3.10 only runs in CI.
- The open risks in both decisions files (pipeline R1 to R12, logins R1 to R7).

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

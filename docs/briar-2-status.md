# briar 2.0: work status

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
  `~/.briar/knowledge/<company>/`, pipeline notes, history, freshness). Those
  files decide; this file reports.
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
  PRs #79 to #97 had green CI on their last commit. Nobody has reviewed
  them (the maintainer is the only reviewer).

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
  - `RegistryChoice`: `auth` targets are checked against the registry when
    the command runs (tests add fake targets). Other choices are `str`
    Enums in `commands/_enums.py`, each with a test that keeps it equal to
    its registry (`tests/unit/cli/test_typer_routes.py`).
- **`settings.py`**: env (`BRIAR_COMPANY`, `BRIAR_STORE`, empty counts as
  unset) > project config file > git `origin` (owner, repo) > the option's
  default. One file is read, never a merge: the nearest `.briar.toml`, or
  `pyproject.toml` with `[tool.briar]`, found upward from the cwd; in one
  folder `.briar.toml` hides `pyproject.toml`. It feeds the Typer default
  map and the settings table of `doctor` (labels `env (<VAR>)`,
  `config (<file>) [<table>]`, `inferred (git origin)`, `-`).
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
  `impl.verbose = getattr(args, "verbose", False)` never turned on (`plan`
  is gone since spec 006).
- **Startup** (CPU time, median of 7, busy machine, against the 1.x code):
  `import briar.cli` 734 ms (1.x) vs 804 ms; `main(['version'])` after
  import 45 ms vs 37 ms. About 60 ms slower overall: Typer and the option
  classes load at import. Parsing itself is faster. PR #89.

## 5. Next steps, in order

1. **Review and merge #79 to #98 into `v2`**, in order, as merge commits.
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
   `[extract]` key) stops the run with exit 1; `auth status` with no target
   lists every login. The speed PR below is likely moot: after part 3,
   `import briar.cli` is about 350 ms faster than 1.x (section 2).
4. **Optional speed PR** (the user deferred it): import a command's option
   classes only when that command runs (about 25 ms), and lazy-import the
   heavy modules (before spec 006, `python -X importtime -c "import
   briar.cli"` showed `briar.commands.agent` 486 ms, now gone, and
   `briar.commands.runbook` 389 ms cumulative, mostly SQLAlchemy and
   storage, which part 3 removes).
5. **Write the next specs** (spec-kit, from the decisions files):
   - spec 003: the pipeline engine (LangGraph, `briar run/resume/runs`,
     built-in pipelines `ticket`, `board`, `pr-review`, loops, checkpoints;
     D7 to D26, D35, D36, and P1 to P22, A1 to A15 of the ticket-pipeline
     file). It needs the logins of spec 005 for Fireflies and Slack (T6);
   - spec 004: extractors cleaned inside with the same text per extractor,
     `extract` on Typer, the extractor flags (D32, D32a, C12), and the new
     knowledge layout (K1 to K14, K16, K20);
   - spec 005: source logins, in progress (section 2): PR 1 is #97, PR 2
     (AWS, gcloud and Azure) is #98; PR 3 the check before a run; PR 4 the GitHub
     device flow, Linear OAuth and the refresh lock (G19). PR 4 needs the
     GitHub App of briar registered first (G23); its device flow code is in
     `git show c63ccf9:src/briar/auth/_acquirers/github_device.py`.
6. **Site and images**: usebriar.com `/docs` and the landing page (private
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
7. **1.x issues still open on `main`**:
   https://github.com/iklobato/briar/issues/76 and
   https://github.com/iklobato/briar/issues/77.

## 6. What is missing or open

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
- `runbook serve` has no failure alert until Apprise (C20, K4).
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

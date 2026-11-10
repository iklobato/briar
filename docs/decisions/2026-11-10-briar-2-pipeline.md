# briar 2.0: pipeline with loops up to a ready PR

Grill session of 2026-11-10, run against `briar/main` at `ec6d313` (v1.1.101).
Every line below is a decision the maintainer picked. Facts that drove a
choice are in the "Evidence" section at the end, with the command that
produced them.

## Goal

Keep extracting the same sources as today, and replace the one-shot
`agent implement` with a pipeline that loops (plan, code, test, review, CI,
bot review) until the PR is ready for a human. Use ready-made libraries in
place of the hand-written agent loop, LLM adapters and config plumbing.

## Decisions

Format: `ID. Decision: chosen answer. Why. (depends on)`

### Scope and models

- D1. Compatibility: 2.0 may break commands, flags and the runbook YAML. Few users to migrate (3 stars, 151 PyPI downloads last month). (none)
- D2. LLM endpoints: OpenRouter, plus direct Anthropic (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`) as today. One key for many models, and the current Anthropic path keeps working. (none)
- D3. Models: any model at every stage, the coding agent included. Freedom to pick by cost. As a result, the Claude Agent SDK is out: OpenRouter only guarantees Claude Code with the Anthropic first-party provider. (D2)
- D4. End state: the PR is marked ready for review, with green CI. A human reviews and merges. (none)
- D5. Extraction: as today. Scheduled extractors run via `runbook serve`, the 4 task-scoped extractors run per task, and the pipeline reads the blob. (none)
- D6. Loops: local verify (test/lint), self-review by a second agent, plan before code, and answering review bots. The CI loop is implied by D4. (D4)
- D7. Unit: the pipeline runs one ticket. The board pipeline loops over cards and runs the ticket pipeline for each card. (D1)
- D8. Resume: after a crash, resume from the last finished stage. (D4)

### Engine and pipeline definition

- D9. Engine: LangGraph (`StateGraph`, conditional edges for loops, a checkpointer). It runs any model via `ChatOpenAI` / `ChatAnthropic`, and resumes at the exact node by `thread_id`. CrewAI was rejected: 32 direct dependencies, a `pydantic<2.13` pin, and an unknown state id silently starts over. pydantic-ai was rejected: it needs Python 3.11+. (D3, D8)
- D10. Side effects (push, open PR, mark ready, reply to threads, comment on or label the ticket): done by fixed stage code, never by an agent. Agents only edit files and commit locally. This is safer and testable, and `gh` leaves the agent's bash. (D9)
- D11. Pipeline definition: user YAML. Stage types come from a fixed catalog in code. Users compose the order, the loops and the agents (prompt, model, tools from the catalog). The YAML is validated with pydantic. (D9)
- D12. YAML location: `agents:` and `pipelines:` blocks in the runbook YAML. `.briar.toml` keeps settings and the verify commands. (D11)
- D13. Built-in pipelines: `ticket`, `board`, `pr-review`, `ci-fix` and `triage` ship inside the package. A runbook can override a pipeline by id. `briar init` writes a minimal runbook with an empty `agents.<name>.model` per agent, so a briar upgrade updates the pipelines. (D11, D12, D17)
- D14. Verify commands: only from `.briar.toml` `[verify]`. If they are missing, the pipeline stops before any LLM call. (D6)
- D15. Limits: `max_loops` on every loop, plus a USD cap per card (per ticket-pipeline run). The board total has no cap. (D6)
- D16. On a limit hit or an unfixable failure: push, then open a draft PR with a report (last error, loop counts, cost), and mark the card blocked. If the PR already exists, it stays draft and gets a report comment. (D10, D15)
- D17. Default models: none. Each agent must set `model`, and a missing model is a clear error. We do not pick models without cost or quality data. (D3)
- D18. Prompts: the archetype Python classes (`iac/scaffold/archetypes/`) become `.md` prompt files in the package. Shared rules become includes. The YAML points at a prompt file. (D11)
- D19. Board selection: keep the LLM selector (today's `plan/_selector.py`). (D7)
- D20. Board parallelism: independent cards run in parallel worktrees, `max_parallel` default 3. This default was the maintainer's choice, not measured. (D7, D19)
- D21. Checkpoint store: SQLite by default, Postgres when the knowledge store is Postgres (`langgraph-checkpoint-sqlite` / `-postgres`). (D8, D9)
- D22. Human gates: none until the PR is ready. (D4)
- D23. CI wait: no time limit. (D4)
- D24. Bot loop: bots are listed in the pipeline YAML, and the pipeline waits for every listed bot with no time limit. (D6, D23)
- D25. Agent tools: `read`, `write`, `edit`, `bash` (keeping today's allowlist and forbidden tokens from `agent/tools.py`), `git_diff`, `grep`, `glob`, `web_fetch`, and MCP servers per agent (`langchain-mcp-adapters`). `send_message` leaves the catalog. (D11)
- D26. The default MCP servers `think` and `time` are removed, and so is `BRIAR_NO_DEFAULT_MCP`. (D25)

### CLI, packaging and cleanup

- D27. Rollout: a long-lived `v2` branch takes the PRs, and one 2.0 release ships when it merges to `main`. Until then `main` gets only 1.x fixes. (D1)
- D28. Removed: `iac/scaffold` (except what becomes prompts and rules), `iac/models.py`, `iac/config_file.py` and the other dead symbols, `journal`, `dashboard`, `chat`, `mcpserver` and `service/`. Replaced by the engine: `agent/runner.py`, `agent/_llm.py`, `agent/_llms/*`, `error_policy.py`, `mcp/_manager.py`, and the `plan run` loop body. (D1, D9)
- D29. Library swaps: argparse to Typer + pydantic-settings, urllib to httpx (already declared, 0 imports today), `schedule` to APScheduler, and `notify` + `messaging` to Apprise. (D1)
  - D29a (2026-11-10, spec 002): Typer only at the edge: each command's options are declared in Typer and turned into the same `argparse.Namespace` the commands already take; typed models inside come later (specs 003 and 004). `--format`, `-v` and `--version` keep working anywhere on the line. The hidden deprecated aliases go (`--storage`, `--knowledge`, `--store` on `auth`/`secrets`). Shell completion is Typer's own (`--install-completion`, `--show-completion`); `briar completion` and its templates go. `briar extract` stays on argparse until spec 004.
  - D29b (2026-11-10, spec 002 PR 2): settings use a plain pydantic model, not pydantic-settings. 1.x reads 2 env vars, one config file (never a merge of two) and git `origin` for owner and repo, so every source is custom code anyway, and pydantic-settings' only way to put env above the file (`settings_customise_sources`, 5 fixed parameters) breaks the 3-parameter rule. Same behaviour, one direct dependency fewer. (D29a)
- D30. Telemetry: Sentry becomes opt-in, off by default; the state 1.x saved is ignored and the first-run banner goes. Changed on 2026-11-10 while building it: sentry-sdk 2.60.0's `EventScrubber` scrubs `request`, `extra`, `user`, breadcrumbs, frames and spans, but not tags, and it matches whole key names only. So event fields move from tags to `extra`, the SDK's `EventScrubber` runs on them, and the own scrubber stays whole (allow-list, secret-name regex, value regexes) instead of being replaced. (D1)
- D31. MCP registry: the entry `io.github.iklobato/briar` stays at 1.1.94. The 2.0 README says the MCP server exists only in 1.x. (D28)
- D32. Extractors: cleaned inside, with identical output. Each extractor gets a pydantic config (so `extract/canonical.py` and its 74 hidden flags go), there is one shared `extract()` on the base class and one generic `Registry`. A snapshot test proves the markdown is the same. (D5, D29)
  - D32a (2026-11-10, for spec 004): measured 69 extractor flags, not 74. The 58 that copy a canonical flag (21 repo, 14 max, 7 top-n, 5 since-days, 3 sample, 8 author/assignee filters) leave the CLI; a different value per extractor goes in `[extract.<name>]` config. The 7 niche knobs (`--aws-extract-profile/region/service`, `--gov-branch`, `--prhygiene-large-loc`, `--stale-days`, `--meeting-attendee-allow`) become config only; the AWS profile comes from the `auth:` block (login decision L14). The CLI keeps `--repo`, the four author/assignee filters, the provider selectors (`--provider`, `--tracker`, `--meeting`, `--cloud`) and the number flags (`--max`, `--top-n`, `--since-days`, `--sample`).
- D33. Credentials: remove the SSM, Secrets Manager and Vault stores (they were only ever run in tests). The env-file store, the 6 paste-key acquirers, the own `.env` parser and the JWT decoder stay as they are. (D1)
- D34. Python: keep 3.10, 3.11 and 3.12. (D9) Reconfirmed on 2026-11-10 knowing that Python 3.10 reached end of life on 2026-10-01 and that pydantic-settings 2.16+ needs 3.11 (2.15.0 is the last with 3.10).
- D35. CLI: `briar run <pipeline> <target>`, `briar resume <run-id>`, `briar runs` (list, show). `agent implement`, `agent prfix` and `plan run` go away. (D7, D13, D29)
- D36. Run history: LangGraph checkpoint + structlog JSON events, read with `briar runs show <id>`. (D21, D28)

## Resulting design (proposal for the spec, built only from the decisions)

### Pipelines

| Pipeline | Target | Stages |
|---|---|---|
| `ticket` | ticket key | context, plan, implement, verify (loop to implement), review (loop to implement), publish, ci (loop with ci-fixer / conflict-resolver), bots (loop with pr-fixer, back to ci), ready |
| `board` | plan name | the plan is built from the board, then this repeats: the selector picks up to `max_parallel` independent cards, `ticket` runs per card (stacked on done dependencies), writeback, until complete, blocked or the replan cap |
| `pr-review` | PR number | context (pr-review-context), pr-fixer, verify loop, push, ci loop, reply to threads (code) |
| `ci-fix` | PR number | ci loop with ci-fixer |
| `triage` | ticket key | context, triager; code applies the labels and the comment |

### Agents (default tool sets are a proposal; each is configurable in YAML)

| Agent | From today | Tools | Output |
|---|---|---|---|
| planner | new | read, grep, glob, git_diff, web_fetch | `Plan` (steps, files, ticket criteria per step) |
| engineer | `archetypes/engineer.py` | read, write, edit, bash, grep, glob | local commits |
| reviewer | new | read, grep, glob, git_diff | `Verdict` (approve or changes) |
| ci-fixer | `archetypes/pr_ci_fixer.py` | engineer tools; the CI log comes in the input | local commits |
| conflict-resolver | `archetypes/pr_conflict_resolver.py` | engineer tools (merge, never rebase) | local commits |
| pr-fixer | `archetypes/pr_fixer.py` | engineer tools | commits + replies that code posts |
| triager | `archetypes/triager.py` | read, grep, glob | labels, reviewer, comment that code posts |

### Config example

Minimal runbook written by `briar init`:

```yaml
company: acme
agents:
  planner:           {model: ""}
  engineer:          {model: ""}
  reviewer:          {model: ""}
  ci-fixer:          {model: ""}
  conflict-resolver: {model: ""}
  pr-fixer:          {model: ""}
  triager:           {model: ""}
```

Override of a built-in pipeline (the numbers are placeholders until the first runs are measured):

```yaml
agents:
  engineer:
    model: <openrouter-model-id>
    prompt: prompts/engineer.md
    tools: [read, write, edit, bash, grep, glob]
    max_iter: 30
    mcp: [github]
  reviewer:
    model: <cheaper-model-id>
    tools: [read, grep, glob, git_diff]
    output: verdict
pipelines:
  ticket:
    budget_usd: <per-card cap>
    bots: [coderabbitai]
    stages:
      - {id: context,   type: context}
      - {id: plan,      type: agent, agent: planner, output: plan}
      - {id: implement, type: agent, agent: engineer}
      - {id: verify,    type: command, on_fail: implement, max_loops: 3}
      - {id: review,    type: agent, agent: reviewer, on_changes: implement, max_loops: 2}
      - {id: publish,   type: open_pr}
      - {id: ci,        type: wait_ci, fixer: ci-fixer, conflicts: conflict-resolver, max_loops: 3}
      - {id: bots,      type: bot_review, fixer: pr-fixer, max_loops: 2}
      - {id: ready,     type: mark_ready}
  board:
    max_parallel: 3
    card_pipeline: ticket
```

`.briar.toml`:

```toml
[verify]
commands = ["<test command>", "<lint command>"]
```

## Open risks

- R1. Any model on the coding agent (D3): tool calling and structured output (`Plan`, `Verdict`) vary by model, and a model without tool calling cannot run. A fail-fast check on the model's capabilities is not decided.
- R2. No wait limit on CI and bots (D23, D24): a stuck CI job or a listed bot that is not installed blocks a run forever. Resume (D8) only helps once someone notices. With `max_parallel` 3, three worktrees can be stuck at once.
- R3. `max_parallel` 3 (D20) is not derived from GitHub rate limits, CPU or cost. Measure it on the first board runs.
- R4. The USD cap is per card only (D15): a board has no total cap, and parallel cards (D20) multiply the cost.
- R5. Cost on the direct Anthropic path (D2): Anthropic returns tokens, not dollars, so the cap needs a price table. Today's `cost_summary` in `agent/runner.py` hardcodes one model's price. OpenRouter returns the cost per call.
- R6. `web_fetch` (D25) can carry prompt injection from a page into an agent that also has `bash` and `write`.
- R7. Removing the MCP server, chat, dashboard and journal (D28) leaves usebriar.com `/docs` and the landing page describing features that are gone. The site repo must be updated with 2.0. The MCP registry and "MCP via Docker" promotion channels stay on 1.x only.
- R8. The `v2` branch (D27) will drift from 1.x fixes on `main` and needs regular merges.
- R9. New direct dependencies: langgraph, langchain-openai, langchain-anthropic, langgraph-checkpoint-sqlite, langgraph-checkpoint-postgres, langchain-mcp-adapters, typer, apscheduler, apprise, structlog. Docker image size and install time are not measured.
- R10. 1.x bug found by reading the code, not by running it: `agent/runner.py` wraps tool results in `{"role": "user", "content": [...]}`, while `openai_llm.py:86` returns `{"role": "tool", ...}` items, and Gemini and Bedrock return whole messages. Multi-turn tool use on those three providers probably sends invalid messages. 2.0 deletes this code; whether to fix it in 1.x is not decided.
- R11. Apprise (D29) changes the config format of the runbook `messages:` block and of `BRIAR_NOTIFY_SINKS`. This is allowed by D1, but needs migration notes.
- R12. The selector (D19) has a known 1.x bug (it trusts the plan notes over the card status), and with D20 it must now pick several independent cards at once.
- R13 (found 2026-11-10 while building spec 002). `release.yml` bumps only the patch number on every push to `main`, and `pyproject.toml` on `v2` says `1.1.101`, so merging `v2` (D27) would publish 1.1.102, not 2.0.0. How the 2.0 release is cut is not decided and is in no spec yet.

## Left for the spec (not decided here)

- The exact YAML schema and the names in the stage type catalog.
- The default `max_loops` per loop, as placeholders until measured.
- What counts as "a listed bot finished" (a review, a comment, or a check).
- How long 1.x keeps getting fixes after 2.0.
- Whether to fix R10 in 1.x.

## Evidence (session 2026-11-10, live runs)

- Usage: `curl -s https://pypistats.org/api/packages/briar-cli/recent` gave `{"last_day":26,"last_month":151,"last_week":106}`. `gh api repos/iklobato/briar` gave `stars 3 forks 0 open_issues 2`.
- Library versions (PyPI JSON API):
  - `crewai 1.15.39 2026-11-09 py <3.14,>=3.10 core-deps 32`, including `chromadb~=1.5.9; lancedb<1.0.0,>=0.33.0; opentelemetry-sdk<2,>=1.45.1; pydantic<2.13,>=2.11.9`
  - `langgraph 1.2.20 2026-11-05 py >=3.10 core-deps 6`
  - `claude-agent-sdk 0.2.180 2026-11-09 py >=3.10 core-deps 5`, wheels about 150 MB (`claude_agent_sdk-0.2.180-py3-none-manylinux_2_17_x86_64.whl 152.1 MB`)
  - `pydantic-ai-slim 2.66.0 2026-11-04 py >=3.11 core-deps 8`
- OpenRouter docs (https://openrouter.ai/docs/guides/guides/claude-code-integration): Claude Code with OpenRouter is "only guaranteed to work with the Anthropic first-party provider". Read through a web search summary, not the page itself.
- CrewAI docs (https://docs.crewai.com/en/guides/flows/inputs-id-deprecation): `restore_from_state_id` not found in persistence means "the kickoff falls back silently to default behavior". Read through a web search summary.
- No agent framework in the repo today: crewai, langchain, langgraph, pydantic_ai and claude_agent_sdk each match 0 times in `src`, `tests`, `pyproject.toml` and `uv.lock` (read-only code survey).
- httpx is unused: `grep -rn "import httpx\|from httpx" src/briar | wc -l` gave `0`.
- Dead or near-dead code and duplicates come from the same survey: the scaffold tree is about 1,670 lines plus 312 in `iac/models.py` and `iac/config_file.py`, and the extract loop is copied at `service/extract.py:107`, `commands/extract.py:105` and `iac/runbook/executor.py:401`.

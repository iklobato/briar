# briar 2.0: the ticket pipeline, its agents and the pipeline YAML

Grill session, 2026-11-10, in two parts. Part 1 (P1 to P22): how the pipeline that turns one
ticket into a ready PR gathers context from the sources (GitHub, Fireflies, Slack, the ticket
tracker, the knowledge blob), its stages and inner loops. Part 2 (A1 to A15): the agents, their
prompts, tools and settings, and how a pipeline is written in YAML in a generic way. Builds on
`2026-11-10-briar-2-pipeline.md` (D1 to D36), `2026-11-10-briar-2-source-logins.md` (L1 to L21)
and `2026-11-10-briar-2-core-cleanup.md` (C1 to C22). For spec 003 (the engine).

Already fixed before this session and not asked again: one ticket per run (D7), resume from the
last stage (D8), LangGraph (D9), side effects by code only (D10), YAML pipelines (D11, D12),
limits per loop and a USD cap per card (D15), a draft PR with a report on an unfixable failure
(D16), no default models (D17), no human gate before the PR (D22), CI and bot loops without a
time limit (D23, D24).

Format: `ID. Decision: chosen answer. Why. (depends on)`

## Decisions

### Context

- P1. Sources of the `ticket` pipeline: the ticket itself (Jira, Linear or GitHub Issues,
  always), the knowledge blob, GitHub live, Fireflies and Slack. The task-scoped extractors that
  C19 removed come back in spec 003 as these context sources. (C19, D5)
- P2. How context is gathered: hybrid. A fixed stage fetches the basics by rule, and agents get
  source tools to dig further when they need to. Covers the obvious cheaply and leaves the rest
  on demand. (P1)
- P3. How context reaches the agents: a new `researcher` agent writes a short brief with
  citations (`.briar/context/brief.md`), and the raw material of each source stays next to it
  (`.briar/context/<source>.md`). Small prompts, nothing lost. (P2, D17)
- P4. Built-in pipelines in the core of spec 003: `ticket`, `board` and `pr-review`. `ci-fix`
  and `triage` leave the core (the `ticket` pipeline has the CI loop inside). Changes D13. (D13)
- P5. Finding meetings and Slack threads: the fixed stage searches by the ticket key (always
  precise); then the researcher writes 2 or 3 topic searches and runs them with the tools.
  Precision first, reach second, bounded cost. (P2, P3)
- P6. Which sources a pipeline uses: a list per company in the runbook
  (`pipelines.ticket.sources: [github, fireflies, slack]`). The L4 pre-flight check covers only
  those, and a failing login stops the run. Explicit and predictable. (P1, L4, L7)
- P7. A ticket without clear acceptance criteria: the researcher proposes criteria from the
  ticket, the meetings and Slack, marks them `inferred` in the brief and in the PR, and the run
  goes on. Nothing blocks, and the human reviewer sees what was assumed. (P3, D22)
- P9. What can appear where other people read it (the PR description, the ticket comment): the
  plan, the criteria (inferred ones marked) and links. No quotes from meetings or Slack. The full
  brief and raw files stay local in `.briar/context/` and are never committed. (P3)
- P10. When sources disagree (a meeting decided X, the ticket says Y): the run stops before any
  code. (P3)
- P13. How a conflict stop is reported: only locally (`briar runs show <id>`) and the card is
  marked blocked. Nothing is written outside: GitHub cannot open a PR without a commit, and a
  detailed comment would break P9. (P10, P9)
- P17. GitHub in the fixed stage: the PRs and issues linked from the ticket or that cite its key.
  Through the planner's tools: similar past PRs, and the history and owners of the files the plan
  touches (known only after the plan). (P2, P12)
- P19. A missing knowledge blob stops the run with "run briar extract". (P1, D5)
- P21. A stale blob stops the run only when the company sets `max_blob_age_days` in the runbook
  and the blob is older; without it, age never stops a run. (P19)

### Stages and inner loops

- P8. Implementation is step by step: each plan step is one engineer round, then the fast
  verify; a failing step loops on that step only; each step is one commit, and resume (D8)
  restarts at the right step. More calls, smaller errors. (D6, D8)
- P11. Review runs once, after all steps: the reviewer sees the whole diff, the plan and the
  criteria, and gives a verdict per criterion (met or missing) plus code problems; "missing" goes
  back to the engineer with the list. (P8, P7)
- P12. Source tools (search meetings, threads, PRs, the ticket) go to the researcher and the
  planner only. The engineer and the reviewer read the brief and the raw files and stay on the
  code, with predictable cost. Extends D25's catalog. (P2, D25)
- P15. Verify: `[verify] step = [...]` runs after every step (for example lint and the tests of
  the changed files), `[verify] full = [...]` runs once after the last step, before review.
  Details D14. (P8, D14)
- P16. A step that cannot be done: the engineer returns `blocked: <reason>`, the planner redoes
  the plan from there keeping the finished steps, with its own `max_loops`. (P8, D15)
- P18. Plan check: before the first step, the reviewer checks that the plan covers every
  criterion; if not, back to the planner (`max_loops`). Catches the costliest error before code.
  (P7, P11)

### Outputs

- P14. On success, code writes on the ticket: a link to the PR and a comment with the plan
  summary and the criteria (inferred ones marked, no meeting or Slack quotes). The ticket status
  is not moved. (P9, D10, L5)

### Other pipelines and resume

- P20. `pr-review` uses the same context stage, focused on the PR: its threads and CI log are
  fixed, and the linked ticket, meetings and Slack come in as in `ticket`. (P1, P4)
- P22. Resume (`briar resume`) reuses the brief from the checkpoint but fetches the ticket again;
  if its text or criteria changed, the context and the plan are redone before going on. (D8, P3)

### Agents

- A1. Roles: 4 agents, `researcher`, `planner`, `engineer`, `reviewer`. Fixing CI, resolving a
  merge conflict and answering PR comments are the engineer with that stage's task prompt.
  Fewer settings; one model for all code work. Replaces the 7-agent table of the D-list. (P3)
- A3. Prompt: built in layers: the role prompt (`.md` in the package, D18), the shared rules
  (includes), the stage's task prompt, then the inputs (brief, plan, error). The user adds
  `instructions:` in the YAML, or points one layer at another `.md`. A briar upgrade keeps
  updating the layers the user did not replace. (A1, D18)
- A4. Structured output: an agent ends by calling a `submit_<output>` tool (`submit_plan`,
  `submit_verdict`, `submit_brief`), validated by a pydantic schema; an invalid call returns the
  error to the agent, which tries again within a limit. Needs a model with tool calling (R1).
  (A1)
- A8. Agent settings in the YAML (core): `model` (required, D17), `instructions`, `tools`,
  `max_iter`. `mcp` and model parameters such as `temperature` stay out of the core (this
  changes D25's per-agent MCP; it fits C5). (A1, A3)
- A9. When a loop goes back to the engineer (a failed step verify, review changes), it starts a
  new session with the step, the current diff and the error. Small context, predictable cost;
  the checkpoint keeps the history for resume. (P8, P11)
- A10. `web_fetch` is given to the researcher and the planner only. The engineer, which has
  `bash` and `write`, never receives web content directly (pipeline R6). (P12, D25)
- A12. Shared rules: all 8 from 1.x stay as prompt includes (`minimum_correct_fix`,
  `read_all_comments_first`, `no_workflow_file_edits`, `commit_as_human`, `no_force_push`,
  `no_new_pr_creation`, `ai_prefix_on_comments`, `skip_approved_green_prs`), even those whose
  action D10 already moved to code. (A3)
- A13. The engineer's bash allowlist is configurable per company in `.briar.toml` (`[bash]
  allow` and `forbid` replace the defaults when set). The default is the 1.x list without `gh`
  (D10) and the 1.x forbidden tokens. (D25)

### Default tools per role (from P12, A10, D25)

| Role | Tools |
|---|---|
| researcher | read, grep, glob, source tools (ticket, PRs, meetings, threads), web_fetch |
| planner | read, grep, glob, git_diff, source tools, web_fetch |
| engineer | read, write, edit, bash, grep, glob, git_diff |
| reviewer | read, grep, glob, git_diff |

### Pipeline YAML

- A2. Order and loops: an ordered list of stages. Each stage declares its outcomes (`pass`,
  `fail`, `changes`, `blocked`) and where each goes (`on: {fail: implement}`), with `max_loops`
  on each way back; with no rule, the next stage follows. Maps directly to LangGraph conditional
  edges. (D9, D11)
- A5. Repetition: a `foreach` stage runs a sub-list of stages once per item
  (`{id: implement, foreach: plan.steps, stages: [...]}`), with the same routing inside. The only
  nesting allowed; `board` uses it over cards; each item gets a checkpoint. (A2, P8)
- A6. Data between stages: named, typed keys. Each stage type declares what it produces and
  reads (`brief`, `plan`, `step`, `verify_error`, `verdict`, ...), each with a pydantic type; the
  YAML only renames when it must. Loading checks that every input has a producer. (A2, A4)
- A7. Changing a built-in pipeline: `extends` plus a patch by stage id (`remove`,
  `insert_after`, `insert_before`, `set`), so a small change keeps receiving the package's
  improvements. Changes D13 (a same-id copy no longer replaces the built-in). (D13, A2)
- A11. Stage types come only from the fixed catalog (D11); there is no user-defined stage and no
  generic shell stage. `verify` runs only the `[verify]` commands of `.briar.toml`. (D11, D14)
- A14. `briar run <pipeline> <target> --check` validates the YAML and the graph (references,
  every loop bounded, every input produced), prints the stages and the ways back, and exits with
  no model or source call. `briar doctor` also validates the runbooks it finds. No new command
  (C1). (A2, A6)
- A15. No parallel stages in the YAML. Parallel work stays where it is: the sources inside the
  context stage and the cards in `board` (D20). (A2)

### Stage catalog (proposal for spec 003, built from the decisions above)

`preflight`, `context`, `agent` (a role plus a task prompt and an output), `foreach`, `verify`
(`step` or `full`), `open_pr`, `ticket_comment`, `wait_ci`, `bot_review`, `mark_ready`, and for
`board`: `select` (the selector, D19) and `writeback`.

### Example

```yaml
agents:
  researcher: {model: <model-id>}
  planner:    {model: <model-id>}
  engineer:   {model: <model-id>, max_iter: 30, instructions: "Use the repo's test helpers."}
  reviewer:   {model: <cheaper-model-id>}

pipelines:
  ticket:
    extends: ticket
    sources: [github, fireflies, slack]
    remove: [bots]
    set:
      review: {max_loops: 1}
```

## Resulting `ticket` pipeline

```
preflight   L4 login check of the listed sources (P6); knowledge blob present and fresh (P19, P21)
context     fixed fetch: ticket, blob, GitHub links (P17), Fireflies and Slack by ticket key (P5)
research    researcher: 2-3 topic searches (P5), brief with criteria (P7) and citations (P3)
            sources disagree -> stop, local report, card blocked (P10, P13)
plan        planner (with source tools, P12)
plan-check  reviewer: plan covers every criterion? no -> plan (max_loops) (P18)
implement   for each step: engineer -> verify step (fail -> same step) -> commit (P8, P15)
            blocked -> plan from here (max_loops) (P16)
verify full fail -> implement (max_loops) (P15)
review      reviewer verdict per criterion; missing -> implement (max_loops) (P11)
publish     push, open PR (plan, criteria, links only, P9); ticket comment with PR link + plan (P14)
ci          wait CI; fail -> ci-fixer / conflict-resolver (max_loops) (D6, D23)
bots        listed bots; comments -> pr-fixer -> ci (max_loops) (D24)
ready       mark the PR ready for review (D4)
```

`board` runs this per card (D7, D19, D20). `pr-review`: preflight, context (PR-focused, P20),
research, pr-fixer, verify, push, ci, reply to threads by code.

## Changes to earlier decisions

- D13: the built-in pipelines in the core are `ticket`, `board`, `pr-review` (P4).
- D14: `[verify]` has `step` and `full` (P15).
- D25: the source tools join the catalog, for the researcher and the planner (P12).
- D16: a conflict between sources stops before any code with a local report only (P13); D16 still
  covers limit hits and unfixable failures after code exists.
- C19: the 4 task-scoped extractors return in spec 003 as context sources (P1).
- New agent: `researcher` (needs its own `model`, D17).
- The D-list's 7 agents become 4 roles with task prompts (A1).
- D13 again: a built-in pipeline is changed with `extends` and a patch, not a same-id copy (A7).
- D25 again: per-agent MCP leaves the core (A8); `web_fetch` only for researcher and planner (A10).

## Out of the core

- The `ci-fix` and `triage` pipelines (P4).
- Source tools for the engineer and the reviewer (P12).
- Moving the ticket status (P14).
- Publishing the brief or any meeting or Slack quote (P9).
- Per-agent MCP servers and model parameters (A8).
- User-defined stage types and a generic shell stage (A11).
- Parallel stages in the YAML (A15).

## Open risks

- T1. The researcher, the plan check and step-by-step implementation add LLM calls; the per-card
  USD cap (D15) has to cover them, and there are no measured costs yet.
- T2. Topic searches (P5) can still pull unrelated meetings or threads; the brief's quality
  depends on the researcher's model.
- T3. `.briar/context/` lives inside the worktree (the file tools only read there) and must never
  be committed or pushed (P9): it needs a git exclude and a test that the push contains no
  `.briar/` path.
- T4. Conflict detection is an LLM judgement: a false positive stops a good run (P10), a miss lets
  a wrong requirement through to review.
- T5. P19 makes a first run on a new setup fail until `briar extract` has run once; the docs and
  `briar init` must say so.
- T6. Fireflies and Slack need the logins of spec 005 (L16, L18) before spec 003 can use them;
  this confirms the order 005 before 003 (L21).
- T7. "PRs that cite the ticket key" uses GitHub search, which has its own rate limit.
- T8. P22 needs a reliable "did the ticket change" check (text and criteria), or resume redoes
  the plan for a cosmetic edit.

- T9. A13 lets a company remove forbidden tokens (for example allow `curl`); together with
  content from sources that could reopen pipeline R6 for that company. The default stays safe;
  `briar doctor` should warn when a company loosens `forbid`.
- T10. A4 and A1 need tool calling on every role's model; a model without it fails at the first
  `submit_*` (pipeline R1).
- T11. A12 keeps rules whose action is now code (push, PR, comments); their text must not tell
  the agent to do what D10 forbids, or the prompt and the code disagree.

## Left for the spec (not decided here)

- The default `max_loops` of the plan-check, step, replan, full-verify and review loops
  (placeholders until measured, as D15 says).
- The default `[verify] step` and `full` commands written by `briar init`.
- The brief's exact sections and the citation format.
- The exact wording and template of the ticket comment and the PR description.
- How many topic searches the researcher may run (P5 says 2 or 3) and their result limits.
- The exact pydantic types of the state keys (A6) and of each `submit_*` schema (A4).
- The text of each role prompt and task prompt (from the 1.x personas, A1, A3).
- The retry limit for an invalid `submit_*` call (A4).

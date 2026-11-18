# Serve quality rules: rules inside briar, on when a repo asks for them

Date: 2026-11-18. Status: settled in a grill, waiting for the maintainer to confirm. Base: `briar/v2` at `3ef5746` plus the open PR 133 (spend caps).

## Problem

The local rules of the maintainer (SOLID style Python, tests, design, scope) protect code only through Claude Code hooks on one machine. The agent of `briar serve` does not see them:

- `ClaudeAgent` runs with `setting_sources: []` and `strict_mcp_config` (`src/briar/serve/agent.py`), so nothing of `~/.claude` (hooks, skills, CLAUDE.md) is loaded. This is on purpose: it keeps personal settings and secrets out of the agent.
- What `serve` has today: `system.md` ("match the conventions of the repo"), a generic `review.md`, and the `format` and `verify` commands of the runbook.
- `src/briar/prompts/rules/` holds 8 files of 1.x that nothing reads.

Measured once: the mechanical checker of the maintainer, run offline on the production file the agent wrote in the first real run on a private company repo, found 5 new violations (2 methods over 20 lines, 1 missing type hint, 1 nesting of 3 levels, 1 broad `except`). That repo does not follow these rules and the agent followed the repo, so the number shows the distance between the two, not a bug. The ticket cost US$ 0.73 (one measure, no spread known).

## How it works

A rule set is one Markdown file with fixed sections:

```text
## Baseline            short text, goes in the system prompt of every agent call
## Review              checklist of the self review
## Review: database    a topic checklist (shipped design sets: only when the diff touches the topic)
```

Where the files are:

```text
src/briar/prompts/rules/     the sets of briar (code, tests, design, scope); the maintainer edits them, they ship in a release
<DIR>/rules/                 the sets of the user, next to the runbooks (DIR is what is passed to `briar serve DIR`)
```

The runbook turns sets on, per company and per repo, like `verify_by_repo`. The default is off:

```yaml
serve:
  rules: [code, tests]
  rules_by_repo:
    acme/api: [code, acme-api, scope]   # acme-api is a set of the user: <DIR>/rules/acme-api.md
```

Steps in a task:

1. The runbook names the sets for the repo of the ticket.
2. briar resolves each name to a file (shipped first, then `<DIR>/rules/`). `code` and `tests` are skipped when the worktree has no Python manifest (`language_detectors`).
3. The `## Baseline` sections fill `{rules}` in `system.md`, which every agent call uses (plan, build, fix, review, answers).
4. The `## Review` sections fill `{rules}` in `review.md`. A set marked as a design topic enters only when the diff touches that topic.
5. A rejection becomes a fix, then verify, then a new review. It counts in `max_loops` and `max_usd`.

With no sets on, `{rules}` is empty and every prompt is the same as today. The text is passed as a value to `format_map`, so braces in code examples are safe.

To change a rule, a user copies a shipped file to `<DIR>/rules/` under a new name, edits it, and lists the new name in `rules`. A shipped set can not be replaced (Q18).

## Decisions

Each line: decision, answer, why, depends on.

| Id | Decision | Answer | Why | Depends on |
|---|---|---|---|---|
| Q1 | Target | The code the `serve` agent writes in the repos it works on. The shipped rules live in the briar repo | This is the product output; the gap was measured there | none |
| Q2 | Left out | A CI job that applies the rules to the briar repo itself | Not chosen | Q1 |
| Q3 | Conflict with the repo style | The repo wins by default. The rules are opt in, per repo | A PR that looks foreign to its repo is a worse PR | Q1 |
| Q4 | How the rules reach the cycle | (a) A condensed baseline in the system prompt, so every agent call has it. (b) A semantic checklist in the self review. NOT built: a mechanical gate in the fix loop, and the full rules read on demand | Cheap and simple first; the mechanical gate needs new code and works only for Python | Q3 |
| Q5 | Owner of the default | Embedded in briar, public repo, MIT. English text, condensed from the local rule files | One default for every user | Q1 |
| Q6 | Who edits | The maintainer edits the shipped sets in the repo and they ship in a release. A user adds sets of their own in `<DIR>/rules/`. This replaces the first answer "only the maintainer" | The user needs rules for specific uses | Q5 |
| Q7 | Shipped families | `code` (Python style), `tests` (Python), `design` (system design, database, API) and `scope` (scope and errors) | The four the maintainer chose | Q5 |
| Q8 | Strength when on | The review rejects only a clear violation in an added line, at most 10 findings, no taste of style. A rejection is a fix loop and counts in `max_loops` and `max_usd` | Same as the local hook: block only the clear case | Q4 |
| Q9 | Opt in shape | `rules: [names]` for a company and `rules_by_repo`, like `verify_by_repo`. Default off. Only the shipped `code` and `tests` are skipped in a repo with no Python manifest; a set of the user is never skipped | Per repo control and no Python rule on a Go repo | Q3, Q7 |
| Q10 | Priority when on | The rules win in new or changed lines. Code the ticket does not touch stays as it is | If the maintainer turned it on, this is what they want | Q9 |
| Q11 | Shipped topic checklists | `code` and `scope` always. The `design` topic checklists (database, API, design) only when the diff touches the subject | Fewer tokens and fewer unrelated findings | Q4, Q7 |
| Q12 | Delivery | PR 1: the setting, the loader, the `{rules}` place in `system.md` and `review.md`, and the `code` family. Then one PR with text only for each of `tests`, `design` and `scope`. Claude drafts the English text from the local files and the maintainer reviews each PR. The 8 dead 1.x files stay for now | Small reviews; public opinions go in one at a time | Q5, Q7 |
| Q13 | Order | These rules first, then PR B (the hard daily cap) | The maintainer chose it. It delays rc1 and the soak, which wait for PR B (B15 of the spend caps decisions) | none |
| Q14 | Proof | The same ticket without and with the rules, measured only: new violations (counted offline by the maintainer checker), cost, fix loops, time. No success threshold set | Decide with the numbers | Q4 |
| Q15 | Where the proof runs | A private copy of the repo of the first real run, at the base commit of that ticket, so it compares with the 5 violations above. The maintainer approved this copy on 2026-11-18 and said the company allows it. The copy is deleted when the proof is done (ask first) | Same ticket, same code | Q14 |
| Q16 | The mechanical checker | Used only as an offline ruler for Q14. Not shipped | It reads Claude Code hook events and ignores `/tmp`; it needs an adapter to be a briar part | Q4, Q14 |
| Q17 | File format | One Markdown file per set, with the sections `## Baseline`, `## Review` and `## Review: <topic>`. The section splitter of the knowledge composer is reused | A user copies a file and edits it | Q6 |
| Q18 | Same name as a shipped set | An error at load. A user adds sets, never replaces one. To change a rule, copy it under a new name | The shipped sets stay the same for everyone | Q6, Q17 |
| Q19 | Review sections of a user set | A section such as `## Review: security` enters whenever its set is on, with no diff trigger | Simple; the user controls it by turning the set on per repo | Q17 |
| Q20 | A file with an error | `serve` stops at load and says what to fix: a section with a wrong name, a set that is turned on and has no file, a name that repeats | Same as an unknown key of the runbook today; a rule that silently does not apply is the worst failure | Q17, Q18 |
| Q21 | `briar doctor` | Shows, per repo, each set, where it comes from (shipped or `<DIR>/rules/<name>.md`) and why a set is skipped | The user can see which rules are in force before spending | Q9, Q18 |
| Q22 | A new shipped name that a user already uses | Accept the load error. The message says to rename the set of the user; the CHANGELOG lists new shipped names and `agents/serve.md` lists all of them | No extra rule; the cost is a rename after an upgrade | Q18 |
| Q23 | The user folder | `<DIR>/rules/`, one folder per DIR, set names global to the DIR. For different rules per company, use different set names and turn them on per company | Simple, versioned with the runbooks | Q6 |

## What of the local setup can be used in briar

From the local `~/.claude`, checked on 2026-11-18.

Used by this plan (as source text, condensed and translated):

- `hooks/quality-baseline.md` (2.8 KB): the base of the `code` baseline.
- `hooks/immutable-rules-review.md` (4.6 KB): the checklist of `code`.
- `hooks/tests-review.md` (3.7 KB) and `imutable-tests.md` (24 KB): `tests`.
- `hooks/system-design-review.md`, `database-review.md`, `api-review.md`: the topic checklists of `design`.
- The `scope` (2.2 KB) and `error-handling` (1.8 KB) skills: `scope`.
- `code-imutatble-rules.md` (46 KB): the long source. Too big to put in a prompt; the condensed files come from it.

Usable later, not in this plan:

- `hooks/immutable-rules-check.py`: stdlib only, about 20 mechanical rules over `ast`, "only new violations against HEAD". It can be a CI job for the briar repo or a `serve` gate, but it needs an adapter first (it reads a hook event from stdin, no command per file).
- `hooks/quality.sh` (format and lint on edit): the equivalent in briar is `format` and `verify` per repo in the runbook, so use those in the runbooks.

Already in briar, no port needed: the bash guards (`bash-write-guard`, `rm-guard`, `git-guard`) are in `BashGuard.FORBIDDEN`; the scope guard is the "smallest change" rule of `system.md`; the cost tracker is `Budget`; `secret-guard` is only partly there (briar blocks a silenced scanner, but does not scan the diff for secrets: an open gap).

Not for briar: the status line, session summary, instincts, skill indexes, the Stripe and network guards, and the personal parts of the global CLAUDE.md (identity, Slack, DNS and registrar notes). Do not load `~/.claude` into the agent to get any of this: that breaks the isolation of `setting_sources: []`.

## Open risks and what is not proven

- The 5 violations and the US$ 0.73 are one ticket on one repo. The effect of the rules on cost and on loops is unknown until Q14 runs.
- Rules that win in new lines (Q10) can make a new function look different from the file around it in an old repo. That is the price of turning them on.
- The self review is the same model reading its own work; it is a weak judge. The mechanical checker is the stronger ruler and is not part of the product (Q4, Q16).
- Topic detection for the shipped `design` checklists (Q11) is a heuristic, not designed yet. Language detection by manifest can be wrong in a monorepo with several languages (Q9).
- The 8 dead 1.x files in `prompts/rules/` sit next to the new ones. Name the new ones so nobody mixes them up (left to the spec).
- Public text with the maintainer opinions in it: each PR needs the maintainer to read it (Q12).
- A user set is text that goes into the system prompt. It is the user own file, so it is trusted, but a wrong one can make the agent worse. The `doctor` view (Q21) is the only guard.
- Q13 delays the release candidate.

## Left to the spec

The names and layout of the shipped files, the exact text of each baseline, how `{rules}` is filled, the topic heuristics of Q11, whether the user folder and the `doctor` view ship in PR 1 or in a PR 2 (they make PR 1 bigger), the exact error messages, the CHANGELOG entry and the docs in `agents/serve.md`.

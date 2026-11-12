# briar 2.0: `briar extract` values from the project config (spec 006, part 4)

Grill session of 2026-11-10 for spec 006 PR 4 (tasks T018 to T023). It
settles how `briar extract` gets every extractor value after its command
line shrinks to `--company`, `--include` and `--repo` (C12), and what
leaves with the output modes (C13). Builds on C11 to C13 in
`2026-11-10-briar-2-core-cleanup.md`, D32 and D32a in
`2026-11-10-briar-2-pipeline.md`, and research R-5 of spec 006.

Fact checked before the questions (all 23 extractors, `add_arguments`
read on 2026-11-10): no extractor has two options for the same shared
concept, so a concept name (`max`, `since_days`, `top_n`, `sample`, `repo`,
the four author/assignee filters) names exactly one option per extractor.
Seven options belong to no concept, and two extractors have a tracker
project list.

## Decisions

- E1. Key names in `[extract.<name>]`: short names, not the 1.x option
  names. `[extract.pr-archaeology] max = 50`, not `pr_max`. Why: a config
  file reads better without the repeated prefix. (C12)
- E2. A shared concept's short name is the concept itself (`max`,
  `since_days`, `top_n`, `sample`, `authors_allow`, `authors_block`,
  `assignees_allow`, `assignees_block`). Why: unambiguous per extractor
  (fact above). (E1)
- E3. The seven options with no concept drop the extractor prefix:
  `aws-infra`: `region`, `profile`, `services`; `meeting-digest`:
  `attendee_allow`; `pr-hygiene`: `large_loc`; `repo-governance`:
  `branch`; `stale-prs`: `days`. Why: one simple rule. (E1)
- E4. The tracker project list of `active-tickets` and `ticket-archaeology`
  is `[extract.<name>] projects = ["ACME", "OPS"]`. Why: a Jira key or a
  Linear team is not a repo, and `--repo` cannot carry them. `--repo`
  still fills it when the tracker uses repo slugs (GitHub or Bitbucket
  Issues), as today. (E1, E8)
- E5. The 1.x name in `[extract.<name>]` (for example `pr_max`) stops the
  run with exit 1 and names the short name to use. Why: one way to write
  each value, and an easy move from 1.x. (E1)
- E6. `[extract]` (no extractor name) takes the shared concepts of E2 and
  the source kinds `provider`, `tracker`, `meeting`, `cloud`, for every
  included extractor. (C12, E2)
- E7. Precedence, lowest first: the extractor default, the existing
  settings `tracker` (top level) and `[repo] provider`, `[extract]`,
  `[extract.<name>]`. The two settings stay valid. Why: files written by
  1.x and by `briar init` keep working. (C12, E6)
- E8. The repo list comes only from `--repo`, `repos = [...]` and
  `[repo] owner/repo`, as today. Neither `[extract]` nor
  `[extract.<name>]` takes `repo`. Why: no new place for the same list.
- E9. A value outside a fixed list (`provider`, `tracker`, `cloud`,
  `meeting`, the `aws-infra` `services`) stops the run with exit 1 and
  lists the allowed values. Types (int against text, list against text)
  are not checked until spec 004. (FR-006a, D32)
- E10. An unknown key, extractor name or 1.x name in `[extract]` or
  `[extract.<name>]` stops the run with exit 1 naming it. Only those two
  tables are checked; other parts of the file are not. Why: FR-006a asks
  for `[extract]`; `pyproject.toml` holds other tools' keys. (E5)
- E11. A `[extract.<name>]` table for an extractor that does not run this
  time is checked (E9, E10) and otherwise ignored, with no warning. (E10)
- E12. A runbook `args:` block is unchanged in this PR: 1.x option names,
  shared concept names, no check of unknown keys. Spec 004 merges the two
  paths (D32). (E1)
- E13. A removed command line flag (`--pr-repo`, `--max`, `--root`,
  `--out-json`, `--merge-claude-md`, `--claude-md-path`, `--blob-name`,
  `--advanced-help`, the source flags) gets argparse's normal
  `unrecognized arguments` error, exit 2; the CHANGELOG and
  `agents/extract.md` explain the move. (C12, C13, C22)
- E14. `briar extract -h` lists the `[extract.<name>]` keys of every
  extractor (short name, default, allowed values), under the three flags.
  (E1)
- E15. `agents/extract.md` has the same key table, built from the code,
  and a test fails when the page and the code differ. (E14)
- E16. The extract parameter tests stay one per option: each writes its
  value in `.briar.toml` with the short name and runs the real command.
  (E1, T019)

Settled by earlier decisions, recorded here so the PR does not reopen them:
`--root` leaves and the file root is the `root` setting only (C11); the
output modes leave with `extract/claude_md.py`, the JSON render, the three
goldens of those modes and their tests, and the legacy-flag notice in
`cli.py` (C13); `blob.golden.md` must stay byte for byte (T021).

## Open risks and things left undecided

- E12 leaves two spellings for the same option until spec 004: the
  runbook uses `pr_max` or `max`, `.briar.toml` only `max`. The niche short
  names of E3 (`region`, `days`, ...) do not work in a runbook.
- E7 keeps two places for the tracker and the repo provider (`tracker` /
  `[repo] provider` and `[extract]`), with `[extract]` winning.
- E9 leaves type errors (`max = "fifty"`) to fail inside the extractor
  until spec 004.
- E14 makes `briar extract -h` about 70 lines long.
- `days` (E3, `stale-prs`) is a short name a reader may find vague.
- Issue https://github.com/iklobato/briar/issues/76 (the `CLAUDE.md`
  index built from one run) stays open for 1.x on `main`; on `v2` the mode
  is gone.

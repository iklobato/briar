# briar 2.0: the last trims of the core cleanup (spec 006, part 5)

Grill session of 2026-11-10 for spec 006 PR 5 (tasks T024 to T028). It
settles how far the last trims of `auth`, `runbook`, `doctor` and `init`
go, and what the rewritten README holds. Already settled before this
session and not reopened: which operations stay (C14 to C16), `auth status`
with no target lists every credential name held (spec 006 clarification),
`runbook serve` ticks every second, a removed command gets the normal
`No such command` error (C22), and secrets live only in `secrets.env` (L3).

Found while preparing the questions: `briar doctor` drops `[repo]` from the
`git remote` hint, because the table renderer reads `[repo]` as markup. PR 5
fixes it with the doctor rework.

## Decisions

- F1. Credential store code: `--cred-store`, `_effective_store_kind` and
  `DestinationPolicy` leave (they only chose between stores). The
  `CredentialStore` interface and its one-entry registry stay; spec 005
  rewrites the logins on top of them. Why: no choice is left to make, but
  the interface is spec 005's to change. (C15, L3)
- F2. `briar doctor --format json` prints one object with two lists,
  `{"settings": [...], "checks": [...]}`. The table format prints the
  settings table first, then the checks. Why: a script reads both, and the
  two have different columns. Changes the JSON of 1.x, which was the check
  list only. (C16, FR-007)
- F3. The settings table of `doctor` gets a `telemetry` row: the tier and
  where it came from (`DO_NOT_TRACK`, `BRIAR_TELEMETRY`, the project config
  or the default). Why: it is the only place left to see the tier since
  `briar telemetry status` left. (C7, F2)
- F4. The README is rewritten from scratch, around the 5 commands. (FR-012)
- F5. Its sections: what briar is and what comes next (the ticket pipelines
  of spec 003); install and quickstart; the 5 commands with a link to each
  manual, and a `.briar.toml` example with `[extract]`; the beginner
  examples (collect facts, a scheduled runbook), adjusted to 2.0;
  telemetry; contributing and license. No "coming from 1.x" table: the
  CHANGELOG has it. (F4)
- F6. The README shows no GIF and no diagram until they are recorded or
  drawn again for 2.0: the 1.x ones show removed commands. The files stay
  in `docs/demo/` and `docs/diagrams/` (the site still loads them), and the
  status file notes the redo. (F4)
- F7. `pyproject.toml` description and keywords match 2.0 now (knowledge
  for agents; no agents, MCP or draft PRs yet). Why: it becomes the PyPI
  page at release; spec 003 updates it again. (F4)
- F8. The `briar init` template gets a commented `[extract]` example (a
  shared `max`, a tracker `projects` list) and the link to
  `agents/extract.md`. (E1 to E16)
- F9. `runbook sweep` users move to `runbook serve DIR`, or to
  `runbook extract` once per file; the manuals show both. (C14)
- F10. With no git origin, the `git remote` check of `briar doctor` says
  "no git origin: run `briar init --owner OWNER --repo REPO`, or set
  [repo] in .briar.toml". Why: only `init` has `--owner` in 2.0, so the
  1.x hint "pass --owner/--repo" did not say where. Ships as its own PR
  on top of PR 5 (decided after PR 5 was opened). (F2, E1)

## Open risks and things left undecided

- F2 changes the `doctor` JSON shape: a script that read the 1.x list
  breaks.
- F6 leaves the README with no picture until someone records the GIFs and
  redraws the diagrams (site repo); nobody owns that yet.
- F1 keeps a one-entry credential store registry until spec 005.
- How 2.0 is released (version 2.0.0 versus the patch bump of
  `release.yml`) is still open (status file, section 5, item 2); it is not
  part of this PR.

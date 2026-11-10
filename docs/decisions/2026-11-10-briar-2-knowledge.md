# briar 2.0: how extract stores knowledge for agents

Grill session, 2026-11-10. Goal: settle, in the simplest form, how `briar extract` stores what
it collects so that other agents (the pipeline, Claude Code, Cursor, a script) can use it, and the
features around it (runbook tasks, merge, history, notes written by the pipeline). Builds on
`2026-11-10-briar-2-pipeline.md` (D5, D32, D32a), `2026-11-10-briar-2-core-cleanup.md` (C11 to
C14, C19) and `2026-11-10-briar-2-ticket-pipeline.md` (P1, P3, P19, P21). For spec 004 (the
extractors) and, for the notes, spec 003.

Starting point (checked in code on 2026-11-10): 23 scheduled extractors; one markdown blob per
company at `<root>/knowledge/<company>.md` with a header and one `## ...` section per extractor;
`briar extract` merges by section (`KnowledgeComposer.merge_markdown`), while `runbook extract`
and `serve` rewrite the whole blob; a runbook task with its own name writes
`knowledge:<company>.<task>`; the blob's age is the file's; C13 removed the CLAUDE.md merge and
the JSON sidecar.

Format: `ID. Decision: chosen answer. Why. (depends on)`

## Decisions

### Shape and place

- K1. Storage: one file per extractor plus a generated `index.md` per company that lists each
  file, what it holds and its date. Merging becomes replacing one file, each file has its own
  age, and an agent reads only what it needs. (none)
- K2. Location: `~/.briar/knowledge/<company>/` by default (the `root` setting changes it). The
  same knowledge serves any repo, pipeline worktree or agent, wherever the command ran. Changes
  the 1.x default `./knowledge`. (K1, C11)
- K3. Other agents: they read `index.md` at the fixed path and open what they need. `briar init`
  adds a short pointer in the repo (the path of the index, no data). (K1, K2)
- K4. Each file is markdown with a short YAML front-matter (`extractor`, `company`,
  `generated_at`, `sources`, `repos`). Agents read it as text; code reads the date without
  guessing; freshness works per extractor. (K1)
- K5. When: the switch to this format is part of spec 004 (the extractor rework). Spec 006 keeps
  the single blob and its `blob.golden.md`; in spec 004 the snapshot test pins each extractor's
  text (unchanged, D32) plus the index. (K1, D32, C3)
- K8. Several repos per company: data about one repo goes to `<company>/<owner>__<repo>/<extractor>.md`;
  data about the company (tickets, meetings) to `<company>/<extractor>.md`. A pipeline working in
  one repo reads that repo's folder and the company files only. (K1)

### What runs and what is written

- K6. All 23 extractors stay in the core (code, review and PRs, tickets and meetings, delivery
  and cloud). (none)
- K7. An extractor that fails keeps its previous file, and the index says "failed on <date>,
  data from <date>"; one that ran and found nothing writes "no data". The agent can tell stale
  from empty. Same idea as 1.x. (K1, K4)
- K9. A runbook task only schedules: which extractors run and when (`cadence`). Every task writes
  into the same company and repo folders; the per-task blob goes. (K1)
- K10. `briar extract --company C` with no `--include` runs the list in `[extract] include` of
  `.briar.toml`, or all 23 when there is no list; `--include` on the command line wins. (C1, C12)
- K11. No size limit per file. (K1)
- K13. History: the last N versions of each extractor file are kept (`<extractor>.<date>.md`,
  `[extract] keep_versions = N`); the index points to the current one. (K1)
- K16. Each run of an extractor fetches its whole window again (`since_days`), never
  incrementally. Simple and always correct. (none)
- K20. `keep_versions` defaults to 1: with no setting, a run overwrites. (K13)

### Freshness for the pipeline

- K12. Changes P19 and P21: with no `index.md` for the company, a pipeline stops ("run briar
  extract"). With `max_blob_age_days` set, it stops only when a file it is going to read (the
  ticket repo's folder and the company files) is older; a stale extractor nobody reads does not
  stop it. Notes (K17) never count. (K4, K8, P19, P21)

### Pointer for other agents

- K14. The pointer that `briar init` writes goes in the repo's `AGENTS.md` (created if missing,
  or a short section added), the file several agents read. (K3)

### Notes written by the pipeline

- K15. Both `ticket` and `board` write back into the knowledge folder: `board` its plan and card
  notes, `ticket` a note per finished ticket; later runs read them. (P1)
- K17. Notes live apart from extracted facts: `<company>/<owner>__<repo>/notes/<ticket>.md` and
  `<company>/plans/<plan>.md`, with `origin: pipeline` in the front-matter; the index lists them
  in their own block. (K15, K4, K8)
- K18. A ticket note is written by code from the run's state, with no model call: the PR link,
  the plan, the criteria (inferred ones marked), the steps, how many times each loop ran, and the
  review result. Cheap, uniform, no invention. (K15, P7, P11)
- K19. Notes are kept forever. (K15)

## Resulting layout

```
~/.briar/knowledge/acme/
  index.md                          # generated: every file, its scope, date, status
  active-tickets.md                 # company-level extractors
  meeting-digest.md
  acme-co__app/                     # one folder per repo
    codebase-conventions.md
    code-hotspots.md
    pr-archaeology.md
    pr-archaeology.2026-11-03.md    # only when keep_versions > 1
    notes/
      ACME-123.md                   # written by the ticket pipeline (origin: pipeline)
  plans/
    q4-board.md                     # written by the board pipeline
```

Each file:

```markdown
---
extractor: code-hotspots
company: acme
repos: [acme-co/app]
sources: [github]
generated_at: 2026-11-10T12:00Z
---
<the same section text the extractor writes today>
```

## Changes to earlier decisions

- P19, P21: freshness is per file the pipeline reads, and "missing" means no `index.md` (K12).
- C11: the default `root` moves from `./knowledge` to `~/.briar/knowledge` (K2).
- C12: `[extract]` gains `include` (K10) and `keep_versions` (K13).
- C13: the CLAUDE.md merge stays removed; `AGENTS.md` gets only a pointer (K14).
- D5: runbook tasks only schedule (K9); `runbook extract` and `serve` replace per extractor file
  instead of the whole blob.
- D32: each extractor's text stays identical; the container changes (K5).

## Open risks

- R-K1. `runbook serve` writes while a pipeline reads: every write must be atomic (write a temp
  file, then rename) or a reader can see half a file.
- R-K2. Notes kept forever (K19) and no size limit (K11): the folder and the index grow without
  bound; a big index costs tokens for every agent that reads it.
- R-K3. Docker and CI: `~/.briar` inside a container is lost unless it is mounted; the docs must
  show the volume.
- R-K4. The knowledge folder holds people's names, review habits and meeting summaries. It stays
  local (P9), but there is no delete command (C6): removing a company means deleting its folder.
- R-K5. `AGENTS.md` (K14) is a file in the user's repo: `briar init` must add only its own short
  section and never rewrite what is there.
- R-K6. Pipeline notes (K15) mix agent work with extracted facts in what later runs read; a wrong
  note can mislead the next run. `origin: pipeline` and the separate block in the index (K17)
  are the only guard.

## Left for the spec (not decided here)

- The exact `index.md` layout (columns, status words).
- The folder name format for a repo (`<owner>__<repo>` is the proposal).
- Which extractors are repo-level and which company-level, one by one.
- The name of the versioned file and when old versions are pruned.
- Whether `briar doctor` shows the knowledge index status.

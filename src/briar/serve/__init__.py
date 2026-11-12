"""`briar serve`: the agentic development pipeline (spec 003; decisions S1 to S35 in
`docs/decisions/2026-11-11-briar-2-serve-pipeline.md`).

PR 1 of spec 003 builds the loop: it finds the work (review comments first, then the
assigned tickets by priority), runs up to `max_parallel` tasks per company, and keeps
the state in the knowledge store. The ticket and review routines arrive in PRs 2 and 3."""

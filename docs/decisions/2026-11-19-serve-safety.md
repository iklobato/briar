# serve: safety before it runs unattended (decisions S1 to S6)

Date: 2026-11-19. Code: `serve/secrets.py`, `serve/clean.py`, `serve/flow.py`, `serve/ticket.py`, `serve/task.py`,
`extract/_providers/github.py`. Docs: `agents/serve.md`, "A secret in a change" and "The text of a ticket".

## Why

Four things could hurt a real colleague or a real repo once `serve` runs on its own. Nothing looked at a diff before a push, and a secret that
reached the history of GitHub cannot be taken back. Every ready PR notified real reviewers, and the list of usual reviewers counted bots. The
text of a ticket went into the plan prompt as it was. And "this repo has no CI" was decided by a fixed 60 seconds.

## Decisions

| # | Decision | Why |
|---|---|---|
| S1 | Every push is preceded by a scan of the lines it would ADD, with the credential patterns of `SecretGuard` and nothing else. | The patterns exist, are tested and need no new dependency. The name rule of `SecretGuard` is left out: `token` and `password` are everywhere in code. A password written by hand is not caught. |
| S2 | A hit gives the agent one fix loop (file and line, never the value). A second hit blocks the task with nothing pushed. | A placeholder in a test looks like a secret, and a block alone would need a manual reopen. |
| S3 | A clean fix is squashed with the commits not pushed yet. | A push sends every commit: without it the commit that held the value would reach the origin next to the commit that removes it. |
| S4 | The draft that a stop makes is scanned too and is not pushed on a hit. | It pushes work that was never verified. |
| S5 | `serve.ask_reviewers` is false by default, and bots are never usual reviewers. | The ask notifies real people. 2.0 is not out, so changing the default breaks nobody. |
| S6 | The title and description of a ticket go between `~~~ticket` fences, and a run of tildes inside is broken. | A ticket can carry instructions. A fence lowers the risk; it is not a guarantee. `serve.ci_grace_seconds` (default 60) makes the wait for a CI that has not started a runbook option. |

## Not covered

- The plan and the report comments that briar posts on a ticket are not scanned.
- Renamed or binary files, entropy rules and hand written passwords.
- A model can still follow text inside a fence.

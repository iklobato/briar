# serve: a refused login stops it, a usage limit pauses it (decisions L1 to L9)

Date: 2026-11-19. Code: `serve/pause.py`, `serve/health.py`, `serve/status.py`, `serve/agent.py`, `serve/loop.py`.
Docs: `agents/serve.md`, "A login that was refused" and "The limit of your plan".

## Why

Before this change a refused login and a usage limit of the plan were failed calls like any other: the task was
blocked and a "briar stopped" comment was written on its ticket, and a poll with a dead login was logged and tried
again for ever. An unattended `serve` needs two different answers: a dead login makes every task fail the same way, so
it should stop and say so; a usage limit lifts by itself, so it should wait and write nothing.

## Decisions

| # | Decision | Why |
|---|---|---|
| L1 | A refused login stops the whole `serve`, not only the company it belongs to. | Every other task would meet the same wall, and a half-running `serve` hides it. The cost: one expired cookie stops the healthy companies too. |
| L2 | A login of the code host or Jira is dead only when its read-only "who am I" gets an HTTP 401. A task or a poll that fails is checked this way before anything stops. | `LoginRejected` is raised for every HTTP answer that is not a network error (a 403, a 404, a 503), so its type cannot say that a login died. `LoginRejected.status` carries the status the adapters saw. |
| L3 | The model login is read from what the SDK says: `AssistantMessage.error == "authentication_failed"` or `api_error_status` 401, then `ResultError`. | Checked with a fake API key and a fake OAuth token (no cost): the same two signals for both. No text is read. |
| L4 | A task hit by a dead login or a usage limit is not blocked and writes nothing on its ticket or PR. It keeps its status, and what was never pushed goes back to what the origin has. `DayPause`, `UsageLimitHit` and `LoginRefused` share one base, `ModelPause`. | The same rule as the daily cap (B13): it is not the fault of the ticket, and a comment would need a manual reopen. |
| L5 | On a stop the loops end, the running tasks get `call_minutes` to finish, the rest are cancelled, and the exit code is 3. | Keeps the paid work of the companies that are fine. The next start resets a cancelled worktree to the origin (decision B26). |
| L6 | The status file `<knowledge root>/work/<company>.status.json` is written at the end of every poll, says `stopping` as soon as the stop is asked and `stopped` when the tasks are done, with the reason, the source and the fix. | A file written only at a stop shows nothing after a kill, a reboot or a crash, and an old `stopped` could mislead. An old `updated_at` shows that `serve` is gone. |
| L7 | A usage limit of the plan pauses the tasks. While it holds, each poll sends one task that needs the model (the first PR, else the first resumed ticket, else the selector) to ask again, and goes on with merged and closed PRs. A call that works opens the gate. | No clock logic and no dependence on the text of the limit. `RateLimitInfo.resets_at` exists, and it is not used: using it to skip polls is a later option. Not seen against a real limit yet. |
| L8 | A dead Slack login never stops `serve`. | The chat is optional (B3). |
| L9 | The start check logs which login of the model it found and warns when `ANTHROPIC_API_KEY` is set next to `CLAUDE_CODE_OAUTH_TOKEN`; `briar doctor` warns the same way. briar does not change the environment. | The key outranks the token, so the run would bill the API and not the plan the token is for. |

## Not covered

- A login that dies inside a scheduled extract: the extract has its own check at start and logs the failure.
- A cookie that expires in a way that is not an HTTP 401 (a redirect to a login page, a 403): it is not recognised, so the poll
  fails every time and is logged, as before. Measure what each source answers to an expired login before relying on this.
- Docker `--restart on-failure` and launchd `KeepAlive` start `serve` again on exit 3. The start check then fails at once on
  the same login. With systemd, `RestartPreventExitStatus=3` stops the loop.
- The stop can take up to `call_minutes`, and a scheduled extract in flight finishes before the process exits.

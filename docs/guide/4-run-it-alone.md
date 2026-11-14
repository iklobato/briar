# Lesson 4: Let it run alone

**You will:** put limits on briar, learn to read its daily report, and keep `serve` running when you close the terminal.
**Time:** about 10 minutes. **Cost:** you decide, with the limits below.
**You need:** lesson 2 done, and one issue that briar finished well.

Do not skip step 1. A tool that spends money must have a limit.

## Step 1. Add brakes

In the `serve:` block of your runbook:

```yaml
      max_new_per_day: 3        # new issues it may start per day
      max_usd_per_day: 10       # when the day cost reaches this, no NEW issue starts
                                # (issues and reviews already open go on)
      format: ["ruff format ."] # your formatter; briar runs it before every commit
```

| Limit | What it stops |
|---|---|
| `max_usd` | Spending on one issue |
| `max_loops` | Fix attempts on one issue |
| `max_new_per_day` | How many new issues start in one day |
| `max_usd_per_day` | Spending in one day (UTC) |

When you trust it, remove `only` and let briar choose among all the issues assigned to you.

## Step 2. Read the daily report

You do not have to read the log. Every time it looks for work, briar rewrites a report at `./knowledge/reports/YYYY-MM-DD.md` (the folder you ran `serve` from). This is a real one, from a quiet day:

```markdown
# briar daily report: myteam, 2026-11-14 (UTC)

Written 02:41 UTC; rewritten every poll.

## Spent today

US$ 0.00 on 0 ticket(s).

## Waiting for a merge

- none

## Stopped

- none

## Notes of the polls

- none
```

| Section | What it tells you |
|---|---|
| Spent today | What the day cost, and on how many tickets |
| Waiting for a merge | PRs that are ready and wait for a person. This is your to-do list. |
| Stopped | Tickets that ended `blocked`, and why |
| Notes of the polls | What a poll skipped, for example a daily limit reached |

If a PR waits for review for 48 hours, briar leaves one reminder note on it.

## Step 3. Keep it running

Use API tokens, not browser sessions. A browser cookie dies in days. For Jira, use an [API token](https://id.atlassian.com/manage-profile/security/api-tokens) with `[auth.jira] method = "token"`. Keep every login in `~/.config/briar/secrets.env`: a background service does not read your shell profile.

On macOS, a LaunchAgent starts `serve` when you log in. The ready-to-copy file, the PATH rule and how to stop it are in the manual: [Run it unattended](../../agents/serve.md#run-it-unattended).

**One rule that bites:** the `PATH` in that file must hold the folder of every program your `verify` command runs (`uv`, `node`, `terraform`). If one is missing, the issue ends `blocked` with `not on the PATH of this process`.

**Stop it:** `launchctl bootout gui/$(id -u)/com.usebriar.serve`

**You did it.** briar now works your assigned issues, inside limits you chose, and it leaves you a report and PRs to merge.

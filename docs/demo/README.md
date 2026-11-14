# The README GIFs

Each GIF of the README is made from a script in this folder, so anyone can record it again
when a command changes. The output in a GIF is the real output of a real run; only the typing
is staged. Waits with no output (a PR waiting for CI, a schedule waiting for its minute) are
cut to 1.5 seconds, so a run of minutes fits in half a minute.

| GIF | Script | What it shows | Needs |
|---|---|---|---|
| `01-first-steps.gif` | `01-first-steps.sh` | `init`, `doctor`, `extract`, reading the result | a fresh clone of a GitHub repo, `gh` logged in |
| `02-serve-an-issue.gif` (not recorded yet) | `02-serve-an-issue.sh` | `serve` takes one issue to a ready PR | a clone, a `runbooks/myteam.yaml`, an issue assigned to you, a Claude login. It spends model money and opens a real PR: close it after |
| `03-keep-facts-fresh.gif` | `03-keep-facts-fresh.sh` | a runbook schedule refreshing the knowledge file | a clone and a `runbooks/myteam.yaml` with a schedule |

The old `01-setup-and-extract.gif` to `04-agent-review.gif` were recorded on briar 1.x and show
commands that no longer exist. They are kept only because the site may still load them.

## Record one

You need `asciinema` and `agg` (the GIF is made from the recording), `briar` and `gh` on the
PATH, and the logins in the environment.

```bash
git clone --depth 1 https://github.com/<you>/<repo> /tmp/demo/repo
DEMO_DIR=/tmp/demo/repo docs/demo/record.sh 01-first-steps

# the serve demo also needs the issue number and the repo:
ISSUE=12 REPO=<you>/<repo> DEMO_DIR=/tmp/demo/repo docs/demo/record.sh 02-serve-an-issue
```

Use a short, neutral folder such as `/tmp/demo/repo`: `briar doctor` prints the path, and a
long path of your own machine ends up in the GIF. Open the GIF before you commit it and check
that no token, no email and no path of your machine shows.

Do not edit a script while it is recording: bash reads a script as it runs.

The serve demo needs a repo whose CI passes on a pull request: briar waits for CI and fixes a
failure, so with a red CI the task ends `blocked` and the GIF shows that instead. (On
2026-11-14 every PR to the `main` of `iklobato/briar` failed the same integration test, so two
recording runs there ended `blocked`.)

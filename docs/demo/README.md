# The README GIFs

Each GIF of the README is made from a script in this folder, so anyone can record it again
when a command changes. The output in a GIF is the real output of a real run; only the typing
is staged. Waits with no output (a PR waiting for CI, a schedule waiting for its minute) are
cut to 1.5 seconds, so a run of minutes fits in half a minute.

| GIF | Script | What it shows | Needs |
|---|---|---|---|
| `hero.gif` | `hero.sh` | one `extract`, then the facts file (top of the README) | a clone of a GitHub repo with releases and CI runs, `gh` logged in |
| `lesson1-setup.gif` | `lesson1-setup.sh` | token, `init`, `doctor` | a fresh clone, `gh` logged in |
| `lesson1-collect.gif` | `lesson1-collect.sh` | `extract` of three readers, reading the file | the same clone, after `lesson1-setup` (needs its `.briar.toml`) |
| `lesson2-serve.gif` | `lesson2-serve.sh` | `serve` takes one issue to a ready PR | a clone, a `runbooks/myteam.yaml`, an issue assigned to you, a Claude login. It spends model money and opens a real PR: close it after |
| `lesson3-schedule.gif` | `lesson3-schedule.sh` | a runbook schedule refreshing the knowledge file | a clone and a `runbooks/myteam.yaml` with a schedule |

The README and the lessons in `docs/guide/` embed these. Rename a GIF only together with those pages.

The old `01-setup-and-extract.gif` to `04-agent-review.gif` were recorded on briar 1.x and show
commands that no longer exist. They are kept only because the site may still load them.

## Record one

You need `asciinema` and `agg` (the GIF is made from the recording), `briar` and `gh` on the
PATH, and the logins in the environment.

```bash
git clone --depth 1 https://github.com/<you>/<repo> /tmp/demo/repo
DEMO_DIR=/tmp/demo/repo docs/demo/record.sh lesson1-setup

# the serve demo also needs the issue number and the repo:
ISSUE=12 REPO=<you>/<repo> DEMO_DIR=/tmp/demo/repo docs/demo/record.sh lesson2-serve
```

Use a short, neutral folder such as `/tmp/demo/repo`: `briar doctor` prints the path, and a
long path of your own machine ends up in the GIF. Open the GIF before you commit it and check
that no token, no email and no path of your machine shows.

Do not edit a script while it is recording: bash reads a script as it runs.

The serve demo needs a repo whose CI passes on a pull request: briar waits for CI and fixes a
failure, so with a red CI the task ends `blocked` and the GIF shows that instead. The README
GIF was recorded on [iklobato/briar-demo](https://github.com/iklobato/briar-demo), a tiny repo
made for this (one function, one test, a CI that runs it). On 2026-11-14 every PR to the `main`
of `iklobato/briar` failed the same integration test, so two recording runs there ended
`blocked`, and the repo was swapped.

Each take of the serve demo opens a real PR and an issue on the demo repo. Close the ones you
do not keep (`gh pr close N --delete-branch`).

# Problems and words

Looking for a lesson? Start at the [guide](README.md).

## If something goes wrong

Start with `briar doctor` (or `briar doctor --runbooks runbooks` for `serve`):
it names what is wrong and how to fix it.

| What you see | What it means | What to do |
|---|---|---|
| `the login check failed` and a list | A login is missing or refused. Each line names the variable. | Set that variable in `secrets.env`, then run `briar doctor` again. |
| `HTTP 401` or `Bad credentials` | The token is wrong, expired, or for another account. | Create a new token and replace it. |
| `Field required` when reading a runbook | The `serve:` block needs `model` and the limits, and has no default. | Fill in `model`, `max_usd`, `max_loops`, `ci_wait_minutes`, `max_review_rounds`. |
| `no agent sandbox: install ...` | `serve` found no sandbox. | macOS has one built in. On Linux, install `bubblewrap` and `socat`. |
| `no model login` | `serve` has no Claude login. | Set `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. |
| `another briar serve runs on the company` | A second `serve` uses the same folder. | Stop the other one first. |
| A ticket ends `blocked`, and the issue gets a comment starting `briar stopped:` | briar hit a limit or a failure and stopped on purpose. The comment says why. | Read the reason. The common ones: a limit was reached (raise it or make the ticket smaller), or your `verify` command fails even without briar (fix that first). |
| `command not found` in a `blocked` report | A program your `verify` command runs is not on the PATH of the process. | Add its folder to the `PATH` of the shell or service that runs `serve`. |
| `serve` prints nothing for minutes | It is waiting for the next check, or has nothing to do. | Normal. It checks every 5 minutes by default. |

## Words you will see

| Word | What it means |
|---|---|
| extractor | A reader for one kind of fact, like "merged PRs" or "CI runs". |
| knowledge | The facts briar saved. One markdown file per team, like `knowledge:myteam`. |
| runbook | A YAML file that says which extractors to run and how often, and how `serve` may work tickets. |
| ticket | A task in Jira, or an issue on GitHub. |
| PR | Pull request: a proposed change to a repo. On GitLab it is called a merge request (MR). |
| CI | The checks that run on every PR (tests, lint). |
| sandbox | A locked box the agent runs in, so it can only touch its own copy of the repo. |

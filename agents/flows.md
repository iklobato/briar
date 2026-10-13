# briar flows: how commands work together

Each flow below chains a few `briar` commands to reach one real result.
Every command is ready to run. The values are examples you swap for your
own:

| Value | Stands for |
|---|---|
| `acme` | Your company key. It names the knowledge blob and the credential variables (`JIRA_ACME_TOKEN`). |
| `acme-co/acme-app` | Your repo, as `owner/repo`. |
| `ACME`, `ACME-42` | Your Jira project and a ticket in it. |
| `482`, `fix/login` | A PR number and its branch. |
| `q3` | A plan name. |
| `runbooks/` | A folder of runbook YAML files. |

Each command has its own page with every flag: [extract.md](extract.md),
[agent.md](agent.md), [plan.md](plan.md), and the rest of
[the index](README.md). To run a flow with Docker, use the mounts in the
[README](../README.md#run-with-docker). Since 1.1.75 the image has `git`
and `gh`, so `agent` and `plan run` work there too. Add your
`~/.gitconfig` and `-e GITHUB_TOKEN` (see
[Run an agent in Docker](../README.md#run-an-agent-in-docker)).

## Flow 1: Set up a new company

Save credentials, check them, then take the first snapshot.

```bash
# 1. Save credentials. Each one goes to ~/.config/briar/secrets.env
briar auth login github-pat --company acme
briar auth login jira-token --company acme
briar auth login aws-static --company acme
briar auth login fireflies --company acme

# 2. Check which credentials each runbook still lacks
briar secrets doctor --examples runbooks/

# 3. First snapshot: repo, Jira project, AWS and meetings
briar extract --company acme --repo acme-co/acme-app \
    --ticket-project ACME --ticket-archaeology-project ACME

# 4. Read what was saved
briar context get knowledge:acme
```

You get `knowledge:acme`, a markdown file with one section per extractor
that had what it needed. Step 2 lists any missing variable by name.

## Flow 2: Implement one Jira ticket

Refresh what the agent needs to know, check the prompt for free, then run.

```bash
# 1. The sections the agent reads: tools, file pairs, reviewers, open PRs, PR history
briar extract --company acme --repo acme-co/acme-app \
    --include codebase-conventions --include code-hotspots \
    --include reviewer-profile --include active-work --include pr-archaeology

# 2. Print the prompt. No clone, no Claude call.
briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ACME-42 --tracker jira --dry-run

# 3. Run it. Meetings and Slack threads that mention ACME-42 are added.
briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ACME-42 --tracker jira

# 4. Look at the draft PR
gh pr list --repo acme-co/acme-app --head chore/acme-42
```

You get a branch `chore/acme-42` and a draft PR. If an acceptance
criterion is unclear, you get a question on the ticket instead.

Step 3 reads Slack only when `SLACK_ACME_TOKEN` and `SLACK_ACME_COOKIE_D`
are set. It reads Fireflies only when `FIREFLIES_ACME_API_KEY` is set.

## Flow 3: Fix the review comments on a PR

```bash
# 1. Fresh reviewer habits, so fixes match what each reviewer wants
briar extract --company acme --repo acme-co/acme-app \
    --include reviewer-profile --include codebase-conventions

# 2. Print the prompt with the PR's comments and failing CI steps
briar agent prfix --company acme --repo acme-co/acme-app \
    --pr 482 --branch fix/login --dry-run

# 3. Fix it, using the meeting where the team decided the approach
briar agent prfix --company acme --repo acme-co/acme-app \
    --pr 482 --branch fix/login --meeting-key 01JB7Q4ZK3M8
```

You get one commit per addressed comment on `fix/login`, and a short reply
on each thread with the commit SHA. A PR that is approved, green, and has
only positive comments is skipped.

Without `--meeting-key`, the agent searches meetings and Slack for
`acme-co/acme-app#482`. Use `--meeting-query` or `--slack-query` to
search for other words, like the reviewer's name.

## Flow 4: Ship a board, card by card

```bash
# 1. Company knowledge for the card summaries
briar extract --company acme --repo acme-co/acme-app \
    --include codebase-conventions --include active-work \
    --include active-tickets --ticket-project ACME

# 2. Build the plan from the Jira project ACME
briar plan build jira:ACME --name q3 --company acme \
    --llm anthropic --with-knowledge

# 3. Read it, and ask what would run first. This changes nothing.
briar plan status q3
briar plan next q3 --company acme --llm anthropic

# 4. Run one card to check the setup
briar plan run q3 --company acme --repo acme-co/acme-app \
    --tracker jira --llm anthropic --limit 1

# 5. See what the loop learned from that card
briar context get knowledge:acme.q3

# 6. Run the rest, and keep going past a failed card
briar plan run q3 --company acme --repo acme-co/acme-app \
    --tracker jira --llm anthropic --continue-on-failure

# 7. Retry a blocked card after you fix the cause
briar plan advance q3 --card ACME-43 --status pending
```

You get a draft PR for each finished card. `plan status q3` shows what
is done, blocked and pending. `briar journal list --command plan.run`
lists each run.

Pass `--tracker jira` for a Jira board. The default for `plan run` is
`github-issues`. If you save to Postgres, add `--store postgres` to every
command in this flow, including `context get`.

## Flow 5: List every tagged AWS resource

Once, with the full detail in a JSON file:

```bash
briar extract --company acme --include aws-infra \
    --aws-extract-service tagging-inventory \
    --aws-extract-region us-east-1 \
    --out-json ./acme-aws.json
```

The blob gets a count per AWS service. `./acme-aws.json` gets each
resource with its ARN, type, region and tags.

On a schedule, with a JSON copy saved next to the blob each time, put
this in `runbooks/acme.yaml`:

```yaml
version: 1
companies:
  acme:
    knowledge:
      store: postgres
      name: knowledge:acme
      config:
        inventory: "true"
    schedules:
      - task: inventory
        every: "day at 03:00"
        extract:
          - name: aws-infra
            args:
              aws_extract_region: us-east-1
              aws_extract_service: [tagging-inventory]
```

Then run it once, and look at the saved copy:

```bash
briar runbook extract runbooks/acme.yaml
briar context list --store postgres --prefix inventory:
briar context get --store postgres inventory:acme
```

You get `inventory:acme`, a JSON blob with every resource. It is
rewritten only when your resources change, so the Postgres history shows
what changed and when.

## Flow 6: Keep knowledge fresh on a server

```bash
# 1. Check credentials for every runbook in the folder
briar secrets doctor --examples runbooks/

# 2. Run every runbook once, now
briar runbook sweep runbooks/

# 3. Stay running and fire each task on its schedule
briar runbook serve runbooks/

# 4. Check the server from a browser, or once in a terminal
briar dashboard --examples runbooks/ --once
```

`runbook serve` logs `scheduler starting: N job(s)`, then
`fire task=... company=...` and `result task=... status=...` for each
run. It does not go to the background by itself. Run it under systemd,
for example:

```ini
# /etc/systemd/system/briar-scheduler.service
[Service]
ExecStart=/usr/local/bin/briar runbook serve /etc/briar/runbooks/
Restart=on-failure
User=briar
EnvironmentFile=/etc/briar/secrets.env
```

When a task fails, briar can alert you. List the channels in
`BRIAR_NOTIFY_SINKS` (for example `slack,telegram`). See
[runbook.md](runbook.md).

## Flow 7: Bitbucket and Linear instead of GitHub and Jira

```bash
# 1. Save credentials for both
briar auth login bitbucket-app-password --company acme
briar auth login linear-api-key --company acme

# 2. Read Bitbucket PR history and the repo's tools
briar extract --company acme --provider bitbucket --repo acme-co/acme-app \
    --include pr-archaeology --include codebase-conventions

# 3. Implement a Linear ticket in the Bitbucket repo
briar agent implement --company acme --provider bitbucket \
    --repo acme-co/acme-app --ticket-key ENG-7 --tracker linear
```

You get the same knowledge sections, read from Bitbucket. Step 3 pushes
a branch `chore/eng-7` and opens a draft PR with a Python script (since
1.1.88; before, it was told to use `curl`, which its shell refuses, so you
opened the PR by hand).

## Flow 8: Many repos, projects and authors at once

```bash
briar extract --company acme \
    --include pr-archaeology --include active-work --include active-tickets \
    --repo acme-co/web --repo acme-co/api --repo acme-co/infra \
    --ticket-project ACME --ticket-project OPS \
    --authors-allow alice --authors-allow bob \
    --authors-block 'dependabot[bot]'
```

You get one PR section that covers all three repos, and one ticket
section for both Jira projects. Only PRs by `alice` or `bob` are kept.
The author filters apply to `pr-archaeology` and `active-work` only.

## Flow 9: Make a config bundle for another system

`briar scaffold` writes a JSON bundle that another system (a webhook
handler, a CI job) can use to run an agent.

```bash
# Jira ticket -> plan -> human approval -> implement, started by a GitHub webhook
briar scaffold implementation --prefix acme \
    --source jira --jira-project ACME \
    --archetype engineer --shape plan-approve-act \
    --trigger-kind github_webhook \
    --owner acme-co --repo acme-app \
    --out acme-impl.json

# A PR fixer that runs every hour, with no approval step
briar scaffold pr-fixes --prefix acme \
    --owner acme-co --repo acme-app \
    --out acme-prfix.json
```

You get two JSON files. `jq . acme-impl.json` shows the archetype,
shape, trigger and source you chose. See [scaffold.md](scaffold.md).

## Flow 10: See what happened

```bash
# Server status once, as text (good for CI)
briar dashboard --examples runbooks/ --once

# The same as a web page on http://127.0.0.1:8080
briar dashboard --examples runbooks/ --port 8080

# Every plan run, newest first
briar journal list --command plan.run

# One run in detail (use an id from the list)
briar journal show 3f9a1c2b7d4e

# Save it as JSON for a report
briar journal export 3f9a1c2b7d4e --as json --out q3-run.json
```

The journal holds each selector decision with its reason, and each card
start, success and failure. Only `plan run` and `scaffold` write journal
sessions. A plain `agent implement` or `agent prfix` does not. Its
record is the PR and the log printed with `-v`.

## Flow 11: Manage knowledge blobs by hand

The store holds plain markdown blobs by name. You can add your own.

```bash
# Save a note from the command line
briar context put memory:reviewer-alice \
    --content "Alice asks for a regression test and a changelog line."

# Save a markdown file as a blob
briar context put memory:acme-house-style --from-file ./house-style.md

# Read, list and delete
briar context get knowledge:acme
briar context list --prefix knowledge:
briar context delete memory:reviewer-alice --yes
```

You get blobs you can read back by name. `memory:` blobs are for people.
The agent never reads them.

The agent reads only extractor sections, found by headings like
`## Codebase conventions`, in blobs named `knowledge:acme...`. A blob
you write by hand reaches the agent only if it uses one of those
headings. When two blobs have the same section, only one is used.

`context` uses the file store unless you pass `--store postgres` (or set
`BRIAR_STORE=postgres`). That is true even when `BRIAR_DATABASE_URL` is
set. See [context.md](context.md).

## Putting it together

A common order for a new team:

1. Flow 1 to save credentials and take the first snapshot.
2. Flow 6 to keep that snapshot fresh on a server.
3. Flow 2 for one ticket, then Flow 4 for a whole board.
4. Flow 3 whenever a reviewer comments on one of those PRs.
5. Flow 10 to check what ran and why.

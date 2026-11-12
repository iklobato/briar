# briar flows: how commands work together

Each flow below chains a few `briar` commands to reach one real result.
Every command is ready to run. The values are examples you swap for your
own:

| Value | Stands for |
|---|---|
| `acme` | Your company key. It names the knowledge blob and the credential variables (`JIRA_ACME_TOKEN`). |
| `acme-co/acme-app` | Your repo, as `owner/repo`. |
| `ACME` | Your Jira project. |
| `runbooks/` | A folder of runbook YAML files. |

Each command has its own page with every flag: [extract.md](extract.md)
and the rest of [the index](README.md). To run a flow with Docker, use the
mounts in the [README](../README.md#run-with-docker).

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

## Flow 2: List every tagged AWS resource

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

## Flow 3: Keep knowledge fresh on a server

```bash
# 1. Check credentials for every runbook in the folder
briar secrets doctor --examples runbooks/

# 2. Run every runbook once, now
briar runbook sweep runbooks/

# 3. Stay running and fire each task on its schedule
briar runbook serve runbooks/
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

## Flow 4: Bitbucket and Linear instead of GitHub and Jira

```bash
# 1. Save credentials for both
briar auth login bitbucket-app-password --company acme
briar auth login linear-api-key --company acme

# 2. Read Bitbucket PR history and the repo's tools
briar extract --company acme --provider bitbucket --repo acme-co/acme-app \
    --include pr-archaeology --include codebase-conventions
```

You get the same knowledge sections, read from Bitbucket.

## Flow 5: Many repos, projects and authors at once

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

## Flow 6: Manage knowledge blobs by hand

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

`context` uses the file store unless you pass `--store postgres` (or set
`BRIAR_STORE=postgres`). That is true even when `BRIAR_DATABASE_URL` is
set. See [context.md](context.md).

## Putting it together

A common order for a new team:

1. Flow 1 to save credentials and take the first snapshot.
2. Flow 3 to keep that snapshot fresh on a server.

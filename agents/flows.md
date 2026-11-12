# briar flows: how commands work together

Each flow below chains a few `briar` commands to reach one real result.
Every command is ready to run. The values are examples you swap for your
own:

| Value | Stands for |
|---|---|
| `acme` | Your company key. It names the knowledge blob and the credential variables (`BRIAR_ACME_JIRA_TOKEN`). |
| `acme-co/acme-app` | Your repo, as `owner/repo`. |
| `ACME` | Your Jira project. |
| `runbooks/` | A folder of runbook YAML files. |

Each command has its own page with every flag: [extract.md](extract.md)
and the rest of [the index](README.md). To run a flow with Docker, use the
mounts in the [README](../README.md#run-with-docker).

## Flow 1: Set up a new company

Save credentials, check them, then take the first snapshot.

```bash
# 1. Say where the Jira site is and which AWS profile (briar never writes this file)
printf 'company = "acme"\n[auth.jira]\nsite = "acme.atlassian.net"\n[auth.aws]\nprofile = "acme"\n' >> .briar.toml

# 2. Write the secrets (logins.md lists the names), readable by you only
mkdir -p ~/.config/briar
cat >> ~/.config/briar/secrets.env <<'ENV'
BRIAR_ACME_GITHUB_TOKEN=<a fine-grained GitHub token>
BRIAR_ACME_JIRA_EMAIL=<your Atlassian email>
BRIAR_ACME_JIRA_TOKEN=<an Atlassian API token>
BRIAR_ACME_FIREFLIES_API_KEY=<a Fireflies API key>
ENV
chmod 600 ~/.config/briar/secrets.env
aws sso login --profile acme             # AWS keeps its own login

# 3. Check every login of acme, live
briar doctor

# 4. Name the Jira project in .briar.toml (a Jira key is not a repo)
cat >> .briar.toml <<'TOML'
[extract.active-tickets]
projects = ["ACME"]

[extract.ticket-archaeology]
projects = ["ACME"]
TOML

# 5. First snapshot: repo, Jira project, AWS and meetings
briar extract --company acme --repo acme-co/acme-app

# 6. Read what was saved
cat ./knowledge/knowledge/acme.md
```

You get `knowledge:acme`, a markdown file with one section per extractor
that had what it needed. Step 2 lists any missing variable by name.

## Flow 2: List every tagged AWS resource

Once, with a count per AWS service. Put the region and the part to read
in `.briar.toml`:

```toml
[extract.aws-infra]
region = "us-east-1"
services = ["tagging-inventory"]
```

```bash
briar extract --company acme --include aws-infra
```

On a schedule, with a JSON copy saved next to the blob each time, put
this in `runbooks/acme.yaml`:

```yaml
version: 1
companies:
  acme:
    knowledge:
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

Then serve the folder; the task first runs at its next 03:00 UTC:

```bash
briar serve runbooks/
```

You get `inventory:acme` (`./knowledge/inventory/acme.md`), a JSON blob
with every resource. It is rewritten only when your resources change, so
its file date shows when they last changed.

## Flow 3: Keep knowledge fresh on a server

```bash
# 1. Check the logins of every runbook, without starting
briar doctor --runbooks runbooks/

# 2. Stay running and fire each task on its schedule
briar serve runbooks/
```

`serve` logs `fire task=... company=...` and `result task=... status=...`
for each run; a folder with no `serve:` block runs only these. It does not
go to the background by itself. Run it under systemd, for example:

```ini
# /etc/systemd/system/briar.service
[Service]
ExecStart=/usr/local/bin/briar serve /etc/briar/runbooks/
Restart=on-failure
User=briar
EnvironmentFile=/etc/briar/secrets.env
```

When a task fails, the error is in the log; 2.0 sends no alert yet. See
[runbooks.md](runbooks.md).

## Flow 4: Bitbucket and Linear instead of GitHub and Jira

```bash
# 1. Say which Bitbucket workspace, then write the secrets of both
printf '[auth.bitbucket]\nworkspace = "acme"\n' >> .briar.toml
printf 'BRIAR_ACME_BITBUCKET_EMAIL=%s\nBRIAR_ACME_BITBUCKET_TOKEN=%s\nBRIAR_ACME_LINEAR_API_KEY=%s\n' \
    "<email>" "<Atlassian API token>" "<Linear API key>" >> ~/.config/briar/secrets.env

# 2. Point the repo extractors at Bitbucket
printf '[extract]\nprovider = "bitbucket"\n' >> .briar.toml

# 3. Read Bitbucket PR history and the repo's tools
briar extract --company acme --repo acme-co/acme-app \
    --include pr-archaeology --include codebase-conventions
```

You get the same knowledge sections, read from Bitbucket.

## Flow 5: Many repos, projects and authors at once

```toml
[extract]
authors_allow = ["alice", "bob"]
authors_block = ["dependabot[bot]"]

[extract.active-tickets]
projects = ["ACME", "OPS"]
```

```bash
briar extract --company acme \
    --include pr-archaeology --include active-work --include active-tickets \
    --repo acme-co/web --repo acme-co/api --repo acme-co/infra
```

You get one PR section that covers all three repos, and one ticket
section for both Jira projects. Only PRs by `alice` or `bob` are kept.
The author filters apply to `pr-archaeology` and `active-work` only.

## Putting it together

A common order for a new team:

1. Flow 1 to save credentials and take the first snapshot.
2. Flow 3 to keep that snapshot fresh on a server.

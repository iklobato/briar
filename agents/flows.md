# Briar — end-to-end usage flows (cookbook)

Recipes that chain several `briar` commands into a real outcome. Most
flows deliberately combine **more than one feature** — a typical shape is
`secrets` (gate) → `extract` (mine) → `agent`/`plan` (act) → `context`
(inspect) → `journal`/`dashboard` (audit). Each flow lists the exact
shell calls with example parameters, a **Features combined** line so you
can see what's working together, and how to verify it. Flow 14 is the
capstone that strings the whole tool into one chain. Placeholders used
throughout:

| Placeholder | Example | Means |
|---|---|---|
| `<COMPANY>` | `acme` | company key — must match a runbook YAML / credential namespace |
| `<OWNER>` | `acme-co` | GitHub org / owner (or Bitbucket workspace) |
| `<REPO>` | `acme-app` | repository slug |
| `<PROJECT>` | `ACME` | tracker project key |

Per-command detail lives in the sibling files ([extract.md](extract.md),
[agent.md](agent.md), [plan.md](plan.md), …). When in doubt, append
`--help` — that output is the contract; this file is the opinionated
how-to-combine-them guide. All flows assume the global flags from
[README.md](README.md) (`--format`, `--verbose`).

---

## Flow 1 — Onboard a brand-new company from zero

Get credentials in place, prove coverage, then take the first cold
snapshot of the world.

```bash
# 1. Acquire per-provider credentials (each lands in ~/.config/briar/secrets.env)
briar auth login github-pat   --company <COMPANY>     # paste a PAT with repo + read:org
briar auth login aws-static   --company <COMPANY>     # paste an access key / secret
briar auth login jira-token   --company <COMPANY>     # paste a Jira API token + base URL
briar auth login fireflies    --company <COMPANY>     # paste your Fireflies API key

# 2. Prove every (company, extractor) pair in your runbooks is covered
briar secrets doctor --examples examples/

# 4. First cold extraction — everything available for the company
briar extract --company <COMPANY> \
    --repo <OWNER>/<REPO> \
    --ticket-project <PROJECT>
```

**Verify:** `briar secrets doctor` shows no `missing` rows; step 4 exits
`0` and `briar context get knowledge:<COMPANY>` returns non-empty markdown.

---

## Flow 2 — Extract AWS + Fireflies + PRs, then fix a PR

The headline flow, touching five features end to end: **secrets** (gate)
→ **extract** (mine infra + meetings + PRs) → **context** (inspect the
blob) → **agent** (fix the PR with the meeting spliced in) → **journal**
(audit what it decided).

```bash
# 1. secrets — gate on credential coverage before spending anything
briar secrets doctor --examples examples/

# 2. extract — one pass covering three sources into knowledge:<COMPANY>
briar extract --company <COMPANY> \
    --include aws-infra \
    --include meeting-digest \
    --include pr-archaeology \
    --repo <OWNER>/<REPO> \
    --aws-extract-region us-east-1 \
    --since-days 14

# 3. context — eyeball what landed before the agent reads it
briar context get knowledge:<COMPANY> | head -40

# 4. agent — address PR #128's open review comments, with the matching
#    Fireflies transcript fetched just-in-time into the prompt.
#    Add --dry-run first to preview the prompt + tools for free.
briar agent prfix --company <COMPANY> \
    --repo <OWNER>/<REPO> \
    --pr 128 --branch fix/login-retry \
    --meeting fireflies --meeting-query "login retry" --meeting-top-k 3 \
    --runbook examples/<COMPANY>.yaml

# 5. journal — audit the agent's decision trail
briar journal list --command-prefix agent.
briar journal show <SESSION_ID>
```

**Features combined:** `secrets` · `extract` (3 extractors) · `context` ·
`agent prfix` (+ JIT meeting context) · `journal`.

**Verify:** step 4 prints `agent-done`; the PR shows new commits + inline
replies prefixed `[AI] `; step 5 lists the session with its ordered
`DecisionEvent`s.

---

## Flow 3 — Implement a Jira ticket end-to-end

Clone, branch, code, test, open a draft PR — one ticket, autonomously —
combining **extract** → **agent implement** → **journal**, with a
**secrets** pre-check.

```bash
# 1. secrets — confirm this company's tracker + repo creds are present
briar secrets doctor --examples examples/

# 2. extract — fresh conventions + ticket context for the engineer agent
briar extract --company <COMPANY> \
    --include codebase-conventions --include active-tickets \
    --repo <OWNER>/<REPO> --ticket-project <PROJECT>

# 3. agent — implement one ticket (ticket-context fetched JIT for the key).
#    --keep-worktree leaves the tree in /tmp to inspect; --dry-run previews.
briar agent implement --company <COMPANY> \
    --repo <OWNER>/<REPO> \
    --ticket-project <PROJECT> --ticket-key <PROJECT>-412 \
    --tracker jira \
    --runbook examples/<COMPANY>.yaml

# 4. journal — read back exactly what the agent did and why
briar journal show <SESSION_ID>
```

**Features combined:** `secrets` · `extract` · `agent implement` (+ JIT
ticket context) · `journal`. For *many* tickets at once, graduate to the
plan loop in Flow 4.

**Verify:** exit `0`, `agent-done` logged, a new draft PR on a fresh
branch, and a journal session recording the decisions.

---

## Flow 4 — Build and run an implementation plan from a board

Turn a tracker board into an ordered plan, then let the selector→implement
→writeback loop ship it card by card — combining **extract** (knowledge),
**plan**, **context** (watch the plan-knowledge blob learn), **dashboard**
and **journal** (monitor + audit).

```bash
# 0. extract — refresh the knowledge the synthesiser will splice in
briar extract --company <COMPANY> --include codebase-conventions \
    --include active-tickets --repo <OWNER>/<REPO> --ticket-project <PROJECT>

# 1. plan build — board → ordered plan, with knowledge:<COMPANY> spliced in
briar plan build "https://github.com/orgs/<OWNER>/projects/7" \
    --company <COMPANY> --name q3-auth \
    --with-knowledge --llm anthropic --store postgres

# 2. plan — inspect what got synthesised before spending money
briar plan status q3-auth --company <COMPANY> --store postgres
briar plan next   q3-auth --company <COMPANY> --store postgres   # what the selector would pick

# 3. plan run — smoke ONE card end-to-end
briar plan run q3-auth \
    --repo <OWNER>/<REPO> \
    --tracker jira --tracker-project <PROJECT> \
    --llm anthropic --company <COMPANY> --store postgres \
    --limit 1

# 4. context — watch the plan-scoped knowledge blob the loop updates
#    after each card (KnowledgeWriter merges learnings into it)
briar context --store postgres get knowledge:<COMPANY>.q3-auth | tail -20

# 5. plan run — let the loop go wide; keep going past a failing card
briar plan run q3-auth \
    --repo <OWNER>/<REPO> \
    --tracker jira --tracker-project <PROJECT> \
    --llm anthropic --company <COMPANY> --store postgres \
    --continue-on-failure

# 6. dashboard + journal — monitor progress and audit each card's run
briar dashboard --examples examples/ --once
briar journal list --command-prefix plan.
```

**Features combined:** `extract` · `plan build/status/next/run` ·
`context` (plan-knowledge blob) · `dashboard` · `journal`.

**Verify:** `plan status` shows cards moving `pending → done`; the
`knowledge:<COMPANY>.q3-auth` blob grows after each card; each completed
card has a PR and a journal session. Exit `1` means the run finished with
blocked cards — read `plan status` to see which.

---

## Flow 5 — Account-wide AWS inventory, persisted on demand

Enumerate every tagged resource and keep a queryable, drift-tracked JSON
companion (the prompt blob stays small — counts only).

```bash
# Option A — ad-hoc, with the full inventory dumped to a JSON sidecar
briar extract --company <COMPANY> \
    --include aws-infra \
    --aws-extract-service tagging-inventory \
    --aws-extract-region us-east-1 \
    --out-json /tmp/<COMPANY>-aws.json

# Option B — scheduled, persisting the companion blob automatically.
#   In the runbook YAML, set on the company's knowledge binding:
#     knowledge:
#       store: postgres
#       name: knowledge:<COMPANY>
#       config: { inventory: "true" }
briar runbook extract examples/<COMPANY>.yaml

# Inspect the companion and watch it drift over time
briar context --store postgres list --prefix inventory:
briar context --store postgres get  inventory:<COMPANY> | jq '.sections[].data.resources | length'
```

**Verify:** the markdown `Resource inventory` section shows per-service
counts; `/tmp/<COMPANY>-aws.json` (or `inventory:<COMPANY>`) carries the
full per-resource rows (ARN, type, region, tags). Re-running only rewrites
the companion when resources actually change.

---

## Flow 6 — Run the scheduler 24/7 (production)

```bash
# 1. Confirm every runbook in the directory has its credentials
briar secrets doctor --examples /etc/briar/runbooks/

# 2. One-shot refresh of every company (good before a release)
briar runbook sweep /etc/briar/runbooks/

# 3. Stay alive and fire each (company, task) on its cron cadence
briar runbook serve /etc/briar/runbooks/
```

Typical systemd unit (the process never daemonises itself):

```ini
# /etc/systemd/system/briar-scheduler.service
[Service]
ExecStart=/usr/local/bin/briar runbook serve /etc/briar/runbooks/
Restart=on-failure
User=briar
EnvironmentFile=/etc/briar/secrets.env
```

**Verify:** `systemctl status briar-scheduler` is active; stdout logs
`scheduler: registered task=<name> next=<iso>`; after the first fire,
`briar journal list --command-prefix runbook.` shows new sessions.

---

## Flow 7 — Fix a PR using a specific meeting as the deciding input

When a reviewer's ask traces back to a particular call, pin that
transcript instead of keyword search.

```bash
# Preview the wiring for free (no LLM call)
briar agent prfix --company <COMPANY> \
    --repo <OWNER>/<REPO> \
    --pr 204 --branch feat/rate-limit \
    --meeting fireflies --meeting-key 01HXXXXMEETINGID \
    --meeting-max-bytes 8000 \
    --dry-run

# Re-run for real once the prompt looks right (drop --dry-run)
briar agent prfix --company <COMPANY> \
    --repo <OWNER>/<REPO> \
    --pr 204 --branch feat/rate-limit \
    --meeting fireflies --meeting-key 01HXXXXMEETINGID \
    --runbook examples/<COMPANY>.yaml
```

**Verify:** the dry-run output shows a `## Meeting context` section in the
system prompt containing the expected transcript.

---

## Flow 8 — Cost-safe agent rollout (free → one card → wide)

Three escalating gates so you never discover a misconfiguration at scale.

```bash
# 1. FREE — render the exact prompt + tool list, skip the LLM entirely
briar agent implement --company <COMPANY> \
    --repo <OWNER>/<REPO> \
    --ticket-project <PROJECT> --ticket-key <PROJECT>-77 \
    --tracker jira --dry-run

# 2. ONE paid card through the plan loop
briar plan run q3-auth --repo <OWNER>/<REPO> \
    --tracker jira --tracker-project <PROJECT> \
    --llm anthropic --company <COMPANY> --limit 1

# 3. GO WIDE
briar plan run q3-auth --repo <OWNER>/<REPO> \
    --tracker jira --tracker-project <PROJECT> \
    --llm anthropic --company <COMPANY> --continue-on-failure
```

**Verify:** step 1 prints `system_prompt_bytes=… tool_count=…` and makes
zero API calls; step 2 produces exactly one PR before you commit to step 3.

---

## Flow 9 — Bitbucket + Linear stack (no GitHub, no Jira)

Every provider is swappable by a flag — the extractors and agents don't
hard-code a vendor.

```bash
# 1. Credentials for the non-default vendors
briar auth login bitbucket-app-password --company <COMPANY>
briar auth login linear-api-key         --company <COMPANY>

# 2. Mine Bitbucket PRs
briar extract --company <COMPANY> \
    --include pr-archaeology --include codebase-conventions \
    --provider bitbucket --repo <OWNER>/<REPO>

# 3. Implement a Linear ticket against a Bitbucket repo
briar agent implement --company <COMPANY> \
    --repo <OWNER>/<REPO> --provider bitbucket \
    --ticket-project ENG --ticket-key ENG-7 --tracker linear
```

**Verify:** extraction's `Active work` / `PR archaeology` sections render
Bitbucket PRs; the agent opens a Bitbucket PR.

---

## Flow 10 — Fan out across many repos, boards, and authors

Repeatable flags compose: `--repo` per repo (canonical), `--ticket-project`
per board (the per-extractor override for tracker extractors, since their
project keys differ in shape from `owner/repo`), author allow/block as
`allow ∩ ¬block`.

```bash
briar extract --company <COMPANY> \
    --include pr-archaeology --include active-tickets --include reviewer-profile \
    --repo <OWNER>/web --repo <OWNER>/api --repo <OWNER>/infra \
    --ticket-project <PROJECT> --ticket-project OPS \
    --authors-allow alice --authors-allow bob \
    --authors-block dependabot --authors-block renovate
```

**Verify:** the blob has one PR section spanning all three repos and one
ticket section spanning both projects; bot PRs are absent.

---

## Flow 11 — Scaffold a downstream automation config

Emit a JSON config bundle another system (CI, a webhook handler) can drive.

```bash
# Issue → plan → approve → implement, triggered by a GitHub webhook
briar scaffold implementation \
    --prefix <COMPANY> \
    --source jira --jira-project <PROJECT> \
    --archetype engineer --shape plan-approve-act \
    --trigger-kind github_webhook \
    --owner <OWNER> --repo <REPO> \
    --out <COMPANY>-impl.json

# A no-human-gate PR-fixer bundle
briar scaffold pr-fixes --prefix <COMPANY> \
    --owner <OWNER> --repo <REPO> \
    --out <COMPANY>-prfix.json
```

**Verify:** the `--out` file is valid JSON; `jq . <COMPANY>-impl.json`
shows the archetype, shape, trigger, and source you selected.

---

## Flow 12 — Operate & audit (dashboard + journal)

```bash
# Snapshot the droplet/runbook state once (CI-friendly), then exit
briar dashboard --examples examples/ --once

# Or serve the read-only HTML status page on loopback
briar dashboard --examples examples/ --host 127.0.0.1 --port 8080

# Inspect what any command decided and why
briar journal list --command-prefix agent.
briar journal show <SESSION_ID>
briar journal export <SESSION_ID> --format json | jq '.events[].decision'
```

**Verify:** the dashboard renders companies/schedules/knowledge sizes;
`journal show` prints the ordered `DecisionEvent`s for the run.

---

## Flow 13 — Direct knowledge & memory management

The store is content-agnostic — read/write any blob by name.

```bash
# Seed a free-form memory blob the agents can splice
briar context put memory:reviewer-alice \
    --content "Alice always asks for a regression test + a changelog entry."

# Or load one from a file
briar context put knowledge:<COMPANY>.house-style --from-file ./house-style.md

# Read / enumerate / clean up
briar context get  knowledge:<COMPANY>
briar context list --prefix knowledge:
briar context list --prefix inventory:
briar context delete memory:reviewer-alice

# Everything above works against shared postgres truth instead of disk
briar context --store postgres list --prefix knowledge:
```

**Verify:** `context get` round-trips what you `put`; `context categories`
shows the distinct prefixes in use.

---

## Flow 14 — Full lifecycle in one sitting (onboard → schedule → ship → audit)

The capstone: every major feature in one chain, taking a company from no
credentials to merged AI-authored PRs with an audit trail.

```bash
# 1. auth + secrets — get credentials in, prove coverage
briar auth login github-pat --company <COMPANY>
briar auth login jira-token --company <COMPANY>
briar auth login aws-static --company <COMPANY>
briar auth login fireflies  --company <COMPANY>
briar secrets doctor --examples examples/

# 2. runbook — stand up scheduled extraction so knowledge stays fresh
#    (knowledge.config.inventory: "true" in the YAML also persists the
#     AWS inventory companion — see Flow 5)
briar runbook sweep examples/                 # one-shot refresh now
# briar runbook serve examples/               # ...or run the daemon

# 3. context — confirm the knowledge + inventory blobs exist
briar context --store postgres list --prefix knowledge:
briar context --store postgres list --prefix inventory:

# 4. plan — board → ordered plan, knowledge spliced in
briar plan build "https://github.com/orgs/<OWNER>/projects/7" \
    --company <COMPANY> --name q3-auth --with-knowledge \
    --llm anthropic --store postgres

# 5. plan run — ship the plan card by card (engineer agent per card)
briar plan run q3-auth --repo <OWNER>/<REPO> \
    --tracker jira --tracker-project <PROJECT> \
    --llm anthropic --company <COMPANY> --store postgres \
    --continue-on-failure

# 6. agent prfix — when a human reviews a shipped PR, address the comments
briar agent prfix --company <COMPANY> --repo <OWNER>/<REPO> \
    --pr 131 --branch q3-auth/card-3 \
    --meeting fireflies --meeting-query "auth review" \
    --runbook examples/<COMPANY>.yaml

# 7. dashboard + journal — monitor the estate and audit every decision
briar dashboard --examples examples/ --once
briar journal list --command-prefix plan.
briar journal list --command-prefix agent.
```

**Features combined:** `auth` · `secrets` · `runbook` (sweep/serve) ·
`extract` (via runbook) · `context` · `plan build/run` · `agent prfix` ·
`dashboard` · `journal` — the whole tool in nine commands.

---

## Composing flows

These chain naturally:

- **Daily loop:** Flow 6 (scheduler) keeps `knowledge:<COMPANY>` fresh →
  Flow 4 (plan run) ships cards against it → Flow 12 audits the result.
- **Reactive PR fix:** a webhook fires Flow 2/7 (prfix) using the latest
  scheduled knowledge, no manual extract needed.
- **Onboarding → first value:** Flow 1 → Flow 2 → Flow 3 takes a new
  company from no credentials to a merged AI-authored PR.

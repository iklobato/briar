# Briar implementation plan — multi-company setup from zero

End-to-end guide for standing up `briar-cli` against one or more
companies, including how to acquire every credential each provider
needs. Intended to be self-contained for an operator (human or
agent) who has the repo cloned but nothing configured.

Pair this with [`examples/multi_company.yaml`](examples/multi_company.yaml)
and [`examples/multi_company.env.example`](examples/multi_company.env.example) —
they show the same three example companies (`acme`, `bitspark`,
`datacore`) that this document references.

---

## Table of contents

1. [What you'll end up with](#1-what-youll-end-up-with)
2. [Prerequisites](#2-prerequisites)
3. [Install + verify](#3-install--verify)
4. [Per-provider credential acquisition](#4-per-provider-credential-acquisition)
   1. [GitHub PAT](#41-github-pat)
   2. [Bitbucket Cloud app password](#42-bitbucket-cloud-app-password)
   3. [Jira API token](#43-jira-api-token)
   4. [Linear API key](#44-linear-api-key)
   5. [AWS IAM / STS credentials](#45-aws-iam--sts-credentials)
   6. [GCP service account](#46-gcp-service-account)
   7. [Azure subscription + identity](#47-azure-subscription--identity)
   8. [Telegram bot + chat ID](#48-telegram-bot--chat-id)
   9. [Slack incoming webhook](#49-slack-incoming-webhook)
   10. [SMTP for the email sink](#410-smtp-for-the-email-sink)
   11. [PagerDuty integration key](#411-pagerduty-integration-key)
   12. [LLM auth (Anthropic / OpenAI / Gemini / Bedrock)](#412-llm-auth)
   13. [Credential-store backends (Vault / AWS Secrets Manager / SSM)](#413-credential-store-backends)
5. [Wire up `secrets.env`](#5-wire-up-secretsenv)
6. [Write the runbook YAML](#6-write-the-runbook-yaml)
7. [Validate with `briar secrets doctor`](#7-validate-with-briar-secrets-doctor)
8. [First extract run](#8-first-extract-run)
9. [Run the scheduler 24/7](#9-run-the-scheduler-247)
10. [Common failures + diagnosis](#10-common-failures--diagnosis)
11. [Adding a new company](#11-adding-a-new-company)
12. [Adding a new vendor](#12-adding-a-new-vendor)

---

## 1. What you'll end up with

- `/etc/briar/secrets.env` — `mode 0600`, holds every credential. Read
  by systemd's `EnvironmentFile=`.
- `examples/*.yaml` — one runbook per company (or one combined runbook
  like `multi_company.yaml`).
- Two long-lived processes via systemd:
  - `briar-scheduler.service` — runs `briar runbook serve examples/`
  - `briar-dashboard.service` — runs `briar dashboard` on port 8080
- A per-company markdown blob under `./knowledge/` (or in Postgres
  when `BRIAR_DATABASE_URL` is set).
- Optional: `briar agent` jobs invoked manually or on a cron, using
  the LLM provider of your choice.
- Optional: scheduler failure alerts to Telegram / Slack / Email /
  PagerDuty via `$BRIAR_NOTIFY_SINKS`.

See [`DEPLOY_EC2.md`](DEPLOY_EC2.md) for the systemd deployment
recipe; this document focuses on credentials + configuration.

---

## 2. Prerequisites

- **Python 3.10+** with `pip install -e .` working in a venv.
- **Operator access** to every system you intend to extract from:
  GitHub org admin (for PAT scopes), Bitbucket workspace admin (for
  app passwords), Atlassian admin (for Jira API tokens), etc.
- **AWS account** if any extractor uses `aws-infra` and you don't
  already have STS/IAM keys.
- **Cloud SDK installs** opt-in via pip extras when applicable
  ([§3](#3-install--verify)).
- **A throwaway test runbook** with one cheap extractor per
  credential type (e.g. `active-work` against one repo) for the
  validation step in [§7](#7-validate-with-briar-secrets-doctor).

---

## 3. Install + verify

```bash
git clone git@github.com:iklobato/briar-cli.git
cd briar-cli
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .                       # base install
briar version                          # should print briar-cli 1.1.0
```

Optional extras — install only what you'll actually use. Each adapter
fails loudly if its SDK is missing, with the right install command
in the error message:

```bash
pip install -e '.[openai]'    # OpenAI LLM
pip install -e '.[gemini]'    # Google Gemini LLM
pip install -e '.[vault]'     # HashiCorp Vault credential store
pip install -e '.[gcp]'       # GCP cloud provider (~80 MB)
pip install -e '.[azure]'     # Azure cloud provider (~40 MB)
pip install -e '.[all]'       # everything above
```

Base install always works for: Anthropic LLM, AWS Bedrock LLM,
GitHub/Bitbucket repo + tracker, Jira, Linear, AWS cloud, AWS
Secrets Manager / SSM, file + Postgres storage, Telegram/Slack/Email/PagerDuty
notifications.

---

## 4. Per-provider credential acquisition

Each subsection lists: **(a)** the env-var names Briar reads,
**(b)** how to obtain the credential, **(c)** what scope/permissions
to grant.

> **Convention:** `{c}` in an env-var name is the **company key from
> the runbook YAML**, uppercased, hyphens turned into underscores.
> So a company named `widget-co` resolves to
> `AWS_WIDGET_CO_ACCESS_KEY_ID`.

### 4.1 GitHub PAT

| Env var | Used by |
|---|---|
| `GITHUB_TOKEN` | All GitHub extractors + tracker (workspace-wide, no `{c}`) |

**Obtain:**

1. https://github.com/settings/tokens → **Generate new token (classic)**.
2. Scopes: `repo` (full), `read:org` (for Issues/PRs in private repos),
   `workflow` (only if you'll use `briar agent` to push branches).
3. Set an expiration ≤ 90 days; rotate via `gh auth token` or a new
   PAT before then.

**Verify:**

```bash
export GITHUB_TOKEN=ghp_xxx
curl -sH "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user | jq .login
```

Should print your GitHub login.

### 4.2 Bitbucket Cloud app password

| Env var | Used by |
|---|---|
| `BITBUCKET_{c}_USERNAME` | per-company Bitbucket repo + tracker |
| `BITBUCKET_{c}_APP_PASSWORD` | per-company auth |
| `BITBUCKET_{c}_WORKSPACE` | default workspace slug for bare repo names |

**Obtain:**

1. Log in as the **machine user** the company has provisioned (NOT
   your personal account — app passwords are user-scoped).
2. https://bitbucket.org/account/settings/app-passwords/ → **Create
   app password**.
3. Scopes (minimum for Briar's read flows):
   - `Repositories: Read`
   - `Issues: Read`
   - `Pull requests: Read`
   - `Pipelines: Read` (if `github-deployments` extractor is enabled
     against this provider)
4. Copy the password **immediately** — Bitbucket won't show it again.

**Verify:**

```bash
curl -su "$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD" \
  https://api.bitbucket.org/2.0/user | jq .username
```

### 4.3 Jira API token

| Env var | Used by |
|---|---|
| `JIRA_{c}_URL` | e.g. `https://acme.atlassian.net` |
| `JIRA_{c}_EMAIL` | bot user's email (used as the basic-auth username) |
| `JIRA_{c}_TOKEN` | Atlassian API token (NOT the user's password) |

**Obtain:**

1. Sign into Atlassian as the **machine user**.
2. https://id.atlassian.com/manage-profile/security/api-tokens →
   **Create API token**.
3. Label it `briar-<company>` so rotation is traceable.
4. Copy the token immediately.

The token works for both Jira and Confluence; it inherits the user's
own permissions, so the bot user needs at least `Browse Projects`
on every project you'll extract from.

**Verify:**

```bash
curl -su "$JIRA_EMAIL:$JIRA_TOKEN" \
  "$JIRA_URL/rest/api/3/myself" | jq .displayName
```

### 4.4 Linear API key

| Env var | Used by |
|---|---|
| `LINEAR_{c}_TOKEN` | LinearTracker |

**Obtain:**

1. https://linear.app/settings/api → **Create key**.
2. Label it `briar-<company>`.
3. Scope: at minimum `Read` on the workspace.

**Quirk:** Linear's `Authorization` header takes the key **as-is** —
NO `Bearer ` prefix. Briar's `LinearTracker` already handles that.

**Verify:**

```bash
curl -s -H "Authorization: $LINEAR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ viewer { name } }"}' \
  https://api.linear.app/graphql | jq .data.viewer.name
```

### 4.5 AWS IAM / STS credentials

| Env var | Used by |
|---|---|
| `AWS_{c}_ACCESS_KEY_ID` | per-company AWS auth |
| `AWS_{c}_SECRET_ACCESS_KEY` | |
| `AWS_{c}_SESSION_TOKEN` | only if STS-based (SSO, AssumeRole) |
| `AWS_{c}_REGION` | default region for that company |

**Obtain (three paths, pick what your org supports):**

**Path A — IAM access keys (simplest, least secure):**

1. AWS console → IAM → Users → `briar-<company>` → Security
   credentials → Create access key.
2. Attach a policy with `ReadOnlyAccess` (or a tighter custom policy
   if your security team requires it — see the policy template in
   [`DEPLOY_EC2.md`](DEPLOY_EC2.md#4-iam-instance-profile)).
3. No `SESSION_TOKEN` needed.

**Path B — SSO-vended STS triplet (most common):**

1. `aws sso login --profile <company>` (whatever your local profile
   is named).
2. Export the resulting STS triplet via the rotate one-liner
   documented in the README (`### Refreshing secrets`).
3. Triplet expires when the SSO session does — rotate when needed.

**Path C — cross-account AssumeRole (cleanest for multi-tenant):**

1. The briar instance's role has `sts:AssumeRole` on each target
   account's read-only role.
2. Set `AWS_{c}_ROLE_ARN=arn:aws:iam::<account>:role/briar-reader`.
3. The `AwsCloudProvider` adapter would need to be extended to honour
   that env var — currently it expects a static triplet OR ambient
   ADC, not explicit AssumeRole. (TODO: extend, see issue tracker.)

**Verify:**

```bash
AWS_ACCESS_KEY_ID=$AWS_ACME_ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=$AWS_ACME_SECRET_ACCESS_KEY \
AWS_SESSION_TOKEN=$AWS_ACME_SESSION_TOKEN \
aws sts get-caller-identity
```

Should print the account ID + ARN of the IAM identity.

### 4.6 GCP service account

| Env var | Used by |
|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | path to SA JSON (workspace-wide) |

**Obtain:**

1. https://console.cloud.google.com/iam-admin/serviceaccounts → pick
   the GCP project the company uses → **Create service account**.
2. Name: `briar-extractor`.
3. Roles (minimum for the `aws-infra` extractor when `cloud: gcp`):
   - `Cloud Run Viewer` (compute listing)
   - `Cloud SQL Viewer` (databases)
   - `Pub/Sub Viewer` (queues)
   - `Logs Viewer` (log groups)
4. Keys tab → **Add key → Create new key → JSON**. Download.
5. Place the JSON on the host (root-owned, `mode 0600`):
   ```bash
   sudo install -m 600 ~/Downloads/sa.json /etc/briar/gcp-<company>-sa.json
   ```
6. Set `GOOGLE_APPLICATION_CREDENTIALS=/etc/briar/gcp-<company>-sa.json`
   in `secrets.env`.

**Cross-project caveat:** GCP service accounts are project-scoped. If
you're extracting multiple GCP companies you need one SA + one JSON
per project. The current `GcpCloudProvider` reads `GOOGLE_APPLICATION_CREDENTIALS`
once — extending it to switch SA files per-company is a small change.

**Required pip extra:** `pip install briar-cli[gcp]`

**Verify:**

```bash
gcloud auth activate-service-account --key-file=/etc/briar/gcp-acme-sa.json
gcloud projects list
```

### 4.7 Azure subscription + identity

| Env var | Used by |
|---|---|
| `AZURE_CLIENT_ID` | Service-principal app ID |
| `AZURE_TENANT_ID` | AAD tenant |
| `AZURE_CLIENT_SECRET` | Service-principal password |

**Obtain (service principal — simplest):**

1. `az login` as a tenant admin.
2. Create the SP:
   ```bash
   az ad sp create-for-rbac \
     --name briar-<company> \
     --role Reader \
     --scopes /subscriptions/<sub-id>
   ```
3. The output JSON contains `appId`, `password`, `tenant` — map them
   to the three env vars above.
4. Subscription ID becomes `cloud_profile` in the runbook YAML.

**Required pip extra:** `pip install briar-cli[azure]`

**Verify:**

```bash
AZURE_CLIENT_ID=... AZURE_TENANT_ID=... AZURE_CLIENT_SECRET=... \
  az login --service-principal -u $AZURE_CLIENT_ID -p $AZURE_CLIENT_SECRET --tenant $AZURE_TENANT_ID
az account show
```

### 4.8 Telegram bot + chat ID

| Env var | Used by |
|---|---|
| `TELEGRAM_BOT_TOKEN` | workspace-wide bot |
| `TELEGRAM_{c}_CHAT_ID` | per-tenant channel/group ID |

**Obtain:**

1. Talk to `@BotFather` on Telegram → `/newbot` → follow prompts.
2. BotFather returns a token like `1234567890:AAA-...`. Save it.
3. Create a Telegram channel/group, **add the bot as admin** (it
   can't post without admin rights), and grab the chat ID:
   - For groups: send a message in the group, then
     `curl -s https://api.telegram.org/bot$TOKEN/getUpdates | jq '.result[-1].message.chat.id'`
   - For channels: chat IDs are negative numbers like `-1001234567890`.

**Verify:**

```bash
curl -s -X POST \
  -d "chat_id=$TELEGRAM_CHAT_ID" \
  -d "text=test from briar" \
  https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage | jq .ok
```

### 4.9 Slack incoming webhook

| Env var | Used by |
|---|---|
| `SLACK_{c}_WEBHOOK_URL` | per-tenant Slack channel |

**Obtain:**

1. https://api.slack.com/apps → **Create New App** → From scratch.
2. Features → Incoming Webhooks → Activate → **Add New Webhook to
   Workspace** → pick the channel.
3. The webhook URL itself is the credential. Treat it as a secret.

**Verify:**

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"text": "test from briar"}' \
  "$SLACK_WEBHOOK_URL"
```

Returns `ok` on success.

### 4.10 SMTP for the email sink

| Env var | Used by |
|---|---|
| `SMTP_HOST` / `SMTP_PORT` (default 587) | workspace-wide SMTP |
| `SMTP_USER` / `SMTP_PASSWORD` | auth |
| `SMTP_STARTTLS` (default `true`) | toggle |
| `EMAIL_FROM` | sender address |
| `EMAIL_{c}_TO` | comma-separated per-tenant recipients |

**Obtain:**

- Either a hosted relay (SendGrid, Mailgun, AWS SES) — sign up,
  create an API user, use SMTP creds from their setup wizard.
- Or your company's own SMTP — ask IT.

**Verify:**

```bash
python -c "
import smtplib
from email.message import EmailMessage
m = EmailMessage(); m['Subject']='test'; m['From']='$EMAIL_FROM'; m['To']='$EMAIL_ACME_TO'
m.set_content('test from briar')
with smtplib.SMTP('$SMTP_HOST', 587) as s:
  s.starttls(); s.login('$SMTP_USER','$SMTP_PASSWORD'); s.send_message(m)
"
```

### 4.11 PagerDuty integration key

| Env var | Used by |
|---|---|
| `PAGERDUTY_{c}_ROUTING_KEY` | per-tenant Events API integration |

**Obtain:**

1. PagerDuty → Services → pick or create a service for the company.
2. **Integrations** tab → Add an integration → **Events API V2**.
3. Copy the **Integration Key** (32-char hex string).

**Verify:**

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{
    "routing_key": "'"$PAGERDUTY_ROUTING_KEY"'",
    "event_action": "trigger",
    "payload": {"summary": "test from briar", "source": "test", "severity": "info"}
  }' \
  https://events.pagerduty.com/v2/enqueue
```

Should return `{"status":"success", ...}`. Resolve the resulting
incident manually so it doesn't sit open.

### 4.12 LLM auth

Only needed if you'll run `briar agent`. Pick one provider per agent
run via `--llm-kind`.

**Anthropic (default):**

- `CLAUDE_CODE_OAUTH_TOKEN` — subscription billing via Claude Code's
  OAuth flow. Run `claude` once; the token lands at
  `~/.claude/token` and can be exported via
  `claude config get` or by hand.
- OR `ANTHROPIC_API_KEY` — pay-as-you-go via console.anthropic.com.

**OpenAI:** `OPENAI_API_KEY` from platform.openai.com (requires
`pip install briar-cli[openai]`).

**Gemini:** `GEMINI_API_KEY` from aistudio.google.com (requires
`pip install briar-cli[gemini]`).

**Bedrock:** no separate key — uses the ambient AWS credential
chain. Just make sure the IAM identity has
`bedrock:InvokeModel` on the model IDs you'll use.

### 4.13 Credential-store backends

The default `EnvFileStore` needs no setup beyond `secrets.env`. The
others need a one-time bootstrap.

**AWS Secrets Manager** (`pip install` not needed — uses boto3):

```bash
aws secretsmanager create-secret \
  --name briar/GITHUB_TOKEN \
  --secret-string "$GITHUB_TOKEN"
# repeat for each credential
```

`briar secrets doctor --store aws-secretsmanager` then reads from
`/briar/<NAME>` paths.

**SSM Parameter Store** (cheaper than Secrets Manager for high-volume reads):

```bash
aws ssm put-parameter \
  --name /briar/GITHUB_TOKEN \
  --value "$GITHUB_TOKEN" \
  --type SecureString
```

**Vault** (requires `pip install briar-cli[vault]`):

```bash
export VAULT_ADDR=https://vault.example.com:8200
export VAULT_TOKEN=hvs.xxx
vault kv put secret/briar/GITHUB_TOKEN value="$GITHUB_TOKEN"
```

---

## 5. Wire up `secrets.env`

```bash
sudo mkdir -p /etc/briar
sudo install -m 750 -o root -g briar -d /etc/briar
sudo cp examples/multi_company.env.example /etc/briar/secrets.env
sudo chmod 600 /etc/briar/secrets.env
sudo chown root:briar /etc/briar/secrets.env
sudoedit /etc/briar/secrets.env    # fill in real values
```

Confirm it's readable by the `briar` service user, NOT by other
users:

```bash
sudo -u briar cat /etc/briar/secrets.env | head -1  # should work
sudo -u nobody cat /etc/briar/secrets.env 2>&1      # should fail with EACCES
```

---

## 6. Write the runbook YAML

Start from [`examples/multi_company.yaml`](examples/multi_company.yaml)
— copy it, then **delete companies you don't have**. For each
company you keep:

1. Rename the top-level key (`acme:` → `<your-company-slug>:`). Use
   lowercase + hyphens.
2. Pick a knowledge store: `file` (simple) or `postgres` (multi-reader).
3. Pick which extractors to run and on what cadence. Cadence DSL:
   - `"minute"` / `"N minutes"`
   - `"hour"` / `"N hours"` / `"hour at :MM"`
   - `"day"` / `"day at HH:MM"`
   - `"monday at HH:MM"` (and every other weekday)
4. For each extractor entry, set `provider:` / `tracker:` / `cloud:`
   to the vendor the company uses.
5. Use repo / project / workspace names that match what's in the
   actual vendor.
6. **Optional:** add a `messages:` block to declare outbound channels
   the agent can use via the typed `send_message` tool:

   ```yaml
   companies:
     acme:
       knowledge: { store: file, name: ./knowledge/acme.md }
       messages:
         ticket_comment: {kind: jira-comment}
         pr_reply:       {kind: github-pr-comment}
         ops_chat:       {kind: slack-channel}
         escalation:     {kind: telegram-chat}
       schedules: [...]
   ```

   Available writer kinds: `jira-comment`, `jira-transition`,
   `slack-channel`, `telegram-chat`, `github-pr-comment`,
   `bitbucket-pr-comment`. Each one has its own `required_env_vars`
   — see `briar secrets doctor` output for coverage.

   When `briar agent prfix` / `briar agent implement` is invoked with
   `--runbook <yaml>`, the agent reads this block and binds the
   `send_message` tool to the configured handles. Without the block,
   the agent falls back to the bash escape hatch (`gh pr comment`,
   `curl`).

Validate the YAML against the schema BEFORE running:

```bash
python -c "
from pathlib import Path
from briar.iac.runbook import load_runbook_file
rb = load_runbook_file(Path('examples/your_company.yaml'))
print(f'companies: {sorted(rb.companies.keys())}')
"
```

A bad YAML fails with a locator-aware Pydantic error pointing at
the line.

---

## 7. Validate with `briar secrets doctor`

Audit every `(company × extractor × provider)` tuple AND every
`(company × messages × writer)` tuple for missing credentials,
without printing values:

```bash
source /etc/briar/secrets.env
briar secrets doctor --examples examples/
```

Sample output:

```
=== acme (all_features.yaml) ===
  ok pr-archaeology (provider=github)
  ok aws-infra (provider=aws)
  ok codebase-conventions (provider=github)
  ok github-deployments (provider=github)
  ok active-work (provider=github)
  X  active-tickets (provider=jira) — MISSING: JIRA_ACME_URL, JIRA_ACME_EMAIL, JIRA_ACME_TOKEN
  ok messages.pr_reply (kind=github-pr-comment)
  X  messages.ticket_comment (kind=jira-comment) — MISSING: JIRA_ACME_URL, JIRA_ACME_EMAIL, JIRA_ACME_TOKEN
  X  messages.ops_chat (kind=slack-channel) — MISSING: SLACK_ACME_WEBHOOK_URL
  X  messages.escalation (kind=telegram-chat) — MISSING: TELEGRAM_BOT_TOKEN, TELEGRAM_ACME_CHAT_ID
```

Exits non-zero if anything is `X`. Fix every red row before the
first scheduled run.

To audit against a remote credential store (e.g. after migrating
off `secrets.env`):

```bash
briar secrets doctor --store aws-secretsmanager --examples examples/
briar secrets doctor --store vault --examples examples/
```

---

## 8. First extract run

One-shot, for one company:

```bash
briar runbook extract examples/multi_company.yaml --task prfix
```

This runs the `prfix` task across every company (filtering by task
name, not company). Watch the log for `extractor-ok` lines per
company; `extractor-skip: is_available() returned False` indicates
missing creds (re-run the doctor).

After it finishes:

```bash
ls -la ./knowledge/
cat ./knowledge/acme.md | head -50    # or whichever company you ran
```

You should see a markdown blob with the per-task heading.

### Agent invocations (optional — for autonomous flows)

```bash
# pr-fixer archetype: clones the PR's branch, fetches the JIT
# pr-review-context (all comments + CI failures with log tails),
# drives an LLM tool-use loop. --dry-run prints the rendered prompt
# without spending tokens.
briar agent prfix \
    --company acme --owner X --repo Y \
    --pr 42 --branch fix-x \
    --runbook examples/all_features.yaml \
    --dry-run

# engineer archetype: clones the default branch, fetches the JIT
# ticket-context (full body + ACs + comments), drives the LLM to
# implement + push + open a PR.
briar agent implement \
    --company acme --owner X --repo Y \
    --ticket-project ACME --ticket-key ACME-42 \
    --tracker jira \
    --runbook examples/all_features.yaml
```

`--runbook <yaml>` loads the company's `messages:` block and binds
the agent's `send_message` tool. Without it, the agent falls back
to the bash escape hatch (`gh pr comment`, `curl`).

---

## 9. Run the scheduler 24/7

See [`DEPLOY_EC2.md`](DEPLOY_EC2.md) for the full systemd recipe.
Short version:

```bash
sudo cp <repo>/services/briar-scheduler.service /etc/systemd/system/
sudo cp <repo>/services/briar-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now briar-scheduler briar-dashboard
sudo systemctl status briar-scheduler briar-dashboard --no-pager
```

Both units pick up `EnvironmentFile=/etc/briar/secrets.env`, so all
the creds are loaded automatically.

### Enabling failure notifications

Add to `/etc/briar/secrets.env`:

```bash
BRIAR_NOTIFY_SINKS=telegram,slack
```

Restart the scheduler and the next failed extract dispatches an
alert to every named sink. One broken sink doesn't crash the
scheduler; each is fire-and-forget.

To smoke-test the notification path end-to-end without waiting for
a real failure, point one extractor at a deliberately-broken repo:

```yaml
- name: pr-archaeology
  args:
    pr_repo: [nonexistent-org/nonexistent-repo]
```

Run once, check the configured sinks for the alert, then revert.

---

## 10. Common failures + diagnosis

| Symptom | Likely cause | Fix |
|---|---|---|
| `extractor-skip: is_available() returned False` | Missing env vars for that provider | Run `briar secrets doctor`; fix every `X` |
| `extractor-failed: ... raised` followed by 401 | Token expired or scope-too-narrow | Rotate; re-check scope list in [§4](#4-per-provider-credential-acquisition) |
| `ERROR: invalid runbook ... Input should be 'pr-archaeology', ...` | YAML used an extractor name not in the Pydantic `Literal`; or a typo | Check spelling; if you added a new extractor, update `iac/runbook/models.py` |
| Dashboard shows empty knowledge blobs | Extractors all skipped — usually missing creds | Re-run doctor; check `journalctl -u briar-scheduler -n 100` |
| `pg-store connect transient failure ... reserved for SUPERUSER` | DO managed Postgres hit slot limit | Briar retries 3× automatically; if persistent, upgrade tier or move to file backend |
| Telegram alert never arrives | Bot isn't an admin in the channel; or chat ID is wrong sign | Re-add bot as admin; verify chat ID via `getUpdates` |
| Slack alert returns non-`ok` body | Webhook URL revoked; or workspace removed the integration | Recreate webhook in Slack app settings |
| `RuntimeError: openai package not installed` | Tried to use OpenAI LLM without the extra | `pip install briar-cli[openai]` |
| GCP extractor returns empty subsections | SA lacks required roles | Add `Cloud Run Viewer` / `Cloud SQL Viewer` / etc. per [§4.6](#46-gcp-service-account) |

For deeper debugging:

```bash
sudo journalctl -u briar-scheduler --since '10 min ago' --no-pager
sudo tail -n 200 /var/log/briar/scheduler.log
BRIAR_VERBOSE=1 briar runbook extract examples/multi_company.yaml --task prfix
```

`BRIAR_LIB_DEBUG=1` additionally surfaces third-party (boto3, httpx,
…) loggers.

---

## 11. Adding a new company

1. Pick a slug (lowercase, hyphens OK): e.g. `widget-co`.
2. Add a section under `companies:` in the runbook YAML.
3. Add the per-company env vars to `secrets.env` using the
   `{c}` → `WIDGET_CO` convention.
4. `briar secrets doctor` to verify coverage.
5. `briar runbook extract <yaml> --task prfix` to smoke-test.
6. Restart `briar-scheduler` (`sudo systemctl restart briar-scheduler`).

The scheduler picks up new companies on the next `daemon-reload` +
restart cycle — schedules don't reload at runtime.

---

## 12. Adding a new vendor

Beyond the six already shipped (GitHub, Bitbucket, Jira, Linear, AWS,
GCP, Azure, Telegram, Slack, Email, PagerDuty, Anthropic, OpenAI,
Gemini, Bedrock, Vault, etc.) — the Strategy + Registry pattern
makes adding more a one-file change. Example for a hypothetical
GitLab repo provider:

```
src/briar/extract/_providers/gitlab.py     # new: GitlabProvider(RepositoryProvider)
src/briar/extract/_providers/__init__.py   # tuple += (GitlabProvider,)
```

Inside `gitlab.py`:

```python
class GitlabProvider(RepositoryProvider):
    kind = "gitlab"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._token = CredEnv.GITLAB_TOKEN.read(company=company)
        # ...

    def is_available(self) -> bool:
        return bool(self._token)

    def list_pulls(self, repo, *, state, max_count):
        # translate GitLab MR JSON → PullRequest dataclass
        ...
```

Then add `GITLAB_TOKEN = "GITLAB_{c}_TOKEN"` to `CredEnv`, register
the cred requirement in `commands/secrets.py:_EXTRACTOR_REQUIREMENTS`,
and you're done. Zero edits to any extractor or the executor.

Tracker / Cloud / LLM / NotificationSink / CredentialStore additions
follow the exact same shape — see [`README.md § The provider ABCs`](README.md)
for the full inventory.

---

## Final checklist before declaring done

- [ ] `briar version` prints the right version on the host.
- [ ] `/etc/briar/secrets.env` exists, `mode 0600`, owner `root:briar`.
- [ ] `briar secrets doctor --examples examples/` exits 0.
- [ ] `briar runbook extract <yaml> --task prfix` succeeds for at
      least one task and writes a markdown blob.
- [ ] `systemctl status briar-scheduler` reports `active (running)`.
- [ ] `systemctl status briar-dashboard` reports `active (running)`.
- [ ] `curl -sI http://<host>:8080/` returns `200 OK`.
- [ ] If `$BRIAR_NOTIFY_SINKS` is set: a deliberate failure
      successfully delivered an alert to every named sink.
- [ ] All in-place credentials rotated to their per-company-bot
      identities (no personal tokens).

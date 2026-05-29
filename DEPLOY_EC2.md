# Deploy `briar-cli` to EC2 (24/7)

Instructions for an agent (or human) running a fresh deploy of this repo onto a brand-new Ubuntu EC2 instance. The result is two `systemd`-managed processes: the scheduler (`briar runbook serve`) and the dashboard (`briar dashboard` on port 8080).

This file is self-contained — you do not need to read `README.md` to follow it. Cross-reference `README.md` only if something here is silent on a detail.

---

## What you're deploying

Two long-lived processes from this repo (`github.com/iklobato/briar`):

| Process | Command | Purpose |
|---|---|---|
| `briar-scheduler.service` | `briar runbook serve examples/` | Registers every `(company, task)` job from the YAMLs in `examples/` and runs them on their declared cadence. |
| `briar-dashboard.service` | `briar dashboard --host 0.0.0.0 --port 8080 --examples examples --knowledge knowledge --repo-path .` | Read-only HTML status page. |

Both run as user `briar`. Both read `/etc/briar/secrets.env` for credentials. Both write to `/var/log/briar/`.

State worth preserving across redeploys: `/opt/briar-scheduler/knowledge/` (mined markdown blobs). Everything else is reproducible from `git clone`.

---

## Prerequisites the operator must provide before you start

Stop and ask the operator if any of these are missing — do NOT guess values.

1. **AWS account access** with permission to launch EC2, create IAM roles, allocate EIPs, and create security groups.
2. **AWS region** (default to `us-east-1` if unspecified).
3. **An EC2 SSH key pair name** in that region (create one if needed — `aws ec2 create-key-pair --key-name briar-deploy --query KeyMaterial --output text > ~/.ssh/briar-deploy.pem && chmod 600 ~/.ssh/briar-deploy.pem`).
4. **Operator's public IP** for the SSH ingress rule (`curl -s ifconfig.me`).
5. **A GitHub deploy key OR Personal Access Token** with `read` access to `iklobato/briar` — the repo is private. The deploy-key path is preferred (no token rotation).
6. **Application credentials** to put in `secrets.env`:
   - `GITHUB_TOKEN` — PAT with `repo` scope (used by every GitHub extractor).
   - `CLAUDE_CODE_OAUTH_TOKEN` — only if you plan to run `briar agent` from the box. Not needed if the box only runs the scheduler + dashboard.
   - `BRIAR_DATABASE_URL` — only if you want the postgres-backed knowledge store instead of file-backed.
   - Per-company AWS credentials (`AWS_<COMPANY>_ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` / `SESSION_TOKEN` / `REGION`) — only required if any runbook YAML uses the `aws-infra` extractor AND you are NOT using the cross-account IAM-role path (see §4).
   - Per-company Fireflies API key (`FIREFLIES_<COMPANY>_API_KEY`) — only required if the runbook includes a `meeting-digest` schedule OR `briar agent` is invoked with `--meeting-key` / `--meeting-query`. Optional; absent key = meeting extractors return empty and the rest of the pipeline runs unchanged.

If the operator cannot produce items 1, 2, 3, 5, and a `GITHUB_TOKEN` for §6, halt and ask.

---

## 1. Provision the EC2 instance

**Instance type:** `t3.nano` (~$4/mo, x86) is the default. Use `t4g.nano` (ARM, ~$3/mo) only if the operator explicitly opts in — `psycopg[binary]` and `boto3` work on ARM but it's one extra debug surface if anything breaks.

**AMI:** Latest Ubuntu 24.04 LTS for the chosen architecture. Look it up via:

```bash
aws ec2 describe-images \
  --owners 099720109477 \
  --filters "Name=name,Values=ubuntu/images/hrd-ssd/ubuntu-noble-24.04-amd64-server-*" \
            "Name=state,Values=available" \
  --query 'sort_by(Images,&CreationDate)[-1].ImageId' \
  --output text --region <region>
```

**Storage:** 10 GB gp3 root volume. No extra EBS volume.

**Security group:** create one named `briar-sg` with:

| Direction | Protocol | Port | Source | Purpose |
|---|---|---|---|---|
| Inbound | TCP | 22 | `<operator-ip>/32` | SSH |
| Inbound | TCP | 8080 | `0.0.0.0/0` | Dashboard (tighten to operator CIDR if requested) |
| Outbound | All | All | `0.0.0.0/0` | Extractors call out to GitHub + AWS |

**Elastic IP:** allocate one and associate it after the instance is running. The dashboard URL needs to survive stop/start.

Concrete launch:

```bash
aws ec2 run-instances \
  --image-id <ami-id> \
  --instance-type t3.nano \
  --key-name <key-pair-name> \
  --security-group-ids <briar-sg-id> \
  --iam-instance-profile Name=briar-instance-profile \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=10,VolumeType=gp3}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=briar-scheduler}]' \
  --region <region>
```

Note `--iam-instance-profile` references the profile you'll create in §4. If you have not created it yet, launch without that flag and attach it via `aws ec2 associate-iam-instance-profile` after §4.

---

## 2. Set up the operator-side SSH config (optional but recommended)

```bash
cat >> ~/.ssh/config <<EOF

Host briar-ec2
  HostName <elastic-ip>
  User ubuntu
  IdentityFile ~/.ssh/briar-deploy.pem
  IdentitiesOnly yes
EOF
```

From here, `ssh briar-ec2` works.

---

## 3. Bootstrap the instance

SSH in once and run this block. It's idempotent — re-running is safe.

```bash
# System packages
sudo apt update
sudo apt install -y python3.12 python3.12-venv git build-essential libpq-dev awscli

# Application user + directories
sudo useradd -m -s /bin/bash briar || true
sudo mkdir -p /opt/briar-scheduler /etc/briar /var/log/briar
sudo chown -R briar:briar /opt/briar-scheduler /var/log/briar
sudo chown root:briar /etc/briar
sudo chmod 750 /etc/briar
```

### Clone the repo

Pick ONE of the two auth paths. **Deploy key is preferred.**

**Path A — deploy key (preferred):**

```bash
# Generate a key as the briar user
sudo -u briar ssh-keygen -t ed25519 -N "" -f /home/briar/.ssh/id_ed25519 -C "briar-ec2-deploy"
sudo -u briar cat /home/briar/.ssh/id_ed25519.pub
# Operator: paste that pubkey into github.com/iklobato/briar → Settings → Deploy keys → Add deploy key (read-only).
# Then back on the EC2:
sudo -u briar ssh-keyscan github.com >> /home/briar/.ssh/known_hosts
sudo -u briar git clone git@github.com:iklobato/briar.git /opt/briar-scheduler
```

**Path B — HTTPS with a token (fallback, requires rotation):**

```bash
sudo -u briar git clone https://<token>@github.com/iklobato/briar.git /opt/briar-scheduler
sudo -u briar git -C /opt/briar-scheduler remote set-url origin https://github.com/iklobato/briar.git
# Don't leave the token in the remote URL — credential helper or operator-side `git pull` keeps it clean.
```

### Install the venv

```bash
sudo -u briar python3.12 -m venv /opt/briar-scheduler/.venv
sudo -u briar /opt/briar-scheduler/.venv/bin/pip install -U pip
sudo -u briar /opt/briar-scheduler/.venv/bin/pip install -e /opt/briar-scheduler
sudo -u briar /opt/briar-scheduler/.venv/bin/briar version
```

If `briar version` prints a version, the install is good.

---

## 4. IAM (instance profile) — only if any runbook uses `aws-infra`

Skip this entire section if none of your `examples/*.yaml` reference the `aws-infra` extractor. Confirm with:

```bash
grep -l "aws-infra" /opt/briar-scheduler/examples/*.yaml
```

If empty output, skip to §5.

### Path A — same-account companies (simplest)

The EC2 lives in the same AWS account it'll mine.

1. Create role `briar-instance-role`, trust principal `ec2.amazonaws.com`.
2. Attach a custom policy with **read-only** permissions:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": [
         "ecs:Describe*", "ecs:List*",
         "rds:Describe*", "rds:List*",
         "lambda:List*", "lambda:Get*",
         "sqs:ListQueues", "sqs:GetQueueAttributes",
         "logs:Describe*", "logs:Get*", "logs:Filter*"
       ],
       "Resource": "*"
     }]
   }
   ```

3. Create instance profile `briar-instance-profile`, add the role to it.
4. Associate with the instance (already in the launch command above, or after the fact via `aws ec2 associate-iam-instance-profile --instance-id <id> --iam-instance-profile Name=briar-instance-profile`).
5. In `secrets.env` (§5), leave the per-company AWS variables UNSET. `boto3` falls through to the instance role.

### Path B — cross-account companies

Each company is a different AWS account. Two sub-options:

- **Static keys** (mirrors the current DO droplet exactly): drop `AWS_<COMPANY>_ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` / `SESSION_TOKEN` / `REGION` into `secrets.env`. No instance profile needed for the extractor's purposes (still create the role for any other AWS calls).
- **AssumeRole** (cleaner long-term): each target account has a read-only role that trusts `briar-instance-role`. Set `AWS_<COMPANY>_ROLE_ARN` in `secrets.env`. Requires application code support — verify by grepping `src/briar/extract/aws_infra.py` for `assume_role` before assuming it works; if it doesn't, fall back to static keys.

If unsure, use static keys. They're known-good with this codebase.

---

## 5. Secrets file

```bash
sudo tee /etc/briar/secrets.env >/dev/null <<'EOF'
GITHUB_TOKEN=<paste-pat>

# Per-company AWS static keys (omit if §4 Path A handled it)
# AWS_ACME_ACCESS_KEY_ID=...
# AWS_ACME_SECRET_ACCESS_KEY=...
# AWS_ACME_SESSION_TOKEN=...
# AWS_ACME_REGION=us-east-2

# Optional — only if running `briar agent` from this box
# CLAUDE_CODE_OAUTH_TOKEN=...

# ── Knowledge store DSN ──
# Three resolution layers; first non-empty wins:
#   1. YAML `knowledge.config.dsn_env: <NAME>` → reads ${NAME}
#   2. BRIAR_{COMPANY}_DATABASE_URL (auto-detected per company)
#   3. BRIAR_DATABASE_URL (global fallback)
# Recommended for multi-company deploys sharing one cluster:
#   BRIAR_KB_DATABASE_URL=postgresql://briar_kb:<pwd>@<host>:25060/<db>?sslmode=require
# Then point each `runbooks/*.yaml`'s knowledge.config.dsn_env at it.
# BRIAR_KB_DATABASE_URL=...
# BRIAR_DATABASE_URL=...

# ── Jira credentials (per-company, pick ONE auth strategy) ──
# Token auth (Atlassian-recommended):
# JIRA_ACME_URL=https://acme.atlassian.net
# JIRA_ACME_EMAIL=bot@acme.com
# JIRA_ACME_TOKEN=<api-token>
# Session-cookie auth (browser-extracted; either token alone is enough):
# JIRA_ACME_AUTH_KIND=session            # force this strategy
# JIRA_ACME_TENANT_SESSION_TOKEN=<value of tenant.session.token cookie>
# JIRA_ACME_SESSION_TOKEN=<value of cloud.session.token cookie>
# JIRA_ACME_XSRF_TOKEN=<value of atlassian.xsrf.token cookie>  # optional
# JIRA_ACME_USER_AGENT=<override>                              # optional

# ── Fireflies.ai (per-company, optional) ──
# Drives the meeting-digest extractor + the JIT meeting-context
# extractor consumed by `briar agent implement` / `prfix`. Skip the
# whole block if you don't use Fireflies — both extractors degrade to
# EMPTY_SECTION and the rest of the pipeline runs unchanged.
# FIREFLIES_ACME_API_KEY=<personal API key from Fireflies → Developer Settings>
EOF

sudo chmod 600 /etc/briar/secrets.env
sudo chown root:briar /etc/briar/secrets.env
```

Verify:

```bash
sudo -u briar bash -c 'set -a; source /etc/briar/secrets.env; set +a; env | grep -E "^(GITHUB_TOKEN|AWS_|CLAUDE_)" | sed "s/=.*/=<set>/"'
```

You should see the variable names with `=<set>` — values are hidden. If a name is missing, fix `secrets.env` before proceeding.

---

## 6. Place runbooks

```bash
ls /opt/briar-scheduler/examples/
```

These were pulled by `git clone`. If the operator wants a different set, replace them now:

```bash
# Example: keep only simple-single-repo
sudo -u briar bash -c 'cd /opt/briar-scheduler && rm examples/multi_company.yaml examples/all_features.yaml'
```

Each YAML must validate. Quick sanity check:

```bash
sudo -u briar /opt/briar-scheduler/.venv/bin/briar runbook extract /opt/briar-scheduler/examples/<one>.yaml --task <one-task-name>
```

That runs once and exits. If it succeeds, the scheduler will succeed too.

---

## 7. systemd units

`/etc/systemd/system/briar-scheduler.service`:

```ini
[Unit]
Description=Briar runbook scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=briar
Group=briar
WorkingDirectory=/opt/briar-scheduler
EnvironmentFile=/etc/briar/secrets.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/briar-scheduler/.venv/bin/briar runbook serve examples/
Restart=always
RestartSec=10
StandardOutput=append:/var/log/briar/scheduler.log
StandardError=append:/var/log/briar/scheduler.log

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/briar-dashboard.service`:

```ini
[Unit]
Description=Briar dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=briar
Group=briar
WorkingDirectory=/opt/briar-scheduler
EnvironmentFile=/etc/briar/secrets.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/briar-scheduler/.venv/bin/briar dashboard --host 0.0.0.0 --port 8080 --examples examples --knowledge knowledge --repo-path .
Restart=always
RestartSec=10
StandardOutput=append:/var/log/briar/dashboard.log
StandardError=append:/var/log/briar/dashboard.log

[Install]
WantedBy=multi-user.target
```

Enable + start both:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now briar-scheduler briar-dashboard
sudo systemctl status briar-scheduler briar-dashboard --no-pager
```

Both should report `active (running)`. If either reports `failed`, see §10 (troubleshooting).

---

## 8. Verify

1. **Credential coverage:** `sudo -u briar bash -c 'set -a; . /etc/briar/secrets.env; set +a; .venv/bin/briar secrets doctor --examples examples/'` — walks every `(company, extractor, provider)` and `(company, messages, writer)` tuple in the runbooks and reports `ok` / `X MISSING:` per row without ever printing values. Fix every `X` before relying on the scheduler.
2. **Dashboard reachable:** browser → `http://<elastic-ip>:8080/`. You should see the read-only status page.
3. **Scheduler logging:** `sudo journalctl -u briar-scheduler -n 50 --no-pager` and `sudo tail -n 50 /var/log/briar/scheduler.log`. Either path shows the same lines — confirm the scheduler logged "registered N jobs" for each runbook.
4. **A job actually fires:** wait until the soonest `every:` cadence elapses (often 1 hour). Re-tail the scheduler log; you should see the extractor name and a row count.
5. **Knowledge blob written:** `ls /opt/briar-scheduler/knowledge/` shows one markdown file per company.

---

## 9. Log rotation

Prevent the 10 GB disk from filling. Drop this once:

`/etc/logrotate.d/briar`:

```
/var/log/briar/*.log {
  daily
  rotate 7
  maxsize 100M
  compress
  delaycompress
  missingok
  notifempty
  copytruncate
}
```

Test: `sudo logrotate -d /etc/logrotate.d/briar`.

---

## 10. Deploy + rollback

**Deploy (operator's laptop):**

```bash
ssh briar-ec2 'sudo -u briar bash -c "cd /opt/briar-scheduler && git pull --ff-only && .venv/bin/pip install -e . --quiet" && sudo systemctl restart briar-scheduler briar-dashboard'
```

**Rollback:**

```bash
ssh briar-ec2 'sudo -u briar git -C /opt/briar-scheduler log --oneline -5'
# Pick a SHA, then:
ssh briar-ec2 'sudo -u briar git -C /opt/briar-scheduler reset --hard <sha> && sudo systemctl restart briar-scheduler briar-dashboard'
```

**Refreshing AWS STS creds (Path B static keys only):** mirror the README's one-liner with the EC2 elastic IP as the destination.

---

## 11. State backups (optional)

`/opt/briar-scheduler/knowledge/` is the only non-reproducible state. Either:

- **Cheap:** add a third systemd timer that runs `aws s3 sync /opt/briar-scheduler/knowledge/ s3://<bucket>/briar-knowledge/` nightly. The instance role needs `s3:PutObject` on that bucket.
- **Cleaner:** set `BRIAR_DATABASE_URL` to a managed RDS Postgres, switch the store backend to `postgres` (`storage/postgres.py` already supports it). Adds ~$15/mo; only do this if the operator wants blob history.

Skip if the operator hasn't asked for backups.

---

## 12. Troubleshooting

| Symptom | Look at | Likely cause |
|---|---|---|
| `systemctl status` shows `failed` immediately | `journalctl -u briar-scheduler -n 100` | Missing env var, bad YAML, or import error from `pip install -e .` |
| Dashboard 502 / connection refused | `sudo ss -tlnp \| grep 8080` | Service crashed; check the journal |
| `aws-infra` extractor fails with `NoCredentialsError` | `sudo -u briar aws sts get-caller-identity` | Instance profile not attached, or per-company keys missing from `secrets.env` |
| `gh`/GitHub API 401 | `echo $GITHUB_TOKEN \| head -c 10` in the service's env | PAT expired or wrong scope |
| Disk fills | `df -h /` | Add the logrotate config from §9, or grow the EBS volume |
| Scheduler runs but nothing in `knowledge/` | `briar runbook extract examples/<file>.yaml --task <name>` directly | Runbook task name mismatch, or extractor's `is_available()` returned False (credentials missing) |

---

## 13. Acceptance checklist (final)

Before reporting the deploy done, verify ALL of these:

- [ ] `systemctl status briar-scheduler` → `active (running)`
- [ ] `systemctl status briar-dashboard` → `active (running)`
- [ ] `curl -sI http://<elastic-ip>:8080/` → `200 OK`
- [ ] `sudo journalctl -u briar-scheduler --since '5 min ago'` shows the scheduler registered jobs (no Python tracebacks)
- [ ] One full extraction cycle has completed and written a file under `/opt/briar-scheduler/knowledge/`
- [ ] `sudo systemctl is-enabled briar-scheduler briar-dashboard` → both `enabled` (will start on reboot)
- [ ] `/etc/briar/secrets.env` mode is `600`, owner `root:briar`
- [ ] `/etc/logrotate.d/briar` is in place and `logrotate -d` reports no errors

If any item fails, fix it before declaring the deploy done.

---

## Notes for the agent running this

- **Do not commit secrets** to the repo at any point. `secrets.env` is operator-only.
- **Do not run destructive AWS calls** (`terminate-instances`, `delete-security-group`, etc.) without operator confirmation.
- **If `git clone` fails**, stop and surface the auth error — do not try alternate auth paths silently.
- **If `pip install` pulls in a wheel that fails to build**, surface the error verbatim and stop. The likely cause is missing `libpq-dev` or `build-essential` — confirm §3's `apt install` succeeded.
- **Stop and ask** if the operator's runbook YAMLs reference an extractor or schedule cadence you don't recognise.

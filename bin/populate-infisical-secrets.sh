#!/usr/bin/env bash
# Populate the briar Infisical project's `prod` environment with the
# credentials briar-cli needs to run for bitspark + unlock.
#
# Resolution order per key:
#   1. Current shell env (printenv NAME)
#   2. ~/.aws/credentials [bitspark] for AWS_BITSPARK_*
#   3. doctl for BRIAR_KB_DATABASE_URL (managed PG cluster URI)
#
# Anything missing is reported with the value-source the script tried.
# Re-run after exporting the missing vars in the same shell, or fall
# back to `infisical secrets set NAME=value --projectId=$PROJECT --env=prod`
# manually for the holdouts.
#
# Idempotent: re-running overwrites with the same value. No-op when
# everything is already populated.

set -euo pipefail

PROJECT=8e32afc0-a030-460c-a723-7cc0409b759d
ENV=prod
PG_CLUSTER_ID=9ef05287-e724-4f61-afd1-616f88cbdf64

push() {
    # $1 = secret name, $2 = value, $3 = source label for logging
    local name=$1 value=$2 source=$3
    if [ -z "$value" ]; then
        echo "  - $name  (no value found; source tried: $source)"
        return 1
    fi
    if infisical secrets set "$name=$value" --projectId="$PROJECT" --env="$ENV" >/dev/null 2>&1; then
        echo "  + $name  (from $source)"
        return 0
    else
        echo "  X $name  (infisical set failed; source: $source)"
        return 1
    fi
}

read_aws_field() {
    # $1 = profile (e.g. bitspark), $2 = field (e.g. aws_access_key_id)
    local profile=$1 field=$2
    [ -f "$HOME/.aws/credentials" ] || return 0
    awk -v profile="[$profile]" -v field="$field" '
        $0 == profile { in_p=1; next }
        /^\[/ { in_p=0 }
        in_p && $0 ~ "^"field"[ \t]*=" { sub(/^[^=]*=[ \t]*/,""); print; exit }
    ' "$HOME/.aws/credentials"
}

echo "Pushing to Infisical project $PROJECT (env=$ENV)…"
echo

# Workspace-wide secrets from current env
echo "[workspace-wide from env]"
for var in GITHUB_TOKEN ANTHROPIC_API_KEY TELEGRAM_BOT_TOKEN; do
    push "$var" "$(printenv "$var" || true)" "env" || true
done

# BRIAR_KB_DATABASE_URL: prefer env, fall back to doctl
echo
echo "[BRIAR_KB_DATABASE_URL]"
db_url=$(printenv BRIAR_KB_DATABASE_URL || true)
if [ -z "$db_url" ] && command -v doctl >/dev/null 2>&1; then
    db_url=$(doctl databases connection "$PG_CLUSTER_ID" --format URI --no-header 2>/dev/null || true)
    push "BRIAR_KB_DATABASE_URL" "$db_url" "doctl" || true
else
    push "BRIAR_KB_DATABASE_URL" "$db_url" "env" || true
fi

# bitspark — env first, AWS file as fallback for the IAM keys
echo
echo "[bitspark]"
push "SLACK_BITSPARK_WEBHOOK_URL" "$(printenv SLACK_BITSPARK_WEBHOOK_URL || true)" "env" || true
push "TELEGRAM_BITSPARK_CHAT_ID"  "$(printenv TELEGRAM_BITSPARK_CHAT_ID || true)"  "env" || true
for pair in \
    "AWS_BITSPARK_ACCESS_KEY_ID|aws_access_key_id" \
    "AWS_BITSPARK_SECRET_ACCESS_KEY|aws_secret_access_key"; do
    name=${pair%%|*}
    field=${pair##*|}
    val=$(printenv "$name" || true)
    if [ -z "$val" ]; then
        val=$(read_aws_field bitspark "$field")
        push "$name" "$val" "~/.aws/credentials" || true
    else
        push "$name" "$val" "env" || true
    fi
done

# unlock — env only (Jira credentials are not in any standard local file)
echo
echo "[unlock]"
for var in \
    JIRA_UNLOCK_EMAIL JIRA_UNLOCK_TOKEN JIRA_UNLOCK_URL \
    SLACK_UNLOCK_WEBHOOK_URL TELEGRAM_UNLOCK_CHAT_ID \
    AWS_UNLOCK_ACCESS_KEY_ID AWS_UNLOCK_SECRET_ACCESS_KEY AWS_UNLOCK_SESSION_TOKEN AWS_UNLOCK_REGION; do
    push "$var" "$(printenv "$var" || true)" "env" || true
done

echo
echo "Done. Verify coverage with:"
echo "  ssh root@203.0.113.10 'sudo -u briar bash -l -c \"briar secrets doctor --examples /opt/briar/runbooks --store infisical\"'"

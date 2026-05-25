# `briar telemetry`

## Purpose
Inspect and configure the CLI's opt-out error + usage analytics that
flow to Sentry. The package is `briar.telemetry`; this command is the
only operator-facing knob.

## When to use
- First run on a new host — confirm the banner appeared and `status`
  reflects the choice.
- Auditing what's actually being sent (`preview`).
- Disabling telemetry for a CI container or restricted environment.
- Rotating the anonymous identity (`reset`).

## Prerequisites
None. The command never touches the network on its own — it only
reads / writes the local state file at `$XDG_CONFIG_HOME/briar/`.

## What gets sent (every event)

| Tag | What | Why |
|---|---|---|
| `command` | Subcommand name (e.g. `plan.run`, `agent.implement`) | Find which flows error / are popular |
| `outcome` | `ok` / `error` / `interrupt` / `preview` | Coarse success signal |
| `duration_ms` | Wall-clock milliseconds | Catch regressions |
| `install_id` | SHA-256 prefix of a local random UUID (16 hex chars) | De-dup installs without identifying anyone |
| `briar_version` / `python_version` / `os_name` / `os_release` | Build/runtime fingerprint | Reproduce bugs |
| `flags_present` | Comma-joined **flag names only** (e.g. `llm,company,owner`) | Discover which flags people use |
| Provider mix | `provider_kind` / `tracker_kind` / `store_kind` / `llm_provider` / `llm_model` (when set) | Inform default choices |
| Error fields | `error_type` / scrubbed `error_message` (only on error events) | Triage real bugs |

## What NEVER gets sent

This list is enforced by `briar.telemetry._scrubber.Scrubber` and verified by 45 unit tests:

- Flag VALUES (ticket keys, repo names, board URLs, file paths, project keys)
- LLM prompts or completions
- File contents (Read / Write / Edit tool I/O)
- Diffs or commit bodies
- Env-var values
- Username, hostname, IP, home directory path
- Anything matching common credential regexes (AWS keys, GitHub PATs, Anthropic keys, OpenAI keys, Slack tokens, JWTs, PEM)
- Absolute filesystem paths (replaced with `<path>` inline)
- Any string longer than 1 KB (truncated with marker)
- Local-variable frames in tracebacks (`include_local_variables=False`)
- HTTP request/url breadcrumbs the Sentry SDK might pick up

## Sub-ops

| Op | What it does |
|---|---|
| `status` | Print current tier, source (env / config-file / do-not-track / default), hashed install_id, paths |
| `preview --for-command <name>` | Print the exact JSON event that would be sent for one run. **No network call** |
| `off` | Disable telemetry. Persists to the state file |
| `errors-only` | Sentry crash reports only; no usage analytics |
| `full` | Errors + usage analytics (the default) |
| `reset` | Regenerate the install_id — rotates the anonymous identity |

## Commands

### See the current state

```bash
briar telemetry status
briar telemetry status --format json
```

### Inspect exactly what would be sent

```bash
briar telemetry preview --for-command plan.run
```

Returns a JSON object. Pipe to `jq` to inspect. Nothing is sent over
the network.

### Disable everything

```bash
briar telemetry off
# or, for a single invocation:
BRIAR_TELEMETRY=off briar plan ...
# or, industry-standard:
DO_NOT_TRACK=1 briar plan ...
```

### Opt into the errors-only tier

```bash
briar telemetry errors-only
```

Sentry receives crash reports + error-policy decisions; nothing else.

### Re-enable the default (errors + usage)

```bash
briar telemetry full
```

### Rotate your anonymous identity

```bash
briar telemetry reset
# next event carries a different install_id; previous events are
# orphaned from your future activity
```

## Configuration precedence (highest first)

1. `DO_NOT_TRACK=1` — always wins, source `do-not-track`.
2. `BRIAR_TELEMETRY={off,errors-only,full}` — env override, source `env`.
3. State file at `$XDG_CONFIG_HOME/briar/telemetry.json` — set by the `briar telemetry off/errors-only/full` subcommands.
4. Default: `full` — source `default`.

## Where the data goes

Sentry. The DSN is hardcoded into the briar package (this is normal
for OSS CLIs — Sentry DSNs are public-by-design). Override with
`BRIAR_SENTRY_DSN` if your org runs a self-hosted Sentry / relay.

If the hardcoded DSN is empty AND `BRIAR_SENTRY_DSN` is unset, telemetry
silently no-ops — no errors, no network traffic, `status` still reflects
your tier choice.

## Verifying success

After `off` / `errors-only` / `full`:
1. `briar telemetry status --format json` reports the expected tier with `source: config-file`.
2. State file at `$XDG_CONFIG_HOME/briar/telemetry.json` contains the new tier.

After `preview`:
1. The printed JSON has only the allow-listed tags (see "What gets sent" above).
2. No tags appear with secret-shaped values.

## Common failures

| Symptom | Fix |
|---|---|
| Banner re-prints every time | `$XDG_CONFIG_HOME/briar/telemetry.json` isn't being written — likely read-only home. Either fix permissions or set `BRIAR_TELEMETRY` in your shell to suppress the banner |
| `status` shows `tier=full` but no events in Sentry | Either the DSN isn't configured (`dsn_configured: false` in `status`) — check the hardcoded value or set `BRIAR_SENTRY_DSN` — or the network can't reach Sentry from your host |
| Worried something sensitive was sent | Run `briar telemetry preview --for-command <command>` — if the printed JSON doesn't have what you're worried about, neither did the real send. The scrubber runs unconditionally for both paths |
| Need to wipe local telemetry state | `rm -rf $XDG_CONFIG_HOME/briar/` — next run re-shows the banner |

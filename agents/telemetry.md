# `briar telemetry`

briar sends anonymous usage data and error reports to Sentry (an error
tracking service), to help fix bugs. `briar telemetry` shows what is
sent and lets you turn it down or off.

There are three **tiers** (levels):

| Tier | What is sent |
|---|---|
| `full` (default) | One usage event per command run, and error reports. |
| `errors-only` | Error reports only. |
| `off` | Nothing. |

It has six subcommands:

| Subcommand | What it does |
|---|---|
| `status` | Shows the current tier and where that choice came from. |
| `preview` | Prints the exact event briar would send. Sends nothing. |
| `off` | Turns telemetry off and saves that choice. |
| `errors-only` | Sends error reports only, and saves that choice. |
| `full` | Sends usage events and error reports, and saves that choice. |
| `reset` | Makes a new anonymous install ID. |

## What is sent

Each event carries only these fields:

| Field | What it holds |
|---|---|
| `command` | The top-level command name, such as `plan` or `extract`. |
| `outcome` | `ok`, `error` or `interrupt` (you pressed Ctrl-C). |
| `duration_ms` | How long the command took, in milliseconds. |
| `exit_code` | The exit code, only when the command ended through an early exit. |
| `install_id` | The first 16 hex characters of a SHA-256 hash of a random ID made on your machine. The raw ID never leaves your machine. |
| `briar_version`, `python_version`, `os_name`, `os_release` | The briar version, Python version, OS name and OS release. |
| `flags_present` | The **names** of the options that had a value in this run, such as `company,format,store`. Never the values. Defaults count too. |
| `error_type`, `error_message` | Error events only: the error class (such as `CliError`) and the error text after cleaning (see below). |

Sentry also gets the release (such as `briar-cli@1.1.75`) and the environment
(`production`, or the value of `$BRIAR_ENV`).

An error event is sent when a command stops with an error. Most
`error: ...` messages are this kind. In the `errors-only` tier these are
the only events sent.

## What is never sent

- Option values: ticket keys, repo names, board URLs, file paths, project keys.
- Prompts to the AI model, or its answers.
- File contents, diffs, commit messages.
- Env var values.
- Your user name, host name, IP address or home folder.
- Breadcrumbs, request data, local variables in stack traces, and the
  list of installed Python packages.

Before anything is sent, briar cleans every field:

- Fields not in the list above are dropped.
- A value that looks like a secret becomes `<redacted-secret>`. That
  covers AWS access keys, GitHub tokens, Anthropic and OpenAI keys,
  Slack tokens, private keys and JWT-like strings.
- Absolute file paths inside a value become `<path>`.
- Values longer than 1024 characters are cut and end with `...<truncated>`.

## How briar picks the tier

briar checks these in order. The first one that is set wins.

1. `DO_NOT_TRACK=1` in the environment: telemetry is `off`.
2. `BRIAR_TELEMETRY=off`, `errors-only` or `full` in the environment.
3. The saved choice in `$XDG_CONFIG_HOME/briar/telemetry.json` (usually
   `~/.config/briar/telemetry.json`). The `off`, `errors-only` and
   `full` subcommands write this file.
4. The default: `full`.

The first time briar runs, it prints a short notice about telemetry to
stderr, then saves that it did. The notice does not show when you set
`DO_NOT_TRACK` or `BRIAR_TELEMETRY`.

## `briar telemetry status`

### What it does

Prints the current settings as JSON.

### Use it when

- You want to know if telemetry is on, and why.
- You set an env var and want to confirm briar sees it.

### Examples

```bash
# Show the current tier and its source.
briar telemetry status
```

You get JSON with these fields: `tier`, `source` (`do-not-track`, `env`,
`config-file` or `default`), `enabled`, `install_id_hashed`,
`dsn_configured`, `default_dsn_set`, `state_path`, `install_id_path` and
`config_dir`.

```bash
# The same as YAML.
briar telemetry status --format yaml
```

You get the same fields as YAML.

### All parameters

`status` has no flags of its own. The global `--format` flag works (see [setup.md](setup.md)).

## `briar telemetry preview`

### What it does

Builds the event a command run would send right now, and prints it as
JSON. It sends nothing.

### Use it when

- You want to check what briar sends before you decide on a tier.
- You worry that something private would leave your machine.

### Examples

```bash
# Show the event for a made-up command name.
briar telemetry preview
```

You get JSON with `kind`, `command` (here `(preview)`), `outcome`
(`preview`), `duration_ms` (`0`), empty `error_type` and `error_message`,
and the `tags` field list.

```bash
# Show the event as if the command were "plan".
briar telemetry preview --for-command plan
```

You get the same JSON with `command` set to `plan`.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--for-command` | The command name to put in the sample event. It is only a label. | `(preview)` | `--for-command extract` |

### Good to know

- The sample has no `flags_present` field, because no real command ran.
  A real event lists the option names of that run.
- `preview` always prints JSON. `--format` does not change it.

## `briar telemetry off`, `errors-only` and `full`

### What they do

Each one saves a tier in `telemetry.json`. They print the new tier and
the file path as JSON.

### Use it when

- You do not want briar to send anything: `off`.
- You only want to help with crashes: `errors-only`.
- You want to go back to the default: `full`.

### Examples

```bash
# Turn telemetry off for good on this machine.
briar telemetry off
```

You get `{"tier": "off", "state_path": "..."}`.

```bash
# Send error reports only.
briar telemetry errors-only
```

You get `{"tier": "errors-only", ...}`.

```bash
# Back to the default.
briar telemetry full
```

You get `{"tier": "full", ...}`.

To turn it off for one run only, set an env var instead:

```bash
DO_NOT_TRACK=1 briar context list
BRIAR_TELEMETRY=off briar context list
```

You get a normal run with nothing sent.

### All parameters

These three subcommands have no flags of their own.

### Good to know

- The tier is read when the command starts. So `briar telemetry off`
  itself is still counted under the old tier: one last usage event if
  that tier was `full`. To send nothing at all, run
  `DO_NOT_TRACK=1 briar telemetry off`.
- An env var beats the saved file. If `BRIAR_TELEMETRY=full` is set,
  `briar telemetry off` saves `off`, but `status` still shows `full`
  with source `env`.

## `briar telemetry reset`

### What it does

Deletes your install ID and makes a new random one. Events sent after
this can no longer be linked to events sent before. It prints
`rotated`, the new hashed ID and the file path.

### Use it when

- You want a fresh anonymous ID, for example on a cloned machine image.

### Examples

```bash
# Make a new anonymous ID.
briar telemetry reset
```

You get JSON with `"rotated": true` and the new `install_id_hashed`.

```bash
# Confirm the new ID.
briar telemetry status
```

You get the same `install_id_hashed` value that `reset` printed.

### All parameters

`reset` has no flags of its own.

## Where the data goes

Events go to briar's Sentry project. Its address (the DSN) is built into
briar. Set `BRIAR_SENTRY_DSN` to send events to your own Sentry server
instead. An empty `BRIAR_SENTRY_DSN` falls back to the built-in one, so
use a tier or `DO_NOT_TRACK=1` to stop sending.

## Good to know

- The telemetry files live in `$XDG_CONFIG_HOME/briar/` (usually
  `~/.config/briar/`): `telemetry.json` holds the tier, `install_id`
  holds the raw ID. Both have mode `0600`.
- To start over, delete those two files. The notice shows again on the
  next run. Do not delete the whole folder: it can also hold your
  `secrets.env`.
- If the notice shows on every run, briar cannot write `telemetry.json`
  (for example, a read-only home folder). Set `BRIAR_TELEMETRY` in your
  shell instead.
- If `status` says `dsn_configured: true` but nothing reaches Sentry,
  your network may block it.
- `DO_NOT_TRACK=1` and `BRIAR_TELEMETRY=off` also turn off the daily
  "new version available" check. `BRIAR_NO_UPDATE_CHECK=1` turns off only
  that check.

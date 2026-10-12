# `briar secrets`

`briar secrets` checks the keys briar needs. It never prints a key
value, only key names.

It has two subcommands:

| Subcommand | What it does |
|---|---|
| `doctor` | Reads your runbook files and reports, per company, which keys are set and which are missing. |
| `bootstrap` | Loads your `secrets.env` file by hand, to test it. briar already does this at the start of every command. |

A **runbook** is a YAML file that lists, per company, which extractors to
run and how often (see [runbook.md](runbook.md)). Each extractor needs
some keys, such as `GITHUB_TOKEN` or `JIRA_ACME_TOKEN`.

## `briar secrets doctor`

### What it does

Opens every `*.yaml` file in a folder. For each company in each file, it
looks at every extractor and every `messages:` channel. It asks each one
which keys it needs, then checks if those keys are set. It exits with
code 0 when nothing is missing and 1 when anything is missing.

### Use it when

- You set up a new machine and want the list of keys still to add.
- A runbook task was skipped with "likely missing credentials" in the log.
- You added a key to `secrets.env` and want to confirm briar sees it.

### Examples

```bash
# Check every runbook in the runbooks/ folder.
briar secrets doctor --examples runbooks/
```

You get one block per company, like this. In the real output the `~` is a long dash.

```text
=== acme (acme.yaml) ===
  ok pr-archaeology (provider=github)
  X  active-tickets (provider=jira) ~ MISSING: JIRA_ACME_URL, JIRA_ACME_EMAIL, JIRA_ACME_TOKEN
  ok messages.ops_chat (kind=slack-channel)
```

```bash
# Show only the problems.
briar secrets doctor --examples runbooks/ | grep MISSING
```

You get one line per extractor or channel that lacks a key.

```bash
# Check the keys stored in AWS SSM instead of the local environment.
briar secrets doctor --examples runbooks/ --cred-store ssm
```

You get the same report, but each key is looked up in SSM under `/briar/<NAME>`.

### What each row means

| Row | Meaning |
|---|---|
| `ok <extractor> (provider=<kind>)` | Every key this extractor needs is set. |
| `ok <extractor> (no provider deps)` | This extractor needs no keys. |
| `X  <extractor> (provider=<kind>) ~ MISSING: A, B` | These keys are missing. |
| `?  <extractor> ~ unknown extractor, skipping` | The name is not a briar extractor. |
| `ok messages.<handle> (kind=<kind>)` | The message channel has its keys. |
| `X  messages.<handle> (kind=<kind>) ~ MISSING: ...` | The message channel lacks keys. |
| `<file>.yaml: load failed ~ ...` | The file is not a valid runbook. briar skips it. |

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--examples` | The folder with your runbook YAML files. briar reads the `*.yaml` files directly in it (not `.yml`, not subfolders). | `./examples`. If that folder does not exist, briar prints `no runbooks to check` and exits 0. | `--examples runbooks/` |
| `--cred-store` | Where to look the keys up: `envfile`, `aws-secretsmanager`, `ssm` or `vault`. `envfile` means the process environment, which holds your shell vars plus `secrets.env`. `--store` still works as an old name, but prints a note. | `envfile` | `--cred-store vault` |

### Good to know

- Pass a folder, not a file. A file path finds no `*.yaml` inside it,
  so `doctor` prints nothing and exits 0.
- A folder you name that does not exist prints `no examples dir at <dir>` and exits 1.
- `ok` means the key is set, not that it works. An expired token still
  shows `ok`. Run the extractor to test it.
- `doctor` does not check LLM keys (`ANTHROPIC_API_KEY` and others) or
  the failure alert settings (`BRIAR_NOTIFY_SINKS`). Use `briar doctor`
  for a basic LLM key check (see [setup.md](setup.md)).
- To add a missing key, run `briar auth login <target>` (see
  [auth.md](auth.md)) or add a `NAME=value` line to `secrets.env`.

## `briar secrets bootstrap`

### What it does

Reads your `secrets.env` file and loads each `NAME=value` line into the
environment of this one run. Nothing is kept after the command ends. It
prints how many keys it loaded and how many were already set.

briar finds `secrets.env` in this order: `$BRIAR_SECRETS_FILE`, then
`/etc/briar/secrets.env` if it exists, then
`$XDG_CONFIG_HOME/briar/secrets.env` (usually `~/.config/briar/secrets.env`).

### Use it when

- You want to test that briar can read your `secrets.env` file.
- Commands log `credential-bootstrap: envfile failed` and you want to see the error.

### Examples

```bash
# Load the file the same way briar does at start.
briar secrets bootstrap
```

You get a line like `bootstrap envfile: wrote 0 env vars (preserved 12 already-set)`.

```bash
# Read the file and list what would load, but load nothing.
briar secrets bootstrap --dry-run
```

You get `would write N env vars` and the key names. No values.

```bash
# Ask for the envfile loader by name. It fails when there is no file.
briar secrets bootstrap --kind envfile
```

You get the same report, or `bootstrap kind=envfile not configured` and exit code 1 when no file exists.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--kind` | The loader to run. Only `envfile` exists in 1.1.75. | Empty: run every loader that finds its source. | `--kind envfile` |
| `--dry-run` | Read the file and print the names it would load, but set nothing. | off | `--dry-run` |

### Good to know

- Every briar command already loads `secrets.env` before it runs. So
  in a normal run, every key is already set, and `bootstrap` reports
  `wrote 0 env vars (preserved N already-set)`. N is the number of keys
  in your file. That is the healthy result.
- A key you exported in your shell always wins over the file.
- With no file and no `--kind`, briar prints `no credential-bootstrap
  backend configured` and exits 0.
- The file format: `NAME=value` per line. `export NAME=value` also works.
  Lines that start with `#` are skipped. One pair of matching quotes
  around a value is removed. A `#` inside a value is kept.
- For safety, briar never loads these names from the file: `PATH`,
  `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, `PYTHONINSPECT`, `LD_PRELOAD`,
  `LD_LIBRARY_PATH`, `LD_AUDIT`, the `DYLD_*` loader vars, the proxy vars
  (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, upper and lower
  case), and the TLS trust vars (`SSL_CERT_FILE`, `SSL_CERT_DIR`,
  `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`). Set those in your shell if you
  need them.
- Only `secrets.env` is loaded at start. Keys saved with
  `briar auth login --cred-store vault` (or `ssm`, or
  `aws-secretsmanager`) are not loaded for you.

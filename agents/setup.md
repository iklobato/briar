# Setup: `init`, `config show`, `doctor`, shell completion and global flags

These commands help you set briar up and check the setup:

| Command | What it does |
|---|---|
| `briar init` | Writes a starter `.briar.toml` project config file. |
| `briar config show` | Shows each setting's value and where it came from. |
| `briar doctor` | Checks your machine: Python, config, git, keys. |
| `briar --install-completion` | Adds tab completion to your shell. |

This page also covers the global flags `--format`, `--verbose` and
`--version`, which work with every command.

## How briar picks a setting

Many flags repeat the same values on every run: the company, the
knowledge folder, the repo. briar can take them from other places, so you type less. For
each flag, the first source that has a value wins:

1. The flag you type, such as `--company acme`.
2. An env var: `BRIAR_COMPANY` for the company.
3. The project config file: `.briar.toml`, or a `[tool.briar]` table in `pyproject.toml`.
4. Your git `origin` remote, for `--owner` and `--repo` only.
5. briar's built-in default.

briar looks for the config file in the current folder, then in each
parent folder up to `/`. It uses the first `.briar.toml` it finds, or the
first `pyproject.toml` that has a `[tool.briar]` table. If both are in
the same folder, `.briar.toml` wins. A file that is not valid TOML is
skipped with a warning.

### The config keys

```toml
company = "acme"
root    = "./knowledge"
tracker = "jira"
repos   = ["acme-co/acme-app", "acme-co/acme-shared"]
telemetry = "errors-only"

[repo]
owner    = "acme-co"
repo     = "acme-app"
provider = "github"
```

| Key | What it sets | Env var that beats it |
|---|---|---|
| `company` | Every `--company`. | `BRIAR_COMPANY` |
| `root` | The knowledge folder of `extract` (default `./knowledge`; a runbook sets its own `knowledge.root`). | none |
| `tracker` | The tracker of `active-tickets` and `ticket-archaeology`; `[extract]` beats it (see [extract.md](extract.md)). | none |
| `repos` | The repeatable `--repo` list on `extract`. | none |
| `[extract]`, `[extract.<name>]` | Every `briar extract` value besides the three flags. See [extract.md](extract.md). | none |
| `telemetry` | No flag: the telemetry tier, `off` (the default), `errors-only` or `full`. See [Telemetry](#telemetry). | `BRIAR_TELEMETRY`; `DO_NOT_TRACK=1` beats both |
| `[repo] owner`, `repo` | Every `--owner` and `--repo`. On `extract`, they form one `owner/repo` entry when `repos` is not set. | none |
| `[repo] provider` | The code host of the repo extractors; `[extract]` beats it. | none |

In pyproject.toml, put the same keys under `[tool.briar]`, with
`[tool.briar.repo]` for the `[repo]` table.

### Watch out

- A key fills **every** flag with that name.
- `store` is not a key any more: 2.0 has one knowledge store, the folder
  of files. A config file that still sets `store` stops every command
  with exit code 1 and names the file; delete the line. `BRIAR_STORE` is
  no longer read.
- The **credential** store is always the env file in 2.0; a
  `BRIAR_DEFAULT_STORE` left from 1.x that names a removed store stops
  the command (see [auth.md](auth.md)).

## `briar init`

### What it does

Writes a starter `.briar.toml` in the current folder and prints it.
It fills in the company and the repo. The owner and repo
come from your flags, else from your git `origin` remote.

### Use it when

- You start using briar in a repo.
- You want commands to stop asking for `--company`, `--owner` and `--repo`.

### Examples

```bash
# Inside your repo: write .briar.toml for the company acme.
briar init --company acme
```

You get `.briar.toml` with `company = "acme"` and the owner and repo
from your git remote. briar prints `wrote .briar.toml` on
stderr and the file on stdout.

```bash
# Set the repo by hand. Replace an old file.
briar init --company acme --owner acme-co --repo acme-app --force
```

You get a new `.briar.toml` with those values, even if one was there.

```bash
# Put the file one folder up, so it covers several repos.
briar init --company acme --path ../.briar.toml
```

You get `../.briar.toml`. Commands run in any folder below it find it.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--company` | The company name to write. | Empty (`company = ""`), or `BRIAR_COMPANY` if set. | `--company acme` |
| `--owner` | The repo owner to write. | From your git `origin` remote, else empty. | `--owner acme-co` |
| `--repo` | The repo name to write. | From your git `origin` remote, else empty. | `--repo acme-app` |
| `--path` | Where to write the file. | `./.briar.toml` | `--path ../.briar.toml` |
| `--force` | Replace the file if it already exists. | off | `--force` |

### Good to know

- Without `--force`, an existing file stops `init` with
  `.briar.toml already exists`, and exit code 1.
- briar only finds a file named `.briar.toml` (or a `pyproject.toml`).
  A file written with another `--path` name is not read.
- The file also has a commented-out `telemetry` line. Remove the `#`
  to send error reports (see [Telemetry](#telemetry)).
- If a config file already exists above the current folder, its values
  become the defaults for `init` too.
- Since 1.1.75 the Docker image `iklob1/briar` has `git`, so briar can
  read the `origin` remote of the repo you mount. Since 1.1.79 this
  works whoever owns the folder. In 1.1.75 to 1.1.78, on Linux, git
  refused a folder not owned by the container user (uid 1000)
  ("detected dubious ownership"), and `briar doctor` showed `warn` for
  `git remote`. With those versions, pass `--owner` and `--repo`, or set
  `[repo]` in `.briar.toml`, which briar reads without git. Before
  1.1.75 the image had no `git`, so this was always needed.

## `briar config show`

### What it does

Prints each project setting, its value, and its source: `env (BRIAR_COMPANY)`,
`config (.briar.toml)` (with `[repo]` for keys in that table), `inferred (git origin)`, or `-` for `(unset)`. It prints the config file
path on stderr first.

The settings are `company`, `root`, `tracker`, `owner`, `repo` and
`provider`.

### Use it when

- A command uses a company or repo you did not expect.
- You want to check that `.briar.toml` is found.

### Examples

```bash
# Show every setting and where it came from.
briar config show
```

You get a table with the columns `setting`, `value` and `source`, plus
`config file: <path>` on stderr.

```bash
# The same, as JSON for a script.
briar config show --format json
```

You get a JSON list of `{setting, value, source}` rows.

```bash
# Check which company a command will use, from another folder.
BRIAR_COMPANY=acme briar config show
```

You get `company  acme  env (BRIAR_COMPANY)` in the table.

### All parameters

`config show` has no flags of its own. The global `--format` flag works.

### Good to know

- `config show` cannot see flags, because you type those per command. A
  flag always wins over what it shows.
- With no config file, the stderr line says `(none found ...)` and the
  values come from env vars and git only.

## `briar doctor`

### What it does

Runs quick checks on this machine and prints one row per check, with
the status `ok`, `warn` or `fail`. It makes no network calls and opens
no database. It exits with code 1 only when a check fails, so you can
use it in CI.

| Check | `ok` when | Otherwise |
|---|---|---|
| `python` | Python is 3.10 or newer. | `fail` |
| `briar` | Always. Shows the briar version. | |
| `project config` | A `.briar.toml` (or `[tool.briar]`) is found. | `warn`: run `briar init`. |
| `git remote` | An `origin` remote gives an owner and repo. | `warn`: pass `--owner` and `--repo`, or set them in the config. |
| `github token` | `GITHUB_TOKEN` is set. | `warn`: the GitHub extractors need it. |

### Use it when

- You set up a new machine.
- A command fails and you want a quick look at the basics.
- You want a CI step that fails on a broken setup.

### Examples

```bash
# Check this machine.
briar doctor
```

You get a table with the columns `check`, `status` and `detail`.

```bash
# The same as JSON, for CI.
briar doctor --format json
```

You get a JSON list of `{check, status, detail}` rows.

```bash
# Save the result as a CSV file for a report.
briar doctor --format csv > doctor.csv
```

You get `doctor.csv` with a header row and one row per check.

### All parameters

`doctor` has no flags of its own. The global `--format` flag works.

### Good to know

- `doctor` only checks that keys are set, not that they work.
- For the keys one source needs, use `briar auth status <target>
  --company <name>` (see [auth.md](auth.md)).

## Shell completion

### What it does

Adds tab completion to your shell. It completes command names, subcommand
names, flag names and the values of flags with a fixed list (such as
`--format table|json`). It does not complete hidden flags. `briar extract`
completes as a command name only: its flags still use the 1.x parser
until a later 2.0 change.

`briar completion bash` / `zsh` from 1.x was removed in 2.0. The two
global flags below replace it.

### Use it when

- You want `briar <Tab>` to list commands, and `briar auth login --<Tab>` to list flags.

### Examples

Install it for the shell you are using now:

```bash
briar --install-completion
```

briar prints which shell it found and the file it wrote. Open a new
terminal to use it.

To see the script first, or to load it yourself:

```bash
briar --show-completion
```

It prints the script for the shell you are using now.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--install-completion` | Write the completion script for the current shell and load it from your shell config. | off | `briar --install-completion` |
| `--show-completion` | Print the completion script for the current shell. | off | `briar --show-completion` |

### Good to know

- The script calls briar on every Tab, so completion always matches the
  installed version; there is nothing to make again after an upgrade.
- Neither flag reads your config, keys or telemetry settings, and a Tab
  press does not either.

## Global flags

These flags work with every command, before or after the command name.
`briar --format json doctor` and `briar doctor --format json` do the
same thing.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--format` | The output format: `table` or `json`. 1.x also had `yaml`, `csv` and `quiet`; 2.0 removed them (exit code 2). | `table` (a single record still prints as JSON) | `--format json` |
| `--verbose`, `-v` | Show DEBUG logs on stderr. | off | `--verbose` |
| `--version`, `-V` | Print `briar-cli <version>` and stop, with no other work. | off | `--version` |

### `--format`

- `table` prints a table for lists. One record prints as JSON instead.
- `json` prints the same data as JSON.
- Only these commands use `--format`: `config show`, `doctor`,
  `runbook extract` and `runbook sweep`. Other commands ignore it.

```bash
# Read the doctor checks as JSON with jq.
briar --format json doctor | jq '.[].check'
```

You get one check name per line.

### `--verbose`

```bash
# See every step briar takes while it checks your setup.
briar --verbose doctor
```

You get the normal output, plus DEBUG lines on stderr: the arguments,
and which flags the config file and git filled in.

- Logs always go to stderr, so `--format json` output on stdout stays clean.
- Without `--verbose`, briar logs warnings and errors only. 1.x also read
  `BRIAR_VERBOSE` and `BRIAR_LOG_LEVEL`; 2.0 reads neither.
- `runbook` commands log at INFO level even without `--verbose`.
- Logs from HTTP and AWS libraries stay quiet even with `--verbose`.
  Set `BRIAR_LIB_DEBUG=1` to see them too.

## Startup work

Before any command runs (but not for `-h` or `--version`), briar:

1. Loads `secrets.env` into the environment (see [auth.md](auth.md)).
2. Reads `.briar.toml` and your git remote for default values.
3. Sets up telemetry, which is off unless you turned it on (see [Telemetry](#telemetry)).

After a command ends, briar may print a "new version available" note on
stderr. It checks PyPI at most once a day. `BRIAR_NO_UPDATE_CHECK=1`
turns that off.

## Telemetry

briar sends nothing by default. You can turn on error reports, or error
reports and usage counts, so bugs get fixed faster. 1.x had a
`briar telemetry` command for this; 2.0 removed it.

| Source, first wins | Example | Effect |
|---|---|---|
| `DO_NOT_TRACK=1` | `export DO_NOT_TRACK=1` | Off, whatever else says. |
| `BRIAR_TELEMETRY` | `export BRIAR_TELEMETRY=errors-only` | `off`, `errors-only` or `full`. |
| `telemetry` in `.briar.toml` | `telemetry = "full"` | Same values. A value briar does not know is ignored with a warning. |
| Built-in default | | `off` |

- `errors-only` sends crash reports. `full` also sends one usage event per
  command (its name, how long it took, whether it worked).
- No prompts, file contents, ticket keys, repo names, paths or secret
  values leave your machine. Each event carries a hashed random install
  id, never your name, host or IP. The id lives in
  `$XDG_CONFIG_HOME/briar/install_id` (usually
  `~/.config/briar/install_id`); delete the file for a new one.
- 1.x saved its choice in `telemetry.json`, and the 2.0 betas in
  `telemetry.v2.json`. 2.0 reads neither, so set the tier again if you
  want it on.

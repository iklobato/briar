# Setup: `init`, `config show`, `doctor`, `completion` and global flags

These commands help you set briar up and check the setup:

| Command | What it does |
|---|---|
| `briar init` | Writes a starter `.briar.toml` project config file. |
| `briar config show` | Shows each setting's value and where it came from. |
| `briar doctor` | Checks your machine: Python, config, git, keys, store. |
| `briar completion bash` / `zsh` | Prints a shell script for tab completion. |

This page also covers the global flags `--format`, `--verbose` and
`--version`, which work with every command.

## How briar picks a setting

Many flags repeat the same values on every run: the company, the store,
the repo. briar can take them from other places, so you type less. For
each flag, the first source that has a value wins:

1. The flag you type, such as `--company acme`.
2. An env var: `BRIAR_COMPANY` for the company, `BRIAR_STORE` for the knowledge store.
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
store   = "file"
root    = "./knowledge"
tracker = "jira"
repos   = ["acme-co/acme-app", "acme-co/acme-shared"]

[repo]
owner    = "acme-co"
repo     = "acme-app"
provider = "github"

[agent]
model          = "claude-sonnet-4-6"
git_user_name  = "acme-bot"
git_user_email = "bot@acme.com"
```

| Key | Fills these flags | Env var that beats it |
|---|---|---|
| `company` | Every `--company`. | `BRIAR_COMPANY` |
| `store` | Every knowledge `--store` (and `--storage` on `extract`). | `BRIAR_STORE` |
| `root` | Every `--root`, and `--knowledge` on `agent`. | none |
| `tracker` | Every `--tracker`. | none |
| `repos` | The repeatable `--repo` list on `extract`. | none |
| `[repo] owner`, `repo` | Every `--owner` and `--repo`. On `extract`, they form one `owner/repo` entry when `repos` is not set. | none |
| `[repo] provider` | Every `--provider`. | none |
| `[agent] model` | Every `--model` (on `agent`, `plan`, `chat` and `scaffold`). | none |
| `[agent] git_user_name`, `git_user_email` | `--git-user-name` and `--git-user-email` on `agent` and `plan run`. | none |

In pyproject.toml, put the same keys under `[tool.briar]`, with
`[tool.briar.repo]` and `[tool.briar.agent]` for the two tables.

### Watch out

- A key fills **every** flag with that name. So `store` and `root` also
  set `--store` and `--root` on `briar journal`, which only knows the
  `file` store and the `./journal` folder. Pass
  `--store file --root ./journal` to `journal` if you set them.
- `[agent] model` also becomes the default for `briar chat --model`.
  That name may not fit another `--llm` provider.
- `BRIAR_STORE` sets the **knowledge** store. The **credential** store
  for `briar auth` is set by `BRIAR_DEFAULT_STORE`. They are different.

## `briar init`

### What it does

Writes a starter `.briar.toml` in the current folder and prints it.
It fills in the company, the store and the repo. The owner and repo
come from your flags, else from your git `origin` remote.

### Use it when

- You start using briar in a repo.
- You want commands to stop asking for `--company`, `--owner` and `--repo`.

### Examples

```bash
# Inside your repo: write .briar.toml for the company acme.
briar init --company acme
```

You get `.briar.toml` with `company = "acme"`, `store = "file"`, and the
owner and repo from your git remote. briar prints `wrote .briar.toml` on
stderr and the file on stdout.

```bash
# Use Postgres and set the repo by hand. Replace an old file.
briar init --company acme --store postgres --owner acme-co --repo acme-app --force
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
| `--store` | The knowledge store to write: `file` or `postgres`. | `file`, or `BRIAR_STORE` if set. | `--store postgres` |
| `--owner` | The repo owner to write. | From your git `origin` remote, else empty. | `--owner acme-co` |
| `--repo` | The repo name to write. | From your git `origin` remote, else empty. | `--repo acme-app` |
| `--path` | Where to write the file. | `./.briar.toml` | `--path ../.briar.toml` |
| `--force` | Replace the file if it already exists. | off | `--force` |

### Good to know

- Without `--force`, an existing file stops `init` with
  `.briar.toml already exists`, and exit code 1.
- briar only finds a file named `.briar.toml` (or a `pyproject.toml`).
  A file written with another `--path` name is not read.
- The file also has a commented-out `[agent]` table. Remove the `#`
  marks to use it.
- If a config file already exists above the current folder, its values
  become the defaults for `init` too.
- The Docker image `iklob1/briar` has no `git`. Inside it, briar cannot
  read your `origin` remote, so pass `--owner` and `--repo`, or set
  `[repo]` in `.briar.toml`. `briar doctor` shows `warn` for `git remote` there.

## `briar config show`

### What it does

Prints each project setting, its value, and its source: `env (BRIAR_COMPANY)`,
`config (.briar.toml)` (with `[repo]` or `[agent]` for keys in those
tables), `inferred (git origin)`, or `-` for `(unset)`. It prints the config file
path on stderr first.

The settings are `company`, `store`, `root`, `tracker`, `owner`, `repo`,
`provider`, `model`, `git_user_name` and `git_user_email`.

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
| `llm key` | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GEMINI_API_KEY` is set. | `warn`: `agent` and `plan` need one. |
| `github token` | `GITHUB_TOKEN` is set. | `warn`: GitHub extractors and agents need it. |
| `store` | The store is `file`, or it is `postgres` and a database URL is set (`BRIAR_DATABASE_URL` or any `BRIAR_<NAME>_DATABASE_URL`). | `fail` |

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
- The `llm key` check does not count `CLAUDE_CODE_OAUTH_TOKEN`. If you
  use that token for Anthropic, the `warn` there is safe to ignore.
- For the keys each runbook needs, use `briar secrets doctor` (see
  [creds.md](creds.md)).

## `briar completion`

### What it does

Prints a script that adds tab completion to your shell. It completes
command names, subcommand names and flag names. It does not complete
flag values or hidden flags.

### Use it when

- You want `briar <Tab>` to list commands, and `briar extract --<Tab>` to list flags.

### Examples

For bash, add this line to `~/.bashrc`:

```bash
eval "$(briar completion bash)"
```

You get completion in every new bash shell.

For zsh, add this line to `~/.zshrc`, after `compinit` runs (most zsh
setups already run it):

```bash
eval "$(briar completion zsh)"
```

You get completion in every new zsh shell.

To start your shell faster, save the script once and load the file:

```bash
briar completion zsh > ~/.briar-completion.zsh
```

You get the file `~/.briar-completion.zsh`. Add `source ~/.briar-completion.zsh` to `~/.zshrc`.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `shell` (positional) | The shell: `bash` or `zsh`. Required. | none | `zsh` |

### Good to know

- The command and flag names are fixed inside the script. After you
  upgrade briar, make the script again so new flags show up.

## Global flags

These flags work with every command, before or after the command name.
`briar --format json doctor` and `briar doctor --format json` do the
same thing.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--format` | The output format: `table`, `json`, `yaml`, `csv` or `quiet`. | `table` (a single record still prints as JSON) | `--format json` |
| `--verbose`, `-v` | Show DEBUG logs on stderr. `BRIAR_VERBOSE=1` does the same. | off | `--verbose` |
| `--version`, `-V` | Print `briar-cli <version>` and stop, with no other work. | off | `--version` |

### `--format`

- `table` prints a table for lists. One record (such as `telemetry status`)
  prints as JSON instead.
- `json`, `yaml` and `csv` print the same data in those formats.
- `quiet` prints only the `id` field of each row, one per line. Most
  briar rows have no `id`, so it often prints nothing. Use it when you
  only care about the exit code.
- Only these commands use `--format`: `config show`, `doctor`,
  `context put`, `context list`, `context categories`, `runbook extract`,
  `runbook sweep`, the `telemetry` subcommands except `preview`, and the
  `plan` subcommands. Other commands ignore it.

```bash
# Read the context list as JSON with jq.
briar --format json context list | jq '.[].name'
```

You get one blob name per line.

### `--verbose`

```bash
# See every step briar takes while it checks your setup.
briar --verbose doctor
```

You get the normal output, plus DEBUG lines on stderr: the arguments,
and which flags the config file and git filled in.

- Logs always go to stderr, so `--format json` output on stdout stays clean.
- Without `--verbose`, briar logs warnings and errors only.
  `BRIAR_LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING` or `ERROR`) changes that.
- `runbook` commands log at INFO level even without `--verbose`.
- Logs from HTTP and AWS libraries stay quiet even with `--verbose`.
  Set `BRIAR_LIB_DEBUG=1` to see them too.

## Startup work

Before any command runs (but not for `-h` or `--version`), briar:

1. Loads `secrets.env` into the environment (see [auth.md](auth.md)).
2. Sets up the decision journal (see [journal.md](journal.md)).
3. Sets up telemetry and shows the first-run notice once (see [telemetry.md](telemetry.md)).
4. Reads `.briar.toml` and your git remote for default values.

After a command ends, briar may print a "new version available" note on
stderr. It checks PyPI at most once a day. `BRIAR_NO_UPDATE_CHECK=1`
turns that off.

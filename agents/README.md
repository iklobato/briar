# briar command manuals

This folder has one manual per `briar` command. People read them, and so
do AI agents that run briar. If you are an agent, read this page first,
then the one file for your task.

The output of `briar <command> -h` is the final word on flags. These
pages explain what the flags do, when to use them, and what can go wrong.
They match briar-cli 1.1.75.

## How each manual is laid out

| Section | What it tells you |
|---|---|
| What it does | The command in a few sentences. |
| Use it when | Real situations where you reach for it. |
| Before you start | Credentials, files and tools it needs. |
| Examples | Commands you can run, each with what you get. |
| All parameters | Every flag: meaning, default, allowed values, example. |
| Good to know | Limits, traps and fixes for common errors. |

## The files

| File | Commands |
|---|---|
| [flows.md](flows.md) | Recipes that chain several commands into one result. Start here for "how do I...". |
| [setup.md](setup.md) | `briar init`, `briar doctor`, shell completion, telemetry: set up a project and check it. |
| [extract.md](extract.md) | `briar extract`: read your tools into a knowledge blob (23 extractors). |
| [serve.md](serve.md) | `briar serve`: watch the tickets assigned to briar and the review comments on its PRs (GitHub, Jira). |
| [runbook.md](runbook.md) | `briar runbook extract`, `serve`: run extractors from YAML, once or on a schedule. |
| [auth.md](auth.md) | `briar auth login`, `status`: save credentials and see which are set. |

## Flags that work on every command

You can put these anywhere on the command line.

| Flag | Default | What it does |
|---|---|---|
| `--format table\|json` | `table` for lists, `json` for one record. | Output shape. Use `json` when another program reads the output. |
| `--verbose`, `-v` | Off. | Debug logs on stderr. Without it, briar logs only warnings and errors. |
| `--version`, `-V` | Off. | Print `briar-cli <version>` and exit. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | It worked. |
| `1` | It ran and failed. Examples: missing credentials, a service error, a runbook with a block briar 2.0 removed, or you said no to a prompt. |
| `2` | Bad flags. Also used when a command crashes with an unexpected error. |
| `130` | You pressed Ctrl-C. |

Check the exit code before you read the output.

## Where settings come from

briar looks up each setting in this order and takes the first one it finds:

1. The flag you typed.
2. An environment variable (`BRIAR_COMPANY`).
3. The project file `.briar.toml`, or `[tool.briar]` in `pyproject.toml`.
   briar looks in the current folder, then each parent folder.
4. The git `origin` remote, for the repo owner and name only.
5. The built-in default.

`briar doctor` prints each setting and where it came from. See
[setup.md](setup.md).

## The knowledge store and blob names

briar saves knowledge as named markdown blobs in one store, a folder of
files: a blob is `./knowledge/<prefix>/<rest>.md`, so `knowledge:acme`
is `./knowledge/knowledge/acme.md`. Change the folder with `--root` (or
`root` in `.briar.toml`). 1.x could also use Postgres; 2.0 removed it,
and `store` in `.briar.toml` (or `store: postgres` in a runbook) now
stops the command with exit code 1.

| Blob name | Written by | Read by |
|---|---|---|
| `knowledge:acme` | `briar extract`, `briar runbook` | You, and any agent you point at it. |
| `inventory:acme` | `briar runbook`, when its `knowledge:` block has `config: {inventory: "true"}` | You (JSON with every resource). |

## Credentials

briar reads credentials from environment variables. At startup it also
loads one secrets file, the first that exists:

1. The path in `BRIAR_SECRETS_FILE`.
2. `/etc/briar/secrets.env` (for servers).
3. `$XDG_CONFIG_HOME/briar/secrets.env`, or `~/.config/briar/secrets.env`.

A variable already set in your shell wins over the file.
`briar auth login` writes to this file. Every login carries the company
and the source in its name, like `BRIAR_ACME_JIRA_TOKEN` for company
`acme`; the 1.x names (`GITHUB_TOKEN`, `JIRA_ACME_TOKEN`) are no longer
read. The non-secret choices (Jira `site`, Bitbucket `workspace`) go in
`[auth]` of `.briar.toml`.

When a command fails for lack of credentials, run
`briar auth status <source> --company <name>`: it lists the keys that
source needs and which are set. See [auth.md](auth.md).

To read what briar saved, open the file: `knowledge:acme` is
`./knowledge/knowledge/acme.md` in the file store. 1.x had
`briar context` for this; 2.0 removed it.

## Rules for AI agents that run briar

1. Read `briar <command> -h` before you use a flag you have not seen.
   Do not guess flag names.
2. Use `--format json` when you parse output, and check the exit code first.
3. Do not say a command worked until you have run it and read its output.
4. Never push to `main`, `master` or `dev`, and never force-push a shared branch.
5. Start any comment you post for a person with `[AI] `.
6. Do not skip git hooks (`--no-verify`) unless the person asked for it.

# briar command manuals

This folder has one manual per `briar` command. People read them, and so
do AI agents that run briar. If you are an agent, read this page first,
then the one file for your task.

The output of `briar <command> -h` is the final word on flags. These
pages explain what the flags do, when to use them, and what can go wrong.
They match briar-cli 1.1.72.

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
| [setup.md](setup.md) | `briar init`, `briar config show`, `briar doctor`, `briar completion`: set up a project and check it. |
| [version.md](version.md) | `briar version`: print the installed version. |
| [extract.md](extract.md) | `briar extract`: read your tools into a knowledge blob (23 extractors). |
| [runbook.md](runbook.md) | `briar runbook extract`, `sweep`, `serve`: run extractors from YAML, once or on a schedule. |
| [agent.md](agent.md) | `briar agent implement`, `briar agent prfix`: an AI agent that opens a draft PR for a ticket, or fixes review comments on a PR. |
| [plan.md](plan.md) | `briar plan build`, `show`, `status`, `next`, `advance`, `run`, `list`, `clear`: turn a board into a plan and run it card by card. |
| [scaffold.md](scaffold.md) | `briar scaffold implementation`, `briar scaffold pr-fixes`: write a JSON config bundle for an agent workflow. |
| [context.md](context.md) | `briar context put`, `get`, `list`, `delete`, `categories`: read and write knowledge blobs. |
| [dashboard.md](dashboard.md) | `briar dashboard`: a read-only status page. |
| [auth.md](auth.md) | `briar auth login`, `logout`, `refresh`, `list`, `status`: save and manage credentials. |
| [creds.md](creds.md) | `briar secrets doctor`, `briar secrets bootstrap`: find missing credentials. |
| [journal.md](journal.md) | `briar journal list`, `show`, `export`: read the decisions that `plan run` and `scaffold` recorded. |
| [mcp.md](mcp.md) | `briar mcp serve`, `briar chat`: serve briar as MCP tools, or use them from a chat in your terminal. |
| [telemetry.md](telemetry.md) | `briar telemetry status`, `preview`, `off`, `errors-only`, `full`, `reset`: control what usage data is sent. |

## Flags that work on every command

You can put these anywhere on the command line.

| Flag | Default | What it does |
|---|---|---|
| `--format table\|json\|yaml\|csv\|quiet` | `table` for lists, `json` for one record. | Output shape. Use `json` when another program reads the output. Use `quiet` when only the exit code matters. |
| `--verbose`, `-v` | Off. | Debug logs on stderr. `BRIAR_VERBOSE=1` does the same. Without it, briar logs only warnings and errors. |
| `--version`, `-V` | Off. | Print `briar-cli <version>` and exit. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | It worked. |
| `1` | It ran and failed. Examples: missing credentials, a service error, an agent run that failed, `plan run` stopped on a blocked card, or you said no to a prompt. |
| `2` | Bad flags. Also used when a command crashes with an unexpected error. |
| `130` | You pressed Ctrl-C. |

Check the exit code before you read the output.

## Where settings come from

briar looks up each setting in this order and takes the first one it finds:

1. The flag you typed.
2. An environment variable (`BRIAR_COMPANY`, `BRIAR_STORE`).
3. The project file `.briar.toml`, or `[tool.briar]` in `pyproject.toml`.
   briar looks in the current folder, then each parent folder.
4. The git `origin` remote, for the repo owner and name only.
5. The built-in default.

`briar config show` prints each setting and where it came from. See
[setup.md](setup.md).

## Knowledge stores and blob names

briar saves knowledge as named markdown blobs in a store.

| `--store` | Where blobs live | Default for |
|---|---|---|
| `file` | `./knowledge/<prefix>/<rest>.md`, so `knowledge:acme` is `./knowledge/knowledge/acme.md`. Change the folder with `--root`. | Every command when `BRIAR_DATABASE_URL` is not set. `context`, `mcp serve` and `chat` even when it is set. |
| `postgres` | The database in `BRIAR_DATABASE_URL`. A runbook can pick another one per company. | `extract`, `agent` and `plan` when `BRIAR_DATABASE_URL` is set. |

`BRIAR_STORE=postgres`, or `store = "postgres"` in `.briar.toml`, makes
every command use Postgres.

| Blob name | Written by | Read by |
|---|---|---|
| `knowledge:acme` | `briar extract`, `briar runbook` | The agent (only the sections it uses), the plan selector, `plan build --with-knowledge`. |
| `knowledge:acme.q3` | `briar plan build`, then `plan run` after each card | The plan selector. |
| `inventory:acme` | `briar runbook`, when its `knowledge:` block has `config: {inventory: "true"}` | You (JSON with every resource). |
| `plan:q3` | `briar plan build`, `advance`, `run` | Every `briar plan` subcommand. |
| Any other name, like `memory:reviewer-alice` | `briar context put` | You. The agent never reads it. |

## Credentials

briar reads credentials from environment variables. At startup it also
loads one secrets file, the first that exists:

1. The path in `BRIAR_SECRETS_FILE`.
2. `/etc/briar/secrets.env` (for servers).
3. `$XDG_CONFIG_HOME/briar/secrets.env`, or `~/.config/briar/secrets.env`.

A variable already set in your shell wins over the file.
`briar auth login` writes to this file. Most variables carry the company
name in upper case, like `JIRA_ACME_TOKEN` for company `acme`.
`GITHUB_TOKEN` and the LLM keys have no company part.

When a command fails for lack of credentials, run `briar secrets doctor`.
It lists what each runbook still needs. See [creds.md](creds.md) and
[auth.md](auth.md).

## The journal

`briar plan run` and `briar scaffold` record each decision they make in
a journal session under `./journal/`. Other commands do not write one.
Read them with `briar journal list`, `show` and `export`. Sessions are
append-only. Set `BRIAR_JOURNAL=off` to turn the journal off. See
[journal.md](journal.md).

## Rules for AI agents that run briar

1. Read `briar <command> -h` before you use a flag you have not seen.
   Do not guess flag names.
2. Run `briar agent ... --dry-run` before a real agent run. It is free.
3. Use `--format json` when you parse output, and check the exit code first.
4. Do not say a command worked until you have run it and read its output.
5. Never push to `main`, `master` or `dev`, and never force-push a shared branch.
6. Start any comment you post for a person with `[AI] `.
7. Do not skip git hooks (`--no-verify`) unless the person asked for it.

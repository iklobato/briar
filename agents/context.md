# `briar context`

`briar context` lets you read and write **blobs**. A blob is one named
markdown text that briar keeps in its knowledge store. `briar extract`
writes blobs. `briar agent` and `briar plan` read them. With `context`
you can look at them, fix them by hand, or add your own notes.

A blob name has two parts: `category:name`. The part before the `:` is
the **category**. For example, `knowledge:acme` is in the `knowledge`
category.

| Name | Who writes it |
|---|---|
| `knowledge:acme` | `briar extract --company acme` |
| `knowledge:acme.q3` | `briar plan build` and `plan run` (notes for plan `q3`) |
| `plan:q3` | `briar plan build` (the plan itself, in a fixed format) |
| `inventory:acme` | A runbook with `inventory: "true"` (see [runbook.md](runbook.md)) |
| `memory:acme`, `lessons:acme` | You, with `briar context put` |

It has five subcommands: `put`, `get`, `list`, `delete` and `categories`.

## Where blobs live

The **store** is where the blobs are kept. Pick it with `--store`:

| Store | Where blobs live | What you need |
|---|---|---|
| `file` (default) | Markdown files under `--root` (default `./knowledge`). `knowledge:acme` is the file `./knowledge/knowledge/acme.md`. A name with no `:` sits right under the root. A name with a `/` or ending in `.md` is used as a file path as is. | Nothing. |
| `postgres` | Tables in a Postgres database. Every `put` also adds a row to a history table. | The env var `BRIAR_DATABASE_URL`. |

`context` always uses `file` unless you ask for `postgres`. That is true
even when `BRIAR_DATABASE_URL` is set. To switch, pass `--store postgres`,
set `BRIAR_STORE=postgres`, or put `store = "postgres"` in `.briar.toml`.
(`extract`, `agent` and `plan` pick `postgres` on their own when
`BRIAR_DATABASE_URL` is set.)

`--store` and `--root` work before or after the subcommand. These two
lines do the same thing:

```bash
briar context --store postgres list
briar context list --store postgres
```

You get the list of blobs from Postgres both times.

## `briar context put`

### What it does

Creates a blob or replaces it. It does not ask before it overwrites.
It prints the saved blob's details as JSON: name, category, size in
bytes and (for `file`) the path.

### Use it when

- You want to add notes that you keep across runs, such as `lessons:acme`.
- You want to fix a wrong line in an extracted `knowledge:` blob before an agent run.

### Examples

```bash
# Save a file as a blob.
briar context put memory:acme --from-file notes.md
```

You get `./knowledge/memory/acme.md` and its details as JSON.

```bash
# Save a short text.
briar context put lessons:acme --content "Run make test before you push."
```

You get the blob `lessons:acme` with that one line.

```bash
# Save what another command prints. "-" means "read from stdin".
cat notes.md | briar context put memory:acme --content -
```

You get the blob filled with the text from the pipe.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `blob_name` (positional) | The blob to create or replace. Required. | none | `memory:acme` |
| `--content` | The text to save. Use `-` to read it from stdin. | none | `--content "Use pnpm, not npm."` |
| `--from-file` | Read the text from this file. | none | `--from-file notes.md` |
| `--category` | Set the category by hand. The `postgres` store saves it. The `file` store always takes the category from the name. | The part of the name before `:`. | `--category memory` |
| `--store` | `file` or `postgres`. | `file` (or `BRIAR_STORE`, or `store` in `.briar.toml`) | `--store postgres` |
| `--root` | The folder for the `file` store. | `./knowledge` (or `root` in `.briar.toml`) | `--root ./kb` |

### Good to know

- If you pass both `--content` and `--from-file`, `--content` wins.
- If you pass neither, briar reads stdin when it is a pipe. In a plain
  terminal it stops with `no content provided`.
- In bash, `"\n"` inside double quotes is not a new line. Use `--from-file` for text with many lines.
- Do not write `plan:` blobs by hand. They have a fixed format that
  `briar plan` reads. Use the `briar plan` commands to change a plan.
- There is no undo for the `file` store. Copy the file first if you are not sure.

## `briar context get`

### What it does

Prints the body of one blob to stdout.

### Use it when

- You want to read what `briar extract` found.
- You want to save a blob to a file before you change it.

### Examples

```bash
# Read the acme knowledge.
briar context get knowledge:acme
```

You get the markdown text of `knowledge:acme`.

```bash
# Save a copy before you edit it.
briar context get knowledge:acme > acme-backup.md
```

You get the blob in the file `acme-backup.md`.

```bash
# Read a plan's notes from Postgres.
briar context get knowledge:acme.q3 --store postgres
```

You get the notes that `briar plan` keeps for the plan `q3`.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `blob_name` (positional) | The blob to print. Required. | none | `knowledge:acme` |
| `--store` | `file` or `postgres`. | `file` | `--store postgres` |
| `--root` | The folder for the `file` store. | `./knowledge` | `--root ./kb` |

### Good to know

- A missing blob (or an empty one) prints `error: blob not found: <name>` and exits with code 1.
- If you are sure the blob exists, check that `--store` and `--root` point at the right place.

## `briar context list`

### What it does

Lists the blobs in the store with their name, category, size
(`byte_count`) and last change time (`updated_at`).

### Use it when

- You want to see what is in the store.
- You want to check that an extract or a runbook wrote its blob.

### Examples

```bash
# Every blob.
briar context list
```

You get a table with one row per blob.

```bash
# Only names that start with "knowledge:".
briar context list --prefix knowledge:
```

You get only the `knowledge` blobs.

```bash
# The same list as JSON, for a script.
briar context list --format json
```

You get a JSON list you can read with `jq`.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--prefix` | Only show names that start with this text. | Empty (show all). | `--prefix memory:` |
| `--store` | `file` or `postgres`. | `file` | `--store postgres` |
| `--root` | The folder for the `file` store. | `./knowledge` | `--root ./kb` |

The global `--format` flag (`table`, `json`, `yaml`, `csv`, `quiet`)
changes the output (see [setup.md](setup.md)).

### Good to know

- The `file` store lists only `.md` files under `--root`. Other files do
  not show up, such as `./knowledge/acme.inventory.json` from a runbook.
- With the `file` store, `updated_at` is a Unix time stamp in seconds.

## `briar context delete`

### What it does

Removes one blob. It asks `Delete blob <name> from store <store>? [y/N]`
first, unless you pass `--yes`.

### Use it when

- A blob is out of date and you want it gone.
- You wrote a blob under the wrong name.

### Examples

```bash
# Delete a note. briar asks first.
briar context delete memory:acme
```

You get the question, then `deleted memory:acme`.

```bash
# Delete with no question, for scripts.
briar context delete memory:acme --yes
```

You get `deleted memory:acme`, or `not found memory:acme` if it was not there.

```bash
# Delete a note from a store in another folder.
briar context delete lessons:acme --root ./kb --yes
```

You get `./kb/lessons/acme.md` removed.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `blob_name` (positional) | The blob to remove. Required. | none | `memory:acme` |
| `--yes` | Skip the question. | off | `--yes` |
| `--store` | `file` or `postgres`. | `file` | `--store postgres` |
| `--root` | The folder for the `file` store. | `./knowledge` | `--root ./kb` |

### Good to know

- Without a terminal (a pipe or a script), the question counts as "no".
  briar prints `aborted` and exits with code 1. Pass `--yes`.
- `not found` still exits with code 0.

## `briar context categories`

### What it does

Lists each category in the store and how many blobs it holds.

### Use it when

- You want a quick overview of the store.

### Examples

```bash
# Count blobs per category.
briar context categories
```

You get a table with the columns `category` and `blob_count`. Blobs with no category show as `(none)`.

```bash
# The same counts from Postgres.
briar context categories --store postgres
```

You get the counts for the blobs in Postgres.

```bash
# As JSON, for a script.
briar context categories --format json
```

You get a JSON list of `{category, blob_count}` rows.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--store` | `file` or `postgres`. | `file` | `--store postgres` |
| `--root` | The folder for the `file` store. | `./knowledge` | `--root ./kb` |

## Good to know for every subcommand

- `postgres` with no `BRIAR_DATABASE_URL` stops with
  `store 'postgres' requires a DSN`.
- `.briar.toml` can change the defaults: `store` sets `--store` and
  `root` sets `--root`. Run `briar config show` to see what is in effect.
- The knowledge store is not the same as the credential store. Keys and
  tokens are handled by `briar auth` (see [auth.md](auth.md)).

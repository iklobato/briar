# `briar context`

## Purpose
CRUD for named markdown blobs in the `KnowledgeStore`. Same backend
as `briar extract` writes to, same backend `briar agent implement`
reads from. Use this to inspect, edit, or delete the blobs that
drive other commands.

## Subcommands

| Op | Purpose |
|---|---|
| `put` | Create or update a blob |
| `get` | Print a blob's body to stdout |
| `list` | Enumerate stored blobs |
| `delete` | Remove a blob |
| `categories` | Print distinct category prefixes |

## When to use

- Inspect what `briar extract` wrote.
- Hand-edit a `knowledge:*` blob before a `plan build --with-knowledge`.
- Seed a free-form `memory:*` or `lessons:*` blob.
- Confirm a `KnowledgeWriter` updated `knowledge:<company>.<plan>`.

Do NOT use `put` to overwrite `plan:<name>` blobs by hand — they have
a wire format `save_plan` owns. Edit through `briar plan advance` etc.

## Prerequisites

| For | Need |
|---|---|
| `--store file` (default) | Read/write to `--root` (default `./knowledge`) |
| `--store postgres` | `BRIAR_DATABASE_URL` env var |

## Commands

### Read a blob

```bash
briar context get <BLOB_NAME>
# e.g.
briar context get knowledge:acme
briar context get knowledge:acme.acme-q3
briar context get plan:acme-q3
```

**The same with Docker:**

```bash
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context get <BLOB_NAME>
# e.g.
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context get knowledge:acme
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context get knowledge:acme.acme-q3
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context get plan:acme-q3
```

### Write a blob

Three input modes:

```bash
# Inline (small)
briar context put knowledge:demo --content "## title\n- bullet"

# From stdin
cat body.md | briar context put knowledge:demo --content -

# From a file
briar context put knowledge:demo --from-file ./body.md
```

**The same with Docker:**

```bash
# Inline (small)
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context put knowledge:demo --content "## title\n- bullet"

# From stdin
cat body.md | docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context put knowledge:demo --content -

# From a file
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context put knowledge:demo --from-file ./body.md
```

The category defaults to the part before `:` (`knowledge`, `plan`,
`memory`, `lessons`). Override with `--category` only when the
prefix lies.

### List

```bash
briar context list                              # everything
briar context list --prefix knowledge:          # one category
briar context list --format json                # machine-readable
```

**The same with Docker:**

```bash
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context list                              # everything
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context list --prefix knowledge:          # one category
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context list --format json                # machine-readable
```

### Delete

```bash
briar context delete <BLOB_NAME>

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context delete <BLOB_NAME>
```

No confirmation prompt. Double-check the name first.

### See what categories exist

```bash
briar context categories

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar context categories
```

## Verifying success

After `put`: `briar context get <name>` returns the same body.
After `delete`: `briar context get <name>` returns empty (the
"missing" convention is empty string, not error).

## Common failures

| Symptom | Fix |
|---|---|
| `briar context get` returns empty | Either the blob doesn't exist, or you're pointing at the wrong store. Check `--store` and `--root` |
| `put` clobbered something important | There's no undo. Use `briar journal show` if a prior command wrote it to find the body; otherwise restore from backup |
| `BRIAR_DATABASE_URL not set` with `--store postgres` | Either export it or use `--store file` |
| Blob shape looks corrupted (a plan/* blob is a non-JSON markdown body) | You wrote to a managed blob name. Restore by re-running whatever command owns that name (e.g. `briar plan build` for `plan:<name>`) |

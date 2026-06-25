# briar

**Turn the live state of your tools into agent-ready context — then let autonomous agents act on it. All on your machine.**

`briar` is a local-first Python CLI that mines what's actually happening across your stack — GitHub, Bitbucket, AWS/GCP/Azure, Jira, Linear, Fireflies — into a local knowledge store, then runs LLM agents that fix PRs and ship tickets against it. No SaaS, no remote workspace, no data leaving your laptop: your credentials, your machine, your APIs.

```bash
pip install briar-cli
briar version
```

Or run it with Docker, nothing to install:

```bash
docker pull iklob1/briar
docker run --rm iklob1/briar version
```

---

## Run with Docker

Every example below shows a native `briar …` line and its Docker equivalent.
The Docker form mounts what the CLI needs and is otherwise identical:

```bash
# Canonical invocation used throughout this README:
docker run --rm \
    -v "$PWD":/work -w /work \                               # your repo checkout
    -v "$HOME/.config/briar":/home/briar/.config/briar \     # stored credentials
    -e ANTHROPIC_API_KEY \                                   # LLM key from your env
    iklob1/briar <args>
```

- **`dashboard`** also needs a published port and a non-loopback bind:
  `docker run --rm -p 8080:8080 iklob1/briar dashboard --host 0.0.0.0`.
- **`agent` / `plan run`** push git, so add your SSH key and git identity:
  `-v "$HOME/.ssh":/home/briar/.ssh:ro -v "$HOME/.gitconfig":/home/briar/.gitconfig:ro`.
- Pass any extra provider env the same way, e.g. `-e FIREFLIES_ACME_API_KEY`.

---

## Quickstart

The common flow: authenticate a provider, mine its live state into knowledge, then act on it.

```bash
# 1. Authenticate the providers you'll use (tokens land in ~/.config/briar/secrets.env)
briar auth login github-pat --company acme
export ANTHROPIC_API_KEY=sk-ant-...        # LLM key comes from the environment

# 2. Mine a repo's PR history into a knowledge blob
briar extract --company acme \
    --include pr-archaeology \
    --repo acme-co/acme-app --max 50

# 3. Read it back
briar context get knowledge:acme

# 4. Let an agent act on a ticket — clone, branch, code, open a draft PR
briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ACME-42 --tracker jira
```

The same flow with Docker:

```bash
# 1. Authenticate (writes into the mounted ~/.config/briar)
docker run --rm -it -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar auth login github-pat --company acme

# 2. Extract
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar extract --company acme --include pr-archaeology \
    --repo acme-co/acme-app --max 50

# 3. Read it back
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    iklob1/briar context get knowledge:acme

# 4. Let an agent act on a ticket (git push → mount SSH + git identity)
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    -v "$HOME/.ssh":/home/briar/.ssh:ro -v "$HOME/.gitconfig":/home/briar/.gitconfig:ro \
    -e ANTHROPIC_API_KEY \
    iklob1/briar agent implement --company acme --repo acme-co/acme-app \
    --ticket-key ACME-42 --tracker jira
```

Add `--dry-run` to any `agent` command to preview the exact prompt and tools without spending a token.

> **Telemetry:** `briar` ships with opt-out error/usage analytics. No prompts, file contents, ticket keys, repo names, paths, or secret values ever leave the machine. Turn it off with `briar telemetry off`, `BRIAR_TELEMETRY=off`, or `DO_NOT_TRACK=1`.

---

## Less typing

briar resolves every flag through one chain — **CLI flag > env var > project config > built-in default** — so stable values move off the command line. Drop a `.briar.toml` at your repo root:

```toml
company = "acme"
store   = "postgres"

[repo]
owner = "acme-co"
repo  = "acme-app"
```

Then, inside the checkout, the same extract is just:

```bash
briar extract --include pr-archaeology     # company + repo come from config/git

# or with Docker (the mounted $PWD provides the .briar.toml + git checkout):
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar extract --include pr-archaeology
```

Setup helpers: `briar init` (write a starter `.briar.toml`), `briar config show` (resolved value + source for each setting), `briar doctor` (check config / git / creds / store), `eval "$(briar completion bash)"` (tab-completion, also zsh). Each runs under Docker with the canonical invocation, e.g. `docker run --rm -v "$PWD":/work -w /work iklob1/briar doctor`.

---

## More than extraction

- **`briar runbook serve runbooks/`** — keep per-company knowledge fresh on a schedule, in-process (no cron).
  Docker: `docker run --rm -v "$PWD":/work -w /work -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY iklob1/briar runbook serve runbooks/`
- **`briar agent prfix` / `implement`** — address a PR's open review comments, or take a ticket end-to-end (add the SSH + git-identity mounts shown above).
- **`briar plan build` / `run`** — turn a Jira / GitHub Projects board into an ordered plan and run it card by card.
- **`briar dashboard`** — read-only HTML status page:
  `docker run --rm -p 8080:8080 -v "$PWD":/work -w /work iklob1/briar dashboard --host 0.0.0.0`
- **`briar scaffold` · `context` · `secrets doctor` · `journal`** — config bundles, local knowledge blobs, credential coverage, decision audit.

Every command takes `--format json` for scripting, and most list flags repeat (`--include a --include b`).

---

## Install options

```bash
pip install briar-cli                # base: GitHub/Bitbucket/AWS, Jira/Linear, Anthropic + Bedrock, file + Postgres
pip install 'briar-cli[all]'         # everything (OpenAI, Gemini, MCP, GCP, Azure, Vault)

# or pull the all-batteries image (no Python install needed):
docker pull iklob1/briar             # tags: latest, and each release e.g. 1.1.50
```

Individual extras: `[openai]`, `[gemini]`, `[mcp]`, `[gcp]`, `[azure]`, `[vault]`. **Python 3.10+** (tested through 3.12).

---

## Documentation

Full command reference, every flag, runbook-YAML schema, configuration, and recipes:

**📖 [usebriar.com/docs](https://usebriar.com/docs)**

- End-to-end usage flows: [`agents/flows.md`](agents/flows.md)
- Per-command operator manual: [`agents/`](agents/README.md)
- Comprehensive multi-company runbook: [`examples/all_features.yaml`](examples/all_features.yaml)

---

## License

See [LICENSE](LICENSE).

# `briar mcp serve` and `briar chat`

**MCP** (Model Context Protocol) is a standard way for an AI app to call
tools in another program. An MCP **server** offers tools. An MCP
**host** (Claude Code, Claude Desktop, Cursor and others) connects to
the server and lets its AI model call those tools.

- `briar mcp serve` turns briar into an MCP server. An AI app can then
  read and edit briar's knowledge, read its runbook, and run extractors.
- `briar chat` is a small chat in your terminal. It starts
  `briar mcp serve` for you and lets an AI model use those tools while
  you talk to it.

## Install the `mcp` extra

Both commands need the MCP library, which is not in the base install:

```bash
pip install 'briar-cli[mcp]'
```

The Docker image `iklob1/briar` already has it.

`briar agent` and `briar plan run` use the same extra in the other
direction: there, briar is the MCP **host** and starts helper MCP
servers (see [agent.md](agent.md)). Since 1.1.74 they run without the
extra and skip the helpers. They need it only for servers listed under
`mcp:` in a runbook. On this page, briar is the **server**.

## The tools briar offers

| Tool | What it does | Changes data? |
|---|---|---|
| `version` | Returns the briar version. | no |
| `knowledge_list` | Lists blobs, with an optional name `prefix`. | no |
| `knowledge_get` | Returns one blob's text, or null if it does not exist. | no |
| `knowledge_categories` | Lists categories and how many blobs each has. | no |
| `knowledge_put` | Creates or replaces a blob. | yes |
| `knowledge_delete` | Deletes a blob. | yes |
| `runbook_get` | Returns the runbook as JSON. Keys appear only as env var names. | no |
| `runbook_validate` | Checks the runbook and reports the result. | no |
| `mcp_server_set_enabled` | Turns one entry in a company's `mcp:` block on or off, and saves the runbook file. | yes |
| `extract_run` | Runs extractors for a company and merges the result into the blob: the sections of the extractors that ran are replaced, all other sections stay. With no `include` list, it runs every extractor. Pass `repo` (a list of `owner/repo`) for the repo extractors; without it they are skipped. `tracker` (`jira`, `linear`, `github-issues`, `bitbucket-issues`) and `ticket_project` (Jira project keys, Linear teams, or `owner/repo` for Issues) feed the ticket extractors (since 1.1.94). `since_days` and `max_items` work like `briar extract --since-days` and `--max`. | yes |

A **blob** is one named markdown text in briar's knowledge store, such
as `knowledge:acme` (see [context.md](context.md)).

The four tools that change data are **gated**. Each one has a `confirm`
input that is `false` by default. With `confirm=false`, the tool only
returns a preview of what it would do (a dry run). It acts only when
called with `confirm=true`.

The `runbook_*` tools and `mcp_server_set_enabled` need `--runbook`.
Without it, they return the error `no runbook configured`.

## `briar mcp serve`

### What it does

Starts briar's MCP server. It has two **transports** (ways to connect):

- `stdio` (default): the host starts `briar mcp serve` as a child
  process and talks to it over stdin and stdout. Use this for apps on
  your own machine.
- `http`: briar listens on a network port (Streamable HTTP). The MCP
  address is `http://<host>:<port>/mcp`. Use this for remote or browser
  clients.

It runs until the host closes it or you press Ctrl-C. Logs go to
stderr, so stdout stays clean for the MCP messages.

### Use it when

- You want Claude Code, Claude Desktop or Cursor to read and edit briar's knowledge.
- You want an AI app to run extractors for you, with a preview first.
- You want a remote client to reach briar's tools over HTTP.

### Examples

```bash
# Start the server over stdio. A host app normally runs this line for you.
briar mcp serve
```

You get a server that waits for MCP messages on stdin.

Add briar to Claude Code with one line:

```bash
claude mcp add briar -- briar mcp serve
```

You get a server named `briar` in Claude Code. Its tools show up as
`mcp__briar__knowledge_list` and so on. Everything after `--` is the
command Claude Code runs.

```bash
# Let the tools use Postgres and the acme runbook.
claude mcp add briar -- briar mcp serve --store postgres --runbook runbooks/acme.yaml
```

You get the same server, now with the runbook tools turned on. Set
`BRIAR_DATABASE_URL` where Claude Code runs, or in `secrets.env`.

```bash
# Serve over HTTP on this machine only, port 8765.
briar mcp serve --transport http
```

You get an MCP endpoint at `http://127.0.0.1:8765/mcp`, with no login.

```bash
# Serve over HTTP to the network, with a bearer token.
export BRIAR_MCP_TOKEN="$(openssl rand -hex 32)"
briar mcp serve --transport http --host 0.0.0.0 --port 8765 --token-env BRIAR_MCP_TOKEN
```

You get an endpoint that answers only requests with the header
`Authorization: Bearer <token>`. Any other request gets `401 Unauthorized`.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--transport` | `stdio` for apps on this machine, `http` for remote or browser clients. | `stdio` | `--transport http` |
| `--host` | The address to listen on. HTTP only. | `127.0.0.1` (this machine only) | `--host 0.0.0.0` |
| `--port` | The port to listen on. HTTP only. | `8765` | `--port 9000` |
| `--token-env` | The **name** of an env var that holds a bearer token. Every HTTP request must then send `Authorization: Bearer <token>`. You must set it to listen on any host other than `127.0.0.1`, `localhost` or `::1`. HTTP only. | Empty (no token). | `--token-env BRIAR_MCP_TOKEN` |
| `--store` | The knowledge store the tools use: `file` or `postgres`. | `file` (or `BRIAR_STORE`, or `store` in `.briar.toml`) | `--store postgres` |
| `--root` | The folder for the `file` store. | `./knowledge` (or `root` in `.briar.toml`) | `--root ./kb` |
| `--runbook` | The runbook YAML file for the `runbook_*` tools and `mcp_server_set_enabled`. Leave it out to turn those tools off. | Empty (off). | `--runbook runbooks/acme.yaml` |

### Good to know

- `--token-env` takes the name of an env var, not the token itself. This
  keeps the token out of your shell history and process list.
- Listening on a non-loopback host with no token is refused with
  `refusing to bind MCP HTTP server to non-loopback host`.
- If the env var named in `--token-env` is empty or unset, there is no
  token. That only works on `127.0.0.1`.
- A token also works on `127.0.0.1`. Then every request needs it there too.
- The paths in `--root` and `--runbook` are relative to the folder the
  server starts in. A host app may start it in another folder, so
  absolute paths are safer.
- `mcp_server_set_enabled` writes the runbook file again from its parsed
  data. Comments in that file are lost. It refuses to write a literal
  secret into a field that should hold an env var name.
- The extract tool and `knowledge_*` tools use the store from `--store`
  and `--root`. The AI model cannot pick another store.
- Without the `mcp` extra, `briar mcp serve` fails with an import error.
  Install `briar-cli[mcp]`.

## `briar chat`

### What it does

Opens a chat in your terminal. Behind the scenes it starts
`briar mcp serve --transport stdio` and gives its tools to an AI model.
You type a request, such as "list the knowledge blobs for acme". The
model calls briar's tools and answers you.

When the model wants a gated tool (one that changes data), briar first
runs it as a dry run and shows you the preview with `Execute? [y/N]`.
It only runs the real action if you type `y`. The model cannot skip
this question.

Type `/exit` or `/quit`, or press Ctrl-D, to leave.

### Use it when

- You want to look around briar's knowledge by asking in plain words.
- You want to change a blob or run an extract, and check each change first.

### Examples

```bash
# Chat with the default model (Anthropic).
briar chat
```

You get a `you>` prompt. Answers start with `briar>`.

```bash
# Chat with OpenAI's gpt-4o and turn on the runbook tools.
briar chat --llm openai --model gpt-4o --runbook runbooks/acme.yaml
```

You get the same chat, with an OpenAI model that can also read the runbook.

```bash
# Use the Postgres knowledge store.
briar chat --store postgres
```

You get a chat whose tools read and write blobs in Postgres.

### All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--llm` | The AI provider: `anthropic`, `openai`, `gemini` or `bedrock`. | `anthropic` | `--llm gemini` |
| `--model` | The model name. Empty means the provider's default. | Empty, or `model` under `[agent]` in `.briar.toml` | `--model gpt-4o` |
| `--store` | The knowledge store the tools use: `file` or `postgres`. | `file` (or `BRIAR_STORE`, or `store` in `.briar.toml`) | `--store postgres` |
| `--root` | The folder for the `file` store. | `./knowledge` (or `root` in `.briar.toml`) | `--root ./kb` |
| `--runbook` | The runbook file for the runbook tools. Leave it out to turn them off. | Empty (off). | `--runbook runbooks/acme.yaml` |

### What each provider needs

| `--llm` | Key or setup | Default model | Extra install |
|---|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY`, or `CLAUDE_CODE_OAUTH_TOKEN` (it wins when both are set). | `claude-sonnet-4-5` | none |
| `openai` | `OPENAI_API_KEY` | `gpt-4o` | `briar-cli[openai]` |
| `gemini` | `GEMINI_API_KEY` | `gemini-2.5-pro` | `briar-cli[gemini]` |
| `bedrock` | AWS access from the normal AWS chain (role, profile or env vars), with Bedrock model access in your region. | `anthropic.claude-sonnet-4-20250514-v1:0` | none |

### Good to know

- A missing key stops the chat at once with
  `error: LLM (<provider>) credentials missing`.
- If `.briar.toml` sets `model` under `[agent]`, chat uses it as the
  default `--model`. That name may not fit another provider. Pass
  `--model` when you switch `--llm`.
- For one question, the model gets up to 25 rounds of tool calls. After
  that, the chat answers `(hit tool-iteration limit without a final answer)`.
- The chat does not remember earlier questions. Put all the details in
  each request.
- `briar chat` needs the `mcp` extra too, since it starts the server.

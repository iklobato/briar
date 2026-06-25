"""`briar mcp` — serve briar's own features over the Model Context Protocol.

`briar mcp serve` starts an MCP server exposing briar's knowledge, config, and
extraction operations as tools, so any MCP host (Claude Desktop, Cursor,
`briar chat`, a remote client) can drive briar. Mutating tools are gated:
they preview by default and act only when called with ``confirm=true``.

Transports:
  * ``stdio`` (default) — for local desktop hosts and `briar chat`.
  * ``http``            — Streamable HTTP for browser/remote clients (see
                          `--host` / `--port`).
"""

from __future__ import annotations

import argparse
from typing import ClassVar, Dict

from briar.commands.base import Subcommand, SubcommandCommand
from briar.storage import KNOWLEDGE_STORE_NAMES


class ServeOp(Subcommand):
    name = "serve"
    help = "Start the briar MCP server (stdio by default)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--transport",
            default="stdio",
            choices=["stdio", "http"],
            help="Transport: stdio for local hosts (default), http for remote/browser clients",
        )
        parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (http transport only; default: loopback)")
        parser.add_argument("--port", type=int, default=8765, help="HTTP bind port (http transport only; default: 8765)")
        parser.add_argument(
            "--token-env",
            default="",
            help="Env-var NAME holding the bearer token required on HTTP requests " "(required to bind a non-loopback host; http transport only)",
        )
        parser.add_argument(
            "--store",
            default="file",
            choices=list(KNOWLEDGE_STORE_NAMES),
            help="Knowledge store backend the tools operate on (default: file)",
        )
        parser.add_argument("--root", default="./knowledge", help="Local knowledge file root (file store only)")
        parser.add_argument(
            "--runbook",
            default="",
            help="Runbook YAML path for the config tools (omit to disable config tools)",
        )

    def run(self, command: "SubcommandCommand", args: argparse.Namespace) -> int:
        from briar.mcpserver import ServerContext, build_server

        ctx = ServerContext(store=args.store, root=args.root, runbook_path=args.runbook or None)
        server = build_server(ctx)

        if args.transport == "stdio":
            server.run(transport="stdio")
            return 0

        # Streamable HTTP — loopback by default, bearer-auth + bind guard in _auth.
        import os

        from briar.mcpserver._auth import serve_http

        token = os.environ.get(args.token_env, "") if args.token_env else ""
        serve_http(server, host=args.host, port=args.port, token=token)
        return 0


class CommandMcp(SubcommandCommand):
    name = "mcp"
    help = "Serve briar's features over the Model Context Protocol."

    dest: ClassVar[str] = "mcp_op"
    op_noun: ClassVar[str] = "mcp op"
    ops: ClassVar[Dict[str, Subcommand]] = {ServeOp.name: ServeOp()}

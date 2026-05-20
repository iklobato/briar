"""Runs command — list/get + `follow` over WebSocket."""

from __future__ import annotations

import argparse
import json
import urllib.parse

from briar.commands.resource import HandlerMap, CommandResource
from briar.errors import CliError
from briar.http import ApiClient
from briar.ws import WebSocketClient


def _pretty_run_event(text: str) -> None:
    """Pretty-print a single WS frame as `[ts] kind: message`,
    falling back to raw text on non-JSON or non-dict payloads."""
    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        print(text)
        return
    if type(event) is not dict:
        print(text)
        return
    kind = event.get("type") or event.get("event") or "event"
    ts = event.get("ts") or event.get("timestamp") or ""
    msg = event.get("message") or event.get("text") or ""
    prefix = f"[{ts}] " if ts else ""
    if msg:
        print(f"{prefix}{kind}: {msg}")
        return
    print(f"{prefix}{kind}: {json.dumps(event, default=str)[:200]}")


def _stream_run(client: ApiClient, run_id: str, raw: bool) -> int:
    if not client.creds.access:
        raise CliError("not logged in — run `briar login` first")

    base = client.creds.api_base
    ws_base = (
        base.replace("https://", "wss://", 1)
            .replace("http://", "ws://", 1)
            .rstrip("/")
    )
    token = urllib.parse.quote(client.creds.access)
    url = f"{ws_base}/ws/runs/{run_id}/?token={token}"

    ws = WebSocketClient(url)
    ws.connect()
    print(f"connected — streaming /ws/runs/{run_id}/ (Ctrl-C to stop)")
    try:
        for opcode, payload in ws.frames():
            if opcode != WebSocketClient.OP_TEXT:
                continue
            text = payload.decode("utf-8", errors="replace")
            if raw:
                print(text)
                continue
            _pretty_run_event(text)
        return 0
    finally:
        ws.close()


class CommandRuns(CommandResource):
    name = "runs"
    help = "List, fetch, or stream runs (`follow` tails the WS feed)."
    base_path = "/api/v1/runs/"
    columns = ["id", "task", "status", "started_at", "finished_at"]
    read_only = True

    def _add_extras(self, sub: argparse._SubParsersAction) -> None:
        fo = sub.add_parser(
            "follow", help="stream run events over WebSocket",
        )
        fo.add_argument("id")
        fo.add_argument(
            "--raw", action="store_true",
            help="print each WS frame verbatim instead of pretty-printing",
        )

    def _extra_handlers(self) -> HandlerMap:
        return {"follow": self._follow}

    def _follow(self, client: ApiClient, args: argparse.Namespace) -> int:
        return _stream_run(client, args.id, raw=bool(args.raw))

"""`--field key=value` parser shared by every write-capable command.

Value rules:
- `@path.json` reads JSON from disk
- `-` reads the entire stdin (newline-stripped) — keeps secrets out of
  shell history
- otherwise parsed as JSON if possible, else as a literal string
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from briar.errors import CliError


def parse_fields(values: Optional[List[str]]) -> Dict[str, Any]:
    """Convert `["k=v", ...]` to `{"k": v, ...}` with the value rules
    described in the module docstring."""
    if not values:
        return {}
    out: Dict[str, Any] = {}
    for raw in values:
        if "=" not in raw:
            raise CliError(f"--field expects key=value, got: {raw}")
        key, _, value = raw.partition("=")
        key = key.strip()
        if not key:
            raise CliError(f"--field has empty key: {raw}")
        out[key] = _decode_value(value)
    return out


def _decode_value(raw: str) -> Any:
    if raw == "-":
        return sys.stdin.read().rstrip("\n")
    if raw.startswith("@"):
        return json.loads(Path(raw[1:]).read_text())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def load_body(args: argparse.Namespace) -> Dict[str, Any]:
    """Combine `--from-file <path>` and any number of `--field k=v`
    arguments; `--field` overrides keys already present in the file."""
    namespace = vars(args)
    fields = parse_fields(namespace.get("field"))
    path = namespace.get("from_file")
    if not path:
        return fields
    body = json.loads(Path(path).read_text())
    if type(body) is not dict:
        raise CliError(
            f"--from-file must contain a JSON object, "
            f"got {type(body).__name__}"
        )
    body.update(fields)
    return body

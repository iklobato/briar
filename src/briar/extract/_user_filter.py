"""Author / assignee filter shared by GitHub-based extractors.

Pure functions over GitHub API issue / PR payloads — no I/O, no
extractor coupling. The shape of `args` follows the same `*_allow` /
`*_block` convention as the source templates."""

from __future__ import annotations

import argparse
from typing import Any, Iterable, List


def _login_of(value: Any) -> str:
    """Pull `.login` off a GitHub user dict; tolerate None / non-dict."""
    if not isinstance(value, dict):
        return ""
    return value.get("login") or ""


def _logins_of(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    return [_login_of(v) for v in values if isinstance(v, dict)]


def _matches(actual: Iterable[str], allow: List[str], block: List[str]) -> bool:
    actual_set = {l for l in actual if l}
    if allow and not (actual_set & set(allow)):
        return False
    if block and (actual_set & set(block)):
        return False
    return True


def add_user_filter_arguments(
    parser: argparse.ArgumentParser,
    *,
    prefix: str,
) -> None:
    """Register the four `--<prefix>-{authors,assignees}-{allow,block}`
    flags on the given parser. `prefix` matches the extractor's
    existing flag namespace (e.g. `pr` → `--pr-authors-allow`)."""
    parser.add_argument(
        f"--{prefix}-authors-allow", action="append", default=[],
        help="only include items whose author is in this list (repeatable)",
    )
    parser.add_argument(
        f"--{prefix}-authors-block", action="append", default=[],
        help="exclude items whose author is in this list (repeatable)",
    )
    parser.add_argument(
        f"--{prefix}-assignees-allow", action="append", default=[],
        help="only include items whose assignee is in this list (repeatable)",
    )
    parser.add_argument(
        f"--{prefix}-assignees-block", action="append", default=[],
        help="exclude items whose assignee is in this list (repeatable)",
    )


def apply_user_filter(
    items: List[dict],
    args: argparse.Namespace,
    *,
    prefix: str,
) -> List[dict]:
    """Apply the four-axis user filter to a GitHub issue/PR list."""
    authors_allow = list(getattr(args, f"{prefix}_authors_allow", None) or [])
    authors_block = list(getattr(args, f"{prefix}_authors_block", None) or [])
    assignees_allow = list(getattr(args, f"{prefix}_assignees_allow", None) or [])
    assignees_block = list(getattr(args, f"{prefix}_assignees_block", None) or [])

    no_filters = not any(
        (authors_allow, authors_block, assignees_allow, assignees_block)
    )
    if no_filters:
        return items

    out: List[dict] = []
    for item in items:
        author = _login_of(item.get("user"))
        assignees = _logins_of(item.get("assignees")) or [
            _login_of(item.get("assignee"))
        ]
        if not _matches([author], authors_allow, authors_block):
            continue
        if not _matches(assignees, assignees_allow, assignees_block):
            continue
        out.append(item)
    return out

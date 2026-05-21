"""Author / assignee filter shared by GitHub-based extractors.

Pure static helpers over GitHub API issue / PR payloads — no I/O, no
extractor coupling. The shape of `args` follows the same `*_allow` /
`*_block` convention as the source templates."""

from __future__ import annotations

import argparse
from typing import Any, Iterable, List


class UserFilter:
    """Allow/block-list filter applied to GitHub issue + PR payloads.

    Called by the extractors as `UserFilter.apply(items, args, prefix=...)`
    after the raw fetch. The `add_arguments` classmethod contributes the
    four `--<prefix>-*` flags to the extractor's argparse parser."""

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser, *, prefix: str) -> None:
        """Register `--<prefix>-{authors,assignees}-{allow,block}` flags."""
        parser.add_argument(
            f"--{prefix}-authors-allow",
            action="append",
            default=[],
            help="only include items whose author is in this list (repeatable)",
        )
        parser.add_argument(
            f"--{prefix}-authors-block",
            action="append",
            default=[],
            help="exclude items whose author is in this list (repeatable)",
        )
        parser.add_argument(
            f"--{prefix}-assignees-allow",
            action="append",
            default=[],
            help="only include items whose assignee is in this list (repeatable)",
        )
        parser.add_argument(
            f"--{prefix}-assignees-block",
            action="append",
            default=[],
            help="exclude items whose assignee is in this list (repeatable)",
        )

    @classmethod
    def apply(
        cls,
        items: List[dict],
        args: argparse.Namespace,
        *,
        prefix: str,
    ) -> List[dict]:
        """Filter `items` by author/assignee allow/block lists."""
        ns = vars(args)
        authors_allow = list(ns.get(f"{prefix}_authors_allow") or [])
        authors_block = list(ns.get(f"{prefix}_authors_block") or [])
        assignees_allow = list(ns.get(f"{prefix}_assignees_allow") or [])
        assignees_block = list(ns.get(f"{prefix}_assignees_block") or [])

        no_filters = not any(
            (
                authors_allow,
                authors_block,
                assignees_allow,
                assignees_block,
            )
        )
        if no_filters:
            return items

        out: List[dict] = []
        for item in items:
            author = cls._login_of(item.get("user"))
            assignees = cls._logins_of(item.get("assignees")) or [cls._login_of(item.get("assignee"))]
            if not cls._matches([author], authors_allow, authors_block):
                continue
            if not cls._matches(assignees, assignees_allow, assignees_block):
                continue
            out.append(item)
        return out

    @staticmethod
    def _login_of(value: Any) -> str:
        if type(value) is not dict:
            return ""
        return value.get("login") or ""

    @classmethod
    def _logins_of(cls, values: Any) -> List[str]:
        if type(values) is not list:
            return []
        return [cls._login_of(v) for v in values if type(v) is dict]

    @staticmethod
    def _matches(
        actual: Iterable[str],
        allow: List[str],
        block: List[str],
    ) -> bool:
        actual_set = {l for l in actual if l}
        if allow and not (actual_set & set(allow)):
            return False
        if block and (actual_set & set(block)):
            return False
        return True


    @classmethod
    def apply_objs(
        cls,
        items: List[Any],
        args: argparse.Namespace,
        *,
        prefix: str,
    ) -> List[Any]:
        """Same allow/block semantics as ``apply`` but for dataclass
        items (e.g. `_provider.PullRequest`). Reads the author from
        the ``.author`` attribute; assignees are not modelled on the
        normalised PR shape so this method filters on authors only.

        Provider-agnostic by design — the extractors call this on the
        post-provider-normalisation list of objects, so the same
        filter works against GitHub, Bitbucket, or any future
        provider."""
        ns = vars(args)
        authors_allow = list(ns.get(f"{prefix}_authors_allow") or [])
        authors_block = list(ns.get(f"{prefix}_authors_block") or [])
        if not (authors_allow or authors_block):
            return items
        out: List[Any] = []
        allow_set = set(authors_allow)
        block_set = set(authors_block)
        for item in items:
            author = getattr(item, "author", "") or ""
            if authors_allow and author not in allow_set:
                continue
            if authors_block and author in block_set:
                continue
            out.append(item)
        return out


# Back-compat aliases (kept so external callers don't break — both are
# trivial one-liner delegations).
add_user_filter_arguments = UserFilter.add_arguments
apply_user_filter = UserFilter.apply
apply_user_filter_objs = UserFilter.apply_objs

"""Canonical extract flags — one shared knob per concept.

Every repo-backed extractor used to register its own private copy of the
same five concepts (`--pr-repo`, `--risk-repo`, `--hotspots-repo`, … all
mean "which repo(s)"). That produced ~80 flags on `briar extract`. This
module collapses them: the command exposes ONE canonical flag per concept
(`--repo`, `--since-days`, `--max`, `--top-n`, `--sample`, plus the four
author/assignee filters) and this resolver fans each canonical value out
to whichever private dest the included extractor actually reads.

The mapping is *derived*, not hand-maintained: each extractor already
declares its private flags in `add_arguments`, and their dests follow a
consistent suffix convention (`*_repo`, `*_since_days`, `*_top_n`, …).
`_concept_for_dest` reads that convention off the extractor's own parser,
so a new extractor that follows the naming convention is covered for free.

Precedence: an explicitly-passed private flag (e.g. `--reviewer-top-n 3`)
always wins over the canonical one — `apply_canonical` only fills a private
dest that is still sitting at its registered default.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Canonical concepts that accept a list (repeatable flag, default []).
CANONICAL_LIST: Tuple[str, ...] = (
    "repo",
    "authors_allow",
    "authors_block",
    "assignees_allow",
    "assignees_block",
)
# Canonical concepts that accept a single int (default None = "unset").
CANONICAL_SCALAR: Tuple[str, ...] = ("since_days", "max", "top_n", "sample")

# Suffix → canonical concept. Ordered longest/most-specific first so
# `*_max_commits` resolves to `max` before the shorter `*_max` rule, and
# `*_authors_allow` resolves before any hypothetical `*_allow`. First hit
# wins; a dest matching none stays extractor-private (`--gov-branch`,
# `--stale-days`, `--prhygiene-large-loc`, the `aws-extract-*` trio, …).
_SUFFIX_TO_CONCEPT: Tuple[Tuple[str, str], ...] = (
    ("_authors_allow", "authors_allow"),
    ("_authors_block", "authors_block"),
    ("_assignees_allow", "assignees_allow"),
    ("_assignees_block", "assignees_block"),
    ("_since_days", "since_days"),
    ("_max_commits", "max"),
    ("_pr_sample", "sample"),
    ("_diffstat_sample", "sample"),
    ("_top_n", "top_n"),
    ("_repo", "repo"),
    ("_max", "max"),
    ("_limit", "max"),
)

# Exact dests that carry a canonical concept without matching a suffix
# (tracker "project" lists are the repo-equivalent for issue trackers).
_EXACT_DEST_TO_CONCEPT: Dict[str, str] = {
    "ticket_project": "repo",
    "ticket_archaeology_project": "repo",
}


# Canonical concept → the shared flag that replaces every legacy
# per-extractor / per-source flag of that concept.
_CONCEPT_TO_FLAG: Dict[str, str] = {
    "repo": "--repo",
    "since_days": "--since-days",
    "max": "--max",
    "top_n": "--top-n",
    "sample": "--sample",
    "authors_allow": "--authors-allow",
    "authors_block": "--authors-block",
    "assignees_allow": "--assignees-allow",
    "assignees_block": "--assignees-block",
}


def legacy_flag_suggestions(argv: List[str]) -> Dict[str, str]:
    """Map each legacy per-extractor / per-source flag present in `argv`
    to the canonical flag that now covers it. A flag counts as legacy
    when its dest maps to a canonical concept (the canonical flags
    themselves never do). Empty when the command line uses none."""
    suggestions: Dict[str, str] = {}
    for token in argv:
        if not token.startswith("--"):
            continue
        name = token.split("=", 1)[0]
        dest = name[2:].replace("-", "_")
        concept = _concept_for_dest(dest)
        canonical = _CONCEPT_TO_FLAG.get(concept) if concept else None
        if canonical and name != canonical:
            suggestions[name] = canonical
    return suggestions


def _concept_for_dest(dest: str) -> Optional[str]:
    """Canonical concept a private dest belongs to, or None if the dest
    is genuinely extractor-specific."""
    exact = _EXACT_DEST_TO_CONCEPT.get(dest)
    if exact is not None:
        return exact
    for suffix, concept in _SUFFIX_TO_CONCEPT:
        if dest.endswith(suffix):
            return concept
    return None


@dataclass(frozen=True)
class _PrivateFlag:
    """One canonical-mapped private flag of an extractor: the dest it
    fills, the concept it answers to, and the default that signals
    "user did not override this"."""

    dest: str
    concept: str
    default: object


# Cache keyed by extractor class — building a throwaway parser per
# extractor to read its declared dests + defaults is cheap, but doing it
# once per process is cheaper and the spec never changes at runtime.
_SPEC_CACHE: Dict[type, Tuple[_PrivateFlag, ...]] = {}


def _private_flags(extractor) -> Tuple[_PrivateFlag, ...]:
    """The canonical-mapped private flags an extractor declares.

    Introspects the extractor's own `add_arguments` output rather than
    duplicating the flag list here, so the two never drift."""
    cls = type(extractor)
    cached = _SPEC_CACHE.get(cls)
    if cached is not None:
        return cached
    seed = argparse.ArgumentParser(add_help=False)
    extractor.add_arguments(seed)
    flags: List[_PrivateFlag] = []
    for action in seed._actions:
        concept = _concept_for_dest(action.dest)
        if concept is None:
            continue
        flags.append(_PrivateFlag(dest=action.dest, concept=concept, default=action.default))
    spec = tuple(flags)
    _SPEC_CACHE[cls] = spec
    return spec


def _is_set(value: object) -> bool:
    """A canonical value counts as "provided" when it is a non-empty list
    or a non-None scalar."""
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return True


def apply_canonical(ns: argparse.Namespace, extractor) -> None:
    """Fan canonical values on `ns` out to the private dests `extractor`
    reads. Mutates `ns` in place (consistent with the runbook executor's
    arg-injection). A private dest still at its registered default is
    filled from the matching canonical value; an explicitly-overridden
    private dest is left untouched."""
    for flag in _private_flags(extractor):
        canonical_value = getattr(ns, flag.concept, None)
        if not _is_set(canonical_value):
            continue
        if getattr(ns, flag.dest, flag.default) != flag.default:
            continue  # private flag explicitly set — it wins
        setattr(ns, flag.dest, canonical_value)


def register_canonical_flags(parser: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    """Register the canonical flags on `parser` inside a labelled group
    and return the group (so the caller can place core flags alongside).

    These replace the per-extractor private flags on the day-to-day path;
    the private flags remain registered (hidden) for back-compat and for
    the rare same-invocation-divergent case."""
    group = parser.add_argument_group(
        "common extractor options",
        "Apply to every extractor selected with --include. A per-extractor " "override (see --advanced-help) wins over these when both are given.",
    )
    group.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="OWNER/REPO",
        help="Repository (or tracker project) to mine. Repeatable. " "Feeds every included extractor's repo/project list.",
    )
    group.add_argument("--since-days", type=int, default=None, help="Lookback window in days (history-based extractors).")
    group.add_argument("--max", type=int, default=None, help="Max items / commits to inspect per repo.")
    group.add_argument("--top-n", type=int, default=None, help="How many results to surface per repo.")
    group.add_argument("--sample", type=int, default=None, help="How many recent PRs to sample per repo.")
    group.add_argument("--authors-allow", action="append", default=[], help="Only include items whose author is in this list (repeatable).")
    group.add_argument("--authors-block", action="append", default=[], help="Exclude items whose author is in this list (repeatable).")
    group.add_argument("--assignees-allow", action="append", default=[], help="Only include items whose assignee is in this list (repeatable).")
    group.add_argument("--assignees-block", action="append", default=[], help="Exclude items whose assignee is in this list (repeatable).")
    return group


def hide_canonicalised_flags(parser: argparse.ArgumentParser) -> List[str]:
    """Suppress every per-extractor flag that a canonical flag now covers
    from `--help`, so the default help shows only the canonical + core
    surface. Genuinely extractor-specific flags (``--gov-branch``,
    ``--stale-days``, the ``aws-extract-*`` trio, ``--provider`` …) keep
    their help. Returns the list of suppressed flag strings for the
    deprecation notice / advanced-help dump."""
    hidden: List[str] = []
    for action in parser._actions:
        if _concept_for_dest(action.dest) is None:
            continue
        if action.help is argparse.SUPPRESS:
            continue
        action.help = argparse.SUPPRESS
        hidden.extend(action.option_strings)
    return hidden


class AdvancedHelpAction(argparse.Action):
    """`--advanced-help`: print the full per-extractor override surface
    (the flags hidden from the default help) and exit. Mirrors argparse's
    own ``_HelpAction`` so it short-circuits before required-flag checks."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        from briar.extract import EXTRACTORS

        detail = argparse.ArgumentParser(prog="briar extract", add_help=False)
        for extractor in EXTRACTORS.values():
            extractor.add_arguments(detail)
        print("Per-extractor override flags (advanced — prefer the canonical")
        print("--repo / --since-days / --max / --top-n / --sample / --*-allow")
        print("/ --*-block flags shown in `briar extract -h`):\n")
        print(detail.format_help())
        parser.exit()

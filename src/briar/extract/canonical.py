"""Canonical extractor values: one shared knob per concept.

Every repo-backed extractor registers its own private copy of the same
concepts (`pr_repo`, `risk_repo`, `hotspots_repo`, … all mean "which
repo(s)"). This resolver fans one canonical value (`repo`, `since_days`,
`max`, `top_n`, `sample`, the four author/assignee filters) out to
whichever private dest the included extractor actually reads. Since 2.0
the values come from `--repo` and the `[extract]` table of the project
config (decision C12), and from a runbook's `args`; the private flags are
no longer on the command line.

The mapping is *derived*, not hand-maintained: each extractor already
declares its private flags in `add_arguments`, and their dests follow a
consistent suffix convention (`*_repo`, `*_since_days`, `*_top_n`, …).
`_concept_for_dest` reads that convention off the extractor's own parser,
so a new extractor that follows the naming convention is covered for free.

Precedence: an explicitly-set private value (e.g. `reviewer_top_n = 3`)
always wins over the canonical one — `apply_canonical` only fills a private
dest that is still sitting at its registered default.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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


# The concepts the `[extract]` table may set for every included extractor.
# `repo` is not one of them: it is `--repo` (or `repos` / `[repo]`).
SHARED_CONCEPTS: Tuple[str, ...] = (
    "since_days",
    "max",
    "top_n",
    "sample",
    "authors_allow",
    "authors_block",
    "assignees_allow",
    "assignees_block",
)


# Tracker-project flags are mapped to the `repo` concept so `--repo` can
# also feed `active-tickets` / `ticket-archaeology`, but only for trackers
# whose projects are repo slugs (GitHub / Bitbucket Issues): a Jira key or
# Linear team is never `owner/repo`.
_TRACKER_PROJECT_DESTS = frozenset({"ticket_project", "ticket_archaeology_project"})


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
        if flag.dest in _TRACKER_PROJECT_DESTS and not _tracker_takes_repo_slugs(ns, extractor):
            continue
        setattr(ns, flag.dest, canonical_value)


class OptionConcept:
    """Which shared concept one extractor option answers to, by the suffix rules above."""

    def __init__(self, dest: str) -> None:
        self._dest = dest

    def concept(self) -> Optional[str]:
        """`max` for `pr_max`; None for an extractor-specific option such as `gov_branch`."""
        return _concept_for_dest(self._dest)


def _tracker_takes_repo_slugs(ns: argparse.Namespace, extractor) -> bool:
    tracker_cls = extractor.provider_class_for(ns)
    return tracker_cls is not None and tracker_cls.project_is_repo_slug


class _ReplaceDefaultAppend(argparse._AppendAction):
    """`append` whose first explicit value replaces the default list.

    `--repo`'s default is seeded from `.briar.toml` or the git remote
    (`config.apply_config_defaults`, `infer`). Plain `append` extends that
    default, so `--repo other/x` also mined the config repo."""

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: ANN001
        if getattr(namespace, self.dest, None) is self.default:
            setattr(namespace, self.dest, [])
        super().__call__(parser, namespace, values, option_string)

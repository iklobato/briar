"""Unit tests for the canonical-flag resolver."""

from __future__ import annotations

import argparse

import pytest
from briar.extract import EXTRACTORS
from briar.extract.canonical import _concept_for_dest, apply_canonical, register_canonical_flags


def _seed_namespace(extractor) -> argparse.Namespace:
    """Namespace with the extractor's private defaults AND the canonical
    flag defaults, mirroring how `briar extract` parses both onto one ns."""
    parser = argparse.ArgumentParser(add_help=False)
    register_canonical_flags(parser)
    extractor.add_arguments(parser)
    return parser.parse_args([])


@pytest.mark.parametrize(
    "dest,expected",
    [
        ("pr_repo", "repo"),
        ("reviewer_repo", "repo"),
        ("ticket_project", "repo"),
        ("ticket_archaeology_project", "repo"),
        ("hotspots_since_days", "since_days"),
        ("risk_max_commits", "max"),
        ("pr_max", "max"),
        ("cihealth_limit", "max"),
        ("reviewer_top_n", "top_n"),
        ("reviewer_pr_sample", "sample"),
        ("prhygiene_diffstat_sample", "sample"),
        ("pr_authors_allow", "authors_allow"),
        ("pr_assignees_block", "assignees_block"),
        # genuinely extractor-specific — must NOT be captured
        ("gov_branch", None),
        ("stale_days", None),
        ("prhygiene_large_loc", None),
        ("aws_extract_region", None),
        ("meeting_attendee_allow", None),
        ("provider", None),
        ("tracker", None),
    ],
)
def test_concept_for_dest_classification(dest, expected):
    assert _concept_for_dest(dest) == expected


def test_canonical_repo_fans_out_to_private_dest():
    ext = EXTRACTORS["reviewer-profile"]
    ns = _seed_namespace(ext)
    ns.repo = ["acme/app", "acme/shared"]
    ns.top_n = 9
    ns.sample = 40

    apply_canonical(ns, ext)

    assert ns.reviewer_repo == ["acme/app", "acme/shared"]
    assert ns.reviewer_top_n == 9
    assert ns.reviewer_pr_sample == 40


def test_explicit_private_flag_wins_over_canonical():
    ext = EXTRACTORS["reviewer-profile"]
    ns = _seed_namespace(ext)
    ns.repo = ["acme/app"]
    ns.top_n = 9
    ns.reviewer_top_n = 3  # user passed --reviewer-top-n explicitly

    apply_canonical(ns, ext)

    assert ns.reviewer_repo == ["acme/app"]  # canonical filled (no override)
    assert ns.reviewer_top_n == 3  # explicit override preserved


def test_unset_canonical_leaves_private_default_untouched():
    ext = EXTRACTORS["reviewer-profile"]
    ns = _seed_namespace(ext)
    # No canonical values set at all.
    apply_canonical(ns, ext)
    assert ns.reviewer_repo == []
    assert ns.reviewer_top_n == 5  # the extractor's own default


def test_unique_flags_are_never_touched_by_canonical():
    ext = EXTRACTORS["stale-prs"]
    ns = _seed_namespace(ext)
    ns.repo = ["acme/app"]
    ns.max = 50
    apply_canonical(ns, ext)
    assert ns.stale_repo == ["acme/app"]  # repo → stale_repo
    assert ns.stale_max == 50  # max → stale_max
    assert ns.stale_days == 14  # unique threshold, default preserved


def test_every_repo_backed_extractor_accepts_canonical_repo():
    """Smoke: --repo must reach a private dest for every extractor that
    declares a repo/project list, so the canonical flag is never a silent
    no-op."""
    for name, ext in EXTRACTORS.items():
        ns = _seed_namespace(ext)
        ns.repo = ["owner/name"]
        before = {k: list(v) if isinstance(v, list) else v for k, v in vars(ns).items()}
        apply_canonical(ns, ext)
        changed = {k for k, v in vars(ns).items() if before[k] != v}
        # Either the extractor has no repo concept (file/meeting-only) or
        # --repo landed on exactly its repo dest.
        repo_dests = {k for k in vars(ns) if _concept_for_dest(k) == "repo"}
        if repo_dests:
            assert changed & repo_dests, f"{name}: --repo did not reach {repo_dests}"

"""User-filter (allowlist + blocklist) tests across the four layers
the feature touches: extractor helper, CLI source template, runbook
YAML schema, runbook executor."""

from __future__ import annotations

import argparse
import unittest

from briar.extract._user_filter import (
    add_user_filter_arguments,
    apply_user_filter,
)


# ---------------------------------------------------------------------------
# Extractor-helper core logic
# ---------------------------------------------------------------------------

_ITEMS = [
    {"user": {"login": "alice"},     "assignees": [{"login": "alice"}]},
    {"user": {"login": "bob"},       "assignees": []},
    {"user": {"login": "bot[bot]"}, "assignees": [{"login": "carol"}]},
    {"user": {"login": "carol"},     "assignees": [{"login": "alice"}]},
]


def _ns(**kw) -> argparse.Namespace:
    base = {
        "pr_authors_allow":   [], "pr_authors_block":   [],
        "pr_assignees_allow": [], "pr_assignees_block": [],
    }
    base.update(kw)
    ns = argparse.Namespace()
    for k, v in base.items():
        setattr(ns, k, v)
    return ns


class ApplyUserFilterTests(unittest.TestCase):
    def test_no_filter_returns_everything(self) -> None:
        self.assertEqual(apply_user_filter(_ITEMS, _ns(), prefix="pr"), _ITEMS)

    def test_authors_allow_intersects(self) -> None:
        kept = apply_user_filter(
            _ITEMS, _ns(pr_authors_allow=["alice", "bob"]), prefix="pr",
        )
        self.assertEqual({i["user"]["login"] for i in kept}, {"alice", "bob"})

    def test_authors_block_subtracts(self) -> None:
        kept = apply_user_filter(
            _ITEMS, _ns(pr_authors_block=["bot[bot]"]), prefix="pr",
        )
        self.assertNotIn("bot[bot]", {i["user"]["login"] for i in kept})

    def test_allow_then_block(self) -> None:
        # allow alice + bob + bot, then block bot — should leave alice + bob
        kept = apply_user_filter(
            _ITEMS,
            _ns(
                pr_authors_allow=["alice", "bob", "bot[bot]"],
                pr_authors_block=["bot[bot]"],
            ),
            prefix="pr",
        )
        self.assertEqual({i["user"]["login"] for i in kept}, {"alice", "bob"})

    def test_assignees_filter(self) -> None:
        # only items with assignee=alice
        kept = apply_user_filter(
            _ITEMS, _ns(pr_assignees_allow=["alice"]), prefix="pr",
        )
        # alice→[alice]   ✓
        # bob→[]          ✗
        # bot→[carol]     ✗
        # carol→[alice]   ✓
        self.assertEqual(
            {i["user"]["login"] for i in kept}, {"alice", "carol"},
        )

    def test_argparse_wiring(self) -> None:
        parser = argparse.ArgumentParser()
        add_user_filter_arguments(parser, prefix="pr")
        ns = parser.parse_args([
            "--pr-authors-allow", "alice",
            "--pr-authors-allow", "bob",
            "--pr-authors-block", "bot[bot]",
        ])
        self.assertEqual(ns.pr_authors_allow, ["alice", "bob"])
        self.assertEqual(ns.pr_authors_block, ["bot[bot]"])
        self.assertEqual(ns.pr_assignees_allow, [])


# ---------------------------------------------------------------------------
# Source template — does the GitHub source emit the filters?
# ---------------------------------------------------------------------------

class SourceGithubFiltersTests(unittest.TestCase):
    def test_filters_appear_in_source_config(self) -> None:
        from briar.iac.scaffold.sources.github import SourceGithub
        ns = argparse.Namespace(
            owner="iklobato",
            repo="lightapi",
            auth_mode="pat",
            github_secret_id="some-uuid",
            github_authors_allow=["alice", "bob"],
            github_authors_block=["dependabot[bot]"],
            github_assignees_allow=[],
            github_assignees_block=["bot"],
        )
        src = SourceGithub().build_source(ns, key_prefix="t")
        self.assertEqual(src["config"]["authors_allow"], ["alice", "bob"])
        self.assertEqual(src["config"]["authors_block"], ["dependabot[bot]"])
        self.assertNotIn("assignees_allow", src["config"])  # empty omitted
        self.assertEqual(src["config"]["assignees_block"], ["bot"])

    def test_no_filters_emits_clean_config(self) -> None:
        from briar.iac.scaffold.sources.github import SourceGithub
        ns = argparse.Namespace(
            owner="iklobato",
            repo="lightapi",
            auth_mode="pat",
            github_secret_id="x",
            github_authors_allow=[],
            github_authors_block=[],
            github_assignees_allow=[],
            github_assignees_block=[],
        )
        src = SourceGithub().build_source(ns, key_prefix="t")
        # No user-filter keys at all when everything is empty.
        for k in (
            "authors_allow", "authors_block",
            "assignees_allow", "assignees_block",
        ):
            self.assertNotIn(k, src["config"])


# ---------------------------------------------------------------------------
# Runbook YAML schema — typed Pydantic fields round-trip
# ---------------------------------------------------------------------------

class RunbookYamlFiltersTests(unittest.TestCase):
    def test_github_filters_in_schema(self) -> None:
        import tempfile
        from pathlib import Path
        from briar.iac.runbook import load_runbook_file

        yaml = """
version: 1
companies:
  acme:
    profile: acme
    runbooks:
      - template: implementation
        prefix: x
        owner: o
        repo: r
        sources:
          - kind: github
            authors_allow: ["alice"]
            authors_block: ["dependabot[bot]"]
            assignees_allow: ["bob"]
        trigger:
          kind: schedule_cron
"""
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(yaml)
        f.close()
        rb = load_runbook_file(Path(f.name))
        src = rb.companies["acme"].runbooks[0].sources[0]
        self.assertEqual(src.authors_allow, ["alice"])
        self.assertEqual(src.authors_block, ["dependabot[bot]"])
        self.assertEqual(src.assignees_allow, ["bob"])


# ---------------------------------------------------------------------------
# Runbook executor — does the YAML field land in the Namespace?
# ---------------------------------------------------------------------------

class RunbookExecutorFlattenTests(unittest.TestCase):
    def test_github_filters_flatten_into_namespace(self) -> None:
        from briar.iac.runbook.executor import _apply_github_source
        from briar.iac.runbook.models import GithubSourceEntry

        spec = GithubSourceEntry(
            kind="github",
            authors_allow=["alice", "bob"],
            authors_block=["bot"],
        )
        ns = argparse.Namespace()
        _apply_github_source(spec, ns)
        self.assertEqual(ns.github_authors_allow, ["alice", "bob"])
        self.assertEqual(ns.github_authors_block, ["bot"])
        # assignee fields not set on the spec — shouldn't appear on ns
        self.assertFalse(hasattr(ns, "github_assignees_allow"))


if __name__ == "__main__":
    unittest.main()

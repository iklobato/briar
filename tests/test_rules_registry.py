"""Rules registry — verify rule files parse, scope-based reuse works,
and archetypes inherit the right rule set."""

from __future__ import annotations

import unittest

from briar.iac.scaffold.archetypes import ARCHETYPES
from briar.iac.scaffold.rules import Rule, RuleRegistry


class RulesRegistryTests(unittest.TestCase):
    def test_all_rules_parse_cleanly(self) -> None:
        rules = RuleRegistry.all()
        # At minimum the six rules we shipped extracted from the
        # pr-archetypes' inline prose must be there.
        names = {r.name for r in rules}
        for required in {
            "commit-as-human",
            "no-force-push",
            "skip-approved-green-prs",
            "no-workflow-file-edits",
            "no-new-pr-creation",
            "read-all-comments-first",
            "minimum-correct-fix",
        }:
            self.assertIn(required, names, f"missing required rule {required!r}")

    def test_get_returns_rule_by_name(self) -> None:
        rule = RuleRegistry.get("commit-as-human")
        self.assertEqual(rule.severity, "blocking")
        self.assertIn("git config user.name", rule.prose)

    def test_get_unknown_rule_raises(self) -> None:
        with self.assertRaises(KeyError):
            RuleRegistry.get("not-a-real-rule")

    def test_pr_fixer_inherits_its_scoped_rules(self) -> None:
        rules = RuleRegistry.for_archetype("pr-fixer")
        names = {r.name for r in rules}
        # Should pick up every rule whose applies_to contains pr-fixer.
        self.assertIn("commit-as-human", names)
        self.assertIn("no-force-push", names)
        self.assertIn("read-all-comments-first", names)
        self.assertIn("minimum-correct-fix", names)
        # Each rule that applies should land exactly once.
        self.assertEqual(len(rules), len(names))

    def test_pr_conflict_resolver_skips_read_all_comments(self) -> None:
        """read-all-comments-first is scoped to pr-fixer only — the
        conflict resolver doesn't need it (it works on conflict markers,
        not review threads)."""
        rules = RuleRegistry.for_archetype("pr-conflict-resolver")
        names = {r.name for r in rules}
        self.assertNotIn("read-all-comments-first", names)
        self.assertIn("commit-as-human", names)
        self.assertIn("no-force-push", names)

    def test_engineer_archetype_inherits_no_pr_rules(self) -> None:
        """The `engineer` archetype is for full implementation, not PR
        follow-up. None of the PR-scoped rules should apply."""
        rules = RuleRegistry.for_archetype("engineer")
        names = {r.name for r in rules}
        self.assertNotIn("commit-as-human", names)  # scoped to pr-* only
        self.assertNotIn("no-force-push", names)
        self.assertNotIn("read-all-comments-first", names)

    def test_rules_ordered_by_severity(self) -> None:
        """Blocking rules render before mandatory before advisory so the
        agent sees the strictest constraints first."""
        rules = RuleRegistry.for_archetype("pr-fixer")
        severities = [r.severity for r in rules]
        # All `blocking` come before any `mandatory` come before any `advisory`.
        order = {"blocking": 0, "mandatory": 1, "advisory": 2}
        for i in range(len(severities) - 1):
            self.assertLessEqual(order[severities[i]], order[severities[i + 1]])

    def test_archetype_persona_includes_rule_section(self) -> None:
        """build_persona should splice the rule registry output into the
        backstory under a `## Inherited rules` heading."""
        arch = ARCHETYPES["pr-fixer"]
        persona = arch.build_persona("acme/repo")
        self.assertIn("## Inherited rules", persona["backstory"])
        self.assertIn("commit-as-human", persona["backstory"])
        self.assertIn("no-force-push", persona["backstory"])

    def test_archetype_persona_omits_rules_when_none_apply(self) -> None:
        """An archetype with no scoped rules should NOT get an empty
        `## Inherited rules` block — keeps the prompt tight."""
        arch = ARCHETYPES["engineer"]
        persona = arch.build_persona("acme/repo")
        # No PR-scoped rule should leak into engineer.
        self.assertNotIn("commit-as-human", persona["backstory"])


if __name__ == "__main__":
    unittest.main()

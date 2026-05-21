"""Rule registry — loads every `.md` rule file at import time.

Agents declare nothing; rules opt in via their own `applies_to`
frontmatter. To add a rule that affects all three PR archetypes, drop
one `.md` here — no archetype file needs to change."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Tuple

from briar.iac.scaffold.rules.base import Rule, RuleParseError, parse_rule_file


log = logging.getLogger(__name__)


class RuleRegistry:
    """Walks the rules/ directory at import time and exposes the
    parsed rules indexed by name + a fast `for_archetype` lookup."""

    _RULES: Dict[str, Rule] = {}

    @classmethod
    def _load(cls) -> None:
        if cls._RULES:
            return
        here = Path(__file__).resolve().parent
        for path in sorted(here.glob("*.md")):
            try:
                rule = parse_rule_file(path)
            except RuleParseError as exc:
                log.error("rules: %s", exc)
                raise
            cls._RULES[rule.name] = rule
        log.debug("rules: loaded %d rules from %s", len(cls._RULES), here)

    @classmethod
    def all(cls) -> Tuple[Rule, ...]:
        cls._load()
        return tuple(cls._RULES.values())

    @classmethod
    def get(cls, name: str) -> Rule:
        cls._load()
        if name not in cls._RULES:
            known = sorted(cls._RULES)
            raise KeyError(f"unknown rule {name!r}; known: {known}")
        return cls._RULES[name]

    @classmethod
    def for_archetype(cls, archetype_name: str) -> Tuple[Rule, ...]:
        """Return every rule whose `applies_to` includes this archetype
        (or [all]), sorted by severity (blocking → mandatory → advisory)
        so the rendered backstory shows the strictest rules first."""
        cls._load()
        order = {"blocking": 0, "mandatory": 1, "advisory": 2}
        matched: List[Rule] = [r for r in cls._RULES.values() if r.applies(archetype_name)]
        matched.sort(key=lambda r: (order.get(r.severity, 99), r.name))
        return tuple(matched)


__all__ = ["Rule", "RuleParseError", "RuleRegistry", "parse_rule_file"]

"""Rule contract — one reusable directive that any agent archetype
can inherit.

A rule is a markdown file with YAML frontmatter:

    ---
    name: commit-as-human
    severity: blocking            # blocking | mandatory | advisory
    applies_to: [pr-fixer, pr-conflict-resolver, pr-ci-fixer]
    enforced_by: [prompt]         # prompt | tool-absence | runtime-check
    ---

    <markdown body — the actual rule text the agent reads>

Frontmatter declares the rule's scope. Archetypes do NOT have to know
about rules: when an archetype's name appears in any rule's
`applies_to`, that rule's body is spliced into the archetype's
backstory at persona-build time. To add a new rule that hits all three
PR archetypes, drop one `.md` into rules/ — no archetype file needs
to change."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple


_VALID_SEVERITY = {"blocking", "mandatory", "advisory"}
_VALID_ENFORCED_BY = {"prompt", "tool-absence", "runtime-check"}


@dataclass(frozen=True)
class Rule:
    """One reusable directive."""

    name: str
    severity: str
    applies_to: Tuple[str, ...]
    enforced_by: Tuple[str, ...]
    prose: str
    source_path: str = ""

    @property
    def is_global(self) -> bool:
        """`applies_to: [all]` means every archetype inherits it."""
        return "all" in self.applies_to

    def applies(self, archetype_name: str) -> bool:
        return self.is_global or archetype_name in self.applies_to

    def render(self) -> str:
        """How the rule reads inside an archetype's backstory."""
        return self.prose.strip()


@dataclass
class RuleParseError(Exception):
    path: Path
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def parse_rule_file(path: Path) -> Rule:
    """Parse one .md file into a Rule. Raises RuleParseError on any
    structural problem so a typo in a rule file fails fast at import
    rather than producing a silently-broken agent."""
    import yaml

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise RuleParseError(path=path, message="missing YAML frontmatter (must start with `---`)")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise RuleParseError(path=path, message="frontmatter is not closed (need a second `---` line)")
    front_raw, body = parts[1], parts[2]
    try:
        meta: Dict[str, Any] = yaml.safe_load(front_raw) or {}
    except yaml.YAMLError as exc:
        raise RuleParseError(path=path, message=f"frontmatter is invalid YAML — {exc}") from exc
    if not isinstance(meta, dict):
        raise RuleParseError(path=path, message="frontmatter must be a YAML mapping")

    name = str(meta.get("name") or "").strip()
    if not name:
        raise RuleParseError(path=path, message="frontmatter must declare `name`")
    severity = str(meta.get("severity") or "").strip()
    if severity not in _VALID_SEVERITY:
        raise RuleParseError(path=path, message=f"severity must be one of {sorted(_VALID_SEVERITY)!r}, got {severity!r}")

    applies_raw = meta.get("applies_to") or []
    if isinstance(applies_raw, str):
        applies_raw = [applies_raw]
    if not isinstance(applies_raw, list) or not applies_raw:
        raise RuleParseError(path=path, message="`applies_to` must be a non-empty list of archetype names (or [all])")
    applies = tuple(str(x).strip() for x in applies_raw if str(x).strip())

    enforced_raw = meta.get("enforced_by") or ["prompt"]
    if isinstance(enforced_raw, str):
        enforced_raw = [enforced_raw]
    if not isinstance(enforced_raw, list):
        raise RuleParseError(path=path, message="`enforced_by` must be a list")
    for tag in enforced_raw:
        if tag not in _VALID_ENFORCED_BY:
            raise RuleParseError(path=path, message=f"enforced_by entry {tag!r} not in {sorted(_VALID_ENFORCED_BY)!r}")
    enforced = tuple(str(x).strip() for x in enforced_raw)

    prose = body.strip()
    if not prose:
        raise RuleParseError(path=path, message="rule body (after frontmatter) is empty")

    return Rule(
        name=name,
        severity=severity,
        applies_to=applies,
        enforced_by=enforced,
        prose=prose,
        source_path=str(path),
    )

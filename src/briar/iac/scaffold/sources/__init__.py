"""Source kind registry.

Strategy pattern. Each kind lives in its own module; this file glues
them into `SOURCE_TEMPLATES`. Adding a new kind = one module + one
entry below."""

from __future__ import annotations

from typing import Dict

from briar.iac.scaffold.sources.aws import SourceAws
from briar.iac.scaffold.sources.base import SourceTemplate
from briar.iac.scaffold.sources.github import SourceGithub
from briar.iac.scaffold.sources.jira import SourceJira


SOURCE_TEMPLATES: Dict[str, SourceTemplate] = {
    t.kind: t for t in (
        SourceGithub(),
        SourceJira(),
        SourceAws(),
    )
}


__all__ = ["SOURCE_TEMPLATES", "SourceTemplate"]

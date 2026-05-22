"""Source kind registry.

Strategy pattern. Each kind lives in its own module; this file glues
them into `SOURCE_TEMPLATES`. Adding a new kind = one module + one
entry below."""

from __future__ import annotations

from typing import Dict

from briar._registry import build_registry
from briar.iac.scaffold.sources.aws import SourceAws
from briar.iac.scaffold.sources.base import SourceTemplate
from briar.iac.scaffold.sources.bitbucket import SourceBitbucket
from briar.iac.scaffold.sources.github import SourceGithub
from briar.iac.scaffold.sources.jira import SourceJira


SOURCE_TEMPLATES: Dict[str, SourceTemplate] = build_registry(
    (SourceGithub(), SourceBitbucket(), SourceJira(), SourceAws()),
    kind="scaffold source",
    name_attr="kind",
)


__all__ = ["SOURCE_TEMPLATES", "SourceTemplate"]

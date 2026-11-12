"""N6 as refined by M1 to M4 (`docs/decisions/2026-11-12-briar-2-open-mr-rule.md`): a ticket
that already has an open PR/MR of anyone but briar is not a candidate. The open PRs/MRs of
each allowed repo are read once per poll; a match is the key in the source branch, the title or
the description, or a Jira remote link of the ticket to one of them."""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, List, Sequence

from briar.extract._provider import PullRequest
from briar.iac.runbook.models import ServeSettings
from briar.serve.state import WorkSource
from briar.serve.work import BranchName, WorkItem


class OpenWork:
    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    # ponytail: the newest 100 open PRs/MRs of a repo; page when a repo keeps more open.
    OPEN_CAP = 100

    def __init__(self, sources: Any, settings: ServeSettings) -> None:
        self._sources = sources
        self._settings = settings

    def free(self, items: Sequence[WorkItem]) -> List[WorkItem]:
        repos = sorted({repo for item in items for repo in item.repos or (item.repo,) if repo})
        host = self._sources.code_host()
        pulls = {repo: host.list_pulls(repo, state="open", max_count=self.OPEN_CAP) for repo in repos}
        return [item for item in items if not self._taken(item, pulls)]

    def _taken(self, item: WorkItem, pulls: Dict[str, List[PullRequest]]) -> bool:
        style, own = self._sources.style(), BranchName(self._settings).of(item)
        links = set(self._sources.jira_links().remote_links(item.key)) if item.source is WorkSource.JIRA else set()
        cites = re.compile(rf"(?<![\w-]){re.escape(item.key)}(?![\w])")
        others = [(style.pull_url(repo, pull.number), pull) for repo in item.repos or (item.repo,) for pull in pulls.get(repo, []) if pull.head_ref != own]
        found = next((url for url, pull in others if url in links or cites.search(f"{pull.head_ref}\n{pull.title}\n{pull.body}")), "")
        if found:
            self.LOG.info("serve: %s skipped: %s is open for it", item.key, found)
        return bool(found)


__all__ = ["OpenWork"]

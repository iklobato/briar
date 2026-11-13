"""F11 of `docs/decisions/2026-11-12-fireflies-company-filter.md`: a Fireflies key can be a
personal account that records the meetings of several companies. When two or more runbook
companies use the same key and one of them has no company filter (F10b), its knowledge gets
the meetings of the others. Keys are compared by their sha256 and never shown."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict, Iterator, List, Sequence, Tuple

from briar.auth.logins import CompanyLogins
from briar.iac.runbook.executor import RunbookLogins, RunbookSchedules, load_runbook_file
from briar.iac.runbook.models import ExtractEntry


@dataclass(frozen=True)
class MeetingCompany:
    """One runbook company with a meeting digest: its name and whether it filters."""

    name: str
    filters: bool


class SharedMeetingKeys:
    """The warnings of the runbooks of a folder, one per shared key."""

    STEP = "meeting-digest"
    FILTER_ARGS: ClassVar[Tuple[str, ...]] = ("meeting_title_any", "meeting_attendee_allow")
    MEMBER_SOURCES: ClassVar[Tuple[str, ...]] = ("jira", "gitlab", "slack")

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def warnings(self) -> List[str]:
        by_key: Dict[str, List[MeetingCompany]] = {}
        for digest, company in self._companies():
            by_key.setdefault(digest, []).append(company)
        return [self._warning(group) for group in by_key.values() if len(group) > 1 and not all(company.filters for company in group)]

    def _companies(self) -> Iterator[Tuple[str, MeetingCompany]]:
        runbooks = [load_runbook_file(path) for path in sorted(self._directory.glob("*.yaml"))]
        for runbook, name in [(runbook, name) for runbook in runbooks for name in runbook.companies]:
            steps = [step for schedule in RunbookSchedules.for_company(runbook.companies[name]) for step in schedule.extract if step.name == self.STEP]
            logins = RunbookLogins(runbook).company(name)
            key = logins.fireflies().api_key()
            if steps and key:
                yield hashlib.sha256(key.encode()).hexdigest(), MeetingCompany(name, self._filters(steps, logins))

    def _filters(self, steps: Sequence[ExtractEntry], logins: CompanyLogins) -> bool:
        """F10b: a title word, an allowed email, or a member source."""
        argued = any(step.args.get(name) for step in steps for name in self.FILTER_ARGS)
        return argued or any(source in self.MEMBER_SOURCES for source in logins.configured())

    def _warning(self, group: Sequence[MeetingCompany]) -> str:
        names = ", ".join(company.name for company in group)
        open_names = ", ".join(company.name for company in group if not company.filters)
        return (
            f"{names} use the same Fireflies key and {open_names} keeps every meeting of it: give {open_names} a "
            "`meeting_title_any` or `meeting_attendee_allow`, or a Jira, GitLab or Slack login"
        )


__all__ = ["MeetingCompany", "SharedMeetingKeys"]

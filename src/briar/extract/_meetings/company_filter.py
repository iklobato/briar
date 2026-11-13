"""Which meetings of a meeting key belong to the company (decisions F1 to F12 in
`docs/decisions/2026-11-12-fireflies-company-filter.md`).

One key can be a personal account that records the meetings of several companies, where every
meeting carries one participant email (the owner). So a meeting is kept when ANY signal says it
is of the company: a title word, an attendee or organizer email (exact, or `@domain`), or a
speaker who is a member of the company (from its Jira, GitLab and Slack)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Callable, ClassVar, FrozenSet, List, Mapping, Protocol, Sequence, Tuple

from briar.auth.logins import CompanyLogins
from briar.errors import AuthError, CliError
from briar.extract._meeting import Meeting


class MembersUnreadable(CliError):
    """A member source failed: the meetings step stops for that run (F6)."""


class MemberSource(Protocol):
    """The people of the company as one source knows them (F3)."""

    source: str

    def names(self) -> List[str]: ...


class PersonName:
    """A full name with case, accents and extra spaces taken out (F4)."""

    def __init__(self, text: str) -> None:
        self._text = text

    def key(self) -> str:
        plain = unicodedata.normalize("NFKD", self._text)
        letters = "".join(char for char in plain if not unicodedata.combining(char))
        return " ".join(letters.casefold().split())


class CompanyMembers:
    """The name keys of every member source of the company."""

    def __init__(self, sources: Sequence[MemberSource]) -> None:
        self._sources = sources

    def keys(self) -> FrozenSet[str]:
        found: List[str] = []
        for source in self._sources:
            try:
                found.extend(source.names())
            except AuthError as error:
                raise MembersUnreadable(f"the members of {source.source} could not be read: {error}") from error
        return frozenset(key for key in (PersonName(name).key() for name in found) if key)


class MemberSources:
    """The member sources of a company: each of Jira, GitLab and Slack that has a login (F12)."""

    def __init__(self, logins: CompanyLogins) -> None:
        self._logins = logins

    def configured(self) -> List[MemberSource]:
        builders: Mapping[str, Callable[[], MemberSource]] = {"jira": self._jira, "gitlab": self._gitlab, "slack": self._slack}
        return [builders[name]() for name in self._logins.configured() if name in builders]

    def _jira(self) -> MemberSource:
        from briar.extract._trackers.jira import JiraMembers, JiraSiteClient

        return JiraMembers(JiraSiteClient(self._logins.jira()).client())

    def _gitlab(self) -> MemberSource:
        from briar.extract._providers.gitlab import GitlabApi, GitlabMembers

        return GitlabMembers(GitlabApi(self._logins.gitlab()))

    def _slack(self) -> MemberSource:
        from briar.serve.slack import SlackApi, SlackMembers

        return SlackMembers(SlackApi(self._logins.slack(), None))


@dataclass(frozen=True)
class MeetingFilter:
    """F1: title words (substrings, any case), allowed emails (`a@b.c` or `@b.c`), member name keys.
    With none of them every meeting is kept (F10b)."""

    title_words: Tuple[str, ...] = ()
    allowed: Tuple[str, ...] = ()
    members: FrozenSet[str] = frozenset()

    DOMAIN_MARK: ClassVar[str] = "@"

    def keeps(self, meeting: Meeting) -> bool:
        if not (self.title_words or self.allowed or self.members):
            return True
        return self._titled(meeting) or self._emailed(meeting) or self._spoken(meeting)

    def _titled(self, meeting: Meeting) -> bool:
        title = meeting.title.casefold()
        return any(word.casefold() in title for word in self.title_words if word.strip())

    def _emailed(self, meeting: Meeting) -> bool:
        emails = [email.strip().casefold() for email in [*meeting.attendees, meeting.organizer]]
        return any(self._allows(email) for email in emails if self.DOMAIN_MARK in email)

    def _allows(self, email: str) -> bool:
        for entry in (allowed.strip().casefold() for allowed in self.allowed):
            if email == entry or (entry.startswith(self.DOMAIN_MARK) and email.endswith(entry)):
                return True
        return False

    def _spoken(self, meeting: Meeting) -> bool:
        return any(PersonName(name).key() in self.members for name in meeting.speakers)


__all__ = ["CompanyMembers", "MeetingFilter", "MemberSource", "MemberSources", "MembersUnreadable", "PersonName"]

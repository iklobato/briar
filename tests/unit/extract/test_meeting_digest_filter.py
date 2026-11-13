"""`meeting-digest` keeps only the meetings of the company (decisions F1 to F12 in
`docs/decisions/2026-11-12-fireflies-company-filter.md`): the signals are joined with OR, the
pages go on until enough meetings are kept, and a member source that fails stops the step."""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

import pytest

from briar.auth.check import LoginRejected
from briar.extract._meeting import Meeting, MeetingPage
from briar.extract.meeting_digest import ExtractMeetingDigest
from tests.unit.fake_logins import FakeLogins

DURATION_SEC = 600


class Meetings:
    def one(self, title: str, attendees: Sequence[str] = (), speakers: Sequence[str] = ()) -> Meeting:
        return Meeting(title, title, "2026-11-12T10:00:00Z", DURATION_SEC, "owner@gmail.com", list(attendees), speakers=list(speakers))


@dataclass(frozen=True)
class FakeMeetings:
    """The `MeetingProvider` of the digest: the window as pages of `max_count` from `skip`."""

    meetings: List[Meeting]
    skips: List[int] = field(default_factory=list)

    def is_available(self) -> bool:
        return True

    def list_meetings(self, page: MeetingPage) -> List[Meeting]:
        self.skips.append(page.skip)
        return self.meetings[page.skip : page.skip + page.size]


@dataclass(frozen=True)
class FakeMembers:
    source: str
    found: List[str] = field(default_factory=list)
    error: str = ""

    def names(self) -> List[str]:
        if self.error:
            raise LoginRejected(self.error)
        return self.found


class Digest:
    """Runs the step with its flags, the fake meetings and the fake member sources."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, meetings: FakeMeetings) -> None:
        self._monkeypatch = monkeypatch
        self._meetings = meetings

    def titles(self, flags: Sequence[str], members: Sequence[FakeMembers] = ()) -> List[str]:
        self._monkeypatch.setattr("briar.extract._meetings.make_meeting", lambda kind, logins: self._meetings)
        self._monkeypatch.setattr("briar.extract._meetings.company_filter.MemberSources.configured", lambda sources: list(members))
        section = ExtractMeetingDigest().extract(self._args(flags))
        return [subsection.title for subsection in section.subsections]

    def _args(self, flags: Sequence[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        ExtractMeetingDigest().add_arguments(parser)
        args = parser.parse_args(list(flags))
        settings: Dict[str, Any] = {"company": "acme", "logins": FakeLogins(environ={"BRIAR_ACME_FIREFLIES_API_KEY": "ff_xxx"}).logins()}
        vars(args).update(settings)
        return args


class TestMeetingDigestFilter:
    def test_title_words_keep_the_meetings_of_the_company(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeMeetings([Meetings().one("AI Infra Daily Sync"), Meetings().one("abc-defg-hij")])

        titles = Digest(monkeypatch, fake).titles(["--meeting-title-any", "ai infra"])

        assert [any(name in title for title in titles) for name in ("AI Infra Daily Sync", "abc-defg-hij")] == [True, False]

    def test_an_exact_email_or_a_domain_keeps_a_meeting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """F7: matched by briar now, so it joins the other signals with OR."""
        fake = FakeMeetings([Meetings().one("standup", ["a@x.com"]), Meetings().one("retro", ["b@y.com"]), Meetings().one("demo", ["c@z.com"])])

        titles = Digest(monkeypatch, fake).titles(["--meeting-attendee-allow", "a@x.com", "--meeting-attendee-allow", "@y.com"])

        assert [any(name in title for title in titles) for name in ("standup", "retro", "demo")] == [True, True, False]

    def test_a_member_speaker_keeps_a_meeting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeMeetings([Meetings().one("abc-defg-hij", speakers=["Bo Wang"]), Meetings().one("kxg-gmfh-yiv", speakers=["Someone Else"])])

        titles = Digest(monkeypatch, fake).titles([], [FakeMembers("slack", ["bo wang"])])

        assert [any(name in title for title in titles) for name in ("abc-defg-hij", "kxg-gmfh-yiv")] == [True, False]

    def test_pages_go_on_until_max_meetings_are_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """F8: the first pages can be all of other companies."""
        fake = FakeMeetings([Meetings().one("other"), Meetings().one("other"), Meetings().one("Sync"), Meetings().one("Sync")])

        Digest(monkeypatch, fake).titles(["--meeting-max", "1", "--meeting-title-any", "sync"])

        assert fake.skips == list(range(len(fake.meetings) - 1))

    def test_a_short_page_ends_the_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeMeetings([Meetings().one("other")])

        assert (Digest(monkeypatch, fake).titles(["--meeting-title-any", "sync"]), fake.skips) == ([], [0])

    def test_no_filter_keeps_every_meeting_of_one_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """F10b: no title words, no allowed emails, no member source."""
        fake = FakeMeetings([Meetings().one("abc-defg-hij"), Meetings().one("standup")])

        assert (len(Digest(monkeypatch, fake).titles([])), fake.skips) == (len(fake.meetings), [0])

    def test_a_member_source_that_fails_stops_only_this_step(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        """F6: no meeting of another company slips in, and the log says why."""
        fake = FakeMeetings([Meetings().one("standup")])
        caplog.set_level(logging.ERROR)

        titles = Digest(monkeypatch, fake).titles([], [FakeMembers("jira", error="HTTP 403")])

        assert (titles, fake.skips) == ([], [])
        assert "meeting-digest stopped: the members of jira could not be read: HTTP 403" in caplog.text

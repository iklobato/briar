"""WP1 of the production plan (decision D9): a login of a source counts as dead only when its read-only "who am I" is refused with
an HTTP 401. `LoginRejected` is raised for every HTTP answer that is not a network error, so a 403 or 404 of one resource, a 503
of an outage and an unreachable source must never stop `serve`. A dead login of GitHub, GitLab or Jira in a task is confirmed
this way before the task is blocked; a dead Slack never counts (the chat is optional, decision B3)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from briar.auth.check import LoginRejected, SourceUnreachable
from briar.errors import CliError
from briar.serve.health import LoginWatch
from briar.serve.state import WorkStatus
from tests.unit.serve.fakes import Companies
from tests.unit.serve.ticket_fakes import FakeGithubSide, FakeTrackerSide, Files, Items, Plans, RoutineSources, ScriptedAgent, Setup, Setups, Step

UNAUTHORIZED = 401
FORBIDDEN = 403
NOT_FOUND = 404
SERVICE_UNAVAILABLE = 503
BAD_CREDENTIALS = "HTTP 401: Bad credentials"
# The prep of a task asks "who am I" once (the identity of the commits): a login that dies in the middle of a task lives for that call.
ONE_CALL = 1
# The verify command that fails once `other.txt` exists.
NO_OTHER = "test ! -e other.txt"


class Errors:
    """What `who_am_i` raises, as the adapters raise it."""

    def unauthorized(self) -> LoginRejected:
        return LoginRejected.at(BAD_CREDENTIALS, UNAUTHORIZED)

    def at(self, status: int) -> LoginRejected:
        return LoginRejected.at(f"HTTP {status}: no", status)


@dataclass(frozen=True)
class Refusal:
    """What `who_am_i` of the code host and of Jira does, and what `LoginWatch` must say about it."""

    github: Optional[Exception]
    jira: Optional[Exception]
    dead: Optional[str]


class Refusals:
    def all(self) -> List[Refusal]:
        return [
            Refusal(Errors().unauthorized(), None, "github"),
            Refusal(None, Errors().unauthorized(), "jira"),
            Refusal(Errors().at(FORBIDDEN), None, None),
            Refusal(Errors().at(NOT_FOUND), None, None),
            Refusal(Errors().at(SERVICE_UNAVAILABLE), None, None),
            Refusal(SourceUnreachable("timed out"), None, None),
            Refusal(CliError("could not read the setting"), None, None),
            Refusal(LoginRejected("no status, a refusal of briar"), None, None),
            Refusal(None, Errors().at(FORBIDDEN), None),
            Refusal(None, None, None),
        ]

    def ids(self) -> List[str]:
        return ["github-401", "jira-401", "github-403", "github-404", "github-503", "unreachable", "cli-error", "no-status", "jira-403", "fine"]


class Watched:
    """The routine sources with a `who_am_i` that raises what the case says."""

    def sources(self, case: Refusal) -> RoutineSources:
        github = FakeGithubSide(account_errors=[case.github] if case.github else [])
        jira = FakeTrackerSide(account_errors=[case.jira] if case.jira else [])
        return RoutineSources(github_side=github, tracker_side=jira)


class TestLoginWatch:
    @pytest.mark.parametrize("case", Refusals().all(), ids=Refusals().ids())
    def test_only_a_401_of_who_am_i_makes_a_login_dead(self, case: Refusal, tmp_path: Path) -> None:
        company = Companies(tmp_path).company({"jira": {"ENG": {"repos": ["acme/app"]}}})

        dead = LoginWatch(company, Watched().sources(case)).dead()

        assert (dead.source if dead else None) == case.dead

    def test_the_dead_login_carries_its_company_the_answer_and_the_fix(self, tmp_path: Path) -> None:
        company = Companies(tmp_path).company({"jira": {"ENG": {"repos": ["acme/app"]}}})

        dead = LoginWatch(company, Watched().sources(Refusal(Errors().unauthorized(), None, "github"))).dead()

        assert dead is not None
        assert (dead.company, dead.detail, dead.fix) == ("acme", BAD_CREDENTIALS, company.logins.source("github").fix())

    def test_a_company_with_no_jira_never_asks_jira(self, tmp_path: Path) -> None:
        company = Companies(tmp_path).company({"repos": ["acme/app"]})
        jira = FakeTrackerSide(account_errors=[Errors().unauthorized()])

        dead = LoginWatch(company, RoutineSources(tracker_side=jira)).dead()

        assert dead is None


class TestADeadLoginInATask:
    """The PR of the ticket is refused and "who am I" says 401 (the login died in the middle of the task: the prep asked it once
    and it lived): `serve` stops, the ticket is not blocked."""

    def made(self, setups: Setups, github: FakeGithubSide, extra: Dict[str, Any]) -> Setup:
        setup = setups.make(extra)
        return replace(setup, sources=replace(setup.sources, github_side=github))

    def refused(self, setups: Setups, account: Optional[Exception]) -> Setup:
        github = FakeGithubSide(refusals=["the token was refused"], account_errors=[account] if account else [], account_lives=ONE_CALL)
        setup = self.made(setups, github, {})
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())
        return setup

    def test_a_dead_login_stops_serve_and_does_not_block_the_ticket(self, setups: Setups) -> None:
        setup = self.refused(setups, Errors().unauthorized())

        entry, dead = setup.entry("ENG-1"), setup.company.stop.reason()

        assert (entry.status is not WorkStatus.BLOCKED, entry.report, setup.company.stop.asked()) == (True, "", True)
        assert dead is not None
        assert (dead.source, dead.company, dead.detail) == ("github", "acme", BAD_CREDENTIALS)

    def test_a_dead_login_writes_no_blocked_comment_on_the_ticket(self, setups: Setups) -> None:
        setup = self.refused(setups, Errors().unauthorized())

        assert [text for _, text in setup.sources.tracker_side.comments if "blocked" in text.lower()] == []

    def test_a_refusal_with_a_working_login_still_blocks_the_ticket(self, setups: Setups) -> None:
        """A 403 of one resource: `who_am_i` works, so the login is alive and the ticket is blocked as before."""
        setup = self.refused(setups, None)

        entry = setup.entry("ENG-1")

        assert (entry.status, setup.company.stop.asked()) == (WorkStatus.BLOCKED, False)

    def test_a_limit_of_the_task_does_not_ask_who_am_i(self, setups: Setups) -> None:
        """A task that hit `max_loops` is blocked by its own limit: no login is asked about it, however dead one would be."""
        github = FakeGithubSide(account_errors=[Errors().unauthorized()], account_lives=ONE_CALL)
        setup = self.made(setups, github, {"max_loops": 1, "verify": [NO_OTHER]})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes={"other.txt": "x"}), Step(writes={"other.txt": "y"})]), Items().jira())

        assert (setup.entry("ENG-1").status, setup.company.stop.asked(), len(github.account_calls)) == (WorkStatus.BLOCKED, False, ONE_CALL)

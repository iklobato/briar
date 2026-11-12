"""The login check of a runbook (spec 005 PR 3): `runbook extract` checks every
company and task before running any (J7); `runbook serve` checks at start and
stops on any failed check later (J8). Fireflies answers through a fake
`urlopen_with_retry`: no network."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, List
from unittest import mock

import pytest

from briar.auth.check import LoginCheckFailed
from briar.extract.base import ExtractedSection
from briar.iac.runbook import RunbookScheduler, extract_runbook, load_runbook_file

CLI_ERROR = 1
TWO_COMPANIES = "version: 1\ncompanies:\n  acme:\n    extract:\n      - name: meeting-digest\n  globex:\n    extract:\n      - name: meeting-digest\n"


@dataclass(frozen=True)
class FirefliesAnswers:
    """`urlopen_with_retry` of the Fireflies GraphQL call: the user of the key."""

    calls: List[str] = field(default_factory=list)

    def urlopen(self, request: Any, timeout: float) -> io.BytesIO:
        self.calls.append(json.loads(request.data)["query"])
        return io.BytesIO(json.dumps({"data": {"user": {"email": "ops@example.test"}}}).encode())


@dataclass(frozen=True)
class Extracts:
    """Stands in for `ExtractMeetingDigest.extract`: records each run, returns no data."""

    companies: List[str] = field(default_factory=list)

    def extract(self, args: Any) -> ExtractedSection:
        self.companies.append(args.company)
        return ExtractedSection()


class Runbook:
    """A runbook folder in `tmp_path` where only `acme` has a Fireflies login."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._tmp_path = tmp_path
        self._monkeypatch = monkeypatch

    def write(self) -> Path:
        self._monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", "PLACEHOLDER-not-a-secret")
        self._monkeypatch.delenv("BRIAR_GLOBEX_FIREFLIES_API_KEY", raising=False)
        path = self._tmp_path / "companies.yaml"
        path.write_text(TWO_COMPANIES)
        return path


@dataclass(frozen=True)
class Seams:
    extracts: Extracts
    answers: FirefliesAnswers


class TestRunbookExtract:
    @pytest.fixture
    def runbook(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        return Runbook(tmp_path, monkeypatch).write()

    @pytest.fixture
    def seams(self, monkeypatch: pytest.MonkeyPatch) -> Seams:
        seams = Seams(Extracts(), FirefliesAnswers())
        monkeypatch.setattr("briar.extract._meetings.fireflies.urlopen_with_retry", seams.answers.urlopen)
        monkeypatch.setattr("briar.extract.meeting_digest.ExtractMeetingDigest.extract", seams.extracts.extract)
        return seams

    def test_one_failed_company_runs_nothing(self, runbook: Path, seams: Seams) -> None:
        with pytest.raises(LoginCheckFailed) as caught:
            extract_runbook(load_runbook_file(runbook))

        assert str(caught.value) == (
            "the login check failed; fix these, then run again:\n"
            "  - fireflies (globex): no login saved (BRIAR_GLOBEX_FIREFLIES_API_KEY); run `briar auth login fireflies --company globex --runbook FILE`"
        )
        assert seams.extracts.companies == []

    def test_the_company_filter_checks_only_that_company(self, runbook: Path, seams: Seams) -> None:
        extract_runbook(load_runbook_file(runbook), company="acme")

        assert (seams.extracts.companies, len(seams.answers.calls)) == (["acme"], 1)

    def test_the_command_exits_1(self, cli: Callable[..., SimpleNamespace], runbook: Path, seams: Seams) -> None:
        run = cli("runbook", "extract", str(runbook))

        assert (run.code, "fireflies (globex): no login saved" in run.err, seams.extracts.companies) == (CLI_ERROR, True, [])


class TestServe:
    def test_a_failed_check_at_start_does_not_start(self, cli: Callable[..., SimpleNamespace], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        Runbook(tmp_path, monkeypatch).write()

        with mock.patch("briar.extract._meetings.fireflies.urlopen_with_retry", side_effect=FirefliesAnswers().urlopen):
            with mock.patch.object(RunbookScheduler, "run_forever") as loop:
                run = cli("runbook", "serve", str(tmp_path))

        assert (run.code, loop.called) == (CLI_ERROR, False)
        assert "  - fireflies (globex): no login saved" in run.err

    def test_a_job_whose_check_fails_stops_serve(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """J8: a token that expires after start stops `serve`, for every company."""
        Runbook(tmp_path, monkeypatch).write()
        scheduler = RunbookScheduler(tmp_path)
        jobs = scheduler.register_all()
        failure = LoginCheckFailed("the login check failed; fix these, then run again:\n  - fireflies (acme): the login was refused (HTTP 401: Unauthorized)")

        with mock.patch("briar.iac.runbook.extract_runbook", side_effect=failure):
            jobs[0].job.run()
        with pytest.raises(LoginCheckFailed) as caught:
            scheduler.run_forever(0)

        assert caught.value is failure

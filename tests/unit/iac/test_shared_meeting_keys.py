"""F11 of `docs/decisions/2026-11-12-fireflies-company-filter.md`: one Fireflies key set for
two or more runbook companies, and one of them keeps every meeting, mixes the meetings of the
companies; `doctor --runbooks` warns, and never shows the key."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest

from briar.iac.runbook.meeting_keys import SharedMeetingKeys

SHARED = "ff_PLACEHOLDER-not-a-secret"


class Runbooks:
    """A folder with one runbook of two companies, each with a scheduled meeting digest."""

    def __init__(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path

    def write(self, args: Dict[str, str]) -> Path:
        folder = self._tmp_path / "runbooks"
        folder.mkdir()
        companies = "".join(self._company(name, step_args) for name, step_args in args.items())
        (folder / "work.yaml").write_text(f"version: 1\ncompanies:\n{companies}")
        return folder

    def _company(self, name: str, step_args: str) -> str:
        return f'  {name}:\n    schedules:\n      - task: daily\n        every: "day at 03:17"\n        extract:\n          - name: meeting-digest\n            args: {{{step_args}}}\n'


class TestSharedMeetingKeys:
    def test_a_shared_key_with_a_company_that_keeps_every_meeting_warns_without_the_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", SHARED)
        monkeypatch.setenv("BRIAR_BETA_FIREFLIES_API_KEY", SHARED)
        folder = Runbooks(tmp_path).write({"acme": "meeting_title_any: [standup]", "beta": ""})

        (warning,) = SharedMeetingKeys(folder).warnings()

        assert warning == (
            "acme, beta use the same Fireflies key and beta keeps every meeting of it: give beta a "
            "`meeting_title_any` or `meeting_attendee_allow`, or a Jira, GitLab or Slack login"
        )
        assert SHARED not in warning

    def test_a_shared_key_where_every_company_filters_is_fine(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", SHARED)
        monkeypatch.setenv("BRIAR_BETA_FIREFLIES_API_KEY", SHARED)
        monkeypatch.setenv("BRIAR_BETA_SLACK_TOKEN", "xoxc-PLACEHOLDER-not-a-secret")
        folder = Runbooks(tmp_path).write({"acme": "meeting_title_any: [standup]", "beta": ""})

        assert SharedMeetingKeys(folder).warnings() == []

    def test_different_keys_do_not_warn(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", SHARED)
        monkeypatch.setenv("BRIAR_BETA_FIREFLIES_API_KEY", f"{SHARED}-beta")
        folder = Runbooks(tmp_path).write({"acme": "", "beta": ""})

        assert SharedMeetingKeys(folder).warnings() == []

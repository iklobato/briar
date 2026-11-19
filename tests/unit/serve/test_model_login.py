"""WP1 of the production plan (D2): the start check says which login of the model `serve` found, and warns when `ANTHROPIC_API_KEY` is set
next to the plan token: the key outranks the token, so the run would bill the API and not the plan the maintainer chose (P20)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

import pytest

from briar.auth.check import LoginCheckFailed
from briar.serve.work import ModelLogin, ServeCheck
from tests.unit.serve.fakes import Companies, FakeSources
from tests.unit.serve.test_work import Sandboxes

SECRET = "PLACEHOLDER-not-a-secret"


class Environments:
    """The environments of the cases: which model logins are set."""

    def key(self) -> Dict[str, str]:
        return {"ANTHROPIC_API_KEY": SECRET}

    def token(self) -> Dict[str, str]:
        return {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}

    def both(self) -> Dict[str, str]:
        return {**self.key(), **self.token()}

    def blank_key(self) -> Dict[str, str]:
        return {"ANTHROPIC_API_KEY": "  ", **self.token()}


class TestFound:
    @pytest.mark.parametrize(
        "environ, name",
        [
            (Environments().key(), "ANTHROPIC_API_KEY"),
            (Environments().token(), "CLAUDE_CODE_OAUTH_TOKEN"),
            (Environments().both(), "ANTHROPIC_API_KEY"),
            (Environments().blank_key(), "CLAUDE_CODE_OAUTH_TOKEN"),
            ({}, ""),
        ],
        ids=["key", "token", "both-the-key-outranks-the-token", "blank-key", "none"],
    )
    def test_the_login_the_sdk_will_use(self, environ: Dict[str, str], name: str) -> None:
        assert ModelLogin(environ).found() == name

    def test_a_key_next_to_the_token_is_a_warning_that_names_both(self) -> None:
        warning = ModelLogin(Environments().both()).warning()

        assert "ANTHROPIC_API_KEY" in warning
        assert "CLAUDE_CODE_OAUTH_TOKEN" in warning
        assert "unset ANTHROPIC_API_KEY" in warning

    @pytest.mark.parametrize(
        "environ", [Environments().key(), Environments().token(), Environments().blank_key(), {}], ids=["key", "token", "blank-key", "none"]
    )
    def test_one_login_or_none_is_no_warning(self, environ: Dict[str, str]) -> None:
        assert ModelLogin(environ).warning() == ""


class TestAtStart:
    """The model login line comes before the read and write checks: the fake logins of these tests fail them, after it."""

    def start(self, environ: Dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("briar.serve.work.WorkSources", lambda logins, kind: FakeSources())
        company = Companies(tmp_path).company({"repos": ["acme/app"]})
        with pytest.raises(LoginCheckFailed):
            ServeCheck([company], environ, Sandboxes().ready()).require()

    def warnings(self, caplog: pytest.LogCaptureFixture) -> List[str]:
        return [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]

    def test_the_log_says_which_login_it_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.work")

        self.start(Environments().token(), tmp_path, monkeypatch)

        assert "serve: model login: CLAUDE_CODE_OAUTH_TOKEN" in caplog.text
        assert self.warnings(caplog) == []

    def test_a_key_next_to_the_token_is_warned_about(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.work")

        self.start(Environments().both(), tmp_path, monkeypatch)

        assert any("ANTHROPIC_API_KEY" in text and "CLAUDE_CODE_OAUTH_TOKEN" in text for text in self.warnings(caplog))

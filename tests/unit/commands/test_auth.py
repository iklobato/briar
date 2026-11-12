"""`briar auth`: dispatch and `status` (decision C15; spec 005 G9, I7).

`status` with no source lists the names held; with a source it lists what that
login needs. The pasted sources check their secret names; the CLI sources
(`aws`, `gcp`, `azure`) check this machine, offline."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

USAGE_ERROR = 2
FAKE_SECRET = "PLACEHOLDER-not-a-secret"


class TestUnknownAction:
    def test_unknown_action_is_a_usage_error(self, cli: Callable[..., SimpleNamespace]) -> None:
        assert cli("auth", "make-up").code == USAGE_ERROR


class TestStatusWithoutTarget:
    """`auth status` with no source lists every credential name held, as 1.x `auth list` did."""

    def test_empty_store_says_no_credentials(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIAR_ACME_JIRA_TOKEN", raising=False)

        run = cli("auth", "status", "--company", "nobody")

        assert run.code == 0
        assert "no credentials" in run.out

    def test_company_filter_substring(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_ACME_JIRA_TOKEN", FAKE_SECRET)
        monkeypatch.setenv("BRIAR_OTHER_JIRA_TOKEN", FAKE_SECRET)

        run = cli("auth", "status", "--company", "acme")

        assert run.code == 0
        assert "BRIAR_ACME_JIRA_TOKEN" in run.out
        assert "BRIAR_OTHER_JIRA_TOKEN" not in run.out

    def test_names_only_never_values(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)

        run = cli("auth", "status")

        assert "BRIAR_ACME_FIREFLIES_API_KEY" in run.out
        assert FAKE_SECRET not in run.out


class TestStatusOfAPastedLogin:
    def test_a_missing_secret_is_miss_and_exit_1(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIAR_ACME_FIREFLIES_API_KEY", raising=False)

        run = cli("auth", "status", "fireflies", "--company", "acme")

        assert run.code == 1
        assert "MISS  BRIAR_ACME_FIREFLIES_API_KEY" in run.out

    def test_a_saved_secret_is_ok_and_exit_0(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_ACME_FIREFLIES_API_KEY", FAKE_SECRET)

        run = cli("auth", "status", "fireflies", "--company", "acme")

        assert run.code == 0
        assert "ok    BRIAR_ACME_FIREFLIES_API_KEY" in run.out


class TestStatusOfACliLogin:
    """I7: before the live check of PR 3, the CLI on PATH and the local login state."""

    def test_aws_without_the_cli_and_without_a_profile(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("PATH", str(tmp_path))

        run = cli("auth", "status", "aws", "--company", "acme")

        assert run.code == 1
        assert "MISS  `aws` on PATH" in run.out
        assert "ok    no `profile` under [auth.aws]: runs use the boto3 default chain" in run.out

    def test_gcp_names_the_missing_adc_file_and_project(self, cli: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))

        run = cli("auth", "status", "gcp", "--company", "acme")

        assert run.code == 1
        assert f"MISS  ADC file in {tmp_path}/.config/gcloud" in run.out
        assert "MISS  `project` under [auth.gcp]" in run.out

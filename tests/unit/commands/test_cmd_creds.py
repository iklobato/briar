"""`briar secrets` — doctor + bootstrap."""

from __future__ import annotations

import pytest


class TestDoctor:
    def test_missing_examples_dir_returns_1(self, cli, tmp_root) -> None:
        # examples dir doesn't exist
        result = cli("secrets", "doctor", "--examples", str(tmp_root / "no-such-dir"))
        assert result.code == 1
        assert "no examples dir" in result.out

    def test_empty_examples_dir_returns_0(self, cli, tmp_root) -> None:
        # Empty examples dir → no audit lines, no missing
        result = cli("secrets", "doctor", "--examples", str(tmp_root / "examples"))
        assert result.code == 0

    def test_unknown_action_returns_1(self, cli) -> None:
        # argparse rejects unknown sub-action with exit 2
        result = cli("secrets", "unknown")
        assert result.code == 2


class TestBootstrap:
    def test_bootstrap_no_kind_no_backend_available(self, cli, mocker) -> None:
        # `auto_bootstrap()` returns an empty list when no backend is
        # available (no envfile, no remote vault). The command should
        # exit 0 with a "nothing configured" message — startup must be
        # robust to a fresh install.
        mocker.patch(
            "briar.credentials._bootstraps.auto_bootstrap",
            return_value=[],
        )
        result = cli("secrets", "bootstrap")
        assert result.code == 0
        assert "no credential-bootstrap" in result.out

    def test_bootstrap_kind_not_available(self, cli) -> None:
        # `env_sandbox` autouse fixture redirects BRIAR_SECRETS_FILE to a
        # non-existent path so the envfile bootstrap can't restore anything.
        # EnvFileBootstrap.is_available() returns False deterministically
        # — exit 1 with "not configured".
        result = cli("secrets", "bootstrap", "--kind", "envfile")
        assert result.code == 1
        assert "not configured" in result.out

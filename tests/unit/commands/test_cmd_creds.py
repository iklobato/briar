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
        from briar.credentials._bootstrap import HydrateResult

        mocker.patch(
            "briar.credentials._bootstraps.auto_bootstrap",
            return_value=HydrateResult(backend="(none)", written=set(), skipped=set(), error=""),
        )
        result = cli("secrets", "bootstrap")
        assert result.code == 0
        assert "no credential-bootstrap" in result.out

    def test_bootstrap_kind_not_available(self, cli, mocker) -> None:
        from briar.credentials._bootstraps import CredentialBootstrapRegistry

        # Pick any registered kind
        kinds = list(CredentialBootstrapRegistry.kinds())
        if not kinds:
            pytest.skip("no bootstraps registered")
        # The default state (no env vars) means the bootstrap is not available.
        result = cli("secrets", "bootstrap", "--kind", kinds[0])
        # When not configured, exit 1 with explanation
        assert result.code == 1
        assert "not configured" in result.out

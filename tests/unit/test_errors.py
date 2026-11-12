"""Exception hierarchy."""

from __future__ import annotations

import pytest

from briar.errors import AuthError, CliError, ConfigError


class TestHierarchy:
    @pytest.mark.parametrize("subclass", [AuthError, ConfigError])
    def test_subclasses_of_cli_error(self, subclass) -> None:
        assert issubclass(subclass, CliError)

    def test_cli_error_is_exception(self) -> None:
        assert issubclass(CliError, Exception)

"""Exception hierarchy + ApiError body shortening."""

from __future__ import annotations

import pytest

from briar.errors import ApiError, AuthError, CliError, ConfigError


class TestHierarchy:
    @pytest.mark.parametrize("subclass", [AuthError, ConfigError, ApiError])
    def test_subclasses_of_cli_error(self, subclass) -> None:
        assert issubclass(subclass, CliError)

    def test_cli_error_is_exception(self) -> None:
        assert issubclass(CliError, Exception)


class TestApiErrorMessage:
    def test_format_includes_method_path_status_body(self) -> None:
        e = ApiError(404, "not found", "GET", "/api/users")
        assert "GET /api/users" in str(e)
        assert "404" in str(e)
        assert "not found" in str(e)

    def test_attributes_preserved(self) -> None:
        e = ApiError(500, {"k": 1}, "POST", "/api/data")
        assert e.status == 500
        assert e.method == "POST"
        assert e.path == "/api/data"
        assert e.body == {"k": 1}

    def test_string_body_truncated_at_300_chars(self) -> None:
        body = "x" * 500
        e = ApiError(400, body, "GET", "/x")
        # Message has prefix + first 300 chars of body
        assert "x" * 300 in str(e)
        assert "x" * 400 not in str(e)

    @pytest.mark.parametrize(
        "body",
        [
            "<!DOCTYPE html><html>err</html>",
            "<!doctype html>...",
            "<html lang=en>...</html>",
            "  \n  <!doctype html>",
        ],
    )
    def test_html_body_replaced_with_placeholder(self, body: str) -> None:
        e = ApiError(500, body, "POST", "/x")
        assert "server returned HTML" in str(e)
        assert "<html" not in str(e).lower() or "(server returned HTML" in str(e)

    def test_dict_body_json_dumped(self) -> None:
        e = ApiError(500, {"error": "x"}, "POST", "/x")
        assert '"error"' in str(e)

    def test_unserializable_body_does_not_raise(self) -> None:
        # `default=str` falls back to repr-like string
        e = ApiError(500, object(), "POST", "/x")
        assert "object at" in str(e) or "object" in str(e)

    def test_none_body_serialised_as_null(self) -> None:
        e = ApiError(204, None, "DELETE", "/x")
        assert "null" in str(e)

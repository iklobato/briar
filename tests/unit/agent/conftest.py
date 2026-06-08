"""LLM-adapter test fixtures, local to tests/unit/agent.

Mirrors the top-level ``fake_anthropic_messages`` style: each fixture
patches the SDK client *seam* the adapter lazy-imports, returns a mock
the test scripts with ``.return_value`` / ``.side_effect``, and exposes
helpers to build realistic SDK error objects.

The four provider SDKs (anthropic, openai, google-generativeai, boto3)
are all installed in .venv, so we patch the real client classes rather
than the import function — this exercises the adapter's real request
construction and response parsing.

Doc URLs for the modelled payloads/errors are cited inline next to each
fixture / test, per the project test conventions.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

# The `google.generativeai` package emits a one-shot ``FutureWarning`` on
# first import (it's deprecated upstream in favour of `google.genai`).
# The project runs pytest with ``filterwarnings = ["error"]``, which would
# turn that SDK-level deprecation into a spurious collection error the
# moment any Gemini test imports the SDK — at fixture-setup or call time,
# where a per-test ``filterwarnings`` mark can't reach.
#
# Pre-import the SDK here, once, with the warning suppressed. After this
# the module is cached in ``sys.modules`` so subsequent imports by the
# adapter / fixtures never re-emit the warning. This suppresses ONLY this
# specific deprecation message; every other warning still fails the suite.
try:  # pragma: no cover - import-availability guard
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        import google.generativeai  # noqa: F401
except ImportError:  # pragma: no cover - SDK is an opt-in extra
    pass

# ── anthropic ─────────────────────────────────────────────────────────
#
# Errors: https://docs.anthropic.com/en/api/errors — the SDK raises
# ``anthropic.APIStatusError`` subclasses whose ``.status_code`` mirrors
# the HTTP status (authentication_error 401, rate_limit_error 429,
# overloaded_error 529, invalid_request_error 400). Constructing them
# requires a real ``httpx.Response`` (the SDK reads ``.status_code`` off
# it), so we build a minimal one.


@pytest.fixture
def anthropic_error() -> Any:
    """Factory → an anthropic SDK exception with the given HTTP status.

    ``anthropic_error(429, cls=anthropic.RateLimitError)`` etc."""
    import anthropic
    import httpx

    def make(status: int, *, cls: Any = None, message: str = "boom") -> Exception:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(status, request=request)
        exc_cls = cls or anthropic.APIStatusError
        return exc_cls(message, response=response, body=None)

    return make


@pytest.fixture
def fake_openai_client(mocker: Any) -> Any:
    """Patch ``openai.OpenAI`` → a mock whose
    ``.chat.completions.create`` the test scripts.

    Returns the ``create`` mock (set ``.return_value`` / ``.side_effect``)."""
    create = mocker.MagicMock()
    client = mocker.MagicMock()
    client.chat.completions.create = create
    mocker.patch("openai.OpenAI", return_value=client)
    return create


@pytest.fixture
def openai_error() -> Any:
    """Factory → an openai SDK exception.

    Errors: https://platform.openai.com/docs/guides/error-codes —
    ``openai.AuthenticationError`` (401), ``RateLimitError`` (429),
    ``BadRequestError`` (400, context_length_exceeded), ``APITimeoutError``."""
    import httpx
    import openai

    def make(status: int, *, cls: Any = None, message: str = "boom") -> Exception:
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        if cls is openai.APITimeoutError:
            return openai.APITimeoutError(request=request)
        response = httpx.Response(status, request=request)
        exc_cls = cls or openai.APIStatusError
        return exc_cls(message, response=response, body=None)

    return make


@pytest.fixture
def fake_gemini_model(mocker: Any) -> Any:
    """Patch ``google.generativeai`` so ``GenerativeModel(...)`` returns a
    mock whose ``.generate_content`` the test scripts; also stubs
    ``configure`` so no real API key handshake happens.

    Returns the ``generate_content`` mock."""
    import google.generativeai as genai

    generate = mocker.MagicMock()
    model = mocker.MagicMock()
    model.generate_content = generate
    mocker.patch.object(genai, "GenerativeModel", return_value=model)
    mocker.patch.object(genai, "configure")
    return generate


@pytest.fixture
def fake_bedrock_client(mocker: Any) -> Any:
    """Patch ``boto3.client('bedrock-runtime')`` → a mock whose
    ``.converse`` the test scripts. Returns the ``converse`` mock."""
    converse = mocker.MagicMock()
    client = mocker.MagicMock()
    client.converse = converse
    mocker.patch("boto3.client", return_value=client)
    return converse


@pytest.fixture
def botocore_client_error() -> Any:
    """Factory → a ``botocore.exceptions.ClientError``.

    Bedrock Converse errors:
    https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html
    — botocore raises ``ClientError`` whose ``response['Error']['Code']`` is
    e.g. ThrottlingException / ValidationException / AccessDeniedException."""
    from botocore.exceptions import ClientError

    def make(code: str, *, message: str = "boom", op: str = "Converse") -> ClientError:
        return ClientError({"Error": {"Code": code, "Message": message}}, op)

    return make

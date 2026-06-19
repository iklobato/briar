"""Shared pytest fixtures for the briar test suite.

Fixtures here are kept boundary-focused: env isolation, clock control,
filesystem sandbox, HTTP/SDK mocking. Subsystem-specific fixtures live
in nested conftest.py files (tests/unit/agent/conftest.py, etc.)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

import pytest

_PREFIXES_TO_SCRUB = (
    "BRIAR_",
    "GITHUB_",
    "JIRA_",
    "AWS_",
    "ANTHROPIC_",
    "OPENAI_",
    "GEMINI_",
    "CLAUDE_CODE_",
    "BITBUCKET_",
    "LINEAR_",
    "FIREFLIES_",
    "VAULT_",
    "SLACK_",
    "TELEGRAM_",
    "PAGERDUTY_",
    "SMTP_",
    "EMAIL_",
)


@pytest.fixture(autouse=True)
def env_sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Strip every credential-shaped env var before each test, AND
    redirect ``BRIAR_SECRETS_FILE`` to a per-test empty path.

    Two tests on the same machine must not influence each other through
    `os.environ`. This is the single biggest source of order-coupling
    bugs that `pytest-randomly` would otherwise surface.

    `BRIAR_SECRETS_FILE` override is non-obvious but load-bearing:
    `EnvFileBootstrap` runs during `briar.cli.main` startup and
    re-hydrates any envfile it finds, undoing this fixture's
    `monkeypatch.delenv` calls. Pointing at a per-test nonexistent
    path makes the bootstrap a deterministic no-op so the env stays
    sandboxed."""
    for key in list(os.environ):
        if key.startswith(_PREFIXES_TO_SCRUB):
            monkeypatch.delenv(key, raising=False)
    sandbox_envfile = tmp_path_factory.mktemp("envfile-sandbox") / "secrets.env"
    monkeypatch.setenv("BRIAR_SECRETS_FILE", str(sandbox_envfile))


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """A `tmp_path` with the directory shape briar commands expect.

    Avoids 12 different tests each calling `mkdir` on the same names."""
    for name in ("knowledge", "journal", "examples", "worktree", "runbooks"):
        (tmp_path / name).mkdir()
    return tmp_path


@pytest.fixture
def caplog_briar(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Capture only the `briar.*` logger tree at DEBUG.

    Avoids drowning assertions in third-party noise (boto3, httpx, ...)."""
    caplog.set_level(logging.DEBUG, logger="briar")
    return caplog


@pytest.fixture
def fake_subprocess(mocker: Any) -> SimpleNamespace:
    """Patch `subprocess.run`; record argv, return canned CompletedProcess.

    `responses[tuple(cmd)] = CompletedProcess(...)` to script per-call
    behavior. Default is success with empty stdout/stderr. Asserts
    `shell=False` to catch command-injection regressions."""
    from subprocess import CompletedProcess

    state = SimpleNamespace(calls=[], responses={}, default_returncode=0)

    def run(cmd: Any, *args: Any, **kwargs: Any) -> CompletedProcess:
        if kwargs.get("shell"):
            raise AssertionError(f"fake_subprocess: shell=True forbidden in tests, got cmd={cmd!r}")
        state.calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [cmd])
        key = tuple(state.calls[-1])
        if key in state.responses:
            return state.responses[key]
        return CompletedProcess(args=cmd, returncode=state.default_returncode, stdout="", stderr="")

    mocker.patch("subprocess.run", side_effect=run)
    return state


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> Callable[..., SimpleNamespace]:
    """Invoke `briar.cli.main([...argv])` and return exit code + captured I/O.

    Patches `configure_logging` to a no-op so pytest's caplog handler
    survives — the CLI normally calls `basicConfig(force=True)` which
    nukes all handlers including caplog's."""

    # No-op configure so caplog's handler stays attached.
    monkeypatch.setattr("briar.cli.configure_logging", lambda verbose=False: None)

    # Hermetic resolution: neutralise the two AMBIENT inputs the CLI reads
    # before dispatch — git `origin` inference and project-config discovery.
    # Without this a test invoking `main()` would pick up the checkout's own
    # remote (CI clones add an `origin`, so `--owner`/`--repo` get inferred
    # and required-flag assertions flip) or a stray `.briar.toml`. Tests that
    # exercise inference/config do so by calling the resolvers directly.
    monkeypatch.setattr("briar.infer.git_remote_slug", lambda cwd=None: None)
    monkeypatch.setattr("briar.config.load_project_config", lambda *a, **k: {})

    def invoke(*argv: str, env: dict[str, str] | None = None) -> SimpleNamespace:
        if env:
            for k, v in env.items():
                monkeypatch.setenv(k, v)
        monkeypatch.setattr(sys, "argv", ["briar", *argv])
        from briar import cli as cli_module

        try:
            code = cli_module.main(list(argv))
        except SystemExit as exc:
            code = int(exc.code or 0)
        out_err = capsys.readouterr()
        return SimpleNamespace(code=code, out=out_err.out, err=out_err.err)

    return invoke


# ─── store fixtures (parametrized in tests via indirect) ──────────────


@pytest.fixture
def file_store(tmp_root: Path) -> Any:
    """A `StoreFile` rooted in the test's tmp_root/knowledge dir."""
    from briar.storage.file import StoreFile

    return StoreFile(root=tmp_root / "knowledge")


@pytest.fixture
def pg_store() -> Iterator[Any]:
    """A `StorePostgres` against `BRIAR_TEST_PG_DSN` (skip if unset)."""
    dsn = os.environ.get("BRIAR_TEST_PG_DSN")
    if not dsn:
        pytest.skip("BRIAR_TEST_PG_DSN not set")
    from briar.storage.postgres import StorePostgres  # type: ignore[import-not-found]

    store = StorePostgres(dsn=dsn)
    yield store


@pytest.fixture(params=["file"])
def store(request: pytest.FixtureRequest, file_store: Any) -> Any:
    """Parametrized store. Postgres lane added by tests that opt in."""
    return file_store


# ─── HTTP / SDK mocking helpers ───────────────────────────────────────


@pytest.fixture
def fake_anthropic_messages(mocker: Any):
    """Patch `anthropic.Anthropic().messages.create` with a configurable mock.

    Returns the mock so tests can set `.return_value` / `.side_effect`."""
    mock_messages = mocker.MagicMock()
    mock_client = mocker.MagicMock()
    mock_client.messages = mock_messages
    mocker.patch("anthropic.Anthropic", return_value=mock_client)
    return mock_messages

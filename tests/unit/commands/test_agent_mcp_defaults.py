"""Built-in default MCP servers + the runbook/default merge.

`CommandAgent._load_mcp_block` resolves the servers for a run by layering
the company's runbook `mcp:` block over briar's built-in defaults
(think/time) and reporting which defaults survived as always-on. These
tests pin that merge and the opt-out paths.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from briar.commands.agent import CommandAgent
from briar.errors import CliError
from briar.mcp import McpClientManager, McpError
from briar.mcp.defaults import DEFAULT_MCP_SERVERS


def _args(**over):
    base = {"runbook": "", "company": "acme", "no_default_mcp": False}
    base.update(over)
    return SimpleNamespace(**base)


def test_defaults_module_has_think_and_time() -> None:
    assert set(DEFAULT_MCP_SERVERS) == {"think", "time"}
    think = DEFAULT_MCP_SERVERS["think"]
    assert think.command == "npx"
    assert "@modelcontextprotocol/server-sequential-thinking" in think.args
    assert think.purpose  # non-empty → router-legible
    assert DEFAULT_MCP_SERVERS["time"].command == "uvx"


def test_no_runbook_yields_defaults_all_always_on() -> None:
    servers, always_on = CommandAgent._load_mcp_block(_args())
    assert set(servers) == {"think", "time"}
    assert set(always_on) == {"think", "time"}


def test_flag_opt_out_drops_defaults() -> None:
    servers, always_on = CommandAgent._load_mcp_block(_args(no_default_mcp=True))
    assert servers == {}
    assert always_on == ()


def test_env_opt_out_drops_defaults(monkeypatch) -> None:
    monkeypatch.setenv("BRIAR_NO_DEFAULT_MCP", "true")
    servers, always_on = CommandAgent._load_mcp_block(_args())
    assert servers == {}
    assert always_on == ()


_RUNBOOK_WITH_AWS_AND_THINK = """
companies:
  acme:
    mcp:
      aws:
        transport: stdio
        command: uvx
        args: ["awslabs.aws-api-mcp-server@latest"]
        purpose: "AWS"
      think:
        transport: stdio
        command: my-think
        args: []
        purpose: "custom thinker"
"""


@pytest.fixture
def mcp_sdk_missing(monkeypatch):
    """A plain `pip install briar-cli` (no `[mcp]` extra): `import mcp` fails."""
    monkeypatch.setitem(sys.modules, "mcp", None)


def test_missing_sdk_skips_builtin_defaults(mcp_sdk_missing) -> None:
    # Regression: the defaults used to stay in, and McpClientManager then
    # aborted every agent run (even --dry-run) with "requires the `mcp` package".
    servers, always_on = CommandAgent._load_mcp_block(_args())
    assert servers == {}
    assert always_on == ()


def test_missing_sdk_keeps_runbook_servers_so_they_still_fail_loudly(mcp_sdk_missing, tmp_path) -> None:
    runbook = tmp_path / "rb.yaml"
    runbook.write_text(_RUNBOOK_WITH_AWS_AND_THINK, encoding="utf-8")
    servers, always_on = CommandAgent._load_mcp_block(_args(runbook=str(runbook)))

    # Only what the operator asked for: the runbook's two servers, no default `time`.
    assert set(servers) == {"aws", "think"}
    assert servers["think"].command == "my-think"
    assert always_on == ()
    with pytest.raises(McpError, match=r"briar-cli\[mcp\]"):
        McpClientManager(servers).start()


def test_missing_sdk_error_is_a_one_line_cli_error(mcp_sdk_missing) -> None:
    # The CLI entry point prints a CliError as one `error:` line and exits 1;
    # any other exception is logged with a traceback and exits 2.
    with pytest.raises(CliError, match=r"pip install 'briar-cli\[mcp\]'"):
        McpClientManager({"time": DEFAULT_MCP_SERVERS["time"]}).start()


def test_runbook_servers_merge_over_defaults(tmp_path, monkeypatch) -> None:
    # A runbook adds `aws` (new) and overrides `think` (reuses the handle).
    runbook = tmp_path / "rb.yaml"
    runbook.write_text(_RUNBOOK_WITH_AWS_AND_THINK, encoding="utf-8")
    servers, always_on = CommandAgent._load_mcp_block(_args(runbook=str(runbook)))

    assert set(servers) == {"think", "time", "aws"}
    # The runbook's `think` won (override by handle)…
    assert servers["think"].command == "my-think"
    # …so only the untouched default (`time`) stays always-on; `aws` and the
    # overridden `think` are routable like any runbook server.
    assert set(always_on) == {"time"}

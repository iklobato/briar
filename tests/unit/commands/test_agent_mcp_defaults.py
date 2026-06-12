"""Built-in default MCP servers + the runbook/default merge.

`CommandAgent._load_mcp_block` resolves the servers for a run by layering
the company's runbook `mcp:` block over briar's built-in defaults
(think/time) and reporting which defaults survived as always-on. These
tests pin that merge and the opt-out paths.
"""

from __future__ import annotations

from types import SimpleNamespace


from briar.commands.agent import CommandAgent
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


def test_runbook_servers_merge_over_defaults(tmp_path, monkeypatch) -> None:
    # A runbook adds `aws` (new) and overrides `think` (reuses the handle).
    runbook = tmp_path / "rb.yaml"
    runbook.write_text(
        """
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
""",
        encoding="utf-8",
    )
    servers, always_on = CommandAgent._load_mcp_block(_args(runbook=str(runbook)))

    assert set(servers) == {"think", "time", "aws"}
    # The runbook's `think` won (override by handle)…
    assert servers["think"].command == "my-think"
    # …so only the untouched default (`time`) stays always-on; `aws` and the
    # overridden `think` are routable like any runbook server.
    assert set(always_on) == {"time"}

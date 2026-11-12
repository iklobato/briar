"""`McpClientManager` on a plain `pip install briar-cli` (no `[mcp]` extra)."""

from __future__ import annotations

import sys

import pytest

from briar.errors import CliError
from briar.iac.runbook.models import McpServerBinding
from briar.mcp import McpClientManager


@pytest.fixture
def mcp_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mcp", None)


def test_start_without_the_sdk_fails_with_the_one_line_install_hint(mcp_sdk_missing: None) -> None:
    # The CLI prints a CliError as one `error:` line and exits 1; any other
    # exception would be logged with a traceback.
    servers = {"aws": McpServerBinding(transport="stdio", command="uvx", args=["awslabs.aws-api-mcp-server@latest"], purpose="AWS")}

    with pytest.raises(CliError, match=r"^MCP support requires the `mcp` package\. Install it with: pip install 'briar-cli\[mcp\]'$"):
        McpClientManager(servers).start()

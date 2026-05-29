"""Characterization tests for `AgentRunner._dispatch_tool`.

Pins the dispatch contract before/after the registry refactor: route by
tool name, surface ToolError as is_error, fire the bash commit-hook only
for the bash tool, and bind send-message only when channels are configured.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from briar.agent.runner import AgentRunConfig, AgentRunner, AgentRunResult
from briar.agent.tools import ToolError


def _runner(tmp_path: Path, *, with_send: bool = False) -> AgentRunner:
    fake_llm = mock.MagicMock()
    fake_llm.kind = "anthropic"
    fake_store = mock.MagicMock()
    fake_store.list.return_value = []
    fake_store.get.return_value = ""
    messages = {"eng": {"kind": "telegram-chat"}} if with_send else {}
    return AgentRunner(
        AgentRunConfig(
            company="acme",
            task="implement",
            archetype_name="engineer",
            workdir=tmp_path,
            knowledge_store=fake_store,
            target="acme/widgets",
            messages=messages,
        ),
        llm=fake_llm,
    )


def _result() -> AgentRunResult:
    return AgentRunResult(company="acme", task="implement")


def test_dispatch_routes_to_named_tool(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r._read.run = mock.MagicMock(return_value="READ_OUT")
    res = r._dispatch_tool(r._read.name, {"path": "x"}, _result())
    assert res == {"content": "READ_OUT", "is_error": False}
    r._read.run.assert_called_once_with(path="x")


def test_dispatch_unknown_tool_is_error(tmp_path: Path) -> None:
    res = _runner(tmp_path)._dispatch_tool("frobnicate", {}, _result())
    assert res["is_error"] is True
    assert "unknown tool" in res["content"]


def test_dispatch_bash_records_commit(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r._bash.run = mock.MagicMock(return_value="[main a1b2c3d] do the thing")
    result = _result()
    res = r._dispatch_tool(r._bash.name, {"command": "git commit -m x"}, result)
    assert res["is_error"] is False
    assert result.commits == ["a1b2c3d"]


def test_dispatch_non_bash_does_not_record_commit(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    # Output looks like a commit line, but a non-bash tool must not record it.
    r._write.run = mock.MagicMock(return_value="[main a1b2c3d] not from bash")
    result = _result()
    r._dispatch_tool(r._write.name, {"path": "x", "content": "y"}, result)
    assert result.commits == []


def test_dispatch_tool_error_becomes_is_error(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r._edit.run = mock.MagicMock(side_effect=ToolError("bad edit"))
    res = r._dispatch_tool(r._edit.name, {}, _result())
    assert res["is_error"] is True
    assert "bad edit" in res["content"]


def test_dispatch_unexpected_error_becomes_is_error(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r._read.run = mock.MagicMock(side_effect=RuntimeError("boom"))
    res = r._dispatch_tool(r._read.name, {}, _result())
    assert res["is_error"] is True
    assert "RuntimeError" in res["content"]


def test_dispatch_send_tool_when_bound(tmp_path: Path) -> None:
    r = _runner(tmp_path, with_send=True)
    assert r._send is not None
    r._send.run = mock.MagicMock(return_value="SENT")
    res = r._dispatch_tool(r._send.name, {"channel": "eng", "body": "hi"}, _result())
    assert res == {"content": "SENT", "is_error": False}


def test_dispatch_send_tool_unknown_when_unbound(tmp_path: Path) -> None:
    r = _runner(tmp_path, with_send=False)
    assert r._send is None
    res = r._dispatch_tool("send_message", {"channel": "eng", "body": "hi"}, _result())
    assert res["is_error"] is True
    assert "unknown tool" in res["content"]

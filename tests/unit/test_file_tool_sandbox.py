"""Security characterization: the file tools confine paths to their
allowed roots. Pins the sandbox boundary before/after consolidating the
three identical `_validate` copies into one place."""

from __future__ import annotations

from pathlib import Path

import pytest

from briar.agent.tools import EditFileTool, ReadFileTool, ToolError, WriteFileTool

_FILE_TOOLS = [ReadFileTool, WriteFileTool, EditFileTool]


def test_read_inside_root_ok(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    assert ReadFileTool(allowed_roots=[tmp_path]).run(str(tmp_path / "f.txt")) == "hello"


@pytest.mark.parametrize("tool_cls", _FILE_TOOLS)
def test_absolute_path_outside_root_rejected(tmp_path: Path, tool_cls) -> None:
    tool = tool_cls(allowed_roots=[tmp_path])
    with pytest.raises(ToolError, match="outside allowed roots"):
        tool._validate("/etc/passwd")


@pytest.mark.parametrize("tool_cls", _FILE_TOOLS)
def test_dotdot_traversal_rejected(tmp_path: Path, tool_cls) -> None:
    root = tmp_path / "work"
    root.mkdir()
    tool = tool_cls(allowed_roots=[root])
    with pytest.raises(ToolError, match="outside allowed roots"):
        tool._validate(str(root / ".." / "secret.txt"))


@pytest.mark.parametrize("tool_cls", _FILE_TOOLS)
def test_symlink_escape_rejected(tmp_path: Path, tool_cls) -> None:
    root = tmp_path / "work"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    link = root / "link"
    link.symlink_to(secret)  # symlink inside root pointing OUTSIDE it
    tool = tool_cls(allowed_roots=[root])
    # resolve() follows the symlink to the real (outside) path → rejected.
    with pytest.raises(ToolError, match="outside allowed roots"):
        tool._validate(str(link))


@pytest.mark.parametrize("tool_cls", _FILE_TOOLS)
def test_path_inside_root_accepted(tmp_path: Path, tool_cls) -> None:
    tool = tool_cls(allowed_roots=[tmp_path])
    resolved = tool._validate(str(tmp_path / "sub" / "f.txt"))
    assert resolved == (tmp_path / "sub" / "f.txt").resolve()

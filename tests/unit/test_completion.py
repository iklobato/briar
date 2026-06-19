"""Shell-completion generation."""

from __future__ import annotations

import subprocess

import pytest

from briar.commands import CommandRegistry
from briar.completion import introspect, render


@pytest.fixture
def commands():
    return {n: c for n, c in CommandRegistry.build().items() if n != "completion"}


def test_introspect_captures_flags_and_subcommands(commands):
    specs = {s.name: s for s in introspect(commands)}
    # extract is flat with canonical flags visible, legacy ones hidden.
    assert "--repo" in specs["extract"].flags
    assert "--pr-repo" not in specs["extract"].flags  # suppressed
    # agent is a subcommand command.
    assert set(specs["agent"].subcommands) == {"prfix", "implement"}
    assert "--pr" in specs["agent"].subcommands["prfix"]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_render_includes_commands_and_flags(commands, shell):
    script = render(shell, commands)
    assert "extract" in script and "agent" in script
    assert "--repo" in script
    assert "complete -F _briar briar" in script if shell == "bash" else "compdef _briar briar" in script


def test_render_rejects_unknown_shell(commands):
    with pytest.raises(ValueError, match="unsupported shell"):
        render("fish", commands)


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_emitted_script_is_syntactically_valid(commands, shell):
    script = render(shell, commands)
    interp = "bash" if shell == "bash" else "zsh"
    if not _has(interp):
        pytest.skip(f"{interp} not installed")
    proc = subprocess.run([interp, "-n"], input=script, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr


def _has(binary: str) -> bool:
    from shutil import which

    return which(binary) is not None

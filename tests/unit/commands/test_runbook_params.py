"""`briar runbook` — PARAMETRIC flag-effect coverage.

Companion to test_runbook.py (extract/sweep/serve dispatch). This file
asserts the *observable effect* of every flag in
/tmp/cli_manifest/runbook.md:

  extract  file (positional, required) · --task
  sweep    directory (positional, required)
  serve    directory (positional, required) · --tick

The loader / extractor / scheduler are patched at the seam
``commands/runbook.py`` imports them from, so we assert the exact argument
each flag drives into the collaborator call — a swapped/dropped/ignored
flag must make a test FAIL. No real extraction, no real scheduling loop.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _row(company: str, task: str, status: str, output: str) -> SimpleNamespace:
    return SimpleNamespace(company=company, task=task, status=status, output=output)


# ───────────────────────── extract: file + --task ──────────────────────


class TestExtractFlags:
    def test_file_positional_required(self, cli) -> None:
        result = cli("runbook", "extract")
        assert result.code == 2  # argparse: positional `file` missing

    def test_file_value_reaches_loader(self, cli, mocker, tmp_path) -> None:
        yaml_file = tmp_path / "acme.yaml"
        yaml_file.write_text("x: 1\n")
        load = mocker.patch("briar.commands.runbook.load_runbook_file", return_value="RB")
        mocker.patch("briar.commands.runbook.extract_runbook", return_value=[])
        result = cli("runbook", "extract", str(yaml_file))
        assert result.code == 0
        # The exact path the user passed reaches the loader.
        assert load.call_args.args[0].name == "acme.yaml"

    def test_task_value_reaches_extractor(self, cli, mocker, tmp_path) -> None:
        yaml_file = tmp_path / "acme.yaml"
        yaml_file.write_text("x: 1\n")
        mocker.patch("briar.commands.runbook.load_runbook_file", return_value="RB")
        extract = mocker.patch("briar.commands.runbook.extract_runbook", return_value=[])
        result = cli("runbook", "extract", str(yaml_file), "--task", "nightly")
        assert result.code == 0
        # extract_runbook(runbook, task) — second positional carries the filter.
        assert extract.call_args.args == ("RB", "nightly")

    def test_task_default_empty(self, cli, mocker, tmp_path) -> None:
        yaml_file = tmp_path / "acme.yaml"
        yaml_file.write_text("x: 1\n")
        mocker.patch("briar.commands.runbook.load_runbook_file", return_value="RB")
        extract = mocker.patch("briar.commands.runbook.extract_runbook", return_value=[])
        result = cli("runbook", "extract", str(yaml_file))
        assert result.code == 0
        assert extract.call_args.args == ("RB", "")

    def test_rows_render_in_output(self, cli, mocker, tmp_path) -> None:
        yaml_file = tmp_path / "acme.yaml"
        yaml_file.write_text("x: 1\n")
        mocker.patch("briar.commands.runbook.load_runbook_file", return_value="RB")
        mocker.patch(
            "briar.commands.runbook.extract_runbook",
            return_value=[_row("acme", "weekly", "ok", "wrote 3 files")],
        )
        result = cli("runbook", "extract", str(yaml_file))
        assert result.code == 0
        for token in ("acme", "weekly", "ok", "wrote 3 files"):
            assert token in result.out


# ──────────────────────────── sweep: directory ─────────────────────────


class TestSweepFlags:
    def test_directory_positional_required(self, cli) -> None:
        result = cli("runbook", "sweep")
        assert result.code == 2

    def test_directory_value_drives_glob(self, cli, mocker, tmp_path) -> None:
        # The passed directory is the one scanned; the matching yaml is loaded.
        (tmp_path / "only.yaml").write_text("x: 1\n")
        load = mocker.patch("briar.commands.runbook.load_runbook_file", return_value="RB")
        mocker.patch("briar.commands.runbook.extract_runbook", return_value=[])
        result = cli("runbook", "sweep", str(tmp_path))
        assert result.code == 0
        assert "--- only.yaml ---" in result.out
        assert load.call_args.args[0].name == "only.yaml"


# ──────────────────────────── serve: directory + --tick ────────────────


class TestServeFlags:
    def test_directory_positional_required(self, cli) -> None:
        result = cli("runbook", "serve")
        assert result.code == 2

    def test_directory_value_reaches_scheduler_ctor(self, cli, mocker, tmp_path) -> None:
        from pathlib import Path

        fake = SimpleNamespace(
            register_all=mocker.MagicMock(return_value=[]),
            run_forever=mocker.MagicMock(),
        )
        ctor = mocker.patch("briar.commands.runbook.RunbookScheduler", return_value=fake)
        result = cli("runbook", "serve", str(tmp_path))
        assert result.code == 0
        assert ctor.call_args.args[0] == Path(str(tmp_path))

    @pytest.mark.parametrize("tick_str,tick_val", [("2.5", 2.5), ("0.25", 0.25), ("10", 10.0)], ids=["2.5", "0.25", "10"])
    def test_tick_value_reaches_run_forever(self, cli, mocker, tmp_path, tick_str, tick_val) -> None:
        fake = SimpleNamespace(
            register_all=mocker.MagicMock(return_value=[]),
            run_forever=mocker.MagicMock(),
        )
        mocker.patch("briar.commands.runbook.RunbookScheduler", return_value=fake)
        result = cli("runbook", "serve", str(tmp_path), "--tick", tick_str)
        assert result.code == 0
        fake.run_forever.assert_called_once_with(tick_val)

    def test_tick_default_is_one(self, cli, mocker, tmp_path) -> None:
        fake = SimpleNamespace(
            register_all=mocker.MagicMock(return_value=[]),
            run_forever=mocker.MagicMock(),
        )
        mocker.patch("briar.commands.runbook.RunbookScheduler", return_value=fake)
        result = cli("runbook", "serve", str(tmp_path))
        assert result.code == 0
        fake.run_forever.assert_called_once_with(1.0)

    def test_tick_non_float_exit_2(self, cli, tmp_path) -> None:
        result = cli("runbook", "serve", str(tmp_path), "--tick", "not-a-float")
        assert result.code == 2  # argparse type=float rejects it

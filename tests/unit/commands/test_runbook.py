"""`briar runbook` — extract / sweep / serve dispatch.

The runbook loader, extractor, and scheduler are stubbed at the seam
``commands/runbook.py`` imports them from (module-top imports), so we
assert the command's exit code, what it rendered, and that the
scheduler was driven with the right tick — no real extraction, no real
scheduling loop.
"""

from __future__ import annotations

from types import SimpleNamespace


from briar.errors import CliError


def _row(company: str, task: str, status: str, output: str) -> SimpleNamespace:
    """A stand-in for an extract result row (.company/.task/.status/.output)."""
    return SimpleNamespace(company=company, task=task, status=status, output=output)


# ─────────────────────────── extract ───────────────────────────────


class TestExtract:
    def test_extract_renders_rows_exit_0(self, cli, mocker, tmp_path) -> None:
        yaml_file = tmp_path / "acme.yaml"
        yaml_file.write_text("placeholder: true\n")
        load = mocker.patch("briar.commands.runbook.load_runbook_file", return_value="RB")
        extract = mocker.patch(
            "briar.commands.runbook.extract_runbook",
            return_value=[_row("acme", "weekly", "ok", "wrote 3 files")],
        )
        result = cli("runbook", "extract", str(yaml_file))
        assert result.code == 0
        # Observable: the rendered table carries the row data.
        assert "acme" in result.out
        assert "weekly" in result.out
        assert "wrote 3 files" in result.out
        # The file path was loaded and the (no) task filter forwarded.
        assert load.call_args.args[0].name == "acme.yaml"
        assert extract.call_args.args == ("RB", "")

    def test_extract_forwards_task_filter(self, cli, mocker, tmp_path) -> None:
        yaml_file = tmp_path / "acme.yaml"
        yaml_file.write_text("placeholder: true\n")
        mocker.patch("briar.commands.runbook.load_runbook_file", return_value="RB")
        extract = mocker.patch("briar.commands.runbook.extract_runbook", return_value=[])
        result = cli("runbook", "extract", str(yaml_file), "--task", "nightly")
        assert result.code == 0
        assert extract.call_args.args == ("RB", "nightly")

    def test_extract_missing_file_arg_usage_error(self, cli) -> None:
        result = cli("runbook", "extract")
        assert result.code == 2  # argparse: positional `file` required

    def test_extract_load_failure_surfaces_as_error(self, cli, mocker, tmp_path) -> None:
        yaml_file = tmp_path / "acme.yaml"
        yaml_file.write_text("placeholder: true\n")
        mocker.patch("briar.commands.runbook.load_runbook_file", side_effect=CliError("unreadable runbook"))
        result = cli("runbook", "extract", str(yaml_file))
        # CliError is caught at the top-level CLI and rendered as exit 1.
        assert result.code == 1
        assert "unreadable runbook" in result.err


# ─────────────────────────── sweep ─────────────────────────────────


# ─────────────────────────── serve ─────────────────────────────────


class TestServe:
    def test_serve_not_a_directory_exit_1(self, cli, tmp_path) -> None:
        not_dir = tmp_path / "f.txt"
        not_dir.write_text("x")
        result = cli("runbook", "serve", str(not_dir))
        assert result.code == 1
        assert f"{not_dir} is not a directory" in result.err

    def test_serve_default_tick_is_one(self, cli, mocker, tmp_path) -> None:
        fake_scheduler = SimpleNamespace(
            register_all=mocker.MagicMock(return_value=[]),
            check_logins=mocker.MagicMock(),
            run_forever=mocker.MagicMock(),
        )
        mocker.patch("briar.commands.runbook.RunbookScheduler", return_value=fake_scheduler)
        result = cli("runbook", "serve", str(tmp_path))
        assert result.code == 0
        fake_scheduler.run_forever.assert_called_once_with(1.0)

    def test_serve_bad_tick_value_usage_error(self, cli, tmp_path) -> None:
        result = cli("runbook", "serve", str(tmp_path), "--tick", "not-a-float")
        assert result.code == 2  # argparse type=float rejects it


class TestDispatch:
    def test_missing_op_usage_error(self, cli) -> None:
        result = cli("runbook")
        assert result.code == 2

    def test_unknown_op_usage_error(self, cli) -> None:
        result = cli("runbook", "frobnicate")
        assert result.code == 2
        assert "No such command 'frobnicate'" in result.err

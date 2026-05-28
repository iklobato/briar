"""`briar journal` — list / show / export sessions."""

from __future__ import annotations

import json


def _seed_session(tmp_root, command: str = "test.cmd") -> str:
    """Open + close a journal session via the public API, return its ID."""
    from briar.journal import Journal, make_journal_store
    from briar.journal._journal import set_active_journal
    from briar.journal import record, session

    store = make_journal_store("file", file_root=tmp_root / "journal")
    set_active_journal(Journal(store, sinks=[]))
    with session(command=command) as sess:
        record("test.event", value="hello", rationale="seed")
    set_active_journal(None)
    return sess.session_id


class TestJournalList:
    def test_list_empty_says_no_sessions(self, cli, tmp_root) -> None:
        result = cli("journal", "list", "--root", str(tmp_root / "journal"))
        assert result.code == 0
        assert "no sessions" in result.out

    def test_list_after_seeding_shows_entries(self, cli, tmp_root) -> None:
        sid = _seed_session(tmp_root)
        result = cli("journal", "list", "--root", str(tmp_root / "journal"))
        assert result.code == 0
        assert sid in result.out

    def test_list_filter_by_command_prefix(self, cli, tmp_root) -> None:
        _seed_session(tmp_root, "scaffold.x")
        _seed_session(tmp_root, "extract.y")
        result = cli("journal", "list", "--root", str(tmp_root / "journal"), "--command", "scaffold.")
        assert result.code == 0
        assert "scaffold.x" in result.out
        assert "extract.y" not in result.out


class TestJournalShow:
    def test_show_missing_session_raises_clierror(self, cli, tmp_root) -> None:
        result = cli("journal", "show", "--root", str(tmp_root / "journal"), "nope")
        assert result.code == 1
        assert "not found" in result.err

    def test_show_existing_renders_markdown(self, cli, tmp_root) -> None:
        sid = _seed_session(tmp_root)
        result = cli("journal", "show", "--root", str(tmp_root / "journal"), sid)
        assert result.code == 0
        assert sid in result.out or "test.cmd" in result.out


class TestJournalExport:
    def test_export_markdown_to_stdout(self, cli, tmp_root) -> None:
        sid = _seed_session(tmp_root)
        result = cli("journal", "export", "--root", str(tmp_root / "journal"), sid)
        assert result.code == 0
        assert result.out  # non-empty markdown

    def test_export_json_parseable(self, cli, tmp_root) -> None:
        # `--as json` (not `--format`) so the global position-independent
        # `--format` extractor can't steal it — see commands/journal.py.
        sid = _seed_session(tmp_root)
        result = cli("journal", "export", "--root", str(tmp_root / "journal"), sid, "--as", "json")
        assert result.code == 0
        parsed = json.loads(result.out)
        assert parsed["session_id"] == sid

    def test_export_markdown_to_file(self, cli, tmp_root) -> None:
        sid = _seed_session(tmp_root)
        out = tmp_root / "out.md"
        result = cli("journal", "export", "--root", str(tmp_root / "journal"), sid, "--out", str(out))
        assert result.code == 0
        assert out.exists()
        assert sid in out.read_text() or "test.cmd" in out.read_text()

    def test_export_missing_session_raises(self, cli, tmp_root) -> None:
        result = cli("journal", "export", "--root", str(tmp_root / "journal"), "nope")
        assert result.code == 1

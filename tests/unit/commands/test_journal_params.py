"""`briar journal` — PARAMETRIC flag-effect coverage.

Companion to test_journal.py (list/show/export happy/unhappy paths).
This file asserts the *observable effect* of every flag in
/tmp/cli_manifest/journal.md:

  list    --store (choices file) · --root · --command · --limit
  show    --store · --root · session_id (positional)
  export  --store · --root · session_id (positional) · --as (md|json) · --out

The journal store is patched at the seam
``briar.commands.journal.make_journal_store`` with a recording fake, so we
assert the exact (kind, root) the factory got, the exact filter/limit
passed to ``store.list``, and the chosen serialization/destination of
``export`` — a swapped/dropped/ignored flag must make a test FAIL. The
fake returns *real* Session / JournalRef objects (render_markdown +
to_dict run for real). No network.
"""

from __future__ import annotations

import json

import pytest

from briar.journal import DecisionEvent, Session
from briar.journal.store.base import JournalRef


def _session(command: str = "test.cmd", target: str = "acme") -> Session:
    s = Session(command=command, target=target)
    s.record(DecisionEvent(choice="a.b", value="v", rationale="why"))
    s.close()
    return s


def _ref(session_id: str, command: str = "test.cmd") -> JournalRef:
    return JournalRef(
        session_id=session_id,
        command=command,
        target="acme",
        started_at="2026-01-01T00:00:00Z",
        ended_at="2026-01-01T00:01:00Z",
        decision_count=1,
    )


class _RecordingJournalStore:
    """Records list/get calls; returns canned refs / sessions."""

    def __init__(self) -> None:
        self.list_calls: list = []
        self.get_calls: list = []
        self._refs: list = []
        self._sessions: dict = {}

    def seed_refs(self, *refs: JournalRef) -> None:
        self._refs.extend(refs)

    def seed_session(self, session: Session) -> None:
        self._sessions[session.session_id] = session

    def list(self, *, command_prefix: str = "", limit: int = 50) -> list:
        self.list_calls.append((command_prefix, limit))
        refs = [r for r in self._refs if r.command.startswith(command_prefix)]
        return refs[:limit]

    def get(self, session_id: str):
        self.get_calls.append(session_id)
        return self._sessions.get(session_id)


@pytest.fixture
def journal_seam(mocker):
    """Patch ``make_journal_store`` at the command seam; capture (kind, root)."""
    from types import SimpleNamespace

    state = SimpleNamespace(store=_RecordingJournalStore(), factory_calls=[])

    def factory(kind, file_root=None):
        state.factory_calls.append((kind, file_root))
        return state.store

    mocker.patch("briar.commands.journal.make_journal_store", side_effect=factory)
    return state


# ───────────────────────── --store / --root (all three subcmds) ────────


class TestStoreAndRootFlags:
    @pytest.mark.parametrize(
        "subcmd_args",
        [
            ("list",),
            ("show", "sid-1"),
            ("export", "sid-1"),
        ],
        ids=["list", "show", "export"],
    )
    def test_store_choice_file_reaches_factory(self, cli, journal_seam, subcmd_args) -> None:
        journal_seam.store.seed_session(_session())  # for show/export get()
        journal_seam.store.seed_refs(_ref("sid-1"))
        # session_id 'sid-1' must resolve; seed it explicitly for show/export.
        s = _session()
        journal_seam.store._sessions["sid-1"] = s
        result = cli("journal", *subcmd_args, "--store", "file")
        assert result.code == 0
        assert journal_seam.factory_calls[0][0] == "file"

    def test_store_default_is_file(self, cli, journal_seam) -> None:
        result = cli("journal", "list")
        assert result.code == 0
        assert journal_seam.factory_calls[0][0] == "file"

    def test_invalid_store_choice_exit_2(self, cli) -> None:
        result = cli("journal", "list", "--store", "postgres")
        assert result.code == 2
        assert "invalid choice" in result.err

    def test_root_value_reaches_factory(self, cli, journal_seam) -> None:
        from pathlib import Path

        result = cli("journal", "list", "--root", "/tmp/briar-journal-root")
        assert result.code == 0
        assert journal_seam.factory_calls[0][1] == Path("/tmp/briar-journal-root")

    def test_root_default_is_dot_journal(self, cli, journal_seam) -> None:
        from pathlib import Path

        result = cli("journal", "list")
        assert result.code == 0
        assert journal_seam.factory_calls[0][1] == Path("./journal")


# ──────────────────────────── list --command / --limit ─────────────────


class TestListFilterFlags:
    def test_command_filter_reaches_store(self, cli, journal_seam) -> None:
        journal_seam.store.seed_refs(_ref("a1", "scaffold.x"), _ref("b1", "extract.y"))
        result = cli("journal", "list", "--command", "scaffold.")
        assert result.code == 0
        # The exact prefix reached store.list; only matching refs render.
        assert journal_seam.store.list_calls[0][0] == "scaffold."
        assert "a1" in result.out
        assert "b1" not in result.out

    def test_command_filter_default_empty(self, cli, journal_seam) -> None:
        journal_seam.store.seed_refs(_ref("a1", "scaffold.x"))
        result = cli("journal", "list")
        assert result.code == 0
        assert journal_seam.store.list_calls[0][0] == ""

    def test_limit_value_reaches_store(self, cli, journal_seam) -> None:
        result = cli("journal", "list", "--limit", "7")
        assert result.code == 0
        assert journal_seam.store.list_calls[0][1] == 7

    def test_limit_default_is_50(self, cli, journal_seam) -> None:
        result = cli("journal", "list")
        assert result.code == 0
        assert journal_seam.store.list_calls[0][1] == 50

    def test_limit_non_int_exit_2(self, cli) -> None:
        result = cli("journal", "list", "--limit", "lots")
        assert result.code == 2  # argparse type=int rejects it


# ──────────────────────────── show session_id ──────────────────────────


class TestShowPositional:
    def test_session_id_required(self, cli) -> None:
        result = cli("journal", "show")
        assert result.code == 2

    def test_session_id_reaches_store_get(self, cli, journal_seam) -> None:
        s = _session(command="scaffold.run")
        journal_seam.store.seed_session(s)
        result = cli("journal", "show", s.session_id)
        assert result.code == 0
        assert journal_seam.store.get_calls == [s.session_id]
        # Rendered markdown carries the session's recorded content.
        assert "scaffold.run" in result.out


# ──────────────────────────── export --as / --out ──────────────────────


class TestExportFlags:
    def test_session_id_required(self, cli) -> None:
        result = cli("journal", "export")
        assert result.code == 2

    def test_as_markdown_default_renders_markdown(self, cli, journal_seam) -> None:
        s = _session(command="scaffold.run")
        journal_seam.store.seed_session(s)
        result = cli("journal", "export", s.session_id)
        assert result.code == 0
        # Default markdown — not JSON-parseable, contains the command heading.
        assert "scaffold.run" in result.out
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.out)

    def test_as_json_emits_parseable_dict(self, cli, journal_seam) -> None:
        s = _session(command="scaffold.run")
        journal_seam.store.seed_session(s)
        result = cli("journal", "export", s.session_id, "--as", "json")
        assert result.code == 0
        parsed = json.loads(result.out)
        assert parsed["session_id"] == s.session_id
        assert parsed["command"] == "scaffold.run"

    def test_as_invalid_choice_exit_2(self, cli) -> None:
        result = cli("journal", "export", "sid", "--as", "xml")
        assert result.code == 2
        assert "invalid choice" in result.err

    def test_out_default_dash_goes_to_stdout(self, cli, journal_seam) -> None:
        s = _session()
        journal_seam.store.seed_session(s)
        result = cli("journal", "export", s.session_id)
        assert result.code == 0
        assert result.out  # printed to stdout

    def test_out_path_writes_file_json(self, cli, journal_seam, tmp_path) -> None:
        s = _session(command="scaffold.run")
        journal_seam.store.seed_session(s)
        out = tmp_path / "session.json"
        result = cli("journal", "export", s.session_id, "--as", "json", "--out", str(out))
        assert result.code == 0
        assert out.exists()
        parsed = json.loads(out.read_text())
        assert parsed["session_id"] == s.session_id
        assert f"wrote {out}" in result.err  # status on stderr

    def test_out_path_writes_file_markdown(self, cli, journal_seam, tmp_path) -> None:
        s = _session(command="scaffold.run")
        journal_seam.store.seed_session(s)
        out = tmp_path / "session.md"
        result = cli("journal", "export", s.session_id, "--out", str(out))
        assert result.code == 0
        assert "scaffold.run" in out.read_text()
        assert f"wrote {out}" in result.err  # status on stderr

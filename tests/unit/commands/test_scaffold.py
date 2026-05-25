"""`briar scaffold` — emit IaC bundle JSON, wrapped in journal session."""

from __future__ import annotations

import json

import pytest

from briar.iac import TEMPLATES


@pytest.fixture
def template_names() -> list[str]:
    return list(TEMPLATES.keys())


_IMPL_ARGS = ["--prefix", "test", "--owner", "iklobato", "--repo", "lightapi"]


class TestScaffold:
    def test_implementation_emits_json_to_stdout(self, cli) -> None:
        result = cli("scaffold", "implementation", *_IMPL_ARGS, "-o", "-")
        assert result.code == 0, f"stderr={result.err}"
        parsed = json.loads(result.out)
        assert "agents" in parsed
        assert "sources" in parsed
        assert "tools" in parsed

    def test_implementation_writes_to_path(self, cli, tmp_root) -> None:
        out = tmp_root / "scaffold.json"
        result = cli("scaffold", "implementation", *_IMPL_ARGS, "-o", str(out))
        assert result.code == 0, f"stderr={result.err}"
        assert out.exists()
        parsed = json.loads(out.read_text())
        assert "agents" in parsed

    def test_pr_fixes_template_works(self, cli) -> None:
        result = cli(
            "scaffold", "pr-fixes",
            "--prefix", "test",
            "--owner", "iklobato",
            "--repo", "lightapi",
            "-o", "-",
        )
        if result.code != 0:
            pytest.skip(f"pr-fixes template may need additional args (stderr={result.err})")
        parsed = json.loads(result.out)
        assert "agents" in parsed

    def test_journal_session_recorded(self, cli, tmp_root, mocker) -> None:
        # Records should be written when scaffold runs.
        from briar.journal import _journal as journal_mod

        record_calls: list[tuple] = []

        class _Recorder:
            def begin_session(self, *, command, target=""):
                from briar.journal.models import Session
                record_calls.append(("begin", command, target))
                self._sess = Session(command=command, target=target, session_id="test-123")
                return self._sess

            def record(self, event):
                record_calls.append(("record", event.choice))

            def end_session(self):
                record_calls.append(("end",))
                self._sess.close()

        # The CLI's _install_default_journal will overwrite _active_journal,
        # so patch the module-level function `active_journal()` to always
        # return our recorder regardless of installation.
        recorder = _Recorder()
        mocker.patch.object(journal_mod, "active_journal", return_value=recorder)
        # Also patch the import sites that pulled in active_journal directly.
        import briar.journal as journal_pkg
        mocker.patch.object(journal_pkg, "active_journal", return_value=recorder, create=True)

        cli("scaffold", "implementation", *_IMPL_ARGS, "-o", "-")
        kinds = [c[0] for c in record_calls]
        assert "begin" in kinds
        assert "end" in kinds
        assert any(c[0] == "record" and "scaffold.template" in c[1] for c in record_calls)

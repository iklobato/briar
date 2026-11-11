"""`briar extract` — run extractors, write markdown blob."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, Tuple

import pytest
from pytest_mock import MockerFixture
from time_machine import TimeMachineFixture

from briar.config import load_project_config
from briar.extract.base import ExtractedSection, empty_section

# The login check passes: these tests are about what a run does after it (spec 005 PR 3).
pytestmark = pytest.mark.usefixtures("logins_pass")


@pytest.fixture(autouse=True)
def run_in_tmp_root(tmp_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The knowledge root is the `root` setting, default `./knowledge` (decision C11), so
    each run happens in `tmp_root` and writes under `tmp_root/knowledge`."""
    monkeypatch.chdir(tmp_root)


class TestExtractFailureMode:
    def test_all_empty_raises_clierror_exit_1(self, cli, tmp_root, mocker) -> None:
        # Stub every extractor's is_available=True + extract returns EMPTY.
        from briar.extract import EXTRACTORS

        for ext in EXTRACTORS.values():
            mocker.patch.object(ext, "is_available", return_value=True)
            mocker.patch.object(ext, "extract", return_value=empty_section())
        result = cli(
            "extract",
            "--company", "acme",
        )
        assert result.code == 1
        assert "nothing extracted" in result.err

    def test_no_extractors_available_raises_clierror(self, cli, tmp_root, mocker) -> None:
        from briar.extract import EXTRACTORS

        for ext in EXTRACTORS.values():
            mocker.patch.object(ext, "is_available", return_value=False)
        result = cli(
            "extract",
            "--company", "acme",
        )
        assert result.code == 1


class TestExtractInclude:
    def test_include_filter_only_runs_selected(self, cli, tmp_root, mocker) -> None:
        from briar.extract import EXTRACTORS

        called_names: list[str] = []
        for name, ext in EXTRACTORS.items():
            mocker.patch.object(ext, "is_available", return_value=True)

            def _stub(args, *, _n=name) -> ExtractedSection:
                called_names.append(_n)
                return empty_section()

            mocker.patch.object(ext, "extract", side_effect=_stub)

        # Pick the first known extractor name.
        target = next(iter(EXTRACTORS.keys()))
        cli(
            "extract",
            "--company", "acme",
            "--include", target,
        )
        # All-empty path: rc=1, but only the included one was called.
        assert called_names == [target]

    def test_default_include_runs_all_available(self, cli, tmp_root, mocker) -> None:
        from briar.extract import EXTRACTORS

        called: set[str] = set()
        for name, ext in EXTRACTORS.items():
            mocker.patch.object(ext, "is_available", return_value=True)

            def _stub(args, *, _n=name) -> ExtractedSection:
                called.add(_n)
                return empty_section()

            mocker.patch.object(ext, "extract", side_effect=_stub)

        cli(
            "extract",
            "--company", "acme",
        )
        assert called == set(EXTRACTORS.keys())


class TestExtractWriteSuccess:
    def test_writes_blob_with_default_name(self, cli, tmp_root, mocker) -> None:
        from briar.extract import EXTRACTORS

        # First extractor returns a non-empty section; rest are unavailable.
        first_name, first_ext = next(iter(EXTRACTORS.items()))
        for name, ext in EXTRACTORS.items():
            mocker.patch.object(ext, "is_available", return_value=(name == first_name))
        mocker.patch.object(
            first_ext,
            "extract",
            return_value=ExtractedSection(title="x", body="markdown body"),
        )
        result = cli(
            "extract",
            "--company", "acme",
            "--include", first_name,
        )
        assert result.code == 0
        assert (tmp_root / "knowledge" / "knowledge" / "acme.md").exists()

class TestExtractKeepsOtherSections:
    """`--include x` refreshes x's section and keeps the rest of the blob.

    Before 1.1.82 the blob was replaced whole, so refreshing one extractor
    wiped every other section the agents read."""

    @staticmethod
    def _seed(root, **bodies: str) -> None:
        from briar.extract import EXTRACTORS
        from briar.extract.composer import render_markdown
        from briar.storage import make_store

        # Real titles read "<heading> \N{EM DASH} <detail>"; the splitter keys on <heading>.
        sections = [ExtractedSection(title=EXTRACTORS[n].heading + " \N{EM DASH} old", body=b) for n, b in bodies.items()]
        make_store("file", file_root=root).put("knowledge:acme", render_markdown(company="acme", sections=sections))

    @staticmethod
    def _sections(root) -> dict:
        from briar.extract import EXTRACTORS
        from briar.extract.composer import KnowledgeComposer
        from briar.storage import make_store

        found = KnowledgeComposer.split_sections(make_store("file", file_root=root).get("knowledge:acme") or "")
        return {n: found.get(EXTRACTORS[n].heading, "") for n in ("pr-archaeology", "reviewer-profile", "code-hotspots")}

    @staticmethod
    def _run(cli, root, *include: str):
        args = ["extract", "--company", "acme"]
        for name in include:
            args += ["--include", name]
        return cli(*args)

    def test_refreshing_one_extractor_keeps_the_others(self, cli, tmp_root, mocker) -> None:
        from briar.extract import EXTRACTORS

        root = tmp_root / "knowledge"
        self._seed(root, **{"pr-archaeology": "old prs", "reviewer-profile": "old reviewers"})
        pr = EXTRACTORS["pr-archaeology"]
        mocker.patch.object(pr, "is_available", return_value=True)
        mocker.patch.object(pr, "extract", return_value=ExtractedSection(title=pr.heading + " \N{EM DASH} 1 repo(s)", body="new prs"))

        result = self._run(cli, root, "pr-archaeology")

        assert result.code == 0, result.err
        sections = self._sections(root)
        assert "new prs" in sections["pr-archaeology"]
        assert "old prs" not in sections["pr-archaeology"]
        assert "old reviewers" in sections["reviewer-profile"]

    def test_included_extractor_with_no_data_drops_its_old_section(self, cli, tmp_root, mocker) -> None:
        from briar.extract import EXTRACTORS

        root = tmp_root / "knowledge"
        self._seed(root, **{"code-hotspots": "old hotspots", "reviewer-profile": "old reviewers"})
        pr, hot = EXTRACTORS["pr-archaeology"], EXTRACTORS["code-hotspots"]
        for ext in (pr, hot):
            mocker.patch.object(ext, "is_available", return_value=True)
        mocker.patch.object(pr, "extract", return_value=ExtractedSection(title=pr.heading, body="new prs"))
        mocker.patch.object(hot, "extract", return_value=empty_section())

        result = self._run(cli, root, "pr-archaeology", "code-hotspots")

        assert result.code == 0, result.err
        sections = self._sections(root)
        assert sections["code-hotspots"] == ""
        assert "old reviewers" in sections["reviewer-profile"]

    def test_included_extractor_that_cannot_run_keeps_its_old_section(self, cli, tmp_root, mocker) -> None:
        from briar.extract import EXTRACTORS

        root = tmp_root / "knowledge"
        self._seed(root, **{"code-hotspots": "old hotspots"})
        pr, hot = EXTRACTORS["pr-archaeology"], EXTRACTORS["code-hotspots"]
        mocker.patch.object(pr, "is_available", return_value=True)
        mocker.patch.object(hot, "is_available", return_value=False)
        mocker.patch.object(pr, "extract", return_value=ExtractedSection(title=pr.heading, body="new prs"))

        result = self._run(cli, root, "pr-archaeology", "code-hotspots")

        assert result.code == 0, result.err
        assert "old hotspots" in self._sections(root)["code-hotspots"]


class TestExtractOutputSnapshot:
    """Every file `briar extract` writes, byte for byte, for fixed extractor output.

    The 2.0 refactors move the write path (spec 001) and rewrite the
    extractors (spec 004) under the rule "same output". A changed byte here
    is a changed output. Regenerate on purpose with UPDATE_GOLDEN=1 (not a
    BRIAR_ name: the hermetic fixture in tests/conftest.py strips those)."""

    GOLDEN_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "extract_snapshot"
    UPDATE_ENV = "UPDATE_GOLDEN"
    FROZEN_AT = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    @pytest.fixture
    def frozen_clock(self, time_machine: TimeMachineFixture) -> None:
        time_machine.move_to(self.FROZEN_AT, tick=False)

    @pytest.fixture
    def stubbed_extractors(self, mocker: MockerFixture) -> None:
        from briar.extract import EXTRACTORS

        prs, hotspots = EXTRACTORS["pr-archaeology"], EXTRACTORS["code-hotspots"]
        prs_section = ExtractedSection(
            title=prs.heading + " \N{EM DASH} 1 repo(s)",
            body="new prs",
            data={"repos": 1},
            subsections=[ExtractedSection(title="acme/widgets", body="- #1 fix login")],
        )
        hotspots_section = ExtractedSection(title=hotspots.heading, body="- src/app.py", data={"files": ["src/app.py"]})
        for extractor, section in ((prs, prs_section), (hotspots, hotspots_section)):
            mocker.patch.object(extractor, "is_available", return_value=True)
            mocker.patch.object(extractor, "extract", return_value=section)

    @pytest.fixture
    def project_dir(self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch, frozen_clock: None) -> Path:
        """The cwd of the run, with a stored blob holding a section the run must keep. The
        knowledge root comes from `.briar.toml`, the way `--root` moved there (C11, T021)."""
        from briar.extract import EXTRACTORS
        from briar.extract.composer import render_markdown
        from briar.storage import make_store

        monkeypatch.chdir(tmp_root)
        monkeypatch.setattr("briar.config.load_project_config", load_project_config)
        (tmp_root / ".briar.toml").write_text('root = "kb"\n')
        kept = ExtractedSection(title=EXTRACTORS["reviewer-profile"].heading + " \N{EM DASH} old", body="old reviewers")
        make_store("file", file_root=tmp_root / "kb").put("knowledge:acme", render_markdown(company="acme", sections=[kept]))
        return tmp_root

    @pytest.fixture
    def outputs(self, cli: Callable[..., SimpleNamespace], project_dir: Path, stubbed_extractors: None) -> Dict[str, str]:
        extract_run = cli(*self._extract_argv(project_dir))
        assert extract_run.code == 0, extract_run.err  # fixture guard: a failed run has no files to compare
        return {"blob.golden.md": (project_dir / "kb" / "knowledge" / "acme.md").read_text()}

    def _extract_argv(self, project_dir: Path) -> Tuple[str, ...]:
        return (
            "extract",
            "--company", "acme",
            "--include", "pr-archaeology",
            "--include", "code-hotspots",
        )

    @classmethod
    def _assert_matches_golden(cls, outputs: Dict[str, str], name: str) -> None:
        golden = cls.GOLDEN_DIR / name
        if os.environ.get(cls.UPDATE_ENV):
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_text(outputs[name])
        assert outputs[name] == golden.read_text()

    def test_blob_keeps_other_sections_and_matches_golden(self, outputs: Dict[str, str]) -> None:
        self._assert_matches_golden(outputs, "blob.golden.md")

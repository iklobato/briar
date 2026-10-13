"""Extract service — selection, gating, and the all-empty failure path."""

from __future__ import annotations

import pytest

from briar.errors import CliError
from briar.extract import EXTRACTORS
from briar.extract.base import ExtractedSection, empty_section
from briar.service import GateMode
from briar.service import extract as es


def _root(tmp_path):
    return str(tmp_path / "knowledge")


def _stub_all(mocker, *, available: bool, section: ExtractedSection) -> None:
    for ext in EXTRACTORS.values():
        mocker.patch.object(ext, "is_available", return_value=available)
        mocker.patch.object(ext, "extract", return_value=section)


def test_execute_writes_blob(tmp_path, mocker) -> None:
    name = next(iter(EXTRACTORS))
    _stub_all(mocker, available=False, section=empty_section())
    # Make exactly one extractor available + productive.
    mocker.patch.object(EXTRACTORS[name], "is_available", return_value=True)
    mocker.patch.object(EXTRACTORS[name], "extract", return_value=ExtractedSection(title="T", body="body"))

    out = es.run_extract(company="acme", include=[name], root=_root(tmp_path))
    assert out.executed is True
    assert out.result["blob_name"] == "knowledge:acme"
    assert out.result["ran"] == [name]
    assert out.result["section_count"] == 1


def test_dry_run_does_not_write_or_call_extract(tmp_path, mocker) -> None:
    name = next(iter(EXTRACTORS))
    mocker.patch.object(EXTRACTORS[name], "is_available", return_value=True)
    extract_spy = mocker.patch.object(EXTRACTORS[name], "extract", return_value=ExtractedSection(title="T", body="b"))

    out = es.run_extract(company="acme", include=[name], root=_root(tmp_path), gate=GateMode.DRY_RUN)
    assert out.executed is False
    assert name in out.summary
    # DRY_RUN must not invoke the expensive extractor call, and must not write.
    extract_spy.assert_not_called()
    from briar.service import knowledge as ks

    assert ks.get_blob(blob_name="knowledge:acme", root=_root(tmp_path)) is None


def test_all_empty_raises_clierror(tmp_path, mocker) -> None:
    _stub_all(mocker, available=True, section=empty_section())
    with pytest.raises(CliError, match="nothing extracted"):
        es.run_extract(company="acme", root=_root(tmp_path))


def test_custom_blob_name_honored(tmp_path, mocker) -> None:
    name = next(iter(EXTRACTORS))
    _stub_all(mocker, available=False, section=empty_section())
    mocker.patch.object(EXTRACTORS[name], "is_available", return_value=True)
    mocker.patch.object(EXTRACTORS[name], "extract", return_value=ExtractedSection(title="T", body="b"))

    out = es.run_extract(company="acme", include=[name], blob_name="knowledge:custom", root=_root(tmp_path))
    assert out.result["blob_name"] == "knowledge:custom"


class TestRunExtractKeepsOtherSections:
    """The MCP `extract_run` tool refreshes the sections it ran and keeps
    the rest of the blob. Up to 1.1.82 it replaced the blob whole."""

    @staticmethod
    def _seed(root: str, **bodies: str) -> None:
        from briar.extract.composer import render_markdown
        from briar.storage import make_store

        # Real titles read "<heading> \N{EM DASH} <detail>"; the splitter keys on <heading>.
        sections = [ExtractedSection(title=EXTRACTORS[n].heading + " \N{EM DASH} old", body=b) for n, b in bodies.items()]
        make_store("file", file_root=root).put("knowledge:acme", render_markdown(company="acme", sections=sections))

    @staticmethod
    def _sections(root: str) -> dict:
        from briar.iac.scaffold._knowledge import KnowledgeSplicer
        from briar.storage import make_store

        splicer = KnowledgeSplicer.from_store(make_store("file", file_root=root), "acme")
        return {n: splicer.section(n) for n in ("pr-archaeology", "reviewer-profile", "code-hotspots")}

    def test_refreshing_one_extractor_keeps_the_others(self, tmp_path, mocker) -> None:
        root = _root(tmp_path)
        self._seed(root, **{"pr-archaeology": "old prs", "reviewer-profile": "old reviewers"})
        pr = EXTRACTORS["pr-archaeology"]
        mocker.patch.object(pr, "is_available", return_value=True)
        mocker.patch.object(pr, "extract", return_value=ExtractedSection(title=pr.heading + " \N{EM DASH} 1 repo(s)", body="new prs"))

        es.run_extract(company="acme", include=["pr-archaeology"], root=root)

        sections = self._sections(root)
        assert "new prs" in sections["pr-archaeology"]
        assert "old prs" not in sections["pr-archaeology"]
        assert "old reviewers" in sections["reviewer-profile"]

    def test_included_extractor_with_no_data_drops_its_old_section(self, tmp_path, mocker) -> None:
        root = _root(tmp_path)
        self._seed(root, **{"code-hotspots": "old hotspots", "reviewer-profile": "old reviewers"})
        pr, hot = EXTRACTORS["pr-archaeology"], EXTRACTORS["code-hotspots"]
        for ext in (pr, hot):
            mocker.patch.object(ext, "is_available", return_value=True)
        mocker.patch.object(pr, "extract", return_value=ExtractedSection(title=pr.heading, body="new prs"))
        mocker.patch.object(hot, "extract", return_value=empty_section())

        es.run_extract(company="acme", include=["pr-archaeology", "code-hotspots"], root=root)

        sections = self._sections(root)
        assert sections["code-hotspots"] == ""
        assert "old reviewers" in sections["reviewer-profile"]

    def test_included_extractor_that_cannot_run_keeps_its_old_section(self, tmp_path, mocker) -> None:
        root = _root(tmp_path)
        self._seed(root, **{"code-hotspots": "old hotspots"})
        pr, hot = EXTRACTORS["pr-archaeology"], EXTRACTORS["code-hotspots"]
        mocker.patch.object(pr, "is_available", return_value=True)
        mocker.patch.object(hot, "is_available", return_value=False)
        mocker.patch.object(pr, "extract", return_value=ExtractedSection(title=pr.heading, body="new prs"))

        es.run_extract(company="acme", include=["pr-archaeology", "code-hotspots"], root=root)

        assert "old hotspots" in self._sections(root)["code-hotspots"]


class TestRunExtractCanonicalArgs:
    """`extractor_args` takes the canonical keys (`repo`, `since_days`,
    `max`, ...) like `briar extract` and runbook YAML. Up to 1.1.83 they were
    set on the namespace but never fanned out, so over MCP every repo
    extractor stayed unavailable."""

    def test_repo_key_makes_repo_extractor_runnable(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")

        out = es.run_extract(
            company="acme",
            include=["pr-archaeology"],
            root=_root(tmp_path),
            extractor_args={"repo": ["acme/api"]},
            gate=GateMode.DRY_RUN,
        )

        assert "would run 1 extractor(s) ['pr-archaeology']" in out.summary

    def test_repo_and_max_reach_the_private_dests(self, tmp_path, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        pr = EXTRACTORS["pr-archaeology"]
        extract = mocker.patch.object(pr, "extract", return_value=ExtractedSection(title=pr.heading, body="b"))

        es.run_extract(company="acme", include=["pr-archaeology"], root=_root(tmp_path), extractor_args={"repo": ["acme/api"], "max": 7})

        ns = extract.call_args.args[0]
        assert ns.pr_repo == ["acme/api"]
        assert ns.pr_max == 7

    def test_private_key_wins_over_canonical(self, tmp_path, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        pr = EXTRACTORS["pr-archaeology"]
        extract = mocker.patch.object(pr, "extract", return_value=ExtractedSection(title=pr.heading, body="b"))

        es.run_extract(
            company="acme",
            include=["pr-archaeology"],
            root=_root(tmp_path),
            extractor_args={"repo": ["acme/api"], "pr_repo": ["acme/web"]},
        )

        assert extract.call_args.args[0].pr_repo == ["acme/web"]

"""`briar extract` — run extractors, write markdown blob."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from briar.extract.base import ExtractedSection, empty_section


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
            "--storage", "file",
            "--root", str(tmp_root / "knowledge"),
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
            "--storage", "file",
            "--root", str(tmp_root / "knowledge"),
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
        result = cli(
            "extract",
            "--company", "acme",
            "--include", target,
            "--storage", "file",
            "--root", str(tmp_root / "knowledge"),
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
            "--storage", "file",
            "--root", str(tmp_root / "knowledge"),
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
            "--storage", "file",
            "--root", str(tmp_root / "knowledge"),
        )
        assert result.code == 0
        assert (tmp_root / "knowledge" / "knowledge" / "acme.md").exists()

    def test_writes_blob_with_custom_name(self, cli, tmp_root, mocker) -> None:
        from briar.extract import EXTRACTORS

        first_name, first_ext = next(iter(EXTRACTORS.items()))
        for name, ext in EXTRACTORS.items():
            mocker.patch.object(ext, "is_available", return_value=(name == first_name))
        mocker.patch.object(
            first_ext,
            "extract",
            return_value=ExtractedSection(title="x", body="body"),
        )
        result = cli(
            "extract",
            "--company", "acme",
            "--blob-name", "custom:name",
            "--include", first_name,
            "--storage", "file",
            "--root", str(tmp_root / "knowledge"),
        )
        assert result.code == 0
        # The custom name routes through StoreFile's "category:rest" parser.
        assert (tmp_root / "knowledge" / "custom" / "name.md").exists()


class TestExtractJsonSidecar:
    def test_out_json_writes_parallel_file(self, cli, tmp_root, mocker) -> None:
        from briar.extract import EXTRACTORS

        first_name, first_ext = next(iter(EXTRACTORS.items()))
        for name, ext in EXTRACTORS.items():
            mocker.patch.object(ext, "is_available", return_value=(name == first_name))
        mocker.patch.object(
            first_ext,
            "extract",
            return_value=ExtractedSection(title="x", body="body"),
        )
        out = tmp_root / "json-out" / "acme.json"
        result = cli(
            "extract",
            "--company", "acme",
            "--include", first_name,
            "--storage", "file",
            "--root", str(tmp_root / "knowledge"),
            "--out-json", str(out),
        )
        assert result.code == 0
        assert out.exists()

    def test_out_json_empty_string_no_json_file(self, cli, tmp_root, mocker) -> None:
        from briar.extract import EXTRACTORS

        first_name, first_ext = next(iter(EXTRACTORS.items()))
        for name, ext in EXTRACTORS.items():
            mocker.patch.object(ext, "is_available", return_value=(name == first_name))
        mocker.patch.object(
            first_ext,
            "extract",
            return_value=ExtractedSection(title="x", body="body"),
        )
        # Pass empty string — no JSON sidecar should be written.
        result = cli(
            "extract",
            "--company", "acme",
            "--include", first_name,
            "--storage", "file",
            "--root", str(tmp_root / "knowledge"),
        )
        assert result.code == 0


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
        from briar.iac.scaffold._knowledge import KnowledgeSplicer
        from briar.storage import make_store

        splicer = KnowledgeSplicer.from_store(make_store("file", file_root=root), "acme")
        return {n: splicer.section(n) for n in ("pr-archaeology", "reviewer-profile", "code-hotspots")}

    @staticmethod
    def _run(cli, root, *include: str):
        args = ["extract", "--company", "acme", "--storage", "file", "--root", str(root)]
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

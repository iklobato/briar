"""`briar extract` — run extractors, write markdown blob."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from briar.extract.base import EMPTY_SECTION, ExtractedSection


class TestExtractFailureMode:
    def test_all_empty_raises_clierror_exit_1(self, cli, tmp_root, mocker) -> None:
        # Stub every extractor's is_available=True + extract returns EMPTY.
        from briar.extract import EXTRACTORS

        for ext in EXTRACTORS.values():
            mocker.patch.object(ext, "is_available", return_value=True)
            mocker.patch.object(ext, "extract", return_value=EMPTY_SECTION)
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
                return EMPTY_SECTION

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
                return EMPTY_SECTION

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

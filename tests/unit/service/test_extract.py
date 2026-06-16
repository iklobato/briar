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

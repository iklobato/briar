"""Extractor orchestration, presentation-free and gated.

Selects extractors, checks availability, runs the available ones, composes
the markdown blob, and writes it. Shared by `briar extract` (CLI) and the
MCP/dashboard surfaces. The expensive, outward-facing part is the live
GitHub/AWS/etc. calls each extractor makes — so DRY_RUN reports *which*
extractors would run and where the blob would land, without calling them.

Each extractor reads its flags off an `argparse.Namespace`. Programmatic
callers don't have one, so — like `RunbookExtractor._collect_sections` —
we synthesize a per-extractor namespace from its own argparse defaults,
inject `company`, then overlay caller-supplied overrides.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from briar.errors import CliError
from briar.extract import EXTRACTORS
from briar.extract.base import ExtractedSection
from briar.extract.canonical import apply_canonical
from briar.extract.composer import KnowledgeComposer, render_json, render_markdown
from briar.service._gating import GateMode, GateResult
from briar.storage import KnowledgeRef, KnowledgeStore, make_store


def write_knowledge_blob(
    store: KnowledgeStore,
    *,
    blob_name: str,
    company: str,
    sections: List[ExtractedSection],
    refreshed: List[str],
) -> KnowledgeRef:
    """Merge `sections` into the stored blob and write it back.

    `refreshed` holds the headings of the extractors that ran, with or
    without data: their old sections are replaced (or dropped when empty).
    Sections of every other extractor stay. The one write path for both
    `briar extract` and the MCP `extract_run` tool."""
    md = KnowledgeComposer.merge_markdown(
        company=company,
        existing=store.get(blob_name),
        fresh=render_markdown(company=company, sections=sections),
        refreshed=refreshed,
    )
    return store.put(blob_name, md, category="knowledge")


def _namespace_for(name: str, *, company: str, overrides: Dict[str, Any]) -> argparse.Namespace:
    """Build the argparse namespace one extractor expects: its own defaults,
    then `company` (unless overridden), then caller overrides on top.
    Canonical keys (`repo`, `since_days`, `max`, ...) fan out to the
    extractor's private dests, as in `briar extract` and runbook YAML;
    a private key in `overrides` wins."""
    extractor = EXTRACTORS[name]
    seed = argparse.ArgumentParser(add_help=False)
    extractor.add_arguments(seed)
    ns = seed.parse_args([])
    if company and "company" not in overrides:
        setattr(ns, "company", company)
    for key, value in overrides.items():
        setattr(ns, key, value)
    apply_canonical(ns, extractor)
    return ns


def _availability(selected: List[str], *, company: str, overrides: Dict[str, Any]) -> Dict[str, bool]:
    return {name: EXTRACTORS[name].is_available(_namespace_for(name, company=company, overrides=overrides)) for name in selected}


def run_extract(
    *,
    company: str,
    include: Optional[List[str]] = None,
    storage: str = "file",
    blob_name: str = "",
    root: str = "./knowledge",
    out_json: str = "",
    extractor_args: Optional[Dict[str, Any]] = None,
    gate: GateMode = GateMode.EXECUTE,
) -> GateResult:
    """Run the selected extractors and write the composed blob.

    Raises `CliError` when every enabled extractor returns empty — same
    contract as the CLI today."""
    selected = include or list(EXTRACTORS.keys())
    overrides = dict(extractor_args or {})
    target = blob_name or f"knowledge:{company}"

    if gate is GateMode.DRY_RUN:
        avail = _availability(selected, company=company, overrides=overrides)
        runnable = [n for n, ok in avail.items() if ok]
        unavailable = [n for n, ok in avail.items() if not ok]
        return GateResult.previewed(
            f"would run {len(runnable)} extractor(s) {runnable} (skipping unavailable {unavailable}) " f"and write to {target!r} via store={storage}"
        )

    sections: List[ExtractedSection] = []
    refreshed: List[str] = []
    ran: List[str] = []
    skipped: List[str] = []
    empty: List[str] = []
    for name in selected:
        ext = EXTRACTORS[name]
        ns = _namespace_for(name, company=company, overrides=overrides)
        if not ext.is_available(ns):
            skipped.append(name)
            continue
        refreshed.append(ext.heading)
        section = ext.extract(ns)
        if section.is_empty:
            empty.append(name)
            continue
        sections.append(section)
        ran.append(name)

    if not sections:
        raise CliError("nothing extracted — every enabled extractor returned empty")

    ref = write_knowledge_blob(
        make_store(storage, file_root=Path(root)),
        blob_name=target,
        company=company,
        sections=sections,
        refreshed=refreshed,
    )

    result: Dict[str, Any] = {
        "blob_name": ref.name,
        "byte_count": ref.byte_count,
        "section_count": len(sections),
        "ran": ran,
        "skipped": skipped,
        "empty": empty,
    }
    if out_json:
        json_path = Path(out_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(render_json(company=company, sections=sections))
        result["json_path"] = str(json_path)

    return GateResult.performed(
        f"wrote blob {ref.name!r} ({ref.byte_count} bytes, {len(sections)} sections) via store={storage}",
        result,
    )

"""Runbook executor — walks `RunbookFile` and writes a knowledge file
per company. The API-driven apply/destroy paths were removed when the
CLI dropped its remote-call surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Tuple

import yaml
from pydantic import ValidationError

from briar.errors import ConfigError
from briar.iac.runbook.models import (
    CompanyEntry,
    KnowledgeBinding,
    RunbookFile,
)


ExtractRow = Tuple[str, str, str]  # (company, status, output_path)


class RunbookLoader:
    """Parse YAML or JSON into the typed schema."""

    @staticmethod
    def load(path: Path) -> RunbookFile:
        try:
            raw = path.read_text()
        except FileNotFoundError as exc:
            raise ConfigError(f"runbook not found: {path}") from exc

        suffix = path.suffix.lower()
        try:
            data = (
                yaml.safe_load(raw)
                if suffix in {".yaml", ".yml"}
                else json.loads(raw)
            )
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            raise ConfigError(f"{path}: invalid {suffix or 'JSON'} — {exc}") from exc

        if type(data) is not dict:
            raise ConfigError(f"{path}: top-level must be a mapping")

        try:
            return RunbookFile.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(f"invalid runbook {path}\n{exc}") from exc


class RunbookExtractor:
    """Walks `RunbookFile.companies` and runs each company's extractors.

    Stateless; everything flows through the classmethods so the public
    surface stays a single `RunbookExtractor.extract(runbook)` call."""

    @classmethod
    def extract(cls, runbook_file: RunbookFile) -> List[ExtractRow]:
        """Returns rows of (company, status, output_path).

        Lazy-imports the extract subpackage so callers who never run
        this don't pay the boto3 import cost."""
        from briar.extract import EXTRACTORS
        from briar.extract.composer import KnowledgeComposer
        from briar.storage import make_store

        rows: List[ExtractRow] = []
        for company_name, company in runbook_file.companies.items():
            if not company.extract:
                rows.append((company_name, "skipped (no extract section)", ""))
                continue

            binding = cls._binding_for(company, company_name)
            sections = cls._collect_sections(company, EXTRACTORS)

            if not sections:
                rows.append((company_name, "empty (no sections)", binding.name))
                continue

            md = KnowledgeComposer.markdown(
                company=company_name, sections=sections,
            )
            file_root = Path(binding.root) if binding.root else None
            store = make_store(binding.store, file_root=file_root)
            ref = store.put(binding.name, md, category="knowledge")
            rows.append((
                company_name,
                f"wrote {ref.byte_count} bytes via store={binding.store}",
                binding.name,
            ))
        return rows

    @staticmethod
    def _binding_for(
        company: CompanyEntry,
        company_name: str,
    ) -> KnowledgeBinding:
        """Explicit `knowledge:` wins; `knowledge_file:` is the legacy
        shortcut; otherwise default to `./knowledge/<company>.md`."""
        if company.knowledge is not None:
            return company.knowledge
        if company.knowledge_file:
            return KnowledgeBinding(store="file", name=company.knowledge_file)
        return KnowledgeBinding(
            store="file", name=f"./knowledge/{company_name}.md",
        )

    @staticmethod
    def _collect_sections(company: CompanyEntry, registry):
        sections = []
        for entry in company.extract:
            extractor = registry.get(entry.name)
            if extractor is None:
                continue
            seed = argparse.ArgumentParser(add_help=False)
            extractor.add_arguments(seed)
            ns = seed.parse_args([])
            for k, v in entry.args.items():
                setattr(ns, k, v)
            if not extractor.is_available(ns):
                continue
            section = extractor.extract(ns)
            if section is not None:
                sections.append(section)
        return sections


# Back-compat aliases for callers and tests.
load_runbook_file = RunbookLoader.load
extract_runbook = RunbookExtractor.extract

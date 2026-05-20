"""Runbook executor — walks `RunbookFile`, runs extractors, writes the
per-company knowledge file."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

import yaml
from pydantic import ValidationError

from briar.errors import ConfigError
from briar.extract.base import ExtractedSection
from briar.iac.runbook.models import (
    CompanyEntry,
    ExtractEntry,
    KnowledgeBinding,
    RunbookFile,
    ScheduleEntry,
)


_DEFAULT_TASK = "extractors"
_DEFAULT_EVERY = "day at 03:17"

# Empty string sentinel for "run every task" in the public extract API.
NO_TASK_FILTER = ""


@dataclass
class ExtractRow:
    """One row in the result of `RunbookExtractor.extract`."""

    company: str
    task: str
    status: str
    output: str


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
            data = yaml.safe_load(raw) if suffix in {".yaml", ".yml"} else json.loads(raw)
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            raise ConfigError(f"{path}: invalid {suffix or 'JSON'} — {exc}") from exc

        if type(data) is not dict:
            raise ConfigError(f"{path}: top-level must be a mapping")

        try:
            return RunbookFile.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(f"invalid runbook {path}\n{exc}") from exc


class RunbookSchedules:
    """Coalesce the YAML's legacy + new shapes into a uniform list."""

    @staticmethod
    def for_company(company: CompanyEntry) -> List[ScheduleEntry]:
        items: List[ScheduleEntry] = list(company.schedules)
        if company.extract and not any(s.task == _DEFAULT_TASK for s in items):
            items.append(
                ScheduleEntry(
                    task=_DEFAULT_TASK,
                    every=_DEFAULT_EVERY,
                    extract=list(company.extract),
                )
            )
        return items


class RunbookExtractor:
    """Runs the extractors. `task` is the filter — empty string runs
    every schedule; a non-empty value runs only the matching task."""

    @classmethod
    def extract(cls, runbook_file: RunbookFile, task: str = NO_TASK_FILTER) -> List[ExtractRow]:
        from briar.extract import EXTRACTORS
        from briar.extract.composer import KnowledgeComposer
        from briar.storage import make_store

        rows: List[ExtractRow] = []
        for company_name, company in runbook_file.companies.items():
            schedules = RunbookSchedules.for_company(company)
            if task:
                schedules = [s for s in schedules if s.task == task]
            if not schedules:
                if not task:
                    rows.append(ExtractRow(company_name, "-", "skipped (no schedule)", ""))
                continue

            binding = cls._binding_for(company, company_name)

            for schedule in schedules:
                sections = cls._collect_sections(schedule.extract, EXTRACTORS)
                if not sections:
                    rows.append(ExtractRow(company_name, schedule.task, "empty (no sections)", binding.name))
                    continue

                md = KnowledgeComposer.markdown(company=company_name, sections=sections)
                file_root = Path(binding.root) if binding.root else Path("./knowledge")
                store = make_store(binding.store, file_root=file_root)
                # Default task writes the canonical blob; other tasks
                # append `.<task>` so concurrent writes don't clobber.
                blob_name = binding.name if schedule.task == _DEFAULT_TASK else cls._task_blob_name(binding.name, schedule.task)
                ref = store.put(blob_name, md, category="knowledge")
                rows.append(ExtractRow(company_name, schedule.task, f"wrote {ref.byte_count} bytes via store={binding.store}", blob_name))
        return rows

    @staticmethod
    def _binding_for(company: CompanyEntry, company_name: str) -> KnowledgeBinding:
        if company.knowledge.name:
            return company.knowledge
        if company.knowledge_file:
            return KnowledgeBinding(store="file", name=company.knowledge_file)
        return KnowledgeBinding(store="file", name=f"./knowledge/{company_name}.md")

    @staticmethod
    def _task_blob_name(base_name: str, task: str) -> str:
        """For non-default tasks, suffix the blob name with `.<task>`."""
        if base_name.endswith(".md"):
            return f"{base_name[:-3]}.{task}.md"
        return f"{base_name}.{task}"

    @staticmethod
    def _collect_sections(extract_list: List[ExtractEntry], registry: Any) -> List[ExtractedSection]:
        sections: List[ExtractedSection] = []
        for entry in extract_list:
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
            if not section.is_empty:
                sections.append(section)
        return sections


# Back-compat aliases.
load_runbook_file = RunbookLoader.load
extract_runbook = RunbookExtractor.extract

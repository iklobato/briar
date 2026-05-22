"""Runbook executor — walks `RunbookFile`, runs extractors, writes the
per-company knowledge file."""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

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
from briar.log_context import log_context


log = logging.getLogger(__name__)


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
        log.debug("runbook-load: reading path=%s", path)
        try:
            raw = path.read_text()
        except FileNotFoundError as exc:
            log.error("runbook-load: file not found path=%s", path)
            raise ConfigError(f"runbook not found: {path}") from exc

        suffix = path.suffix.lower()
        try:
            data = yaml.safe_load(raw) if suffix in {".yaml", ".yml"} else json.loads(raw)
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            log.error("runbook-load: parse error path=%s suffix=%s err=%s", path, suffix, exc)
            raise ConfigError(f"{path}: invalid {suffix or 'JSON'} — {exc}") from exc

        if type(data) is not dict:
            log.error("runbook-load: top-level is not a mapping path=%s got=%s", path, type(data).__name__)
            raise ConfigError(f"{path}: top-level must be a mapping")

        try:
            model = RunbookFile.model_validate(data)
        except ValidationError as exc:
            log.error("runbook-load: schema validation failed path=%s\n%s", path, exc)
            raise ConfigError(f"invalid runbook {path}\n{exc}") from exc
        log.debug("runbook-load: parsed path=%s companies=%d", path, len(model.companies))
        return model


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
        log.info("runbook-extract: starting companies=%d task_filter=%r", len(runbook_file.companies), task or "(all)")
        for company_name, company in runbook_file.companies.items():
            with log_context(company=company_name):
                schedules = RunbookSchedules.for_company(company)
                if task:
                    schedules = [s for s in schedules if s.task == task]
                if not schedules:
                    log.info("runbook-extract: no matching schedule (task_filter=%r)", task or "(all)")
                    if not task:
                        rows.append(ExtractRow(company_name, "-", "skipped (no schedule)", ""))
                    continue

                binding = cls._binding_for(company, company_name)
                log.debug("runbook-extract: knowledge binding store=%s name=%s root=%s", binding.store, binding.name, binding.root or "(default)")

                for schedule in schedules:
                    with log_context(task=schedule.task):
                        cls._run_schedule(
                            company_name=company_name,
                            schedule=schedule,
                            binding=binding,
                            registry=EXTRACTORS,
                            composer=KnowledgeComposer,
                            make_store=make_store,
                            rows=rows,
                            company=company_name,
                        )
        log.info("runbook-extract: finished total_rows=%d", len(rows))
        return rows

    @classmethod
    def _run_schedule(
        cls,
        *,
        company_name: str,
        schedule: ScheduleEntry,
        binding: KnowledgeBinding,
        registry: Any,
        composer: Any,
        make_store: Any,
        rows: List[ExtractRow],
        company: str = "",
    ) -> None:
        """Execute one schedule entry. Three failure points are all
        routed through `_record_failure` so the log + notify + row
        shape stays consistent and the failure paths don't drift in
        style across edits."""
        wall_start = time.perf_counter()
        log.info("schedule-start: every=%r extract_count=%d", schedule.every, len(schedule.extract))

        try:
            sections = cls._collect_sections(schedule.extract, registry, company=company)
        except Exception as exc:  # noqa: BLE001
            cls._record_failure(rows, company_name=company_name, company=company, task=schedule.task, reason="collect_sections raised", blob_name=binding.name, exc=exc)
            return

        if not sections:
            log.warning("schedule-empty: zero non-empty sections")
            rows.append(ExtractRow(company_name, schedule.task, "empty (no sections)", binding.name))
            return

        log.debug("schedule-compose: rendering markdown sections=%d", len(sections))
        md = composer.markdown(company=company_name, sections=sections)

        file_root = Path(binding.root) if binding.root else Path("./knowledge")
        log.debug("schedule-store-open: store=%s file_root=%s", binding.store, file_root)
        try:
            store = make_store(binding.store, file_root=file_root)
        except Exception as exc:  # noqa: BLE001
            cls._record_failure(rows, company_name=company_name, company=company, task=schedule.task, reason=f"store open raised: {binding.store}", blob_name=binding.name, exc=exc)
            return

        blob_name = binding.name if schedule.task == _DEFAULT_TASK else cls._task_blob_name(binding.name, schedule.task)

        # Single-connection compare-and-set. The store implementation
        # compares md5 server-side (or local md5 for the file backend)
        # and only writes when the content changed. Skip path leaves
        # `updated_at` and history rows untouched — saves Postgres
        # traffic, history bloat, and downstream LLM re-read tokens.
        try:
            outcome = store.put_if_changed(blob_name, md, category="knowledge")
        except Exception as exc:  # noqa: BLE001
            cls._record_failure(rows, company_name=company_name, company=company, task=schedule.task, reason=f"put_if_changed raised: {blob_name}", blob_name=blob_name, exc=exc)
            return

        elapsed_ms = int((time.perf_counter() - wall_start) * 1000)
        if not outcome.wrote:
            log.info(
                "schedule-skip: output unchanged blob=%s hash=%s bytes=%d elapsed_ms=%d",
                blob_name,
                outcome.new_hash,
                outcome.byte_count,
                elapsed_ms,
            )
            rows.append(
                ExtractRow(
                    company_name,
                    schedule.task,
                    f"skipped (unchanged, {outcome.byte_count} bytes, hash={outcome.new_hash[:8]})",
                    blob_name,
                )
            )
            return

        log.info(
            "schedule-done: blob=%s bytes=%d hash=%s elapsed_ms=%d prev_hash=%s",
            blob_name,
            outcome.byte_count,
            outcome.new_hash,
            elapsed_ms,
            outcome.prev_hash or "(none)",
        )
        rows.append(ExtractRow(company_name, schedule.task, f"wrote {outcome.byte_count} bytes via store={binding.store}", blob_name))

    @classmethod
    def _record_failure(
        cls,
        rows: List[ExtractRow],
        *,
        company_name: str,
        company: str,
        task: str,
        reason: str,
        blob_name: str,
        exc: Exception,
    ) -> None:
        """The "schedule step raised" boundary — three sites used to
        duplicate this four-line shape (log.exception + notify + row
        append). One place now."""
        log.exception("schedule-failed: %s", reason)
        cls._notify_failure(company, task, reason, str(exc))
        rows.append(ExtractRow(company_name, task, f"failed ({reason} — see traceback)", blob_name))

    @staticmethod
    def _notify_failure(company: str, task: str, reason: str, detail: str) -> None:
        """Dispatch a failure notification to every sink listed in
        ``$BRIAR_NOTIFY_SINKS`` (comma-separated). Silent no-op when
        the env var is empty.

        Each sink is fire-and-forget — a sink failure is logged but
        does NOT propagate (a broken Telegram bot must not crash the
        extractor). Per-company creds resolve through the standard
        ``CredEnv`` path."""
        from briar.env_vars import CredEnv
        from briar.notify import make_sink

        raw = CredEnv.BRIAR_NOTIFY_SINKS.read()
        if not raw:
            return
        title = f"briar: {company or '?'} / {task or '?'} failed"
        body = f"{reason}\n\n{detail[:1500]}"
        for kind in (k.strip() for k in raw.split(",") if k.strip()):
            try:
                sink = make_sink(kind, company=company)
            except Exception:  # noqa: BLE001
                log.exception("notify-failure: unknown sink kind=%s — skipping", kind)
                continue
            if not sink.is_available():
                log.debug("notify-failure: sink=%s not available (no creds) — skipping", kind)
                continue
            try:
                ok = sink.send(title=title, body=body)
                log.info("notify-failure: sink=%s ok=%s", kind, ok)
            except Exception:  # noqa: BLE001
                log.exception("notify-failure: sink=%s raised", kind)

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
    def _collect_sections(
        extract_list: List[ExtractEntry],
        registry: Any,
        *,
        company: str = "",
    ) -> List[ExtractedSection]:
        sections: List[ExtractedSection] = []
        for entry in extract_list:
            with log_context(extractor=entry.name):
                extractor = registry.get(entry.name)
                if extractor is None:
                    log.warning("extractor-skip: not found in registry (known: %s)", sorted(registry.keys()) if hasattr(registry, "keys") else "?")
                    continue
                seed = argparse.ArgumentParser(add_help=False)
                extractor.add_arguments(seed)
                ns = seed.parse_args([])
                # Inject the current company so RepoBackedExtractor._provider
                # can resolve per-tenant creds (BITBUCKET_<COMPANY>_*, etc.).
                # Overridable via the YAML args dict — explicit beats implicit.
                if company and "company" not in entry.args:
                    setattr(ns, "company", company)
                for k, v in entry.args.items():
                    setattr(ns, k, v)
                log.debug("extractor-args: %s", _summarise_args(entry.args))
                if not extractor.is_available(ns):
                    log.warning("extractor-skip: is_available() returned False — likely missing credentials")
                    continue
                started = time.perf_counter()
                try:
                    section = extractor.extract(ns)
                except Exception:  # noqa: BLE001
                    log.exception("extractor-failed: %s.extract raised", entry.name)
                    continue
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                if section.is_empty:
                    log.info("extractor-empty: returned EMPTY_SECTION (elapsed_ms=%d)", elapsed_ms)
                    continue
                log.info(
                    "extractor-ok: title=%r subsections=%d body_bytes=%d elapsed_ms=%d",
                    section.title,
                    len(section.subsections),
                    len(section.body or ""),
                    elapsed_ms,
                )
                sections.append(section)
        return sections


def _summarise_args(args: Dict[str, Any]) -> str:
    """Render extractor args for the log without dumping huge lists.
    Lists get truncated to 3 items + a `(+N more)` suffix; scalars
    pass through verbatim."""
    parts: List[str] = []
    for key, value in args.items():
        if isinstance(value, list):
            head = value[:3]
            suffix = f" (+{len(value) - 3} more)" if len(value) > 3 else ""
            parts.append(f"{key}={head}{suffix}")
            continue
        parts.append(f"{key}={value!r}")
    return ", ".join(parts) if parts else "(no args)"


# Back-compat aliases.
load_runbook_file = RunbookLoader.load
extract_runbook = RunbookExtractor.extract

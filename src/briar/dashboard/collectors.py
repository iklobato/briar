"""Collectors — read-only data probes used by the dashboard.

Each `Collector` subclass owns one concern (cron, companies, knowledge,
log, system, registries). `CollectorRegistry.collect_all()` walks them
in order and returns the merged dict the Jinja template consumes."""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Type

import yaml


class Collector(ABC):
    """Strategy contract — one section of the dashboard."""

    name: ClassVar[str] = ""

    @abstractmethod
    def collect(self) -> Dict[str, Any]:
        """Return the section's payload as a JSON-friendly dict."""


# ---------------------------------------------------------------------------
# Concrete collectors
# ---------------------------------------------------------------------------


class CompaniesCollector(Collector):
    """Walk the runbook YAML directory and describe each company."""

    name = "companies"

    def __init__(self, examples_dir: Path) -> None:
        self._dir = examples_dir

    def collect(self) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text()) or {}
            except yaml.YAMLError as exc:
                rows.append({
                    "file": path.name, "error": str(exc),
                    "companies": [],
                })
                continue
            companies = (data.get("companies") or {})
            for company_name, company in companies.items():
                rows.append({
                    "file": path.name,
                    "company": company_name,
                    "profile": company.get("profile") or "(none)",
                    "extractors": [
                        e.get("name", "?") for e in (company.get("extract") or [])
                    ],
                    "knowledge_file":
                        ((company.get("knowledge") or {}).get("name"))
                        or company.get("knowledge_file")
                        or f"./knowledge/{company_name}.md",
                    "runbooks_declared":
                        len(company.get("runbooks") or []),
                })
        return {"rows": rows, "count": len(rows)}


class KnowledgeCollector(Collector):
    """List every extracted knowledge file with size + mtime."""

    name = "knowledge"

    def __init__(self, root: Path) -> None:
        self._root = root

    def collect(self) -> Dict[str, Any]:
        if not self._root.exists():
            return {"rows": [], "root": str(self._root), "missing": True}
        rows: List[Dict[str, Any]] = []
        for path in sorted(self._root.rglob("*.md")):
            stat = path.stat()
            rows.append({
                "path": str(path.relative_to(self._root)),
                "byte_count": stat.st_size,
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc,
                ).strftime("%Y-%m-%d %H:%M UTC"),
                "head": _safe_head(path, lines=3),
            })
        return {"rows": rows, "root": str(self._root)}


class CronCollector(Collector):
    """Read /etc/cron.d/briar-scheduler (or fallback path)."""

    name = "cron"

    def __init__(self, cron_path: Path) -> None:
        self._path = cron_path

    def collect(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"present": False, "path": str(self._path)}
        text = self._path.read_text()
        entries: List[Dict[str, str]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" in stripped.split(" ", 1)[0]:
                continue
            parts = stripped.split(None, 6)
            if len(parts) < 7:
                continue
            schedule = " ".join(parts[:5])
            user = parts[5]
            command = parts[6]
            entries.append({
                "schedule": schedule, "user": user, "command": command,
            })
        return {
            "present": True,
            "path": str(self._path),
            "entries": entries,
            "raw": text,
        }


class ScheduleLogCollector(Collector):
    """Tail the scheduler log."""

    name = "scheduler_log"

    def __init__(self, log_path: Path, *, tail_lines: int = 80) -> None:
        self._path = log_path
        self._tail = tail_lines

    def collect(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"present": False, "path": str(self._path), "lines": []}
        with self._path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 32_000))
            chunk = fh.read().decode("utf-8", errors="replace")
        lines = chunk.splitlines()[-self._tail:]
        last_cycle = ""
        for line in reversed(lines):
            if "cycle done" in line:
                last_cycle = line
                break
        return {
            "present": True,
            "path": str(self._path),
            "lines": lines,
            "last_cycle": last_cycle,
            "byte_count": size,
        }


class ExtractorsCollector(Collector):
    """Describe the EXTRACTORS registry — the strategies available."""

    name = "extractors"

    def collect(self) -> Dict[str, Any]:
        from briar.extract import EXTRACTORS
        rows = [
            {
                "name": ext.name,
                "description": ext.description,
                "requires_github": ext.requires_github,
                "requires_aws": ext.requires_aws,
            }
            for ext in EXTRACTORS.values()
        ]
        return {"rows": rows, "count": len(rows)}


class SourcesCollector(Collector):
    """Source-template registry — what kinds the scaffold supports."""

    name = "source_templates"

    def collect(self) -> Dict[str, Any]:
        from briar.iac.scaffold.sources import SOURCE_TEMPLATES
        rows = [
            {
                "kind": tmpl.kind,
                "family": tmpl.family or "(none)",
            }
            for tmpl in SOURCE_TEMPLATES.values()
        ]
        return {"rows": rows, "count": len(rows)}


class TriggersCollector(Collector):
    """Trigger-template registry."""

    name = "trigger_templates"

    def collect(self) -> Dict[str, Any]:
        from briar.iac.scaffold.triggers import TRIGGER_TEMPLATES
        rows = [
            {"kind": tmpl.kind, "description": tmpl.description}
            for tmpl in TRIGGER_TEMPLATES.values()
        ]
        return {"rows": rows, "count": len(rows)}


class StorageCollector(Collector):
    """Knowledge-store registry."""

    name = "storage_backends"

    def collect(self) -> Dict[str, Any]:
        from briar.storage import KnowledgeStoreRegistry
        return {"rows": [{"name": n} for n in KnowledgeStoreRegistry.names()]}


class SystemCollector(Collector):
    """Uptime + disk usage."""

    name = "system"

    def __init__(self, disk_path: Path) -> None:
        self._disk_path = disk_path

    def collect(self) -> Dict[str, Any]:
        usage = shutil.disk_usage(self._disk_path)
        return {
            "hostname": os.uname().nodename,
            "now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "uptime": _read_uptime(),
            "disk_total_gb": round(usage.total / 1_073_741_824, 2),
            "disk_used_gb": round((usage.total - usage.free) / 1_073_741_824, 2),
            "disk_free_gb": round(usage.free / 1_073_741_824, 2),
            "disk_used_pct": (
                round((usage.total - usage.free) / usage.total * 100, 1)
                if usage.total else 0.0
            ),
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class CollectorRegistry:
    """Owns the order and config of every collector in the dashboard."""

    @staticmethod
    def for_paths(
        *,
        examples_dir: Path,
        knowledge_dir: Path,
        cron_path: Path,
        log_path: Path,
        disk_path: Path,
    ) -> List[Collector]:
        return [
            SystemCollector(disk_path=disk_path),
            CronCollector(cron_path=cron_path),
            ScheduleLogCollector(log_path=log_path),
            CompaniesCollector(examples_dir=examples_dir),
            KnowledgeCollector(root=knowledge_dir),
            ExtractorsCollector(),
            SourcesCollector(),
            TriggersCollector(),
            StorageCollector(),
        ]

    @classmethod
    def collect_all(cls, collectors: List[Collector]) -> Dict[str, Any]:
        return {c.name: c.collect() for c in collectors}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_head(path: Path, *, lines: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head: List[str] = []
            for i, line in enumerate(fh):
                if i >= lines:
                    break
                head.append(line.rstrip("\n"))
            return "\n".join(head)
    except OSError:
        return ""


def _read_uptime() -> str:
    try:
        with open("/proc/uptime", "r") as fh:
            seconds = float(fh.read().split()[0])
    except OSError:
        return "unknown"
    days, rem = divmod(int(seconds), 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, _ = divmod(rem, 60)
    return f"{days}d {hours}h {minutes}m"

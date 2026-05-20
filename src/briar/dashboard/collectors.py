"""Collectors — read-only data probes used by the dashboard.

Each `Collector` subclass owns one concern (companies, knowledge,
log, cron, system, registries, …). `CollectorRegistry.collect_all()`
walks them in order and returns the merged dict the Jinja template
consumes."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

import yaml


class Collector(ABC):
    """Strategy contract — one section of the dashboard."""

    name: ClassVar[str] = ""

    @abstractmethod
    def collect(self) -> Dict[str, Any]:
        """Return the section's payload as a JSON-friendly dict."""


# ---------------------------------------------------------------------------
# Companies + knowledge
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
                    "file": path.name, "error": str(exc), "companies": [],
                })
                continue
            for company_name, company in (data.get("companies") or {}).items():
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
                })
        return {"rows": rows, "count": len(rows)}


class KnowledgeCollector(Collector):
    """List every extracted knowledge file with size + mtime."""

    name = "knowledge"

    def __init__(self, root: Path) -> None:
        self._root = root

    def collect(self) -> Dict[str, Any]:
        if not self._root.exists():
            return {
                "rows": [], "root": str(self._root), "missing": True,
                "chart": {"labels": [], "values": []},
            }
        rows: List[Dict[str, Any]] = []
        for path in sorted(self._root.rglob("*.md")):
            stat = path.stat()
            head = _safe_head(path, lines=3)
            section_count = _count_sections(path)
            rows.append({
                "path": str(path.relative_to(self._root)),
                "byte_count": stat.st_size,
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc,
                ).strftime("%Y-%m-%d %H:%M UTC"),
                "head": head,
                "sections": section_count,
            })
        return {
            "rows": rows, "root": str(self._root),
            "chart": {
                "labels": [r["path"] for r in rows],
                "values": [r["byte_count"] for r in rows],
            },
        }


class KnowledgeAggregatesCollector(Collector):
    """Mine the knowledge .md files for cross-company numbers."""

    name = "knowledge_aggregates"

    _MERGED_PR_RE = re.compile(r"merged PR sample:\s*\*\*(\d+)\*\*")
    _OPEN_PR_RE   = re.compile(r"—\s*(\d+)\s+open PR\(s\)")
    _RDS_RE       = re.compile(r"RDS\s+\((\d+)\s+instance")
    _SQS_RE       = re.compile(r"SQS\s+\((\d+)\s+queue")
    _LOG_RE       = re.compile(r"CloudWatch Logs.*?of\s+(\d+)\)?")

    def __init__(self, knowledge_root: Path) -> None:
        self._root = knowledge_root

    def collect(self) -> Dict[str, Any]:
        if not self._root.exists():
            return {
                "files": 0, "total_bytes": 0,
                "merged_prs": 0, "open_prs": 0,
                "rds_instances": 0, "sqs_queues": 0, "log_groups": 0,
            }
        total_files = 0
        total_bytes = 0
        merged_prs = 0
        open_prs = 0
        rds = 0
        sqs = 0
        log_groups = 0
        for path in self._root.rglob("*.md"):
            total_files += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            total_bytes += len(text)
            for m in self._MERGED_PR_RE.finditer(text):
                merged_prs += int(m.group(1))
            for m in self._OPEN_PR_RE.finditer(text):
                open_prs += int(m.group(1))
            for m in self._RDS_RE.finditer(text):
                rds += int(m.group(1))
            for m in self._SQS_RE.finditer(text):
                sqs += int(m.group(1))
            for m in self._LOG_RE.finditer(text):
                log_groups += int(m.group(1))
        return {
            "files": total_files, "total_bytes": total_bytes,
            "merged_prs": merged_prs, "open_prs": open_prs,
            "rds_instances": rds, "sqs_queues": sqs, "log_groups": log_groups,
        }


# ---------------------------------------------------------------------------
# Schedulers + cycle outcomes + cron health
# ---------------------------------------------------------------------------


class CronCollector(Collector):
    """Read /etc/cron.d/briar-scheduler entries."""

    name = "cron"

    def __init__(self, cron_path: Path) -> None:
        self._path = cron_path

    def collect(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"present": False, "path": str(self._path), "entries": []}
        text = self._path.read_text()
        entries: List[Dict[str, Any]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped.split(" ", 1)[0]:
                continue
            parts = stripped.split(None, 6)
            if len(parts) < 7:
                continue
            schedule = " ".join(parts[:5])
            user = parts[5]
            command = parts[6]
            entries.append({
                "schedule": schedule, "user": user, "command": command,
                "next_fire": _next_cron_fire(schedule),
            })
        return {"present": True, "path": str(self._path), "entries": entries}


class CronHealthCollector(Collector):
    """systemctl is-active cron + last fire from journal (best-effort)."""

    name = "cron_health"

    def collect(self) -> Dict[str, Any]:
        active = _run(["systemctl", "is-active", "cron"], timeout=3) == "active"
        enabled = _run(["systemctl", "is-enabled", "cron"], timeout=3) == "enabled"
        return {"daemon_active": active, "daemon_enabled": enabled}


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
            fh.seek(max(0, size - 64_000))
            chunk = fh.read().decode("utf-8", errors="replace")
        lines = chunk.splitlines()[-self._tail:]
        last_cycle = ""
        for line in reversed(lines):
            if "cycle done" in line:
                last_cycle = line
                break
        return {
            "present": True, "path": str(self._path),
            "lines": lines, "last_cycle": last_cycle, "byte_count": size,
        }


class CycleOutcomeCollector(Collector):
    """Parse the scheduler log into per-cycle, per-company status rows."""

    name = "cycle_outcomes"

    _EXTRACT_RE = re.compile(r"\[(?P<ts>[^\]]+)\]\s+extract\s+\S*?(?P<yaml>[\w\-]+)\.yaml")
    _WROTE_RE   = re.compile(r"(?P<company>\w+)\s+wrote\s+(?P<bytes>\d+)\s+bytes")
    _FAILED_RE  = re.compile(r"FAILED\s+(?P<yaml>[\w\-]+\.yaml):\s*(?P<msg>.+)")
    _CYCLE_RE   = re.compile(r"\[(?P<ts>[^\]]+)\]\s+cycle done")

    def __init__(self, log_path: Path) -> None:
        self._path = log_path

    def collect(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"cycles": [], "by_company": {}}
        with self._path.open("rb") as fh:
            text = fh.read().decode("utf-8", errors="replace")
        cycles: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        for line in text.splitlines():
            ex = self._EXTRACT_RE.search(line)
            if ex:
                if current is None:
                    current = {"started_at": ex.group("ts"), "rows": []}
                continue
            wrote = self._WROTE_RE.search(line)
            if wrote and current is not None:
                current["rows"].append({
                    "company": wrote.group("company"),
                    "status": "ok",
                    "bytes": int(wrote.group("bytes")),
                })
                continue
            failed = self._FAILED_RE.search(line)
            if failed and current is not None:
                yaml_name = failed.group("yaml").replace(".yaml", "")
                current["rows"].append({
                    "company": yaml_name,
                    "status": "failed",
                    "bytes": 0,
                    "error": failed.group("msg"),
                })
                continue
            done = self._CYCLE_RE.search(line)
            if done and current is not None:
                current["finished_at"] = done.group("ts")
                cycles.append(current)
                current = None
        recent = cycles[-10:]
        by_company: Dict[str, List[Dict[str, Any]]] = {}
        for cycle in recent:
            for row in cycle["rows"]:
                by_company.setdefault(row["company"], []).append({
                    "at": cycle.get("finished_at", cycle.get("started_at", "")),
                    "status": row["status"],
                    "bytes": row["bytes"],
                })
        return {
            "cycles": recent, "by_company": by_company,
            "total_cycles": len(cycles),
            "chart": {
                "labels": [c.get("finished_at", c.get("started_at", "?"))[-8:-3]
                           for c in recent],
                "companies": sorted(by_company.keys()),
                "series": [
                    {
                        "company": company,
                        "values": [
                            next(
                                (r["bytes"] for r in cycle["rows"]
                                 if r["company"] == company),
                                0,
                            )
                            for cycle in recent
                        ],
                    }
                    for company in sorted(by_company.keys())
                ],
            },
        }


# ---------------------------------------------------------------------------
# Registries (the Strategy plugin families)
# ---------------------------------------------------------------------------


class ExtractorsCollector(Collector):
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
    name = "source_templates"

    def collect(self) -> Dict[str, Any]:
        from briar.iac.scaffold.sources import SOURCE_TEMPLATES
        rows = [
            {"kind": tmpl.kind, "family": tmpl.family or "(none)"}
            for tmpl in SOURCE_TEMPLATES.values()
        ]
        return {"rows": rows, "count": len(rows)}


class TriggersCollector(Collector):
    name = "trigger_templates"

    def collect(self) -> Dict[str, Any]:
        from briar.iac.scaffold.triggers import TRIGGER_TEMPLATES
        rows = [
            {"kind": tmpl.kind, "description": tmpl.description}
            for tmpl in TRIGGER_TEMPLATES.values()
        ]
        return {"rows": rows, "count": len(rows)}


class StorageCollector(Collector):
    name = "storage_backends"

    def collect(self) -> Dict[str, Any]:
        from briar.storage import KnowledgeStoreRegistry
        return {"rows": [{"name": n} for n in KnowledgeStoreRegistry.names()]}


class AwsServicesCollector(Collector):
    name = "aws_services"

    def collect(self) -> Dict[str, Any]:
        from briar.extract.aws_services import AWS_SERVICE_GATHERERS
        return {
            "rows": [{"name": n} for n in sorted(AWS_SERVICE_GATHERERS)],
        }


class LanguageDetectorsCollector(Collector):
    name = "language_detectors"

    def collect(self) -> Dict[str, Any]:
        from briar.extract.language_detectors import LANGUAGE_DETECTORS
        return {
            "rows": [
                {"name": d.name, "manifest": d.manifest}
                for d in LANGUAGE_DETECTORS
            ],
        }


class WorkflowShapesCollector(Collector):
    name = "workflow_shapes"

    def collect(self) -> Dict[str, Any]:
        from briar.iac.scaffold.shapes import WORKFLOW_SHAPES
        return {
            "rows": [
                {"name": s.name, "description": s.description}
                for s in WORKFLOW_SHAPES.values()
            ],
        }


class ArchetypesCollector(Collector):
    name = "archetypes"

    def collect(self) -> Dict[str, Any]:
        from briar.iac.scaffold.archetypes import ARCHETYPES
        return {
            "rows": [
                {
                    "name": a.name,
                    "description": a.description,
                    "role": a.role,
                    "tool_filter": list(a.tool_filter) or ["(no filter)"],
                }
                for a in ARCHETYPES.values()
            ],
        }


class CommandsCollector(Collector):
    name = "commands"

    def collect(self) -> Dict[str, Any]:
        from briar.commands import CommandRegistry
        return {
            "rows": [
                {"name": cmd.name, "help": cmd.help}
                for cmd in CommandRegistry.build().values()
            ],
        }


# ---------------------------------------------------------------------------
# Deploy / git / secrets / connectivity / system health / self
# ---------------------------------------------------------------------------


class GitDeployCollector(Collector):
    """git log -1 + branch + remote URL (sanitised — strips token)."""

    name = "deploy"

    def __init__(self, repo_path: Path) -> None:
        self._path = repo_path

    def collect(self) -> Dict[str, Any]:
        if not (self._path / ".git").exists():
            return {"present": False, "path": str(self._path)}
        opts = ["-C", str(self._path)]
        sha = _run(["git", *opts, "rev-parse", "HEAD"], timeout=3)
        branch = _run(["git", *opts, "rev-parse", "--abbrev-ref", "HEAD"], timeout=3)
        subject = _run(["git", *opts, "log", "-1", "--format=%s"], timeout=3)
        author = _run(["git", *opts, "log", "-1", "--format=%an"], timeout=3)
        committed = _run(["git", *opts, "log", "-1", "--format=%cI"], timeout=3)
        remote = _run(["git", *opts, "remote", "get-url", "origin"], timeout=3)
        remote_clean = re.sub(r"://[^@]+@", "://", remote)
        return {
            "present": True,
            "sha": sha, "short_sha": sha[:7] if sha else "",
            "branch": branch, "subject": subject, "author": author,
            "committed": committed, "remote": remote_clean,
        }


class SecretsCollector(Collector):
    """Read /etc/briar/secrets.env — names and lengths ONLY, never values."""

    name = "secrets"

    def __init__(self, secrets_path: Path) -> None:
        self._path = secrets_path

    def collect(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"present": False, "path": str(self._path), "rows": []}
        rows: List[Dict[str, Any]] = []
        for line in self._path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            rows.append({
                "name": key, "length": len(value), "set": bool(value),
            })
        stat = self._path.stat()
        return {
            "present": True, "path": str(self._path),
            "rows": rows,
            "byte_count": stat.st_size,
            "modified": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc,
            ).strftime("%Y-%m-%d %H:%M UTC"),
        }


class ConnectivityCollector(Collector):
    """TCP-connect probes against the upstreams the extractors need."""

    name = "connectivity"

    DEFAULT_TARGETS: ClassVar[Tuple[Tuple[str, int], ...]] = (
        ("api.github.com", 443),
        ("github.com", 443),
        ("sts.amazonaws.com", 443),
    )

    def __init__(
        self,
        *,
        targets: Optional[Tuple[Tuple[str, int], ...]] = None,
        timeout: float = 2.0,
    ) -> None:
        self._targets = targets or self.DEFAULT_TARGETS
        self._timeout = timeout

    def collect(self) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for host, port in self._targets:
            started = time.monotonic()
            try:
                with socket.create_connection((host, port), timeout=self._timeout):
                    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                    rows.append({
                        "target": f"{host}:{port}",
                        "reachable": True, "latency_ms": elapsed_ms,
                        "error": "",
                    })
            except (OSError, socket.timeout) as exc:
                elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                rows.append({
                    "target": f"{host}:{port}",
                    "reachable": False, "latency_ms": elapsed_ms,
                    "error": str(exc),
                })
        return {"rows": rows}


class SystemCollector(Collector):
    """Uptime, disk, load avg, memory."""

    name = "system"

    def __init__(self, disk_path: Path) -> None:
        self._disk_path = disk_path

    def collect(self) -> Dict[str, Any]:
        usage = shutil.disk_usage(self._disk_path)
        load_1, load_5, load_15 = _read_loadavg()
        mem_total, mem_free, mem_avail = _read_meminfo()
        mem_used = mem_total - mem_avail
        mem_pct = round(mem_used / mem_total * 100, 1) if mem_total else 0.0
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
            "load_1": load_1, "load_5": load_5, "load_15": load_15,
            "mem_total_mb": round(mem_total / 1_048_576, 1),
            "mem_used_mb": round(mem_used / 1_048_576, 1),
            "mem_free_mb": round(mem_free / 1_048_576, 1),
            "mem_used_pct": mem_pct,
            "cpu_count": os.cpu_count() or 1,
        }


class DiskByDirCollector(Collector):
    """Recursive size of each named directory."""

    name = "disk_by_dir"

    def __init__(self, paths: List[Path]) -> None:
        self._paths = paths

    def collect(self) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for path in self._paths:
            size = _du(path)
            rows.append({
                "path": str(path),
                "bytes": size,
                "human": _human_bytes(size),
                "present": path.exists(),
            })
        return {"rows": rows}


class DashboardProcessCollector(Collector):
    """Self-observability: pid, uptime, requests served."""

    name = "dashboard_process"

    def __init__(
        self,
        *,
        started_at: float,
        request_count_fn: Callable[[], int],
        last_render_ms_fn: Callable[[], float],
    ) -> None:
        self._started_at = started_at
        self._req = request_count_fn
        self._render = last_render_ms_fn

    def collect(self) -> Dict[str, Any]:
        uptime_s = int(time.time() - self._started_at)
        days, rem = divmod(uptime_s, 86_400)
        hours, rem = divmod(rem, 3_600)
        minutes, seconds = divmod(rem, 60)
        return {
            "pid": os.getpid(),
            "uptime": f"{days}d {hours}h {minutes}m {seconds}s",
            "request_count": self._req(),
            "last_render_ms": round(self._render(), 1),
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
        repo_path: Path,
        secrets_path: Path,
        du_paths: List[Path],
        process_started_at: float,
        request_count_fn: Callable[[], int],
        last_render_ms_fn: Callable[[], float],
    ) -> List[Collector]:
        return [
            SystemCollector(disk_path=disk_path),
            GitDeployCollector(repo_path=repo_path),
            CronCollector(cron_path=cron_path),
            CronHealthCollector(),
            ScheduleLogCollector(log_path=log_path),
            CycleOutcomeCollector(log_path=log_path),
            ConnectivityCollector(),
            SecretsCollector(secrets_path=secrets_path),
            CompaniesCollector(examples_dir=examples_dir),
            KnowledgeCollector(root=knowledge_dir),
            KnowledgeAggregatesCollector(knowledge_root=knowledge_dir),
            ExtractorsCollector(),
            SourcesCollector(),
            TriggersCollector(),
            StorageCollector(),
            AwsServicesCollector(),
            LanguageDetectorsCollector(),
            WorkflowShapesCollector(),
            ArchetypesCollector(),
            CommandsCollector(),
            DiskByDirCollector(paths=du_paths),
            DashboardProcessCollector(
                started_at=process_started_at,
                request_count_fn=request_count_fn,
                last_render_ms_fn=last_render_ms_fn,
            ),
        ]

    @classmethod
    def collect_all(cls, collectors: List[Collector]) -> Dict[str, Any]:
        return {c.name: c.collect() for c in collectors}


# ---------------------------------------------------------------------------
# Module-private helpers
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


def _count_sections(path: Path) -> int:
    try:
        return sum(
            1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.startswith("## ")
        )
    except OSError:
        return 0


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


def _read_loadavg() -> Tuple[float, float, float]:
    try:
        with open("/proc/loadavg", "r") as fh:
            parts = fh.read().split()
            return float(parts[0]), float(parts[1]), float(parts[2])
    except (OSError, ValueError, IndexError):
        return 0.0, 0.0, 0.0


def _read_meminfo() -> Tuple[int, int, int]:
    """Returns (total, free, available) in bytes from /proc/meminfo."""
    try:
        with open("/proc/meminfo", "r") as fh:
            data: Dict[str, int] = {}
            for line in fh:
                key, _, rest = line.partition(":")
                key = key.strip()
                rest = rest.strip().split()
                if rest:
                    data[key] = int(rest[0]) * 1024
        return (
            data.get("MemTotal", 0),
            data.get("MemFree", 0),
            data.get("MemAvailable", data.get("MemFree", 0)),
        )
    except (OSError, ValueError):
        return 0, 0, 0


def _du(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                continue
    return total


def _human_bytes(n: int) -> str:
    suffixes = ("B", "KB", "MB", "GB", "TB")
    val = float(n)
    for suffix in suffixes:
        if val < 1024 or suffix == suffixes[-1]:
            return f"{val:.1f} {suffix}"
        val /= 1024
    return f"{val:.1f} TB"


def _run(cmd: List[str], *, timeout: float) -> str:
    """Run a command and return stdout (stripped). Empty on any failure."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _next_cron_fire(schedule: str) -> str:
    """Approximate the next fire time for a 5-field cron expression.

    Supports `*`, single ints, and ranges like `0-5`. Lists (`,`) and
    step values (`*/5`) are honoured at the resolution we care about
    (`0 17 * * *` and `0 * * * *` style). Returns ISO timestamp."""
    fields = schedule.split()
    if len(fields) != 5:
        return ""
    minute_set = _cron_field(fields[0], 0, 59)
    hour_set = _cron_field(fields[1], 0, 23)
    dom_set = _cron_field(fields[2], 1, 31)
    month_set = _cron_field(fields[3], 1, 12)
    dow_set = _cron_field(fields[4], 0, 6)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candidate = now + timedelta(minutes=1)
    for _ in range(60 * 24 * 366):
        if (
            candidate.minute in minute_set
            and candidate.hour in hour_set
            and candidate.day in dom_set
            and candidate.month in month_set
            and (candidate.weekday() + 1) % 7 in dow_set
        ):
            return candidate.strftime("%Y-%m-%d %H:%M UTC")
        candidate += timedelta(minutes=1)
    return ""


def _cron_field(spec: str, low: int, high: int) -> set:
    """Tiny cron-field parser. `*`, `n`, `n-m`, `a,b,c`, `*/k` supported."""
    out: set = set()
    for part in spec.split(","):
        step = 1
        body = part
        if "/" in part:
            body, _, step_str = part.partition("/")
            step = int(step_str)
        if body == "*":
            out.update(range(low, high + 1, step))
            continue
        if "-" in body:
            start_str, _, end_str = body.partition("-")
            out.update(range(int(start_str), int(end_str) + 1, step))
            continue
        out.add(int(body))
    return out

"""Collectors — read-only data probes used by the dashboard.

Each `Collector` subclass owns one concern (companies, knowledge,
log, cron, system, registries, …). `CollectorRegistry.collect_all()`
walks them in order and returns the merged dict the Jinja template
consumes."""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Tuple

log = logging.getLogger(__name__)


def _tail_bytes(path: Path, cap: int) -> str:
    """Read the last `cap` bytes of `path`, UTF-8 decoded (errors
    replaced). Empty string when the file is absent. One edge for the
    seek-to-tail dance the log collectors all need."""
    if not path.exists():
        return ""
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - cap))
        return fh.read().decode("utf-8", errors="replace")


def _parse_log_ts(ts: str) -> Any:
    """Parse a scheduler-log ISO timestamp (UTC) → aware datetime, or None.
    Shared by the log-scanning collectors."""
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class Collector(ABC):
    """Strategy contract — one section of the dashboard."""

    name: ClassVar[str] = ""

    @abstractmethod
    def collect(self) -> Dict[str, Any]:
        """Return the section's payload as a JSON-friendly dict."""


# ---------------------------------------------------------------------------
# Scheduler observability
# ---------------------------------------------------------------------------


class SchedulesCollector(Collector):
    """Per-(company, task) schedules read from the YAMLs.

    Each row uses `EveryParser` + a private `schedule.Scheduler` so the
    library computes the next-fire time without sharing global state."""

    name = "schedules"

    def __init__(self, examples_dir: Path) -> None:
        self._dir = examples_dir

    def collect(self) -> Dict[str, Any]:
        import schedule as schedule_mod

        from briar.iac.runbook import EveryParser, RunbookSchedules, load_runbook_file

        # Private scheduler so .do() doesn't pollute the global registry.
        local = schedule_mod.Scheduler()
        rows: List[Dict[str, Any]] = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                runbook = load_runbook_file(path)
            except Exception as exc:  # noqa: BLE001
                log.exception("schedules-collector: failed to load %s", path.name)
                rows.append(
                    {
                        "file": path.name,
                        "company": "(parse error)",
                        "task": "-",
                        "every": str(exc),
                        "next_fire": "",
                        "extractors": [],
                        "ok": False,
                    }
                )
                continue
            for company_name, company in runbook.companies.items():
                for entry in RunbookSchedules.for_company(company):
                    rows.append(
                        self._row(
                            path.name,
                            company_name,
                            entry,
                            EveryParser,
                            local,
                        )
                    )
        return {"rows": rows, "count": len(rows)}

    @staticmethod
    def _row(file_name, company_name, entry, parser, scheduler):
        job = parser.parse(entry.every, scheduler=scheduler)
        job.do(_noop)  # binds + sets job.next_run
        next_fire = job.next_run.strftime("%Y-%m-%d %H:%M UTC") if job.next_run else ""
        return {
            "file": file_name,
            "company": company_name,
            "task": entry.task,
            "every": entry.every,
            "next_fire": next_fire,
            "extractors": [e.name for e in entry.extract],
            "ok": True,
        }


def _noop() -> None:
    pass


class GhStatsCollector(Collector):
    """GitHub API quota + ETag-cache hit count, mined from the
    `briar.extract._gh` log lines.

    Every `gh GET ok` log carries `ratelimit_remaining=N` so we can
    track quota burn over time without making an extra `/rate_limit`
    call. Every `gh GET 304-cache-hit` is a confirmed ETag-cache hit
    (i.e. a request that did NOT count against quota per GitHub's
    docs). The two together visualise the smart-scheduler payoff."""

    name = "gh_stats"

    _OK_RE = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\s+"
        r"\[(?:INFO|DEBUG)[^\]]*\]\s+briar\.extract\._gh:.*?"
        r"gh\s+(?:GET|PAGINATED)\s+ok\s+path=(?P<path>\S+).*?"
        r"ratelimit_remaining=(?P<rem>\d+)"
    )
    _CACHE_HIT_RE = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\s+"
        r"\[(?:INFO|DEBUG)[^\]]*\]\s+briar\.extract\._gh:.*?"
        r"gh\s+GET\s+304-cache-hit\s+path=(?P<path>\S+).*?"
        r"ratelimit_remaining=(?P<rem>\S+)"
    )
    _ERR_RE = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\s+" r"\[ERROR[^\]]*\]\s+briar\.extract\._gh:.*?gh\s+GET\s+non-2xx\s+path=(?P<path>\S+)"
    )

    def __init__(self, log_path: Path) -> None:
        self._log = log_path

    def collect(self) -> Dict[str, Any]:
        from datetime import timedelta

        if not self._log.exists():
            return self._empty()
        text = _tail_bytes(self._log, 512_000)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        window_start = now - timedelta(hours=24)
        ok_count = 0
        cache_hit_count = 0
        err_count = 0
        samples: List[Dict[str, Any]] = []
        last_remaining = -1
        min_remaining = -1
        recent_paths: List[Dict[str, Any]] = []  # last 12 cache-hits

        for line in text.splitlines():
            ok_m = self._OK_RE.search(line)
            if ok_m:
                when = _parse_log_ts(ok_m.group("ts"))
                if when is None or when < window_start:
                    continue
                remaining = int(ok_m.group("rem"))
                ok_count += 1
                last_remaining = remaining
                if min_remaining < 0 or remaining < min_remaining:
                    min_remaining = remaining
                samples.append({"at": when.strftime("%H:%M"), "remaining": remaining})
                continue
            hit_m = self._CACHE_HIT_RE.search(line)
            if hit_m:
                when = _parse_log_ts(hit_m.group("ts"))
                if when is None or when < window_start:
                    continue
                cache_hit_count += 1
                rem_raw = hit_m.group("rem")
                if rem_raw.isdigit():
                    last_remaining = int(rem_raw)
                recent_paths.append(
                    {
                        "at": when.strftime("%H:%M"),
                        "path": hit_m.group("path")[:80],
                    }
                )
                continue
            err_m = self._ERR_RE.search(line)
            if err_m:
                when = _parse_log_ts(err_m.group("ts"))
                if when is None or when < window_start:
                    continue
                err_count += 1

        total = ok_count + cache_hit_count + err_count
        hit_rate = round(100 * cache_hit_count / max(1, total), 1)
        # Keep the sparkline tight — 60 samples is plenty at one-per-minute.
        samples = samples[-60:]
        recent_paths = recent_paths[-12:]
        return {
            "ok_count": ok_count,
            "cache_hit_count": cache_hit_count,
            "err_count": err_count,
            "total": total,
            "hit_rate_pct": hit_rate,
            "last_remaining": last_remaining,
            "min_remaining": min_remaining,
            "samples": samples,
            "recent_cache_hits": list(reversed(recent_paths)),
            "chart": {
                "labels": [s["at"] for s in samples],
                "values": [s["remaining"] for s in samples],
            },
        }

    @staticmethod
    def _empty() -> Dict[str, Any]:
        return {
            "ok_count": 0,
            "cache_hit_count": 0,
            "err_count": 0,
            "total": 0,
            "hit_rate_pct": 0.0,
            "last_remaining": -1,
            "min_remaining": -1,
            "samples": [],
            "recent_cache_hits": [],
            "chart": {"labels": [], "values": []},
        }


class SchedulerProcessCollector(Collector):
    """Is `briar runbook serve` running?"""

    name = "scheduler_process"

    def collect(self) -> Dict[str, Any]:
        out = _run(["pgrep", "-fa", "briar runbook serve"], timeout=2)
        if not out:
            return {"present": False, "pid": 0, "command": ""}
        first_line = out.splitlines()[0]
        pid_str, _, command = first_line.partition(" ")
        return {
            "present": True,
            "pid": int(pid_str) if pid_str.isdigit() else 0,
            "command": command,
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
        chunk = _tail_bytes(self._path, 64_000)
        lines = chunk.splitlines()[-self._tail :]
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
            "byte_count": self._path.stat().st_size,
        }


class CycleOutcomeCollector(Collector):
    """Parse the scheduler log into per-cycle, per-company status rows."""

    name = "cycle_outcomes"

    _EXTRACT_RE = re.compile(r"\[(?P<ts>[^\]]+)\]\s+extract\s+\S*?(?P<yaml>[\w\-]+)\.yaml")
    _WROTE_RE = re.compile(r"(?P<company>\w+)\s+wrote\s+(?P<bytes>\d+)\s+bytes")
    _FAILED_RE = re.compile(r"FAILED\s+(?P<yaml>[\w\-]+\.yaml):\s*(?P<msg>.+)")
    _CYCLE_RE = re.compile(r"\[(?P<ts>[^\]]+)\]\s+cycle done")

    def __init__(self, log_path: Path) -> None:
        self._path = log_path

    def collect(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"cycles": [], "by_company": {}}
        # Tail rather than slurp the whole log — only cycles[-10:] survive
        # below, so a bounded window is enough and keeps render O(1) in log size.
        text = _tail_bytes(self._path, 512_000)
        cycles: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}
        for line in text.splitlines():
            ex = self._EXTRACT_RE.search(line)
            if ex:
                if not current:
                    current = {"started_at": ex.group("ts"), "rows": []}
                continue
            wrote = self._WROTE_RE.search(line)
            if wrote and current:
                current["rows"].append(
                    {
                        "company": wrote.group("company"),
                        "status": "ok",
                        "bytes": int(wrote.group("bytes")),
                    }
                )
                continue
            failed = self._FAILED_RE.search(line)
            if failed and current:
                yaml_name = failed.group("yaml").replace(".yaml", "")
                current["rows"].append(
                    {
                        "company": yaml_name,
                        "status": "failed",
                        "bytes": 0,
                        "error": failed.group("msg"),
                    }
                )
                continue
            done = self._CYCLE_RE.search(line)
            if done and current:
                current["finished_at"] = done.group("ts")
                cycles.append(current)
                current = {}
        recent = cycles[-10:]
        by_company: Dict[str, List[Dict[str, Any]]] = {}
        for cycle in recent:
            for row in cycle["rows"]:
                by_company.setdefault(row["company"], []).append(
                    {
                        "at": cycle.get("finished_at", cycle.get("started_at", "")),
                        "status": row["status"],
                        "bytes": row["bytes"],
                    }
                )
        return {
            "cycles": recent,
            "by_company": by_company,
            "total_cycles": len(cycles),
            "chart": {
                "labels": [c.get("finished_at", c.get("started_at", "?"))[-8:-3] for c in recent],
                "companies": sorted(by_company.keys()),
                "series": [
                    {
                        "company": company,
                        "values": [
                            next(
                                (r["bytes"] for r in cycle["rows"] if r["company"] == company),
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
# Deploy + host health
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
            "sha": sha,
            "short_sha": sha[:7] if sha else "",
            "branch": branch,
            "subject": subject,
            "author": author,
            "committed": committed,
            "remote": remote_clean,
        }


class ConnectivityCollector(Collector):
    """TCP-connect probes against the upstreams the extractors need."""

    name = "connectivity"

    DEFAULT_TARGETS: ClassVar[Tuple[Tuple[str, int], ...]] = (
        ("api.github.com", 443),
        ("github.com", 443),
        ("sts.amazonaws.com", 443),
    )

    def __init__(self, targets: Tuple[Tuple[str, int], ...] = DEFAULT_TARGETS, timeout: float = 2.0) -> None:
        self._targets = targets
        self._timeout = timeout

    def collect(self) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for host, port in self._targets:
            started = time.monotonic()
            try:
                with socket.create_connection((host, port), timeout=self._timeout):
                    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                    rows.append(
                        {
                            "target": f"{host}:{port}",
                            "reachable": True,
                            "latency_ms": elapsed_ms,
                            "error": "",
                        }
                    )
            except (OSError, socket.timeout) as exc:
                elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                rows.append(
                    {
                        "target": f"{host}:{port}",
                        "reachable": False,
                        "latency_ms": elapsed_ms,
                        "error": str(exc),
                    }
                )
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
            "disk_used_pct": (round((usage.total - usage.free) / usage.total * 100, 1) if usage.total else 0.0),
            "load_1": load_1,
            "load_5": load_5,
            "load_15": load_15,
            "mem_total_mb": round(mem_total / 1_048_576, 1),
            "mem_used_mb": round(mem_used / 1_048_576, 1),
            "mem_free_mb": round(mem_free / 1_048_576, 1),
            "mem_used_pct": mem_pct,
            "cpu_count": os.cpu_count() or 1,
        }


class DashboardProcessCollector(Collector):
    """Self-observability: pid, uptime, requests served. Takes a single
    `DashboardSelf` struct so the constructor stays narrow."""

    name = "dashboard_process"

    def __init__(self, self_: "DashboardSelf") -> None:
        self._self = self_

    def collect(self) -> Dict[str, Any]:
        uptime_s = int(time.time() - self._self.started_at)
        days, rem = divmod(uptime_s, 86_400)
        hours, rem = divmod(rem, 3_600)
        minutes, seconds = divmod(rem, 60)
        return {
            "pid": os.getpid(),
            "uptime": f"{days}d {hours}h {minutes}m {seconds}s",
            "request_count": self._self.request_count_fn(),
            "last_render_ms": round(self._self.last_render_ms_fn(), 1),
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class DashboardPaths:
    """The filesystem paths the monitoring collectors read from. Bundled
    into one struct so `from_paths` stays low-arity."""

    examples_dir: Path  # runbook YAMLs — SchedulesCollector
    log_path: Path  # scheduler log — cycle/log/gh collectors
    disk_path: Path  # SystemCollector disk-usage target
    repo_path: Path  # GitDeployCollector checkout


@dataclass
class DashboardSelf:
    """Per-process counters threaded into `DashboardProcessCollector`."""

    started_at: float
    request_count_fn: Callable[[], int]
    last_render_ms_fn: Callable[[], float]


class CollectorRegistry:
    """Owns the order and config of every collector in the dashboard."""

    @staticmethod
    def from_paths(paths: DashboardPaths, dash: DashboardSelf) -> List[Collector]:
        # Monitoring order: health first, then "what's deployed", then the
        # scheduler's liveness + recent activity, connectivity, self-stats.
        return [
            SystemCollector(disk_path=paths.disk_path),
            GitDeployCollector(repo_path=paths.repo_path),
            SchedulerProcessCollector(),
            SchedulesCollector(examples_dir=paths.examples_dir),
            CycleOutcomeCollector(log_path=paths.log_path),
            ScheduleLogCollector(log_path=paths.log_path),
            GhStatsCollector(log_path=paths.log_path),
            ConnectivityCollector(),
            DashboardProcessCollector(self_=dash),
        ]

    # Back-compat alias for existing callers.
    for_paths = from_paths

    @classmethod
    def collect_all(cls, collectors: List[Collector]) -> Dict[str, Any]:
        return {c.name: c.collect() for c in collectors}


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


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
                key, _, raw_value = line.partition(":")
                parts = raw_value.strip().split()
                if parts:
                    data[key.strip()] = int(parts[0]) * 1024
        return (
            data.get("MemTotal", 0),
            data.get("MemFree", 0),
            data.get("MemAvailable", data.get("MemFree", 0)),
        )
    except (OSError, ValueError):
        return 0, 0, 0


def _run(cmd: List[str], *, timeout: float) -> str:
    """Run a command and return stdout (stripped). Empty on any failure."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""

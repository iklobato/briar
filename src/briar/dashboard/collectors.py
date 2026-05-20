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
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, List, Tuple

import yaml


log = logging.getLogger(__name__)


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
                rows.append(
                    {
                        "file": path.name,
                        "error": str(exc),
                        "companies": [],
                    }
                )
                continue
            for company_name, company in (data.get("companies") or {}).items():
                rows.append(
                    {
                        "file": path.name,
                        "company": company_name,
                        "profile": company.get("profile") or "(none)",
                        "extractors": [e.get("name", "?") for e in (company.get("extract") or [])],
                        "knowledge_file": ((company.get("knowledge") or {}).get("name")) or company.get("knowledge_file") or f"./knowledge/{company_name}.md",
                    }
                )
        return {"rows": rows, "count": len(rows)}


class KnowledgeCollector(Collector):
    """Per-blob detail: section breakdown, mined signals, fingerprint,
    age, token estimate. The dashboard uses this to make each knowledge
    file legible at a glance instead of just a byte count."""

    name = "knowledge"

    # Rough chars-per-token ratio for the GPT-family tokenisers. We use 4
    # as a stable approximation — close enough to surface "this blob
    # would cost ~500 tokens to read" magnitudes without bundling tiktoken.
    _CHARS_PER_TOKEN = 4

    # Mined signal patterns. Each one pulls a typed integer out of the
    # standard extractor markdown so the dashboard can render concrete
    # counts ("open PRs: 25") next to the path.
    _SIGNALS = (
        ("merged_prs", re.compile(r"merged PR sample:\s*\*\*(\d+)\*\*")),
        ("open_prs", re.compile(r"—\s*(\d+)\s+open PR\(s\)")),
        ("rds_instances", re.compile(r"RDS\s+\((\d+)\s+instance")),
        ("sqs_queues", re.compile(r"SQS\s+\((\d+)\s+queue")),
        ("log_groups", re.compile(r"CloudWatch Logs.*?of\s+(\d+)\)?")),
        ("repos_covered", re.compile(r"###\s+([A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+)")),
    )

    def __init__(self, store) -> None:  # KnowledgeStore — typed in storage/base
        self._store = store

    def collect(self) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for ref in self._store.list():
            content = self._store.get(ref.name)
            sections_detail = self._split_sections(content)
            signals, repos = self._mine_signals(content)
            head = "\n".join(content.splitlines()[:3])
            fingerprint = ""
            try:
                fingerprint = self._store.fingerprint(ref.name) or ""
            except Exception:  # noqa: BLE001
                log.exception("knowledge-collect: fingerprint failed for %s", ref.name)
            rows.append(
                {
                    "path": ref.name,
                    "byte_count": ref.byte_count,
                    "modified": ref.updated_at or "",
                    "age_human": self._age_human(ref.updated_at, now),
                    "head": head,
                    "sections": len(sections_detail),
                    "sections_detail": sections_detail,
                    "token_estimate": ref.byte_count // self._CHARS_PER_TOKEN,
                    "fingerprint": fingerprint[:8],
                    "signals": signals,
                    "repos_covered": sorted(repos),
                }
            )
        return {
            "rows": rows,
            "root": self._store.name,
            "chart": {
                "labels": [r["path"] for r in rows],
                "values": [r["byte_count"] for r in rows],
            },
        }

    @staticmethod
    def _split_sections(text: str) -> List[Dict[str, Any]]:
        """Walk the markdown and emit one entry per `## ` heading with its
        byte count, line count, and number of `- ` bullets — exactly what
        the dashboard needs to surface 'this section grew from 200 to 8000
        bytes' to a human eye."""
        sections: List[Dict[str, Any]] = []
        current_title = ""
        current_lines: List[str] = []

        def flush() -> None:
            if not current_title:
                return
            body = "\n".join(current_lines)
            bullets = sum(1 for line in current_lines if line.lstrip().startswith("- "))
            sections.append(
                {
                    "title": current_title,
                    "body_bytes": len(body),
                    "line_count": len(current_lines),
                    "bullet_count": bullets,
                }
            )

        for line in text.splitlines():
            if line.startswith("## "):
                flush()
                current_title = line[3:].strip()
                current_lines = []
                continue
            current_lines.append(line)
        flush()
        return sections

    @classmethod
    def _mine_signals(cls, text: str) -> tuple:
        """Pull typed numbers and the repo list out of the standard
        extractor markdown. Returns `(signals_dict, repos_set)`."""
        signals: Dict[str, int] = {}
        repos: set = set()
        for key, pattern in cls._SIGNALS:
            if key == "repos_covered":
                for m in pattern.finditer(text):
                    repos.add(m.group(1))
                continue
            total = 0
            for m in pattern.finditer(text):
                total += int(m.group(1))
            if total:
                signals[key] = total
        return signals, repos

    @staticmethod
    def _age_human(updated_at_iso: str, now: datetime) -> str:
        """Render the gap between `now` and the blob's last-modified time
        in a human chunk ('3m ago', '4h ago', '2d ago'). Falls back to
        empty when the timestamp is missing or unparseable."""
        if not updated_at_iso:
            return ""
        try:
            ts = datetime.fromisoformat(updated_at_iso)
        except ValueError:
            return ""
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = now - ts
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"


class KnowledgeAggregatesCollector(Collector):
    """Mine cross-company numbers out of the stored knowledge blobs."""

    name = "knowledge_aggregates"

    _MERGED_PR_RE = re.compile(r"merged PR sample:\s*\*\*(\d+)\*\*")
    _OPEN_PR_RE = re.compile(r"—\s*(\d+)\s+open PR\(s\)")
    _RDS_RE = re.compile(r"RDS\s+\((\d+)\s+instance")
    _SQS_RE = re.compile(r"SQS\s+\((\d+)\s+queue")
    _LOG_RE = re.compile(r"CloudWatch Logs.*?of\s+(\d+)\)?")

    def __init__(self, store) -> None:  # KnowledgeStore
        self._store = store

    def collect(self) -> Dict[str, Any]:
        total_files = 0
        total_bytes = 0
        merged_prs = 0
        open_prs = 0
        rds = 0
        sqs = 0
        log_groups = 0
        for ref in self._store.list():
            text = self._store.get(ref.name)
            if not text:
                continue
            total_files += 1
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
            "files": total_files,
            "total_bytes": total_bytes,
            "merged_prs": merged_prs,
            "open_prs": open_prs,
            "rds_instances": rds,
            "sqs_queues": sqs,
            "log_groups": log_groups,
        }


# ---------------------------------------------------------------------------
# Schedulers + cycle outcomes + cron health
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
        from briar.iac.runbook import (
            EveryParser,
            RunbookSchedules,
            load_runbook_file,
        )

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


class ScheduleCalendarCollector(Collector):
    """48-hour calendar of scheduler fires: past 24h (from the log) +
    next 24h (computed from each schedule by simulating the job).

    Output shape — designed for direct iteration in the template:
        {
            "window_start": "2026-05-19 22:00 UTC",  # now - 24h, hour-floored
            "window_end":   "2026-05-21 22:00 UTC",  # now + 24h, hour-ceil
            "now":          "2026-05-20 22:14 UTC",
            "buckets": [                              # 48 rows, oldest first
                {
                    "hour":   "2026-05-20 18:00 UTC",
                    "is_now": False,                  # true on the hour containing `now`
                    "is_past": True,                  # bucket's hour < now's hour
                    "fires":  [                       # one entry per fire in this hour
                        {"company":"acme", "task":"prfix",
                         "when":"2026-05-20 18:10 UTC", "kind":"past",
                         "status":"ok", "bytes":3727},
                    ],
                },
                ...
            ],
        }
    """

    name = "schedule_calendar"

    _LOG_FIRE_RE = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\s+\[[^\]]*\]\s+"
        r"briar\.iac\.runbook\.scheduler:\s+"
        r"fire\s+task=(?P<task>\S+)\s+company=(?P<company>\S+)"
    )
    _LOG_RESULT_RE = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\s+\[[^\]]*\]\s+"
        r"briar\.iac\.runbook\.scheduler:\s+"
        r"result\s+task=(?P<task>\S+)\s+company=(?P<company>\S+)\s+"
        r"status=(?P<status>.*?)$"
    )

    def __init__(self, examples_dir: Path, log_path: Path) -> None:
        self._dir = examples_dir
        self._log = log_path

    def collect(self) -> Dict[str, Any]:
        from datetime import timedelta

        now = datetime.now(timezone.utc).replace(microsecond=0)
        now_hour = now.replace(minute=0, second=0)
        window_start = now_hour - timedelta(hours=24)
        window_end = now_hour + timedelta(hours=25)  # 24 future hours INCLUSIVE

        past_fires = self._past_fires(window_start, now)
        future_fires = self._future_fires(now, window_end)

        all_fires = past_fires + future_fires
        all_fires.sort(key=lambda f: f["when_dt"])

        buckets: List[Dict[str, Any]] = []
        cursor = window_start
        bucket_idx: Dict[str, int] = {}
        while cursor < window_end:
            key = cursor.strftime("%Y-%m-%dT%H")
            bucket_idx[key] = len(buckets)
            buckets.append(
                {
                    "hour": cursor.strftime("%Y-%m-%d %H:%M UTC"),
                    "hour_short": cursor.strftime("%H:%M"),
                    "date_label": cursor.strftime("%a %d %b"),
                    "is_now": cursor == now_hour,
                    "is_past": cursor < now_hour,
                    "fires": [],
                }
            )
            cursor = cursor + timedelta(hours=1)

        for fire in all_fires:
            key = fire["when_dt"].strftime("%Y-%m-%dT%H")
            slot = bucket_idx.get(key)
            if slot is None:
                continue
            buckets[slot]["fires"].append(
                {
                    "company": fire["company"],
                    "task": fire["task"],
                    "when": fire["when_dt"].strftime("%H:%M"),
                    "kind": fire["kind"],
                    "status": fire.get("status", ""),
                    "bytes": fire.get("bytes", 0),
                    "elapsed_ms": fire.get("elapsed_ms", 0),
                }
            )

        # 24h aggregates so the calendar header can advertise the
        # dedup hit rate ("3 writes, 9 skipped, 0 failed" = smart-
        # scheduler is paying for itself).
        past = [f for f in all_fires if f["kind"] == "past"]
        write_count = sum(1 for f in past if f.get("status") == "ok")
        skip_count = sum(1 for f in past if f.get("status") == "skipped")
        fail_count = sum(1 for f in past if f.get("status") == "failed")
        skip_pct = round(100 * skip_count / max(1, write_count + skip_count + fail_count), 1)
        return {
            "window_start": window_start.strftime("%Y-%m-%d %H:%M UTC"),
            "window_end": (window_end - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M UTC"),
            "now": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "buckets": buckets,
            "past_count": len(past),
            "future_count": sum(1 for f in all_fires if f["kind"] == "future"),
            "write_count": write_count,
            "skip_count": skip_count,
            "fail_count": fail_count,
            "skip_pct": skip_pct,
        }

    def _past_fires(self, window_start: datetime, now: datetime) -> List[Dict[str, Any]]:
        """Tail the scheduler log and pair fire/result lines."""
        if not self._log.exists():
            return []
        with self._log.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 256_000))
            chunk = fh.read().decode("utf-8", errors="replace")
        fires: List[Dict[str, Any]] = []
        pending: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for line in chunk.splitlines():
            fire_m = self._LOG_FIRE_RE.search(line)
            if fire_m:
                when = self._parse_log_ts(fire_m.group("ts"))
                if when is None or when < window_start or when > now:
                    continue
                rec: Dict[str, Any] = {
                    "company": fire_m.group("company"),
                    "task": fire_m.group("task"),
                    "when_dt": when,
                    "kind": "past",
                    "status": "running",
                }
                fires.append(rec)
                pending[(rec["company"], rec["task"])] = rec
                continue
            result_m = self._LOG_RESULT_RE.search(line)
            if result_m:
                key = (result_m.group("company"), result_m.group("task"))
                if key not in pending:
                    continue
                rec = pending.pop(key)
                status_raw = result_m.group("status").strip()
                skipped_m = re.search(r"skipped\s*\(unchanged,\s*(\d+)\s*bytes", status_raw)
                if skipped_m:
                    # Smart-scheduler dedup hit — output md5 matched the
                    # previously-stored blob so no Postgres write happened.
                    rec["status"] = "skipped"
                    rec["bytes"] = int(skipped_m.group(1))
                    continue
                bytes_m = re.search(r"wrote\s+(\d+)\s+bytes", status_raw)
                if bytes_m:
                    rec["status"] = "ok"
                    rec["bytes"] = int(bytes_m.group(1))
                    continue
                if "failed" in status_raw.lower() or "error" in status_raw.lower():
                    rec["status"] = "failed"
                    continue
                rec["status"] = "done"
        return fires

    def _future_fires(self, now: datetime, window_end: datetime) -> List[Dict[str, Any]]:
        """Simulate each schedule forward to enumerate fires inside the window."""
        import schedule as schedule_mod
        from briar.iac.runbook import (
            EveryParser,
            RunbookSchedules,
            load_runbook_file,
        )

        out: List[Dict[str, Any]] = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                runbook = load_runbook_file(path)
            except Exception:  # noqa: BLE001
                log.exception("calendar: failed to load %s", path.name)
                continue
            for company_name, company in runbook.companies.items():
                for entry in RunbookSchedules.for_company(company):
                    out.extend(
                        self._project_one(
                            company_name,
                            entry,
                            now,
                            window_end,
                            EveryParser,
                            schedule_mod,
                        )
                    )
        return out

    @staticmethod
    def _project_one(
        company: str,
        entry: Any,
        now: datetime,
        window_end: datetime,
        parser: Any,
        schedule_mod: Any,
    ) -> List[Dict[str, Any]]:
        """For a single (company, task) schedule, project future fires by
        starting from job.next_run (the schedule library's correct
        first-run computation) and then walking forward by `job.period`
        because schedule._schedule_next_run rebases on now() each call
        rather than advancing from the previous next_run. Caps at 32
        fires to bound runtime."""
        from datetime import timedelta

        local = schedule_mod.Scheduler()
        job = parser.parse(entry.every, scheduler=local)
        job.do(_noop)
        run_at = job.next_run
        if run_at is None:
            return []
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        # Build the step interval from the job's declared cadence
        # (`schedule.Job` exposes `interval` + `unit`, e.g. (4, "hours")
        # or (1, "days"), but no public `period` attribute).
        unit_to_kwarg = {
            "seconds": "seconds",
            "minutes": "minutes",
            "hours": "hours",
            "days": "days",
            "weeks": "weeks",
        }
        kwarg = unit_to_kwarg.get(job.unit or "")
        step = timedelta(**{kwarg: job.interval}) if kwarg else timedelta(hours=1)
        out: List[Dict[str, Any]] = []
        for _ in range(32):
            if run_at >= window_end:
                break
            if run_at > now:
                out.append(
                    {
                        "company": company,
                        "task": entry.task,
                        "when_dt": run_at,
                        "kind": "future",
                    }
                )
            run_at = run_at + step
        return out

    @staticmethod
    def _parse_log_ts(ts: str) -> Any:
        try:
            return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


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
        r"\[INFO[^\]]*\]\s+briar\.extract\._gh:.*?"
        r"gh\s+GET\s+ok\s+path=(?P<path>\S+).*?"
        r"ratelimit_remaining=(?P<rem>\d+)"
    )
    _CACHE_HIT_RE = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\s+"
        r"\[INFO[^\]]*\]\s+briar\.extract\._gh:.*?"
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
        with self._log.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 512_000))
            text = fh.read().decode("utf-8", errors="replace")

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
                when = ScheduleCalendarCollector._parse_log_ts(ok_m.group("ts"))
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
                when = ScheduleCalendarCollector._parse_log_ts(hit_m.group("ts"))
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
                when = ScheduleCalendarCollector._parse_log_ts(err_m.group("ts"))
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
        with self._path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 64_000))
            chunk = fh.read().decode("utf-8", errors="replace")
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
            "byte_count": size,
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
        with self._path.open("rb") as fh:
            text = fh.read().decode("utf-8", errors="replace")
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

        rows = [{"kind": tmpl.kind, "family": tmpl.family or "(none)"} for tmpl in SOURCE_TEMPLATES.values()]
        return {"rows": rows, "count": len(rows)}


class TriggersCollector(Collector):
    name = "trigger_templates"

    def collect(self) -> Dict[str, Any]:
        from briar.iac.scaffold.triggers import TRIGGER_TEMPLATES

        rows = [{"kind": tmpl.kind, "description": tmpl.description} for tmpl in TRIGGER_TEMPLATES.values()]
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
            "rows": [{"name": d.name, "manifest": d.manifest} for d in LANGUAGE_DETECTORS],
        }


class WorkflowShapesCollector(Collector):
    name = "workflow_shapes"

    def collect(self) -> Dict[str, Any]:
        from briar.iac.scaffold.shapes import WORKFLOW_SHAPES

        return {
            "rows": [{"name": s.name, "description": s.description} for s in WORKFLOW_SHAPES.values()],
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
                    "consumes": list(a.consumes) or ["(none)"],
                }
                for a in ARCHETYPES.values()
            ],
        }


class CommandsCollector(Collector):
    name = "commands"

    def collect(self) -> Dict[str, Any]:
        from briar.commands import CommandRegistry

        return {
            "rows": [{"name": cmd.name, "help": cmd.help} for cmd in CommandRegistry.build().values()],
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
            "sha": sha,
            "short_sha": sha[:7] if sha else "",
            "branch": branch,
            "subject": subject,
            "author": author,
            "committed": committed,
            "remote": remote_clean,
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
            rows.append(
                {
                    "name": key,
                    "length": len(value),
                    "set": bool(value),
                }
            )
        stat = self._path.stat()
        return {
            "present": True,
            "path": str(self._path),
            "rows": rows,
            "byte_count": stat.st_size,
            "modified": datetime.fromtimestamp(
                stat.st_mtime,
                tz=timezone.utc,
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


class DiskByDirCollector(Collector):
    """Recursive size of each named directory."""

    name = "disk_by_dir"

    def __init__(self, paths: List[Path]) -> None:
        self._paths = paths

    def collect(self) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for path in self._paths:
            size = _du(path)
            rows.append(
                {
                    "path": str(path),
                    "bytes": size,
                    "human": _human_bytes(size),
                    "present": path.exists(),
                }
            )
        return {"rows": rows}


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
    """Container for the filesystem paths + knowledge store the
    dashboard's collectors read from. Bundling them into one struct
    keeps the registry's `from_paths` constructor at a reasonable
    arity."""

    examples_dir: Path
    knowledge_store: Any  # KnowledgeStore — Any avoids a cycle
    log_path: Path
    disk_path: Path
    repo_path: Path
    secrets_path: Path
    du_paths: List[Path] = field(default_factory=list)


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
        return [
            SystemCollector(disk_path=paths.disk_path),
            GitDeployCollector(repo_path=paths.repo_path),
            SchedulesCollector(examples_dir=paths.examples_dir),
            ScheduleCalendarCollector(
                examples_dir=paths.examples_dir,
                log_path=paths.log_path,
            ),
            GhStatsCollector(log_path=paths.log_path),
            SchedulerProcessCollector(),
            ScheduleLogCollector(log_path=paths.log_path),
            CycleOutcomeCollector(log_path=paths.log_path),
            ConnectivityCollector(),
            SecretsCollector(secrets_path=paths.secrets_path),
            CompaniesCollector(examples_dir=paths.examples_dir),
            KnowledgeCollector(store=paths.knowledge_store),
            KnowledgeAggregatesCollector(store=paths.knowledge_store),
            ExtractorsCollector(),
            SourcesCollector(),
            TriggersCollector(),
            StorageCollector(),
            AwsServicesCollector(),
            LanguageDetectorsCollector(),
            WorkflowShapesCollector(),
            ArchetypesCollector(),
            CommandsCollector(),
            DiskByDirCollector(paths=paths.du_paths),
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
        return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("## "))
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

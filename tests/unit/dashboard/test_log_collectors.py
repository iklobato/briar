"""Parsing tests for the log-scanning monitoring collectors.

These two collectors turn raw scheduler-log lines into the dashboard's
cycle-outcome and GitHub-quota panels, so their regex parsing is load-bearing.
We feed a synthetic log and assert the extracted structure.
"""

from __future__ import annotations

from datetime import datetime, timezone

from briar.dashboard.collectors import CycleOutcomeCollector, GhStatsCollector


def test_cycle_outcomes_parses_one_cycle(tmp_path) -> None:
    log = tmp_path / "scheduler.log"
    log.write_text(
        "[2026-06-16T03:00:00] extract acme.yaml\n" "acme wrote 1234 bytes\n" "FAILED beta.yaml: connection refused\n" "[2026-06-16T03:00:05] cycle done\n"
    )
    out = CycleOutcomeCollector(log_path=log).collect()

    assert out["total_cycles"] == 1
    cycle = out["cycles"][0]
    assert cycle["started_at"] == "2026-06-16T03:00:00"
    assert cycle["finished_at"] == "2026-06-16T03:00:05"
    rows = {r["company"]: r for r in cycle["rows"]}
    assert rows["acme"]["status"] == "ok" and rows["acme"]["bytes"] == 1234
    assert rows["beta"]["status"] == "failed" and rows["beta"]["error"] == "connection refused"


def test_cycle_outcomes_missing_log_is_empty(tmp_path) -> None:
    out = CycleOutcomeCollector(log_path=tmp_path / "nope.log").collect()
    assert out == {"cycles": [], "by_company": {}}


def test_gh_stats_counts_ok_and_cache_hits(tmp_path) -> None:
    # GhStats only counts events within the last 24h, so stamp "now".
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    log = tmp_path / "scheduler.log"
    log.write_text(
        f"{ts}Z [INFO] briar.extract._gh: gh GET ok path=/repos/x/y ratelimit_remaining=4990\n"
        f"{ts}Z [INFO] briar.extract._gh: gh GET 304-cache-hit path=/repos/x/z ratelimit_remaining=4990\n"
    )
    out = GhStatsCollector(log_path=log).collect()

    assert out["ok_count"] == 1
    assert out["cache_hit_count"] == 1
    assert out["last_remaining"] == 4990
    assert any(h["path"] == "/repos/x/z" for h in out["recent_cache_hits"])


def test_gh_stats_missing_log_degrades(tmp_path) -> None:
    out = GhStatsCollector(log_path=tmp_path / "nope.log").collect()
    assert out["ok_count"] == 0
    assert out["cache_hit_count"] == 0

"""Read-only single-page dashboard for the scheduler droplet.

Aggregates everything visible on disk — companies declared in the
runbook YAMLs, the cron entry, recent log activity, extracted
knowledge files, the extractor/source/trigger registries, and system
state — into a single Jinja-rendered HTML page served over stdlib
HTTP. No write endpoints; safe to expose publicly."""

from __future__ import annotations

from briar.dashboard.server import DashboardServer

__all__ = ["DashboardServer"]

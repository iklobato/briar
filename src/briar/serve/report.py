"""The daily report of `briar serve` (decisions C4 and F1 of the autonomous serve): one
Markdown file per company and UTC day, `<knowledge root>/reports/YYYY-MM-DD.md`, rewritten at
the end of every poll. It is how the maintainer learns what serve did without reading logs:
what waits for a merge, what stopped and why, what the day cost, and what the poll skipped."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List

from briar.serve.state import WorkEntry, WorkStatus
from briar.serve.work import ServeCompany, WorkDay

if TYPE_CHECKING:
    from briar.serve.work import WorkSources


class DailyReport:
    """Writes the report of today for one company."""

    def __init__(self, company: ServeCompany, sources: "WorkSources") -> None:
        self._company = company
        self._sources = sources

    def write(self) -> Path:
        day = WorkDay().today()
        path = self._company.root / "reports" / f"{day}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._text(day))
        return path

    def _text(self, day: str) -> str:
        entries = list(self._company.state.book().entries.values())
        written = datetime.now(timezone.utc).strftime("%H:%M")
        parts = [
            f"# briar daily report: {self._company.name}, {day} (UTC)",
            f"Written {written} UTC; rewritten every poll.",
            "## Spent today\n\n" + self._spent(entries, day),
            "## Waiting for a merge\n\n" + self._listed([self._waiting(entry) for entry in entries if entry.status is WorkStatus.DONE and entry.pr]),
            "## Stopped\n\n" + self._listed([self._stopped(entry) for entry in entries if entry.status is WorkStatus.BLOCKED]),
            "## Notes of the polls\n\n" + self._listed([f"- {note}" for note in dict.fromkeys(self._company.notes.get(day, []))]),
        ]
        return "\n\n".join(parts) + "\n"

    def _spent(self, entries: List[WorkEntry], day: str) -> str:
        spent = [entry.spent_by_day[day] for entry in entries if day in entry.spent_by_day]
        return f"US$ {sum(spent):.2f} on {len(spent)} ticket(s)."

    def _waiting(self, entry: WorkEntry) -> str:
        url = self._sources.style().pull_url(entry.repo, entry.pr)
        since = datetime.fromisoformat(entry.ready_at).strftime("%Y-%m-%d %H:%M UTC") if entry.ready_at else "unknown"
        reviewers = ", ".join(entry.reviewers) or "nobody"
        return f"- {entry.key} {entry.title}: {url} (ready since {since}; reviewers: {reviewers})"

    def _stopped(self, entry: WorkEntry) -> str:
        first = entry.report.split("\n\n")[0].strip() or "no report"
        return f"- {entry.key} {entry.title}: {first}"

    def _listed(self, lines: List[str]) -> str:
        return "\n".join(lines) if lines else "- none"


__all__ = ["DailyReport"]

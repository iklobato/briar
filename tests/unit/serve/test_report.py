"""The daily report of `briar serve` (decisions C4 and F1 of the autonomous serve; test plan
cases K1 to K3): one file per company and UTC day, rewritten every poll, that says what is
waiting for a merge, what stopped and why, what the day cost, and what the poll skipped."""

from __future__ import annotations

import asyncio
from pathlib import Path

from briar.serve.report import DailyReport
from briar.serve.state import WorkEntry, WorkSource, WorkStatus
from briar.serve.work import ServeCompany, WorkDay
from tests.unit.serve.fakes import Companies, FakeSources

PR_NUMBER = 12
SPENT_DONE, SPENT_BLOCKED, SPENT_YESTERDAY = 0.45, 0.25, 9.0


class Entries:
    def record(self, company: ServeCompany) -> None:
        today = WorkDay().today()
        done = WorkEntry(
            key="ENG-1", source=WorkSource.JIRA, repo="acme/app", title="Add the feature", status=WorkStatus.DONE, pr=PR_NUMBER,
            ready_at="2026-11-13T10:00:00+00:00", reviewers=["ann", "bob"], spent_by_day={today: SPENT_DONE, "2026-01-01": SPENT_YESTERDAY},
        )  # fmt: skip
        blocked = WorkEntry(
            key="ENG-2", source=WorkSource.JIRA, repo="acme/app", title="Copy the dataset", status=WorkStatus.BLOCKED,
            report="the plan needs no code change in acme/app; nothing was posted on the ticket\n\nmore detail", spent_by_day={today: SPENT_BLOCKED},
        )  # fmt: skip
        for entry in (done, blocked):
            asyncio.run(company.state.record(entry))


class TestDailyReport:
    def test_the_report_lists_waiting_merges_stops_cost_and_notes(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app"]}}})
        Entries().record(company)
        company.notes.setdefault(WorkDay().today(), []).append("the chat was skipped: slack auth.test: invalid_auth")

        path = DailyReport(company, FakeSources()).write()

        text = path.read_text()
        assert path == tmp_path / "knowledge" / "reports" / f"{WorkDay().today()}.md"
        assert f"- ENG-1 Add the feature: {FakeSources().style().pull_url('acme/app', PR_NUMBER)} (ready since 2026-11-13 10:00 UTC; reviewers: ann, bob)" in text
        assert "- ENG-2 Copy the dataset: the plan needs no code change in acme/app; nothing was posted on the ticket" in text
        assert "more detail" not in text
        assert "US$ 0.70 on 2 ticket(s)" in text
        assert "- the chat was skipped: slack auth.test: invalid_auth" in text

    def test_two_polls_rewrite_the_file_instead_of_adding_to_it(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app"]}}})
        Entries().record(company)
        report = DailyReport(company, FakeSources())

        first = report.write().read_text()
        second = report.write().read_text()

        assert (second.count("- ENG-1 "), first.splitlines()[0]) == (1, f"# briar daily report: acme, {WorkDay().today()} (UTC)")

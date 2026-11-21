"""The page of a task: the memory of the task (decisions W2, W3 and W4 of
`docs/decisions/2026-11-18-serve-event-workflows.md`).

The code writes the Facts, the agent writes the Notes, and every call of the task gets the path of
the page in the system prompt."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from briar.extract._provider import CiState, CommentKind, ReviewComment
from briar.serve.agent import AgentAnswer, AgentRequest
from briar.serve.page import Fact, PageBackfill, PageError, PageFile, PageName, TaskFacts, TaskPages
from briar.serve.state import MoneyStop, WorkEntry, WorkSource, WorkStatus
from briar.serve.work import BudgetReopen, ServeCompanies, ServeCompany, WorkDay, WorkItem, WorkKind
from briar.storage import make_store
from tests.unit.serve.fakes import Companies
from tests.unit.serve.ticket_fakes import Entries, FakeGithubSide, Files, Items, Plans, ScriptedAgent, Setup, Setups, Step

KEY = "ENG-1"
PR = FakeGithubSide.NUMBER
# The cap of a ticket whose money runs out on the call of the Notes.
NOTES_CAP = 0.35
# The cap of the tickets of the tests, and what a maintainer raises it to.
MAX_USD = 5.0
RAISED_USD = 10.0
# The token of the code host of the test that asks the page not to hold it.
TOKEN = "ghp-a-token-that-is-a-secret"
# What the pick of the selector cost, and what an older task spent.
PICK_USD = 0.01
OLD_SPEND_USD = 0.5
# The day of the spends of the backfill tests.
DAY = "2026-11-21"
# The spend of a ticket of three calls of 0.1, then after each of two rounds of one call.
COSTS = (0.3, 0.4, 0.5)


class Facts:
    """The Facts of a page, by their kind."""

    def of(self, page: PageFile) -> Dict[str, List[str]]:
        found: Dict[str, List[str]] = {}
        for fact in page.facts():
            found.setdefault(fact.kind, []).append(fact.text)
        return found

    def kinds(self, page: PageFile) -> List[str]:
        return [fact.kind for fact in page.facts()]


class Store:
    """A knowledge store that remembers what was written, in order, and that fails on a page
    that holds a Fact of one kind (a disk that is full)."""

    def __init__(self, inner: Any, kind: str = "", once: bool = False) -> None:
        self._inner = inner
        self.kind = kind
        self.once = once
        self.failed = False
        self.puts: List[Tuple[str, str]] = []

    def get(self, name: str) -> str:
        return self._inner.get(name)

    def put(self, name: str, content: str, category: str = "") -> Any:
        self.puts.append((name, content))
        if self._breaks(content):
            raise OSError("the disk is full")
        return self._inner.put(name, content, category)

    def _breaks(self, content: str) -> bool:
        """The page holds a Fact of the kind that fails: the disk is full."""
        if not self.kind or f"`{self.kind}:" not in content:
            return False
        if self.once and self.failed:
            return False
        self.failed = True
        return True

    def index_of(self, marker: str) -> int:
        """Where the marker was written, in the order of the writes (-1 when it was not)."""
        for number, (_, content) in enumerate(self.puts):
            if marker in content:
                return number
        return -1


class Broken:
    """A knowledge store where a person breaks the page by hand (a heading of their own) once it holds a marker."""

    def __init__(self, inner: Any, marker: str) -> None:
        self._inner = inner
        self._marker = marker

    def get(self, name: str) -> str:
        return self._inner.get(name)

    def put(self, name: str, content: str, category: str = "") -> Any:
        broken = f"{content}\n## Whatever\n" if self._marker in content else content
        return self._inner.put(name, broken, category)


class Reviews:
    """The review comments and the round items of the tests."""

    def inline(self) -> ReviewComment:
        return ReviewComment(
            id="1", author="ann", body="say why in feature.txt", file_path="feature.txt", line=1,
            kind=CommentKind.INLINE, association="MEMBER", updated_at="t1", thread="1",
        )  # fmt: skip

    def item(self, comments: Sequence[ReviewComment]) -> WorkItem:
        return WorkItem(WorkKind.REVIEW, KEY, WorkSource.JIRA, "acme/app", "Add the feature", pr=PR, comments=tuple(comments))

    def answers(self, reply: str) -> Dict[str, Any]:
        return {"answers": [{"id": "inline:1", "changed": False, "reply": reply}]}


class Opened:
    """A setup whose ticket ENG-1 went through the ticket routine: PR 41 open on `briar/ENG-1`."""

    def __init__(self, setups: Setups) -> None:
        self._setups = setups

    def make(self, extra: Dict[str, Any], store: Optional[Any] = None, token: str = "") -> Setup:
        setup = self._setups.make(extra, store)
        if token:
            setup = self._with_token(setup, token)
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())
        return setup

    def _with_token(self, setup: Setup, token: str) -> Setup:
        """The setup with a token of its own, so the page can be asked not to hold it."""
        side = replace(setup.sources.github_side, token=token)
        return replace(setup, sources=replace(setup.sources, github_side=side))


class TestTheName:
    def test_a_jira_key_stays(self) -> None:
        assert PageName().of("ENG-1") == "ENG-1"

    def test_a_github_key_becomes_one_file_name(self) -> None:
        assert PageName().of("acme/app#7") == "acme__app-7"

    def test_two_repos_with_the_same_number_do_not_collide(self) -> None:
        assert PageName().of("acme/app#7") != PageName().of("acme/api#7")

    def test_the_page_is_under_the_tasks_of_the_company(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company({"repos": ["acme/app"]})

        path = company.page_file(KEY)

        assert path == str(tmp_path / "knowledge" / "work" / "acme" / "tasks" / "ENG-1.md")

    def test_the_page_is_an_absolute_path_and_not_relative_to_the_folder_of_the_run(self, tmp_path: Path) -> None:
        """The agent runs in the worktree: a path relative to here points nowhere."""
        company = Companies(tmp_path / "knowledge").company({"repos": ["acme/app"]})

        assert Path(company.page_file(KEY)).is_absolute()


class TestThePage:
    def test_the_facts_and_the_notes_have_two_headings(self) -> None:
        text = PageFile([Fact("planned:p", "planned", "the plan", "")], "the notes").text()

        assert ("## Facts" in text, "## Notes" in text) == (True, True)

    def test_a_page_is_read_back_as_it_was_written(self) -> None:
        page = PageFile([Fact("planned:p", "planned", "the plan", "")], "the notes")

        back = PageFile.parse(page.text())

        assert (back.facts()[0].text, back.notes()) == ("the plan", "the notes")

    def test_a_fact_with_two_colons_in_its_key_is_read_back(self) -> None:
        page = PageFile([Fact("verify:abc:2", "verify", "the verify passed", "")])

        back = PageFile.parse(page.text())

        assert back.facts()[0].key == "verify:abc:2"

    def test_an_unknown_heading_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown heading"):
            PageFile.parse("## Facts\n\n- `a:b` c\n\n## Whatever\n")

    def test_a_heading_twice_is_refused(self) -> None:
        with pytest.raises(ValueError, match="twice"):
            PageFile.parse("## Facts\n\n- `a:b` c\n\n## Notes\n\nd\n\n## Notes\n\ne\n")

    def test_the_same_fact_key_is_written_once(self) -> None:
        page = PageFile()

        assert (page.add(Fact("planned:p", "planned", "the plan", "")), page.add(Fact("planned:p", "planned", "the plan", ""))) == (True, False)

    def test_a_page_with_no_facts_has_none(self) -> None:
        assert PageFile.parse("## Facts\n\n## Notes\n\n").facts() == []

    def test_a_page_this_code_did_not_write_is_a_page_error_that_says_why(self) -> None:
        with pytest.raises(PageError) as raised:
            PageFile.parse("## Facts\n\n- `a:b` c\n\n## Whatever\n")

        assert str(raised.value) == "the page has an unknown heading: ## Whatever"


class TestTheTextOfOthers:
    """A live run (2026-11-21): the text of a Fact or of the Notes is written by other people and by the agent. A line of it that
    starts with `## ` read back as a heading and broke the page; the lines after the first of a Fact were lost; a line that
    starts with "- `" read back as a Fact of its own."""

    COMMENT = "inline comment by ann: rename it\n\n## Why\nthe name is wrong\n- `ready:x` the PR is ready"

    def test_a_fact_of_many_lines_is_read_back_whole(self) -> None:
        page = PageFile([Fact("built:b", "built", " a.py | 1 +\n b.py | 2 +\n 2 files changed", ""), Fact("verify:b:0", "verify", "ok", "")])

        back = PageFile.parse(page.text())

        assert [fact.text for fact in back.facts()] == ["a.py | 1 +\n b.py | 2 +\n 2 files changed", "ok"]

    def test_a_heading_line_in_a_fact_is_text_and_the_page_still_reads(self) -> None:
        page = PageFile([Fact("comment:inline:1", "comment", self.COMMENT, "")])

        back = PageFile.parse(page.text())

        assert [(fact.key, fact.text) for fact in back.facts()] == [("comment:inline:1", self.COMMENT)]

    def test_a_heading_line_in_the_notes_is_text_and_the_page_still_reads(self) -> None:
        page = PageFile([Fact("planned:p", "planned", "the plan", "")], "## Why\nit was small\n\n- `ready:x` not a fact")

        back = PageFile.parse(page.text())

        assert (back.notes(), [fact.key for fact in back.facts()]) == ("## Why\nit was small\n\n- `ready:x` not a fact", ["planned:p"])


class TestTheBackfill:
    def test_the_page_of_an_entry_that_has_no_page_yet_holds_what_the_state_knows(self) -> None:
        entry = WorkEntry(key=KEY, source=WorkSource.JIRA, repo="acme/app", title="Add the feature", status=WorkStatus.PR_OPEN, plan="the plan", pr=PR)

        page = PageBackfill().of(entry)

        assert Facts().kinds(page) == ["planned", "pr-opened"]

    def test_an_entry_that_is_not_there_gives_an_empty_page(self) -> None:
        assert PageBackfill().of(None).facts() == []

    def test_a_new_task_with_only_the_spend_of_its_pick_has_nothing_to_backfill(self) -> None:
        """A live run (2026-11-21): the pick of the selector makes the entry, so the first Fact of a new task wrote a `cost:backfill`."""
        entry = WorkEntry(key=KEY, source=WorkSource.JIRA, repo="acme/app", title="Add the feature", status=WorkStatus.FOUND, spent_by_day={DAY: PICK_USD})

        assert PageBackfill().of(entry).facts() == []

    def test_an_old_task_keeps_its_spend_next_to_what_it_did(self) -> None:
        entry = WorkEntry(
            key=KEY, source=WorkSource.JIRA, repo="acme/app", title="Add the feature", status=WorkStatus.PR_OPEN, plan="the plan", pr=PR,
            spent_by_day={DAY: OLD_SPEND_USD},
        )  # fmt: skip

        page = PageBackfill().of(entry)

        assert [(fact.kind, fact.text) for fact in page.facts()] == [("planned", "the plan"), ("pr-opened", f"PR #{PR}"), ("cost", "US$ 0.50 in all")]


class TestThePagesOfACompany:
    def page(self, company: ServeCompany, key: str = KEY) -> PageFile:
        return TaskPages(company).read(key)

    def test_the_page_is_written_in_the_knowledge_of_the_company(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company({"repos": ["acme/app"]})

        TaskPages(company).write(KEY, PageFile([Fact("planned:p", "planned", "the plan", "")]))

        assert (tmp_path / "knowledge" / "work" / "acme" / "tasks" / "ENG-1.md").exists()

    def test_a_fact_is_written_once(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company({"repos": ["acme/app"]})
        pages = TaskPages(company)
        pages.write(KEY, PageFile([Fact("planned:p", "planned", "the plan", "")]))

        pages.write(KEY, pages.read(KEY))

        assert len(self.page(company).facts()) == 1


class Observed:
    """The page of one task, seen by the observer that writes its Facts."""

    def __init__(self, company: ServeCompany, item: WorkItem) -> None:
        self.company = company
        self.item = item

    def add(self, kind: str, text: str, key: str = "") -> PageFile:
        asyncio.run(TaskFacts(self.company, self.item).add(kind, text, key))
        return TaskPages(self.company).read(self.item.key)


class TestTheObserver:
    def observed(self, tmp_path: Path) -> Observed:
        company = Companies(tmp_path / "knowledge").company({"repos": ["acme/app"]})
        return Observed(company, Items().jira())

    def test_a_fact_reaches_the_page(self, tmp_path: Path) -> None:
        observed = self.observed(tmp_path)

        page = observed.add("planned", "the plan", "p")

        assert Facts().of(page) == {"planned": ["the plan"]}

    def test_the_same_fact_twice_is_written_once(self, tmp_path: Path) -> None:
        observed = self.observed(tmp_path)

        observed.add("planned", "the plan", "p")
        page = observed.add("planned", "the plan", "p")

        assert len(page.facts()) == 1

    def test_a_page_that_does_not_exist_yet_is_filled_with_what_the_state_knows(self, tmp_path: Path) -> None:
        observed = self.observed(tmp_path)
        asyncio.run(observed.company.state.record(observed.item.entry().model_copy(update={"plan": "the plan of the state"})))

        page = observed.add("built", "the build", "b")

        assert Facts().kinds(page) == ["planned", "built"]


class TestThePageOfATicket:
    """A ticket run writes the facts of what it did (decision W4)."""

    def page(self, setup: Any) -> PageFile:
        return TaskPages(setup.company).read("ENG-1")

    def test_a_ticket_run_writes_what_it_did(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes={"feature.txt": "x\n"}), Plans().approved()]), Items().jira())

        kinds = Facts().kinds(self.page(setup))
        assert ("taken" in kinds, "planned" in kinds, "built" in kinds) == (True, True, True)
        assert ("pr-opened" in kinds, "ready" in kinds, "cost" in kinds) == (True, True, True)

    def test_the_built_fact_holds_the_files_of_the_build(self, setups: Setups) -> None:
        """A live run (2026-11-21): the Fact was written before the first commit, so its diff was empty."""
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        (built,) = Facts().of(self.page(setup))["built"]
        assert "feature.txt" in built

    def test_the_cost_of_the_ticket_counts_the_notes_and_no_fix_loops(self, setups: Setups) -> None:
        """Four calls of 0.1: the plan, the build, the self review and the Notes. The fix loops are of one run, so they stay out."""
        setup = setups.make({"notes": True})
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), Step(structured={"notes": "the notes"})])

        setup.work_on(agent, Items().jira())

        assert Facts().of(self.page(setup))["cost"] == [f"US$ 0.40 of {MAX_USD} spent by the task so far"]

    def test_a_restart_does_not_write_the_same_fact_twice(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes={"feature.txt": "x\n"}), Plans().approved()]), Items().jira())

        setup.work_on(ScriptedAgent([Plans().approved()]), Items().jira())

        kinds = Facts().kinds(self.page(setup))
        assert kinds.count("planned") == 1


class TestTheNotes:
    def test_the_notes_are_a_call_with_no_tools(self, setups: Setups) -> None:
        setup = setups.make({"notes": True})
        agent = ScriptedAgent([Plans().plan(), Step(writes={"feature.txt": "x\n"}), Plans().approved(), Step(structured={"notes": "the notes"})])

        setup.work_on(agent, Items().jira())

        calls = [request for request in agent.requests if request.schema.get("properties", {}).get("notes")]
        assert (len(calls), calls[0].access.value if calls else "") == (1, "answer")

    def test_the_notes_are_written_in_the_page(self, setups: Setups) -> None:
        setup = setups.make({"notes": True})
        agent = ScriptedAgent([Plans().plan(), Step(writes={"feature.txt": "x\n"}), Plans().approved(), Step(structured={"notes": "the notes"})])

        setup.work_on(agent, Items().jira())

        page = TaskPages(setup.company).read("ENG-1")
        assert page.notes() == "the notes"

    def test_the_notes_are_off_when_the_runbook_says_so(self, setups: Setups) -> None:
        setup = setups.make({"notes": False})
        agent = ScriptedAgent([Plans().plan(), Step(writes={"feature.txt": "x\n"}), Plans().approved()])

        setup.work_on(agent, Items().jira())

        assert TaskPages(setup.company).read("ENG-1").notes() == ""

    def test_a_cap_that_stops_the_notes_leaves_the_task_done_and_the_pr_ready(self, setups: Setups) -> None:
        """A note is never worth a finished PR: the cap that stops the call leaves the task done."""
        setup = setups.make({"notes": True, "max_usd": NOTES_CAP})
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), Step(structured={"notes": "the notes"})])

        setup.work_on(agent, Items().jira())

        github = setup.sources.github_side
        assert setup.entry(KEY).status is WorkStatus.DONE
        assert (github.readies, github.drafts) == ([PR], [])
        assert "notes-skipped" in Facts().kinds(TaskPages(setup.company).read(KEY))


class TestAFailingStore:
    """A store that fails on the page of a task: a task that is not over yet stops with the report
    of the failure, and a task that is over only logs it (a page is never worth a finished PR)."""

    def store(self, tmp_path: Path, kind: str, once: bool = False) -> Store:
        return Store(make_store("file", file_root=tmp_path / "knowledge"), kind, once)

    def test_a_store_that_fails_before_the_task_is_over_blocks_it_with_that_report(self, setups: Setups, tmp_path: Path) -> None:
        setup = setups.make({}, self.store(tmp_path, "taken"))

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        entry = setup.entry(KEY)
        assert (entry.status, "the disk is full" in entry.report) == (WorkStatus.BLOCKED, True)

    def test_the_step_that_runs_again_writes_its_fact_once(self, setups: Setups, tmp_path: Path) -> None:
        setup = setups.make({}, self.store(tmp_path, "taken", once=True))
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()])

        setup.work_on(agent, Items().jira())
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        page = TaskPages(setup.company).read(KEY)
        assert Facts().kinds(page).count("taken") == 1

    def test_a_store_that_fails_after_the_task_is_over_only_logs_it(self, setups: Setups, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        setup = setups.make({}, self.store(tmp_path, "ready"))

        with caplog.at_level(logging.ERROR):
            setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        entry = setup.entry(KEY)
        assert (entry.status, setup.sources.github_side.readies) == (WorkStatus.DONE, [PR])
        assert "the page of the task could not be written" in caplog.text

    def test_a_page_a_person_broke_after_the_task_is_over_only_logs_it(self, setups: Setups, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """A page is never worth a finished PR, also when a person broke it by hand: the Facts after the end and the Notes log it."""
        setup = setups.make({"notes": True}, Broken(make_store("file", file_root=tmp_path / "knowledge"), "`ci:"))

        with caplog.at_level(logging.ERROR):
            setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        github = setup.sources.github_side
        assert (setup.entry(KEY).status, github.readies, github.drafts) == (WorkStatus.DONE, [PR], [])
        assert "the page has an unknown heading: ## Whatever" in caplog.text


class TestTheReviewRound:
    """A review round writes what it did: the comment, the reply, the round and what it cost."""

    def opened(self, setups: Setups) -> Setup:
        setup = setups.make({})
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())
        return setup

    def test_a_round_writes_the_comment_the_reply_the_round_and_the_cost(self, setups: Setups) -> None:
        setup = self.opened(setups)
        reviews = Reviews()
        agent = ScriptedAgent([Step(structured=reviews.answers("the name is the one of the ticket"))])

        setup.work_on(agent, reviews.item([reviews.inline()]))

        kinds = Facts().kinds(TaskPages(setup.company).read(KEY))
        assert ("comment" in kinds, "reply" in kinds, "round" in kinds, "cost" in kinds) == (True, True, True, True)

    def test_the_cost_of_a_round_counts_its_notes(self, setups: Setups) -> None:
        """Calls of 0.1: four for the ticket (with its Notes), then the answer and the Notes of the round."""
        setup = setups.make({"notes": True})
        notes = Step(structured={"notes": "the notes"})
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), notes]), Items().jira())
        reviews = Reviews()

        setup.work_on(ScriptedAgent([Step(structured=reviews.answers("done")), notes]), reviews.item([reviews.inline()]))

        costs = Facts().of(TaskPages(setup.company).read(KEY))["cost"]
        assert costs == [f"US$ 0.40 of {MAX_USD} spent by the task so far", f"US$ 0.60 of {MAX_USD} spent by the task so far"]

    def test_the_first_round_is_round_one(self, setups: Setups) -> None:
        setup = self.opened(setups)
        reviews = Reviews()

        setup.work_on(ScriptedAgent([Step(structured=reviews.answers("done"))]), reviews.item([reviews.inline()]))

        keys = [fact.key for fact in TaskPages(setup.company).read(KEY).facts() if fact.kind == "round"]
        assert keys == ["round:1"]

    def test_a_comment_with_a_heading_is_answered_and_the_pr_stays_ready(self, setups: Setups) -> None:
        """A live run (2026-11-21): a comment with a `## Why` line broke the page, the task was blocked, the ready PR went back
        to a draft and the comment got no reply."""
        setup = self.opened(setups)
        reviews = Reviews()
        comment = replace(reviews.inline(), body=TestTheTextOfOthers.COMMENT.removeprefix("inline comment by ann: "))

        setup.work_on(ScriptedAgent([Step(structured=reviews.answers("renamed"))]), reviews.item([comment]))

        github, page = setup.sources.github_side, TaskPages(setup.company).read(KEY)
        assert (setup.entry(KEY).status, github.drafts, len(github.replies)) == (WorkStatus.DONE, [], 1)
        assert (Facts().of(page)["comment"], "ready:x" in [fact.key for fact in page.facts()]) == ([TestTheTextOfOthers.COMMENT], False)

    def test_the_agent_of_the_round_is_told_where_the_page_is(self, setups: Setups) -> None:
        setup = self.opened(setups)
        reviews = Reviews()
        agent = ScriptedAgent([Step(structured=reviews.answers("the name is the one of the ticket"))])

        setup.work_on(agent, reviews.item([reviews.inline()]))

        assert setup.company.page_file(KEY) in agent.requests[0].system_prompt

    def test_the_reply_comes_after_the_comments_are_recorded_as_answered(self, setups: Setups, tmp_path: Path) -> None:
        """A Fact written between the posts and the record would leave the comments unanswered in
        the state, and they would be answered twice."""
        store = Store(make_store("file", file_root=tmp_path / "knowledge"))
        setup = setups.make({}, store)
        reviews = Reviews()
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())
        agent = ScriptedAgent([Step(structured=reviews.answers("the name is the one of the ticket"))])

        setup.work_on(agent, reviews.item([reviews.inline()]))

        answered, replied = store.index_of('"inline:1"'), store.index_of("`reply:inline:1@t1`")
        assert (answered, replied) != (-1, -1)
        assert answered < replied


class TestTheVerifyFacts:
    """The key of a verify Fact is the head and the fix loop: two loops of the same head are two
    results, and the same loop that runs again is one."""

    def steps(self) -> List[Step]:
        """A ticket whose self-review asks for a change: the fix writes nothing, so the head stays."""
        return [Plans().plan(), Step(writes=Files().feature()), Plans().rejected("please add a test"), Step(), Plans().approved()]

    def test_two_fix_loops_of_the_same_head_keep_both_results(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent(self.steps()), Items().jira())

        page = TaskPages(setup.company).read(KEY)
        assert len([fact for fact in page.facts() if fact.kind == "verify"]) == 2

    def test_the_same_loop_that_runs_again_writes_its_result_once(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.work_on(ScriptedAgent(self.steps()), Items().jira())

        setup.work_on(ScriptedAgent([Plans().approved()]), Items().jira())

        page = TaskPages(setup.company).read(KEY)
        assert len([fact for fact in page.facts() if fact.kind == "verify"]) == 2


class TestTheWritePointsWithoutContext:
    """The write points that have no context of their own write their Fact through `TaskFacts.of`."""

    def ended(self, kind: WorkKind) -> WorkItem:
        return WorkItem(kind, KEY, WorkSource.JIRA, "acme/app", "Add the feature", pr=PR)

    def test_a_merged_pr_writes_its_fact(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.save(Entries().planned(MAX_USD))

        setup.work_on(ScriptedAgent([]), self.ended(WorkKind.MERGED))

        assert "merged" in Facts().kinds(TaskPages(setup.company).read(KEY))

    def test_a_closed_pr_writes_its_fact(self, setups: Setups) -> None:
        setup = setups.make({})
        setup.save(Entries().planned(MAX_USD))

        setup.work_on(ScriptedAgent([]), self.ended(WorkKind.CLOSED))

        assert "closed" in Facts().kinds(TaskPages(setup.company).read(KEY))

    def test_a_ticket_that_came_back_writes_its_fact(self, setups: Setups) -> None:
        setup = setups.make({"max_usd": RAISED_USD})
        stop = MoneyStop(cap=MAX_USD, resume_to=WorkStatus.PR_OPEN.value)
        entry = WorkEntry(
            key=KEY, source=WorkSource.JIRA, repo="acme/app", title="Add the feature", status=WorkStatus.BLOCKED,
            spent_by_day={WorkDay().today(): 1.0}, money_stop=stop,
        )  # fmt: skip
        setup.save(entry)

        reopened = asyncio.run(BudgetReopen(setup.company).apply())

        assert reopened == [KEY]
        assert "reopened" in Facts().kinds(TaskPages(setup.company).read(KEY))


class TestTheToken:
    """The token of the code host is a secret: the page of a task never holds it."""

    def test_the_page_of_a_task_never_holds_the_token(self, setups: Setups) -> None:
        setup = Opened(setups).make({}, token=TOKEN)

        assert TOKEN not in TaskPages(setup.company).read(KEY).text()


class TestTheRealPath:
    """The real path: a runbook with no `notes` key (the default is off, after the measure of R5) and the loader of the runbooks."""

    RUNBOOK = (
        "version: 1\ncompanies:\n  acme:\n    knowledge:\n      store: file\n      root: {root}\n"
        "    serve:\n      repos: [acme/app]\n      model: claude-test\n      max_usd: 5\n      max_loops: 3\n"
        "      ci_wait_minutes: 1\n      max_review_rounds: 2\n      verify: ['test -f feature.txt']\n"
    )

    def company(self, tmp_path: Path) -> ServeCompany:
        (tmp_path / "runbooks").mkdir()
        (tmp_path / "runbooks" / "acme.yaml").write_text(self.RUNBOOK.format(root=tmp_path / "knowledge"))
        (company,) = ServeCompanies(tmp_path / "runbooks", {}).load()
        return company

    def test_the_page_of_a_task_of_a_real_runbook_is_where_it_belongs(self, setups: Setups, tmp_path: Path) -> None:
        setup = setups.loaded(self.company(tmp_path))
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), Step(structured={"notes": "the notes"})])

        setup.work_on(agent, Items().jira())

        page = Path(setup.company.page_file(KEY))
        assert (page.exists(), page.parts[-4:]) == (True, ("work", "acme", "tasks", "ENG-1.md"))

    def test_a_runbook_with_no_notes_key_makes_no_notes_call(self, setups: Setups, tmp_path: Path) -> None:
        setup = setups.loaded(self.company(tmp_path))
        agent = ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), Step(structured={"notes": "the notes"})])

        setup.work_on(agent, Items().jira())

        notes_calls = [request for request in agent.requests if request.schema.get("properties", {}).get("notes")]
        assert (notes_calls, TaskPages(setup.company).read(KEY).notes()) == ([], "")

    def test_the_round_of_a_real_runbook_names_the_page_in_the_prompt_of_the_agent(self, setups: Setups, tmp_path: Path) -> None:
        setup = setups.loaded(self.company(tmp_path))
        reviews = Reviews()
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())
        agent = ScriptedAgent([Step(structured=reviews.answers("the name is the one of the ticket"))])

        setup.work_on(agent, reviews.item([reviews.inline()]))

        assert setup.company.page_file(KEY) in agent.requests[0].system_prompt


class NotesFail(ScriptedAgent):
    """Plays its steps, then fails the next call (the Notes) the way a full disk fails the booking of its spend."""

    async def ask(self, request: AgentRequest) -> AgentAnswer:
        if not self.steps:
            raise OSError("No space left on device")
        return await super().ask(request)


class TestTheReviewOfThePage:
    """What a review of the PR found (2026-11-21), each one written to fail on the code before its fix."""

    def ticket(self, setup: Setup) -> None:
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

    def answers(self, reply: str, comment: str = "1") -> Step:
        return Step(structured={"answers": [{"id": f"inline:{comment}", "changed": False, "reply": reply}]})

    def test_a_page_a_person_broke_does_not_stop_the_next_review_round(self, setups: Setups, tmp_path: Path) -> None:
        setup = setups.make({}, Broken(make_store("file", file_root=tmp_path / "knowledge"), "`cost:ticket`"))
        self.ticket(setup)
        reviews = Reviews()

        setup.work_on(ScriptedAgent([self.answers("done")]), reviews.item([reviews.inline()]))

        github = setup.sources.github_side
        assert (setup.entry(KEY).status, github.drafts, len(github.replies)) == (WorkStatus.DONE, [], 1)

    def test_a_page_that_is_not_utf8_still_moves_the_merged_ticket_to_done(self, setups: Setups) -> None:
        setup = setups.make({"status": {"started": "In Progress", "review": "In Review", "done": "Done"}})
        self.ticket(setup)
        Path(setup.company.page_file(KEY)).write_bytes(b"## Facts\n\n- `taken:taken` caf\xe9\n")

        setup.work_on(ScriptedAgent([]), WorkItem(WorkKind.MERGED, KEY, WorkSource.JIRA, "acme/app", "Add the feature", pr=PR))

        assert (setup.entry(KEY).status, (KEY, "Done") in setup.sources.tracker_side.moves) == (WorkStatus.MERGED, True)

    def test_an_edited_comment_writes_the_new_comment_and_the_new_reply(self, setups: Setups) -> None:
        """briar answers a comment again when its author edits it, so the page must hold the edit too."""
        setup = setups.make({})
        self.ticket(setup)
        reviews = Reviews()
        first = reviews.inline()

        setup.work_on(ScriptedAgent([self.answers("first reply")]), reviews.item([first]))
        setup.work_on(ScriptedAgent([self.answers("second reply")]), reviews.item([replace(first, body="now rename it", updated_at="t2")]))

        found = Facts().of(TaskPages(setup.company).read(KEY))
        assert found["comment"] == ["inline comment by ann: say why in feature.txt", "inline comment by ann: now rename it"]
        assert found["reply"] == ["first reply", "second reply"]

    def test_two_rounds_that_change_no_code_keep_both_costs(self, setups: Setups) -> None:
        """Both rounds start at the same head: a cost keyed on the head kept only the first one."""
        setup = setups.make({})
        self.ticket(setup)
        reviews = Reviews()
        second = replace(reviews.inline(), id="2", thread="2", body="and another")

        setup.work_on(ScriptedAgent([self.answers("r1")]), reviews.item([reviews.inline()]))
        setup.work_on(ScriptedAgent([self.answers("r2", "2")]), reviews.item([second]))

        assert Facts().of(TaskPages(setup.company).read(KEY))["cost"] == [f"US$ {cost:.2f} of {MAX_USD} spent by the task so far" for cost in COSTS]

    def test_a_pr_with_no_ci_is_not_written_as_a_ci_that_passed(self, setups: Setups) -> None:
        setup = setups.make({"ci_grace_seconds": 0})
        setup = replace(setup, sources=replace(setup.sources, github_side=replace(setup.sources.github_side, ci=[CiState.NONE])))

        self.ticket(setup)

        assert Facts().of(TaskPages(setup.company).read(KEY))["ci"] == [f"no CI ran on the PR #{PR} (none started within ci_grace_seconds)"]

    def test_a_ticket_that_needs_no_code_writes_why_it_stopped(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(ScriptedAgent([Plans().no_code()]), Items().jira())

        found = Facts().of(TaskPages(setup.company).read(KEY))
        assert found["stopped"] == ["the plan needs no code change in acme/app; nothing was posted on the ticket"]

    def test_a_round_stopped_for_money_writes_why_it_stopped(self, setups: Setups) -> None:
        setup = setups.make({"max_usd": NOTES_CAP})
        self.ticket(setup)
        reviews = Reviews()

        setup.work_on(ScriptedAgent([self.answers("done")]), reviews.item([reviews.inline()]))

        (stopped,) = Facts().of(TaskPages(setup.company).read(KEY))["stopped"]
        assert (setup.entry(KEY).status, stopped.startswith("briar stopped: the task spent its max_usd of 0.35.")) == (WorkStatus.BLOCKED, True)

    def test_a_failed_notes_call_leaves_the_task_done_and_the_pr_ready(self, setups: Setups) -> None:
        setup = setups.make({"notes": True})

        setup.work_on(NotesFail([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        github = setup.sources.github_side
        assert (setup.entry(KEY).status, github.readies, github.drafts) == (WorkStatus.DONE, [PR], [])
        assert Facts().of(TaskPages(setup.company).read(KEY))["cost"] == [f"US$ 0.30 of {MAX_USD} spent by the task so far"]

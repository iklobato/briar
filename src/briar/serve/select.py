"""How `briar serve` chooses its next tickets (spec 008, decisions N1 to N11 and Q1 to Q4 in
`docs/decisions/2026-11-12-briar-2-work-selection.md` and `...-work-selection-pr1.md`):
`Workable` drops the tickets nobody can work now, `Score` ranks the rest with fixed weights,
and `Selector` lets the model pick among the 5 best, with the repo and a reason for each."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Callable, ClassVar, Dict, List, Mapping, Sequence, Tuple

from briar.extract._tracker import Ticket
from briar.iac.runbook.models import ServeSettings
from briar.serve.agent import Access, AgentAnswer, AgentRequest, AgentRunner
from briar.serve.task import Prompts
from briar.serve.work import ServeCompany, WorkItem


class Workable:
    """N6: a ticket in a not-workable status of its Jira project, or with an open blocker,
    is not a candidate. The open PR/MR of someone else waits for PR 4."""

    def __init__(self, settings: ServeSettings) -> None:
        self._settings = settings

    def keep(self, items: Sequence[WorkItem]) -> List[WorkItem]:
        return [item for item in items if self._workable(item)]

    def _workable(self, item: WorkItem) -> bool:
        ticket = item.ticket
        if ticket is None:
            return True
        project = self._settings.jira.get(item.key.split("-", 1)[0])
        not_workable = project.not_workable if project is not None else []
        return ticket.status not in not_workable and ticket.open_blockers == 0


class Score:
    """Q1: fixed weights, documented in `agents/serve.md`. Higher is first; ties: oldest."""

    # Days left to the due date -> points; past the last bound, LATER.
    DUE: ClassVar[Tuple[Tuple[int, int], ...]] = ((-1, 50), (7, 40), (30, 20))
    LATER = 5
    PER_UNBLOCK = 10
    UNBLOCK_CAP = 30
    PRIORITY: ClassVar[Mapping[str, int]] = {"highest": 20, "high": 10, "low": -5, "lowest": -10}
    LABEL = 10
    STARTED = 30
    # `YYYY-MM-DD`: Jira gives the date alone, GitHub a full timestamp.
    DATE_LENGTH = len("2026-01-01")

    def __init__(self, today: date, priority_labels: Sequence[str]) -> None:
        self._today = today
        self._priority_labels = priority_labels

    def of(self, ticket: Ticket) -> int:
        label = self.LABEL if any(name in self._priority_labels for name in ticket.labels) else 0
        started = self.STARTED if ticket.in_progress else 0
        unblocks = min(ticket.unblocks * self.PER_UNBLOCK, self.UNBLOCK_CAP)
        return self._due(ticket.due_date) + unblocks + self.PRIORITY.get(ticket.priority.lower(), 0) + label + started

    def rank(self, items: Sequence[WorkItem]) -> List[WorkItem]:
        return sorted(items, key=self._order)

    def _order(self, item: WorkItem) -> Tuple[int, str]:
        return (-self.of(item.ticket) if item.ticket is not None else 0, item.created_at)

    def _due(self, due: str) -> int:
        if not due:
            return 0
        left = (date.fromisoformat(due[: self.DATE_LENGTH]) - self._today).days
        return next((points for bound, points in self.DUE if left <= bound), self.LATER)


class Selector:
    """N2, N4, N5, N9: one model call over the 5 best; each pick names its ticket, its repo
    (one of the allowed repos of that ticket) and why. The call is paid from the budget of the
    tasks it starts."""

    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    SHORTLIST = 5
    NONE = "none"
    SCHEMA: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}, "repo": {"type": "string"}, "reason": {"type": "string"}},
                    "required": ["key", "repo", "reason"],
                },
            }
        },
        "required": ["picks"],
    }

    def __init__(self, agent: AgentRunner, company: ServeCompany) -> None:
        self._agent = agent
        self._company = company

    async def pick(self, ranked: Sequence[WorkItem], free: int) -> List[WorkItem]:
        if free <= 0 or not ranked:
            return []
        shortlist = list(ranked[: self.SHORTLIST])
        system = Prompts().text("select", {"company": self._company.name, "slots": str(free)})
        request = AgentRequest(Shortlist(shortlist).text(), system, self._company.root, self._company.settings.max_usd, self.SCHEMA, Access.READ)
        answer = await self._agent.ask(request)
        if answer.failed:
            self.LOG.error("serve: the selector failed: %s", answer.reason)
            return []
        return Picks(shortlist, answer).items(free)


class Shortlist:
    """The tickets the model reads: one line each, with the signals and the allowed repos."""

    def __init__(self, items: Sequence[WorkItem]) -> None:
        self._items = items

    def text(self) -> str:
        return "Tickets, best first:\n" + "".join(f"\n- {self._line(item)}" for item in self._items)

    def _line(self, item: WorkItem) -> str:
        ticket = item.ticket
        signals = f"status {ticket.status}, due {ticket.due_date or '-'}, unblocks {ticket.unblocks}, epic {ticket.epic or '-'}" if ticket else ""
        return f"{item.key}: {item.title} ({signals}; repos: {', '.join(item.repos or (item.repo,))})"


class Picks:
    """The structured answer of the selector, kept only where it names a ticket of the
    shortlist and one of its allowed repos; the cost is split over the picks."""

    def __init__(self, shortlist: Sequence[WorkItem], answer: AgentAnswer) -> None:
        self._shortlist = shortlist
        self._answer = answer

    def items(self, free: int) -> List[WorkItem]:
        by_key: Dict[str, WorkItem] = {item.key: item for item in self._shortlist}
        valid = [(by_key[pick["key"]], pick) for pick in self._answer.structured.get("picks") or [] if self._valid(by_key, pick)][:free]
        share = self._answer.cost_usd / len(valid) if valid else 0.0
        return [replace(item, repo=pick["repo"], reason=pick["reason"], selection_cost=share) for item, pick in valid]

    def _valid(self, by_key: Mapping[str, WorkItem], pick: Mapping[str, str]) -> bool:
        item = by_key.get(str(pick.get("key")))
        return item is not None and pick.get("repo") != Selector.NONE and pick.get("repo") in (item.repos or (item.repo,))


@dataclass(frozen=True)
class Choice:
    """The tickets one poll starts: the workable ones, ranked, then picked by the model for
    the free slots. The model is asked again only when the shortlist or the free slots
    change, so a poll with nothing new costs nothing."""

    selector: Selector
    company: ServeCompany
    today: Callable[[], date] = date.today
    # ponytail: one fingerprint per company; a failed model call is retried only when the
    # shortlist or the free slots change.
    asked: List[Tuple[str, ...]] = field(default_factory=list)

    async def choose(self, tickets: Sequence[WorkItem], free: int) -> List[WorkItem]:
        workable = Workable(self.company.settings).keep(tickets)
        ranked = Score(self.today(), self.company.settings.priority_labels).rank(workable)
        fingerprint = (str(free), *(item.key for item in ranked[: Selector.SHORTLIST]))
        if free <= 0 or not ranked or fingerprint in self.asked:
            return []
        self.asked[:] = [fingerprint]
        return await self.selector.pick(ranked, free)


__all__ = ["Choice", "Picks", "Score", "Selector", "Shortlist", "Workable"]

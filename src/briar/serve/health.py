"""Is a login of a company really dead? (WP1 of the production plan, decisions P13 and D9.)

`LoginRejected` is raised for every HTTP answer that is not a network error: a 403 or 404 of one resource, a 503 of an outage and
a 401 all land in it, so its type cannot say that a login died. The read-only "who am I" of the source can: with an HTTP 401 the
login itself is refused. Only that stops `serve`. An unreachable source, any other status and any other error do not: the task
is blocked, or the poll is logged, as before. The chat is not asked: it is optional (decision B3)."""

from __future__ import annotations

from typing import Any, Callable, ClassVar, List, Optional

from briar.auth.check import LoginRejected
from briar.errors import CliError
from briar.serve.pause import DeadLogin
from briar.serve.work import ServeCompany


class LoginWatch:
    """Asks "who am I" of the code host and of Jira of one company, through the sources a task or a poll works with."""

    UNAUTHORIZED: ClassVar[int] = 401

    def __init__(self, company: ServeCompany, sources: Any) -> None:
        self._company = company
        self._sources = sources

    def dead(self) -> Optional[DeadLogin]:
        """The first login of the company that is refused with a 401, or None. It blocks (the clients do): call it in a thread."""
        for build in self._builders():
            found = self._asked(build)
            if found is not None:
                return found
        return None

    def _builders(self) -> List[Callable[[], Any]]:
        builders = [self._sources.code_host]
        if self._company.settings.jira:
            builders.append(self._sources.jira)
        return builders

    def _asked(self, build: Callable[[], Any]) -> Optional[DeadLogin]:
        try:
            probe = build()
        except CliError:
            return None
        try:
            probe.who_am_i()
        except LoginRejected as refused:
            return self._dead(probe.login_source, refused) if refused.status == self.UNAUTHORIZED else None
        except CliError:
            return None
        return None

    def _dead(self, source: str, refused: LoginRejected) -> DeadLogin:
        return DeadLogin(source, self._company.name, str(refused), self._company.logins.source(source).fix())


__all__ = ["LoginWatch"]

"""A GitLab REST surface with no network, for the tests of `GitlabProvider` and `GitlabHost`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class FakeGitlabApi:
    """The `GitlabRest` surface: each path gives its answer (an exception is raised); a
    "POST path" or "PUT path" key answers a write. `calls` records the path and the query
    (or the body of a write) of every call, in order; a write path starts with its verb."""

    answers: Mapping[str, Any]
    calls: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)

    def get(self, path: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        found: Dict[str, Any] = self._answer(path, query)
        return found

    def pages(self, path: str, query: Optional[Dict[str, Any]] = None, cap: int = 0) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = self._answer(path, query)
        return found[:cap] if cap else found

    def text(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        found: str = self._answer(path, query)
        return found

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        found: Dict[str, Any] = self._answer(f"POST {path}", body)
        return found

    def put(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        found: Dict[str, Any] = self._answer(f"PUT {path}", body)
        return found

    def _answer(self, path: str, query: Optional[Dict[str, Any]]) -> Any:
        self.calls.append((path, dict(query or {})))
        found = self.answers[path]
        if isinstance(found, BaseException):
            raise found
        return found

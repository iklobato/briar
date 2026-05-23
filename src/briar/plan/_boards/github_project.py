"""GitHub Projects (v2) board reader.

Projects v2 is a GraphQL-only product, so we don't reuse PyGithub's
v3 REST surface. Instead we POST GraphQL directly through the same
$GITHUB_TOKEN every other GH extractor consumes.

Two URL shapes:

  * `https://github.com/orgs/<org>/projects/<n>`
  * `https://github.com/users/<user>/projects/<n>`

A project item is either a draft (title only) or a linked
issue / PR. For linked issues we pull the body too so the synthesis
step has a real description; drafts get an empty body and the
synthesiser falls back to the title."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

from briar.errors import CliError
from briar.plan._board import BoardReader, BoardRef
from briar.plan._models import PlanCard


log = logging.getLogger(__name__)


_URL_RE = re.compile(
    r"^https?://github\.com/(?P<scope>orgs|users)/(?P<owner>[^/]+)/projects/(?P<number>\d+)",
    re.IGNORECASE,
)
_DEP_RE = re.compile(r"(?:depends on|blocked by|requires|after)\s*[:\-]?\s*#(\d+)", re.IGNORECASE)
_GH_GRAPHQL_URL = "https://api.github.com/graphql"


class GithubProjectBoardReader(BoardReader):
    kind = "github-project"

    def matches(self, url: str) -> bool:
        return bool(_URL_RE.match(url or ""))

    def parse(self, url: str) -> BoardRef:
        m = _URL_RE.match((url or "").strip())
        if not m:
            raise CliError(f"github project URL not recognised: {url!r}")
        scope = m.group("scope")
        owner = m.group("owner")
        number = m.group("number")
        return BoardRef(
            tracker="github-project",
            project=f"{owner}/#{number}",
            url=url,
            owner=owner,
            extras=(("scope", scope), ("number", number)),
        )

    def fetch(self, ref: BoardRef, *, company: str, max_cards: int) -> List[PlanCard]:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            raise CliError("GITHUB_TOKEN not set — required to read GitHub Projects v2")
        scope = ref.extra("scope") or "orgs"
        number = int(ref.extra("number") or "0")
        items = self._fetch_items(token, scope=scope, owner=ref.owner, number=number, limit=max_cards)
        cards: List[PlanCard] = []
        for item in items:
            cards.append(self._to_card(item))
        return cards

    @classmethod
    def _fetch_items(cls, token: str, *, scope: str, owner: str, number: int, limit: int) -> List[Dict[str, Any]]:
        root = "organization" if scope == "orgs" else "user"
        query = """
        query($login: String!, $number: Int!, $first: Int!) {
          %s(login: $login) {
            projectV2(number: $number) {
              title
              items(first: $first) {
                nodes {
                  id
                  type
                  fieldValues(first: 20) {
                    nodes {
                      __typename
                      ... on ProjectV2ItemFieldSingleSelectValue { field { ... on ProjectV2SingleSelectField { name } } name }
                      ... on ProjectV2ItemFieldTextValue { field { ... on ProjectV2FieldCommon { name } } text }
                    }
                  }
                  content {
                    __typename
                    ... on DraftIssue { title body }
                    ... on Issue { number title body url state repository { nameWithOwner } }
                    ... on PullRequest { number title body url state repository { nameWithOwner } }
                  }
                }
              }
            }
          }
        }
        """ % root
        first = max(1, min(limit, 100))
        payload = {
            "query": query,
            "variables": {"login": owner, "number": number, "first": first},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _GH_GRAPHQL_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "briar-cli",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise CliError(f"github graphql HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CliError(f"github graphql request failed: {exc.reason}") from exc

        if "errors" in (data or {}):
            raise CliError(f"github graphql errors: {data['errors']}")
        node = (((data or {}).get("data") or {}).get(root) or {}).get("projectV2")
        if not node:
            raise CliError(f"github project not found for {owner!r} #{number}")
        items = ((node.get("items") or {}).get("nodes")) or []
        return [i for i in items if isinstance(i, dict)]

    @staticmethod
    def _to_card(item: Dict[str, Any]) -> PlanCard:
        content = item.get("content") or {}
        kind = content.get("__typename") or ""
        if kind == "DraftIssue":
            title = str(content.get("title") or "")
            body = str(content.get("body") or "")
            key = f"draft:{(title or item.get('id') or '')[:32]}"
            url = ""
            repo = ""
        else:
            number = content.get("number")
            title = str(content.get("title") or "")
            body = str(content.get("body") or "")
            repo = str((content.get("repository") or {}).get("nameWithOwner") or "")
            url = str(content.get("url") or "")
            key = f"{repo}#{number}" if repo and number else f"#{number}" if number else "draft"

        explicit_deps: List[str] = []
        for match in _DEP_RE.finditer(body or ""):
            ref_key = f"{repo}#{match.group(1)}" if repo else f"#{match.group(1)}"
            if ref_key not in explicit_deps and ref_key != key:
                explicit_deps.append(ref_key)

        status_chip, labels = _field_values(item.get("fieldValues"))
        sources = [f"github:{url}"] if url else []
        notes_bits: List[str] = []
        if status_chip:
            notes_bits.append(f"status={status_chip}")
        if labels:
            notes_bits.append("labels=" + ",".join(labels))
        return PlanCard(
            key=key,
            title=title,
            url=url,
            tracker="github-project",
            summary=(body or "")[:1500],
            depends_on=explicit_deps,
            sources=sources,
            notes="; ".join(notes_bits),
        )


def _field_values(node: Any) -> Tuple[str, List[str]]:
    """Pull `Status` chip + label-ish single-select chips out of the
    GraphQL `fieldValues` blob. Best-effort — schema varies per project."""
    if not isinstance(node, dict):
        return "", []
    rows = node.get("nodes") or []
    status = ""
    labels: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        field_name = ((row.get("field") or {}).get("name") or "").strip().lower()
        value = row.get("name") or row.get("text") or ""
        if not value:
            continue
        if field_name == "status":
            status = str(value)
        elif field_name in ("labels", "label", "type"):
            labels.append(str(value))
    return status, labels

"""Best-effort JSON extraction from LLM responses.

Models wrap JSON in code fences, prepend prose, or append trailing text.
`extract_json` peels the common wrappers and returns the parsed object
when it is dict-shaped, else None. Shared by the plan selector /
synthesiser / writeback so the fence-and-brace handling lives in exactly
one place (it had drifted into two diverging copies)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object out of `text`, tolerating code fences and
    surrounding prose. Returns the dict, or None if nothing dict-shaped
    can be recovered."""
    if not text:
        return None
    candidate = text.strip()
    fenced = _FENCE_RE.match(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        try:
            data = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None

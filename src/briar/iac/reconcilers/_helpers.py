"""Helpers shared by concrete reconcilers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional


def read_text_field(
    spec: Dict[str, Any],
    inline_key: str,
    file_key: str,
) -> Optional[str]:
    """Return the value of `inline_key`, falling back to reading from
    `spec[file_key]` if that's a path. Inline value wins on conflict."""
    inline = spec.get(inline_key)
    if inline is not None:
        return inline
    file_path = spec.get(file_key)
    if not file_path:
        return None
    return Path(file_path).read_text()

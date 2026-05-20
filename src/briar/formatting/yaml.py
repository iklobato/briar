"""YAML formatter — backed by PyYAML.

`safe_dump` covers the shapes the Briar API returns. Block style,
unsorted (to preserve API ordering), Unicode-safe."""

from __future__ import annotations

from typing import Any, List, Optional

import yaml


def to_yaml(value: Any) -> str:
    """Public entry point shared with tests."""
    return yaml.safe_dump(
        value,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


class FormatYaml:
    name = "yaml"

    def render(
        self,
        payload: Any,
        columns: Optional[List[str]] = None,
    ) -> None:
        print(to_yaml(payload).rstrip("\n"))

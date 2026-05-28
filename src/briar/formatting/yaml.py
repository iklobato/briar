"""YAML formatter — backed by PyYAML.

`safe_dump` covers the shapes the extractors produce. Block style,
unsorted (to preserve insertion order), Unicode-safe."""

from __future__ import annotations

from typing import Any, Sequence

import yaml

from briar.formatting.base import Formatter


class FormatYaml(Formatter):
    name = "yaml"

    def render(
        self,
        payload: Any,
        columns: Sequence[str] = (),
    ) -> None:
        print(self.to_yaml(payload).rstrip("\n"))

    @staticmethod
    def to_yaml(value: Any) -> str:
        """Serialise to a YAML string. Exposed as a static method so
        tests (or callers that want the string, not a print) can reuse
        the same dump options."""
        return yaml.safe_dump(
            value,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

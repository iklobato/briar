"""Declarative config file — Pydantic-backed load + section access.

The on-disk shape is JSON. `ConfigSpec` (Pydantic) does all validation
at load time; reconcilers continue to receive `Dict[str, Any]` for
each spec, so this is a non-breaking swap of the validation layer.

Authors who prefer YAML can convert with PyYAML:
    python -c "import yaml,json,sys; json.dump(yaml.safe_load(sys.stdin), sys.stdout)" \\
        < config.yaml > config.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from pydantic import ValidationError

from briar.errors import ConfigError
from briar.iac.models import ConfigSpec


class ConfigFile:
    """Adapter that loads + validates a JSON config, then exposes each
    section as a list of dicts (the shape reconcilers already expect)."""

    def __init__(self, spec: ConfigSpec) -> None:
        self._spec = spec

    @property
    def spec(self) -> ConfigSpec:
        return self._spec

    @property
    def version(self) -> int:
        return self._spec.version

    @classmethod
    def load(cls, path: Path) -> "ConfigFile":
        try:
            raw = path.read_text()
        except FileNotFoundError as exc:
            raise ConfigError(f"config not found: {path}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: invalid JSON — {exc}") from exc
        if type(data) is not dict:
            raise ConfigError(f"{path}: top-level must be a JSON object")
        try:
            spec = ConfigSpec.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(
                f"{path}: invalid config\n{cls._pretty_errors(exc)}"
            ) from exc
        return cls(spec)

    @staticmethod
    def _pretty_errors(exc: ValidationError) -> str:
        """One line per error: `path.to.field: message`."""
        lines = []
        for err in exc.errors():
            location = ".".join(str(part) for part in err["loc"])
            lines.append(f"  {location}: {err['msg']}")
        return "\n".join(lines)

    def section(self, kind: str) -> List[Dict[str, Any]]:
        """Return a section as a list of dicts.

        Dict dispatch — adding a new section type is one entry."""
        getters = {
            "llm_providers": lambda: self._spec.llm_providers,
            "llm_models":    lambda: self._spec.llm_models,
            "sources":       lambda: self._spec.sources,
            "tools":         lambda: self._spec.tools,
            "agents":        lambda: self._spec.agents,
            "workflows":     lambda: self._spec.workflows,
            "triggers":      lambda: self._spec.triggers,
        }
        getter = getters.get(kind)
        if getter is None:
            raise ConfigError(f"unknown config section: {kind!r}")
        return [m.model_dump(exclude_none=False) for m in getter()]

    # Backwards-compatible attribute access — older code paths read
    # `cfg.agents` etc. directly. The lists returned here are the typed
    # spec objects; section() returns plain dicts for reconcilers.

    @property
    def llm_providers(self) -> List[Any]:
        return list(self._spec.llm_providers)

    @property
    def llm_models(self) -> List[Any]:
        return list(self._spec.llm_models)

    @property
    def sources(self) -> List[Any]:
        return list(self._spec.sources)

    @property
    def tools(self) -> List[Any]:
        return list(self._spec.tools)

    @property
    def agents(self) -> List[Any]:
        return list(self._spec.agents)

    @property
    def workflows(self) -> List[Any]:
        return list(self._spec.workflows)

    @property
    def triggers(self) -> List[Any]:
        return list(self._spec.triggers)



"""Runbook YAML writer — the inverse of `RunbookLoader.load`.

`save_runbook_file` serializes a validated `RunbookFile` back to YAML so a
caller can write config that `load_runbook_file` reads. Three guarantees:

  1. **Round-trip safety.** The serialized text is re-parsed through the schema
     in memory *before* any disk write — an unserializable model never reaches
     disk and never leaves a half-written file behind.
  2. **Atomic write.** Write to a sibling temp file, then `os.replace` — a
     concurrent reader sees either the old file or the new one, never a torn one.
  3. **Secret hygiene.** The fields that hold env-var *names* (any `*_env`
     config key) must stay names, never literal secrets. A literal
     leaking in through the write path is rejected with a locator-aware error —
     the same env-var-name indirection `RunbookLoader` relies on, enforced on write.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict

import yaml

from briar.errors import ConfigError
from briar.iac.runbook.models import RunbookFile

log = logging.getLogger(__name__)

# An env-var NAME: an uppercase identifier. Anything else in an env-var-bearing
# field is almost certainly a literal secret that must not be persisted to YAML.
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")


def _reject_literal(field: str, value: str) -> None:
    """Fail closed when an env-var-name field holds something that isn't an
    env-var name — the signature of a leaked literal secret."""
    if not _ENV_NAME.fullmatch(value):
        raise ConfigError(
            f"runbook field {field!r} must be an env-var NAME (e.g. GITHUB_TOKEN), "
            f"not a literal value — got {value!r}. Secrets stay in the environment, "
            "never in the runbook."
        )


def _guard_secrets(rb: RunbookFile) -> None:
    """Walk the model and enforce env-var-name indirection on every field that
    carries one: any config key ending `_env`."""
    for company_name, company in rb.companies.items():
        loc = f"companies.{company_name}"
        for key, value in (company.knowledge.config or {}).items():
            if key.endswith("_env"):
                _reject_literal(f"{loc}.knowledge.config.{key}", str(value))


def _serialize(rb: RunbookFile) -> str:
    """Dump to YAML and prove it round-trips through the schema in memory.

    `exclude_defaults` keeps the file terse — empty optionals (`""`, `[]`,
    sentinel models) are dropped rather than written back as noise."""
    data: Dict[str, Any] = rb.model_dump(mode="json", exclude_defaults=True)
    text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    try:
        RunbookFile.model_validate(yaml.safe_load(text))
    except Exception as exc:  # noqa: BLE001 — re-raise as the CLI-visible error
        raise ConfigError(f"runbook serialization did not round-trip: {exc}") from exc
    return text


def save_runbook_file(path: Path | str, rb: RunbookFile) -> Path:
    """Validate, then atomically write `rb` to `path` as YAML.

    Returns the resolved path. Raises `ConfigError` if the model carries a
    literal secret in an env-var-name field or fails to round-trip — in both
    cases nothing is written to disk."""
    path = Path(path)
    _guard_secrets(rb)
    text = _serialize(rb)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        log.exception("runbook-save: write failed path=%s", path)
        tmp.unlink(missing_ok=True)
        raise
    log.info("runbook-save: wrote path=%s companies=%d bytes=%d", path, len(rb.companies), len(text))
    return path


# Symmetry with `load_runbook_file` in executor.py.
__all__ = ["save_runbook_file"]

"""`briar.service` — presentation-free operations shared by every front-end.

The CLI command handlers, the MCP server, `briar chat`, and the read-write
dashboard all drive briar through this layer instead of duplicating logic.
Functions take keyword args (never `argparse.Namespace`) and return plain
dicts/dataclasses; mutating operations are gated through `GateMode` /
`GateResult` so the dry-run-then-confirm policy lives in one place.
"""

from __future__ import annotations

from briar.service._gating import GateMode, GateResult

__all__ = ["GateMode", "GateResult"]

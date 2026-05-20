"""Allow `python -m briar` to drive the CLI without an installed entry point."""

from __future__ import annotations

import sys

from briar.cli import main


if __name__ == "__main__":
    sys.exit(main())

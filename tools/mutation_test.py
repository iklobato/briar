#!/usr/bin/env python3
"""Manual mutation testing demo for briar leaf modules.

Apply each mutation, run focused tests, verify the suite catches it
(killed=tests failed) or surfaces a gap (survived=tests still pass)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


MUTATIONS = [
    # (label, file, old_string, new_string)
    (
        "error_policy:wait>0 → wait>=0 (would call sleep(0))",
        "src/briar/error_policy.py",
        "if self.wait_seconds > 0:",
        "if self.wait_seconds >= 0:",
    ),
    (
        "error_policy:max_attempts<1 → <=1 (rejects 1 as well)",
        "src/briar/error_policy.py",
        "if max_attempts < 1:",
        "if max_attempts <= 1:",
    ),
    (
        "pagination:isinstance(page, list) → tuple",
        "src/briar/pagination.py",
        "if isinstance(page, list):",
        "if isinstance(page, tuple):",
    ),
    (
        "decorators:except Exception → except ValueError",
        "src/briar/decorators.py",
        "except Exception:",
        "except ValueError:",
    ),
    (
        "errors:HTML detection 9 chars → 8 chars",
        "src/briar/errors.py",
        "stripped[:9].lower()",
        "stripped[:8].lower()",
    ),
    (
        "env_vars:str.upper → str.lower in for_company",
        "src/briar/env_vars.py",
        'normalised = company.upper().replace("-", "_")',
        'normalised = company.lower().replace("-", "_")',
    ),
    (
        "log_context:always-empty filter (return True early)",
        "src/briar/log_context.py",
        "ctx = _CTX.get()\n        if not ctx:",
        "ctx = {}\n        if not ctx:",
    ),
]


def apply_and_test(file_rel: str, old: str, new: str) -> bool:
    """Return True if the mutation was KILLED by the test suite."""
    path = ROOT / file_rel
    original = path.read_text()
    if old not in original:
        return False  # mutation didn't apply
    mutated = original.replace(old, new, 1)
    path.write_text(mutated)
    try:
        result = subprocess.run(
            [
                "uv", "run", "pytest", "-x", "--tb=no", "-q",
                "tests/unit/test_error_policy.py",
                "tests/unit/test_pagination.py",
                "tests/unit/test_decorators.py",
                "tests/unit/test_errors.py",
                "tests/unit/test_env_vars.py",
                "tests/unit/test_log_context.py",
            ],
            cwd=str(ROOT),
            capture_output=True,
            timeout=60,
        )
        killed = result.returncode != 0
        return killed
    finally:
        path.write_text(original)


def main() -> int:
    killed = 0
    survived = 0
    for label, file_rel, old, new in MUTATIONS:
        was_killed = apply_and_test(file_rel, old, new)
        verdict = "KILLED  " if was_killed else "SURVIVED"
        print(f"  [{verdict}] {label}")
        if was_killed:
            killed += 1
        else:
            survived += 1
    total = killed + survived
    score = (killed / total * 100) if total else 0
    print()
    print(f"Mutation score: {killed}/{total} killed ({score:.0f}%)")
    return 0 if survived == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

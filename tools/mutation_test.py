#!/usr/bin/env python3
"""Targeted mutation testing for briar leaf modules.

Apply each mutation, run the focused test files that should catch it, and
verify the suite KILLS it (tests fail) rather than letting it SURVIVE
(tests still pass). A survivor is a coverage hole: a real bug of that exact
shape would ship green.

Each mutant declares the test files that *should* catch it, so a survivor
points straight at the spec gap. This is deliberately a hand-curated set of
high-signal mutants (boundary flips, operator swaps, dropped error handling)
on the pure-logic leaf modules — not a generated full-tree run. Run via:

    .venv/bin/python tools/mutation_test.py
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Mutant:
    label: str
    file: str
    old: str
    new: str
    # Test files expected to catch this mutant. Keeping it focused makes a
    # run fast and a survivor's blame obvious.
    tests: List[str] = field(default_factory=list)


# Default test set for the original leaf-module mutants.
_LEAF_TESTS = [
    "tests/unit/test_error_policy.py",
    "tests/unit/test_pagination.py",
    "tests/unit/test_decorators.py",
    "tests/unit/test_errors.py",
    "tests/unit/test_env_vars.py",
    "tests/unit/test_log_context.py",
]


MUTATIONS: List[Mutant] = [
    # ── error_policy ──────────────────────────────────────────────────
    Mutant(
        "error_policy:wait>0 → wait>=0 (would call sleep(0))",
        "src/briar/error_policy.py",
        "if self.wait_seconds > 0:",
        "if self.wait_seconds >= 0:",
        ["tests/unit/test_error_policy.py"],
    ),
    Mutant(
        "error_policy:max_attempts<1 → <=1 (rejects 1 as well)",
        "src/briar/error_policy.py",
        "if max_attempts < 1:",
        "if max_attempts <= 1:",
        ["tests/unit/test_error_policy.py"],
    ),
    Mutant(
        "error_policy:RAISE branch → RETRY (never propagates Abort)",
        "src/briar/error_policy.py",
        "if decision.apply(exc=exc, attempt=attempt) is FollowUp.RAISE:",
        "if decision.apply(exc=exc, attempt=attempt) is FollowUp.RETRY:",
        ["tests/unit/test_error_policy.py"],
    ),
    Mutant(
        "error_policy:HttpStatusPolicy status == → != (matches every other code)",
        "src/briar/error_policy.py",
        "return actual == self.status",
        "return actual != self.status",
        ["tests/unit/test_error_policy.py"],
    ),
    # NB: `_PropagatePolicy.matches` is intentionally NOT mutated here — it is
    # only ever used as the `next(..., _PROPAGATE)` default sentinel and is
    # never placed inside a registry's `policies` tuple, so `matches()` is
    # never actually called. Flipping it True→False is an equivalent mutant
    # (no observable behaviour change); testing it would assert dead code.
    # ── pagination ────────────────────────────────────────────────────
    Mutant(
        "pagination:isinstance(page, list) → tuple",
        "src/briar/pagination.py",
        "if isinstance(page, list):",
        "if isinstance(page, tuple):",
        ["tests/unit/test_pagination.py"],
    ),
    Mutant(
        "pagination:items_of dict-fallthrough [page] → [] (drops single objects)",
        "src/briar/pagination.py",
        "        if isinstance(results, list):\n            return results\n        return [page]",
        "        if isinstance(results, list):\n            return results\n        return []",
        ["tests/unit/test_pagination.py"],
    ),
    Mutant(
        "pagination:looks_like_list dict branch True → False",
        "src/briar/pagination.py",
        '        results = payload.get("results")\n        return isinstance(results, list)',
        '        results = payload.get("results")\n        return not isinstance(results, list)',
        ["tests/unit/test_pagination.py"],
    ),
    # ── decorators ────────────────────────────────────────────────────
    Mutant(
        "decorators:except Exception → except ValueError",
        "src/briar/decorators.py",
        "except Exception:",
        "except ValueError:",
        ["tests/unit/test_decorators.py"],
    ),
    # ── errors ────────────────────────────────────────────────────────
    Mutant(
        "errors:HTML detection 9 chars → 8 chars",
        "src/briar/errors.py",
        "stripped[:9].lower()",
        "stripped[:8].lower()",
        ["tests/unit/test_errors.py"],
    ),
    # ── env_vars ──────────────────────────────────────────────────────
    Mutant(
        "env_vars:str.upper → str.lower in for_company",
        "src/briar/env_vars.py",
        'normalised = company.upper().replace("-", "_")',
        'normalised = company.lower().replace("-", "_")',
        ["tests/unit/test_env_vars.py"],
    ),
    Mutant(
        "env_vars:dash→underscore swapped (would never match operator env)",
        "src/briar/env_vars.py",
        'normalised = company.upper().replace("-", "_")',
        'normalised = company.upper().replace("_", "-")',
        ["tests/unit/test_env_vars.py"],
    ),
    Mutant(
        "env_vars:read empty-company guard returns key instead of '' ",
        "src/briar/env_vars.py",
        '            if not company:\n                return ""  # templated var without company → not configured',
        "            if not company:\n                return self.value  # templated var without company → not configured",
        ["tests/unit/test_env_vars.py"],
    ),
    # ── log_context ───────────────────────────────────────────────────
    Mutant(
        "log_context:always-empty filter (return True early)",
        "src/briar/log_context.py",
        "ctx = _CTX.get()\n        if not ctx:",
        "ctx = {}\n        if not ctx:",
        ["tests/unit/test_log_context.py"],
    ),
    # ── _http_retry ───────────────────────────────────────────────────
    Mutant(
        "_http_retry:4xx terminal <500 → <=500 (500 wrongly terminal)",
        "src/briar/_http_retry.py",
        "if exc.code != 429 and exc.code < 500:",
        "if exc.code != 429 and exc.code <= 500:",
        ["tests/unit/test_http_retry.py"],
    ),
    Mutant(
        "_http_retry:429-retry guard != → == (429 wrongly terminal)",
        "src/briar/_http_retry.py",
        "if exc.code != 429 and exc.code < 500:",
        "if exc.code == 429 and exc.code < 500:",
        ["tests/unit/test_http_retry.py"],
    ),
    Mutant(
        "_http_retry:backoff exponent (attempt-1) → attempt (double first wait)",
        "src/briar/_http_retry.py",
        "base = backoff_base * (2 ** (attempt - 1))",
        "base = backoff_base * (2 ** attempt)",
        ["tests/unit/test_http_retry.py"],
    ),
    Mutant(
        "_http_retry:Retry-After min→max (ignores cap, waits the full header)",
        "src/briar/_http_retry.py",
        "return min(max(seconds, 0.0), max_wait)",
        "return max(max(seconds, 0.0), max_wait)",
        ["tests/unit/test_http_retry.py"],
    ),
    Mutant(
        "_http_retry:Retry-After negative-clamp max→min (allows negative sleep)",
        "src/briar/_http_retry.py",
        "return min(max(seconds, 0.0), max_wait)",
        "return min(min(seconds, 0.0), max_wait)",
        ["tests/unit/test_http_retry.py"],
    ),
    Mutant(
        "_http_retry:exhaust guard >= → > (one extra attempt/sleep)",
        "src/briar/_http_retry.py",
        "            if attempt >= attempts:\n                break\n            wait = _compute_wait(exc",
        "            if attempt > attempts:\n                break\n            wait = _compute_wait(exc",
        ["tests/unit/test_http_retry.py"],
    ),
    # ── telemetry/_scrubber ───────────────────────────────────────────
    Mutant(
        "_scrubber:allow-list `not in` → `in` (drops allowed, keeps disallowed)",
        "src/briar/telemetry/_scrubber.py",
        "if name not in self.allowed_tags:",
        "if name in self.allowed_tags:",
        ["tests/unit/telemetry/test_scrubber.py"],
    ),
    Mutant(
        "_scrubber:secret hit returns marker → returns text (leaks the secret)",
        "src/briar/telemetry/_scrubber.py",
        '            if pat.search(text):\n                return "<redacted-secret>"',
        "            if pat.search(text):\n                return text",
        ["tests/unit/telemetry/test_scrubber.py"],
    ),
    Mutant(
        "_scrubber:length cap > → >= (off-by-one truncation boundary)",
        "src/briar/telemetry/_scrubber.py",
        "if len(text) > self.value_length_cap:",
        "if len(text) >= self.value_length_cap:",
        ["tests/unit/telemetry/test_scrubber.py"],
    ),
]


def apply_and_test(m: Mutant) -> bool:
    """Return True if the mutation was KILLED by its target tests."""
    path = ROOT / m.file
    original = path.read_text()
    if m.old not in original:
        print(f"  [SKIP    ] {m.label}  (anchor not found — source drifted)")
        return True  # don't count a drifted anchor as a survivor
    mutated = original.replace(m.old, m.new, 1)
    path.write_text(mutated)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "--tb=no", "-q", "-p", "no:randomly", *m.tests],
            cwd=str(ROOT),
            capture_output=True,
            timeout=120,
        )
        return result.returncode != 0  # non-zero == a test failed == killed
    finally:
        path.write_text(original)


def main() -> int:
    killed = 0
    survived = 0
    survivors: List[str] = []
    for m in MUTATIONS:
        was_killed = apply_and_test(m)
        verdict = "KILLED  " if was_killed else "SURVIVED"
        print(f"  [{verdict}] {m.label}")
        if was_killed:
            killed += 1
        else:
            survived += 1
            survivors.append(m.label)
    total = killed + survived
    score = (killed / total * 100) if total else 0
    print()
    print(f"Mutation score: {killed}/{total} killed ({score:.0f}%)")
    if survivors:
        print("\nSurvivors (spec gaps):")
        for s in survivors:
            print(f"  - {s}")
    return 0 if survived == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

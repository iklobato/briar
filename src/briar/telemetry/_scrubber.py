"""PII / secret scrubber.

Defence-in-depth: every telemetry event passes through the `Scrubber`
before it reaches any sink. The scrubber is allow-list-first (drop
anything we didn't explicitly approve) plus regex-driven on the values
that do get through. Both layers exist on purpose — if a new tag slips
past the allow-list, the regex catches accidental tokens; if a regex
misses, the allow-list confines damage.

What gets stripped:
- Tag NAMES not in the explicit `_ALLOWED_TAGS` list.
- Tag VALUES that match common secret regexes (AWS, GitHub, Anthropic).
- Values that contain absolute filesystem paths — collapsed to `<path>`.
- Values longer than 1024 bytes — truncated with marker.
- Anything matching ``r"(?i)(token|key|password|secret|auth|cookie)"``
  embedded in either the tag name or the value.

What's explicitly out of scope:
- Tag VALUES outside the allow-list — those never reach the scrubber
  because they're dropped by the allow-list filter first.
- LLM prompts / completions / file contents — these are NEVER passed
  to the telemetry layer at any call site; the scrubber is the second
  line, not the first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Tuple

# Tags this codebase is allowed to emit. Anything else is dropped at
# the boundary. Updating this list is a deliberate, reviewable change.
_ALLOWED_TAGS: frozenset = frozenset(
    {
        # Command lifecycle
        "command",
        "command_group",
        "outcome",
        "duration_ms",
        "exit_code",
        "interrupted",
        # Identity (anonymised)
        "install_id",
        "briar_version",
        "python_version",
        "os_name",
        "os_release",
        # Provider mix
        "provider_kind",
        "tracker_kind",
        "store_kind",
        "llm_provider",
        "llm_model",
        # Outcomes / errors
        "error_type",
        "error_policy_decision",
        # Plan-flow counters
        "selector_action",
        "cards_done",
        "cards_blocked",
        "cards_pending",
        "iterations",
        # Token accounting
        "input_tokens",
        "output_tokens",
        "cost_usd_est",
        # Flag-presence (names only, no values)
        "flags_present",
    }
)


# Values matching these patterns are credentials / identifiers we never
# want leaving the user's machine. Order matters only for performance —
# match early-exit on a hit.
_SECRET_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"aws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{30,}", re.IGNORECASE),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),  # GitHub PAT (classic)
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),  # GitHub fine-grained PAT
    re.compile(r"sk-ant-[A-Za-z0-9_-]{30,}"),  # Anthropic key
    re.compile(r"sk-[A-Za-z0-9]{30,}"),  # OpenAI key
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack token
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # PEM
    re.compile(r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),  # JWT-ish
)


# Substrings in a tag NAME that mean "secret-shaped". A tag whose name
# matches this is dropped wholesale.
_SUSPECT_NAME = re.compile(r"(?i)token|key(?!_|s_)|password|secret|auth|cookie|credential")


# Per-value length cap. Values longer than this are truncated with a
# trailing marker so reviewers can spot the trim.
_VALUE_LENGTH_CAP = 1024


# Absolute-path regex (Unix + Windows). Replaced inline with `<path>`
# so we keep the value's shape without leaking the filesystem layout.
#
# The Unix pattern is anchored to a "boundary" before the leading
# slash — either start-of-string or whitespace/quote/parens — so a
# URL path component like ``https://api.example.com/v1/foo`` survives
# unmangled. (The earlier unanchored pattern collapsed the path
# segment of URLs in error messages too, scrubbing what was actually
# the most useful signal.)
_ABS_PATH_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"(?:^|(?<=[\s\"'`(\[<]))/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+"),
    re.compile(r"(?:^|(?<=[\s\"'`(\[<]))[A-Z]:\\[A-Za-z0-9._\\ -]+"),
)


@dataclass
class Scrubber:
    """Single instance is fine — stateless on construction. The class
    exists so we can stub the allow-list / patterns in tests."""

    allowed_tags: frozenset = _ALLOWED_TAGS
    secret_patterns: Tuple[re.Pattern, ...] = _SECRET_PATTERNS
    suspect_name_pattern: re.Pattern = _SUSPECT_NAME
    abs_path_patterns: Tuple[re.Pattern, ...] = _ABS_PATH_PATTERNS
    value_length_cap: int = _VALUE_LENGTH_CAP

    def scrub_tags(self, tags: Dict[str, Any]) -> Dict[str, str]:
        """Return a new dict with disallowed tags dropped and remaining
        values cleaned. Always stringifies values."""
        out: Dict[str, str] = {}
        for name, value in tags.items():
            if name not in self.allowed_tags:
                continue
            if self.suspect_name_pattern.search(name):
                continue
            cleaned = self.scrub_value(value)
            if cleaned is not None:
                out[name] = cleaned
        return out

    def scrub_value(self, value: Any) -> str | None:
        """Run a single value through the regex chain. Returns the
        cleaned string, or `None` if the value should be dropped entirely."""
        if value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value)

        # Drop wholesale if the value reeks of a secret pattern.
        for pat in self.secret_patterns:
            if pat.search(text):
                return "<redacted-secret>"

        # Replace any abs-path-shaped substring inline. We deliberately
        # keep the rest of the value (e.g. an exception message minus
        # the filename) — that's the useful signal.
        for pat in self.abs_path_patterns:
            text = pat.sub("<path>", text)

        # Truncate big payloads.
        if len(text) > self.value_length_cap:
            text = text[: self.value_length_cap - 16] + "...<truncated>"
        return text

    def scrub_exception_message(self, message: str) -> str:
        """Same as scrub_value but always returns a string (never None).
        Exception messages benefit from the path-rewrite even when the
        message is small."""
        cleaned = self.scrub_value(message)
        return cleaned or ""

    def scrub_flag_names(self, flags: Iterable[str]) -> str:
        """Comma-joined list of allowed flag names. Strips values entirely;
        names are kept as-is after a simple character whitelist."""
        cleaned = []
        for f in flags:
            name = re.sub(r"[^a-z0-9_-]", "", f.lstrip("-").lower())
            if name and len(name) <= 32:
                cleaned.append(name)
        return ",".join(sorted(set(cleaned)))[: self.value_length_cap]

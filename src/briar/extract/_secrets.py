"""What an extractor may write about a setting of someone infrastructure.

Infrastructure files are full of secrets (a real company had two plain admin passwords in a
`.tfvars` file). The knowledge file is read by every agent and may be handed to any AI tool, so it
must never hold a value that is a secret: key NAMES only (decision E7). Three rules, in this order:
a name that looks like a secret hides any value; a value that looks like a credential is hidden
whatever its name; a `user:password@` inside a URL is cut out of it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, Tuple

from briar.telemetry._scrubber import Scrubber

_SECRET_NAME = re.compile(
    r"(?i)(?<![a-z])(pass|passwd|password|pwd|secret|token|credentials?|cookie|sas|cert|certificate|auth|authorization)(?![a-z])"
    r"|api[_-]?key|access[_-]?key|account[_-]?key|private[_-]?key|connection[_-]?string"
)
_URL_PASSWORD = re.compile(r"(?<=://)[^/@\s:]+:[^/@\s]*@")


@dataclass(frozen=True)
class SecretGuard:
    """Decides what is shown for a name and a value."""

    HIDDEN: ClassVar[str] = "(set)"

    secret_names: re.Pattern
    credential_values: Tuple[re.Pattern, ...]
    url_password: re.Pattern

    @classmethod
    def standard(cls) -> "SecretGuard":
        """The telemetry credential patterns (AWS, GitHub, Slack, PEM, JWT...) plus the name rule."""
        return cls(_SECRET_NAME, tuple(Scrubber().secret_patterns), _URL_PASSWORD)

    def is_secret_name(self, name: str) -> bool:
        return self.secret_names.search(name) is not None

    def shown(self, name: str, value: str) -> str:
        """The value to write for `name`, or `(set)` when the value must not be written."""
        if self.is_secret_name(name) or any(pattern.search(value) for pattern in self.credential_values):
            return self.HIDDEN
        return self.url_password.sub("", value)

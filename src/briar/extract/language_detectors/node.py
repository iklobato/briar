"""Node detector — reads `package.json`."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from briar.extract.language_detectors.base import FileReader, LanguageDetector


_TEST_RUNNER_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("jest", "jest"),
    ("vitest", "vitest"),
)


class DetectNode(LanguageDetector):
    name = "node"
    manifest = "package.json"

    def detect(
        self,
        repo: str,
        reader: FileReader,
    ) -> Optional[Dict[str, str]]:
        text = reader(repo, self.manifest)
        if text is None:
            return None
        # Default to JS; upgrade to TS if the manifest mentions it.
        findings: Dict[str, str] = {"language": "javascript"}
        if "typescript" in text:
            findings["language"] = "typescript"
        for needle, value in _TEST_RUNNER_PATTERNS:
            if needle in text:
                findings["test_runner"] = value
                break
        if "eslint" in text:
            findings["linter"] = "eslint"
        if "prettier" in text:
            findings["formatter"] = "prettier"
        if "knex" in text:
            findings["migrations"] = "knex"
        return findings

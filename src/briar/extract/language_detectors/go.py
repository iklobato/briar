"""Go detector — reads `go.mod`."""

from __future__ import annotations

from typing import Dict

from briar.extract.language_detectors.base import FileReader, LanguageDetector


class DetectGo(LanguageDetector):
    name = "go"
    manifest = "go.mod"

    def detect(
        self,
        repo: str,
        reader: FileReader,
    ) -> Dict[str, str]:
        text = reader(repo, self.manifest)
        if not text:
            return {}
        return {
            "language": "go",
            "test_runner": "go test",
            "formatter": "gofmt",
        }

"""Language detector registry — Strategy + Factory.

Adding a new language (Rust / Ruby / Java / Elixir / ...) = one file
here + one entry in the tuple below. `ExtractCodebaseConventions` is
agnostic to which detectors exist."""

from __future__ import annotations

from typing import Tuple

from briar.extract.language_detectors.base import FileReader, LanguageDetector
from briar.extract.language_detectors.go import DetectGo
from briar.extract.language_detectors.node import DetectNode
from briar.extract.language_detectors.python import DetectPython


LANGUAGE_DETECTORS: Tuple[LanguageDetector, ...] = (
    DetectPython(),
    DetectNode(),
    DetectGo(),
)


__all__ = ["LanguageDetector", "FileReader", "LANGUAGE_DETECTORS"]

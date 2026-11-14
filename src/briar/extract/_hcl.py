"""A small reader of Terraform (HCL) files, for `briar extract`.

It is not a full HCL parser (no HCL library is a dependency). It reads what the extractors need: the
blocks of a file with their labels, their top-level attributes and their child blocks. A value that is
a map, a list or a heredoc is reported as `(complex)` and not parsed. Strings, comments and heredocs
are blanked out before the braces are counted, so a brace inside one of them cannot end a block."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterator, List, Tuple

COMPLEX = "(complex)"
OPENERS, CLOSERS = "{[(", "}])"
KIND_GROUP, LABELS_GROUP = 1, 2


@dataclass(frozen=True)
class HclBlock:
    """`kind "label" ... { attributes and child blocks }`, with the line where it starts."""

    kind: str
    labels: Tuple[str, ...]
    attributes: Dict[str, str]
    children: Tuple["HclBlock", ...]
    line: int


class Blanker:
    """The text with every comment, string content and heredoc body replaced by spaces (newlines
    kept), so offsets and line numbers stay the same."""

    HEREDOC = re.compile(r"<<-?([A-Za-z_]\w*)[ \t]*\n")

    def __init__(self, text: str, out: List[str]) -> None:
        self._text = text
        self._out = out

    def blank(self) -> str:
        index = 0
        while index < len(self._text):
            index = self._skip(index)
        return "".join(self._out)

    def _skip(self, index: int) -> int:
        """Blank what starts at `index`, and return where the next thing starts."""
        char = self._text[index]
        if char == "#" or self._text.startswith("//", index):
            return self._blank_until(index, self._text.find("\n", index))
        if self._text.startswith("/*", index):
            stop = self._text.find("*/", index + len("/*"))
            return self._blank_until(index, -1 if stop == -1 else stop + len("*/"))
        if char == '"':
            return self._string(index)
        heredoc = self.HEREDOC.match(self._text, index) if char == "<" else None
        return self._heredoc(heredoc) if heredoc else index + 1

    def _string(self, index: int) -> int:
        """Blank the content of a string, keeping its two quotes."""
        cursor = index + 1
        while cursor < len(self._text) and self._text[cursor] not in '"\n':
            cursor += len("\\x") if self._text[cursor] == "\\" else 1
        self._blank_until(index + 1, min(cursor, len(self._text)))
        return cursor + 1

    def _heredoc(self, opening: "re.Match[str]") -> int:
        stop = re.compile(rf"^[ \t]*{re.escape(opening.group(1))}[ \t]*$", re.M).search(self._text, opening.end())
        self._blank_until(opening.end(), stop.start() if stop else -1)
        return stop.end() if stop else len(self._text)

    def _blank_until(self, start: int, stop: int) -> int:
        stop = len(self._text) if stop == -1 else stop
        for position in range(start, stop):
            if self._text[position] != "\n":
                self._out[position] = " "
        return stop


@dataclass(frozen=True)
class Region:
    """A stretch `[start, end)` of a file, with its text and the blanked text of the same length."""

    text: str
    cleaned: str
    start: int
    end: int

    def inside(self, start: int, end: int) -> "Region":
        return Region(self.text, self.cleaned, start, end)

    def line_pairs(self) -> Iterator[Tuple[str, str]]:
        """Each line as (raw, blanked)."""
        return zip(self.text[self.start : self.end].split("\n"), self.cleaned[self.start : self.end].split("\n"))

    def line_of(self, offset: int) -> int:
        return self.text.count("\n", 0, offset) + 1


class HclParser:
    HEADER = re.compile(r'^[ \t]*([A-Za-z_][\w-]*)((?:[ \t]+(?:"[^"\n]*"|[A-Za-z_][\w-]*))*)[ \t]*\{', re.M)
    ATTRIBUTE = re.compile(r"^[ \t]*([A-Za-z_][\w-]*)[ \t]*=(?!=)[ \t]*")
    LABEL = re.compile(r'"([^"]*)"|([A-Za-z_][\w-]*)')
    COMPLEX_STARTS = ("{", "[", "<<")

    def parse(self, text: str) -> List[HclBlock]:
        return self._blocks(self._whole(text))

    def attributes(self, text: str) -> Dict[str, str]:
        """The top-level `key = value` pairs of a whole file (a `.tfvars` file is only these)."""
        return self._attributes(self._whole(text))

    def _whole(self, text: str) -> Region:
        return Region(text, Blanker(text, list(text)).blank(), 0, len(text))

    def _blocks(self, region: Region) -> List[HclBlock]:
        found: List[HclBlock] = []
        position = region.start
        while (match := self.HEADER.search(region.cleaned, position, region.end)) is not None:
            opened = match.end() - 1
            body = region.inside(opened + 1, self._closing(region, opened))
            found.append(
                HclBlock(
                    match.group(KIND_GROUP),
                    self._labels(region, match),
                    self._attributes(body),
                    tuple(self._blocks(body)),
                    region.line_of(match.start(KIND_GROUP)),
                )
            )
            position = body.end + 1
        return found

    def _labels(self, region: Region, match: "re.Match[str]") -> Tuple[str, ...]:
        found = self.LABEL.findall(region.text[match.start(LABELS_GROUP) : match.end(LABELS_GROUP)])
        return tuple(quoted or bare for quoted, bare in found)

    def _closing(self, region: Region, opened: int) -> int:
        depth = 0
        for index in range(opened, region.end):
            depth += (region.cleaned[index] == "{") - (region.cleaned[index] == "}")
            if depth == 0:
                return index
        return region.end

    def _attributes(self, region: Region) -> Dict[str, str]:
        found: Dict[str, str] = {}
        depth = 0
        for raw, clean in region.line_pairs():
            match = self.ATTRIBUTE.match(clean) if depth == 0 else None
            if match:
                found[match.group(KIND_GROUP)] = self._value(raw, clean, match.end())
            depth += sum(clean.count(char) for char in OPENERS) - sum(clean.count(char) for char in CLOSERS)
        return found

    def _value(self, raw: str, clean: str, at: int) -> str:
        shape = clean[at:].rstrip()
        if shape.startswith(self.COMPLEX_STARTS):
            return COMPLEX
        value = raw[at : at + len(shape)].strip()
        return value[1:-1] if len(value) >= len('""') and value[0] == '"' and value[-1] == '"' else value

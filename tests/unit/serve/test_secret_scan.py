"""WP2 of the production plan (P33, P34): the scan that looks at what a push would ADD. It reads a unified diff, follows the file and the line of
each added line, and matches only the credential patterns of `SecretGuard` (AWS, GitHub, Slack, PEM, JWT, a password in a URL). It never keeps the
value: a hit is a file and a line. Fake secrets are built at run time so the repo holds none."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pytest

from briar.extract._secrets import SecretGuard
from briar.serve.secrets import Hit, SecretScan

# The length of the fake key of GitHub, and how many times the four letters of the fake AWS key repeat (AKIA plus 16).
GITHUB_BODY = 36
AWS_REPEATS = 4
# Where the added line sits in the diffs of the tests.
SECOND_LINE = 2
THIRD_LINE = 3
HUNK_START = 10
HUNK_LINE = 11
LONG_ENOUGH = 10


class Keys:
    """Fake credentials of each kind, built here and never written as one literal."""

    def aws(self) -> str:
        return "AKIA" + "FAKE" * AWS_REPEATS

    def github(self) -> str:
        return "ghp_" + "x" * GITHUB_BODY

    def slack(self) -> str:
        return "xoxb-" + "1" * LONG_ENOUGH

    def pem(self) -> str:
        return "-----BEGIN RSA " + "PRIVATE KEY-----"

    def url(self) -> str:
        return "https://bot:" + "hunter2pass@example.test/x"


@dataclass(frozen=True)
class Edit:
    """One changed line of a file: the line before it, the one removed and the one added."""

    path: str
    start: int
    context: str
    removed: str
    added: str


class Diffs:
    """Unified diffs as `git diff` writes them."""

    def new_file(self, path: str, lines: List[str]) -> str:
        body = "".join(f"+{line}\n" for line in lines)
        return f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"

    def edit(self, change: Edit) -> str:
        head = f"diff --git a/{change.path} b/{change.path}\n--- a/{change.path}\n+++ b/{change.path}\n"
        return f"{head}@@ -{change.start},3 +{change.start},3 @@\n {change.context}\n-{change.removed}\n+{change.added}\n tail\n"


class Scans:
    def of(self, diff: str) -> List[Hit]:
        return SecretScan(SecretGuard.standard()).findings(diff)


class TestWhatIsFound:
    @pytest.mark.parametrize("make", ["aws", "github", "slack", "pem", "url"])
    def test_each_kind_of_credential_on_an_added_line_gives_its_file_and_line(self, make: str) -> None:
        secret = getattr(Keys(), make)()

        hits = Scans().of(Diffs().new_file("conf/settings.txt", ["first", f"value = {secret}"]))

        assert hits == [Hit("conf/settings.txt", SECOND_LINE)]

    def test_the_line_number_follows_the_hunk(self) -> None:
        hits = Scans().of(Diffs().edit(Edit("src/app.py", HUNK_START, "context", "old = 1", f"key = '{Keys().aws()}'")))

        assert hits == [Hit("src/app.py", HUNK_LINE)]

    def test_every_file_of_the_diff_is_read(self) -> None:
        diff = Diffs().new_file("a.txt", [Keys().github()]) + Diffs().new_file("b.txt", ["fine", "fine", Keys().aws()])

        assert Scans().of(diff) == [Hit("a.txt", 1), Hit("b.txt", THIRD_LINE)]

    def test_two_secrets_on_one_line_are_one_hit(self) -> None:
        assert Scans().of(Diffs().new_file("a.txt", [f"{Keys().aws()} {Keys().github()}"])) == [Hit("a.txt", 1)]


class TestWhatIsNot:
    def test_a_removed_line_is_not_read(self) -> None:
        assert Scans().of(Diffs().edit(Edit("a.py", 1, "context", Keys().aws(), "clean = 1"))) == []

    def test_a_context_line_is_not_read(self) -> None:
        assert Scans().of(Diffs().edit(Edit("a.py", 1, Keys().aws(), "old", "new"))) == []

    def test_the_header_of_a_file_is_not_an_added_line(self) -> None:
        """The path of a file can look like anything: `+++ b/<path>` is a header, not data."""
        assert Scans().of(Diffs().new_file(f"src/{Keys().aws()}.txt", ["nothing here"])) == []

    def test_a_name_that_looks_like_a_secret_is_not_enough(self) -> None:
        """Only values with the shape of a credential count: `token` and `password` are everywhere in code."""
        assert Scans().of(Diffs().new_file("a.py", ['token = "abc"', "password = get_password()"])) == []

    def test_an_empty_diff_has_no_hits(self) -> None:
        assert Scans().of("") == []


class TestTheValueStaysOut:
    def test_a_hit_holds_a_file_and_a_line_and_nothing_of_the_value(self) -> None:
        secret = Keys().github()

        hits = Scans().of(Diffs().new_file("a.txt", [f"x = {secret}"]))

        assert secret not in repr(hits) and secret not in "".join(hit.label() for hit in hits)
        assert [hit.label() for hit in hits] == ["a.txt:1"]

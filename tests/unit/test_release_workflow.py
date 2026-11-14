"""The release workflow (decisions R5 and R13 of the real-world plan): the version it ships.

The step "Decide the version" is read out of `.github/workflows/release.yml` and run for real
against a PyPI that is a local fake server, so the logic under test is the logic that ships."""

from __future__ import annotations

import http.server
import os
import shlex
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
NOT_FOUND, OK_STATUS, BROKEN = 404, 200, 500
# os.system gives the exit code in the high byte; the version is the last-but-one path part.
EXIT_CODE_SHIFT, VERSION_PART = 8, -2


class PypiHandler(http.server.BaseHTTPRequestHandler):
    """A PyPI that knows the versions of `known`: 200 for those, 404 for the rest, 500 when `broken`."""

    known: List[str] = []
    broken = False

    def do_GET(self) -> None:
        version = self.path.rstrip("/").split("/")[VERSION_PART]
        status = BROKEN if self.broken else (OK_STATUS if version in self.known else NOT_FOUND)
        self.send_response(status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_request(self, code: object = "-", size: object = "-") -> None:
        return


@dataclass(frozen=True)
class Decision:
    exit_code: int
    outputs: Dict[str, str]


@pytest.fixture
def pypi() -> Iterator[str]:
    PypiHandler.known, PypiHandler.broken = [], False
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PypiHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/pypi/briar-cli"
    server.shutdown()
    server.server_close()


class Release:
    """Runs the step of the workflow in a folder with a pyproject.toml of `version`."""

    def __init__(self, tmp_path: Path, pypi_url: str) -> None:
        self._tmp_path = tmp_path
        self._pypi_url = pypi_url

    def step(self) -> str:
        job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["release"]
        script = next(step["run"] for step in job["steps"] if step.get("name") == "Decide the version")
        return script.replace("python -", f"{shlex.quote(sys.executable)} -", 1)

    def decide(self, version: str) -> Decision:
        (self._tmp_path / "pyproject.toml").write_text(f'[project]\nname = "briar-cli"\nversion = "{version}"\n')
        script, outputs, log = self._tmp_path / "step.sh", self._tmp_path / "outputs", self._tmp_path / "log"
        script.write_text(self.step())
        outputs.write_text("")
        command = f"cd {shlex.quote(str(self._tmp_path))} && PYPI_URL={shlex.quote(self._pypi_url)} GITHUB_OUTPUT={shlex.quote(str(outputs))} bash {shlex.quote(str(script))} > {shlex.quote(str(log))} 2>&1"
        code = os.system(command) >> EXIT_CODE_SHIFT
        pairs = [line.split("=", 1) for line in outputs.read_text().splitlines() if "=" in line]
        return Decision(code, {key: value for key, value in pairs})


@pytest.fixture
def release(tmp_path: Path, pypi: str) -> Release:
    return Release(tmp_path, pypi)


class TestDecideTheVersion:
    def test_a_version_pypi_does_not_have_is_published_as_it_is(self, release: Release) -> None:
        decision = release.decide("2.0.0")

        assert (decision.exit_code, decision.outputs) == (0, {"current": "2.0.0", "next": "2.0.0", "bump": "false", "publish": "true", "prerelease": "false"})

    def test_a_release_candidate_pypi_does_not_have_is_published_and_marked_a_prerelease(self, release: Release) -> None:
        decision = release.decide("2.0.0rc1")

        assert (decision.exit_code, decision.outputs["next"], decision.outputs["publish"], decision.outputs["prerelease"]) == (0, "2.0.0rc1", "true", "true")

    def test_a_final_version_pypi_has_gets_its_patch_bumped(self, release: Release) -> None:
        PypiHandler.known = ["1.1.101"]

        decision = release.decide("1.1.101")

        assert (decision.exit_code, decision.outputs["next"], decision.outputs["bump"], decision.outputs["publish"]) == (0, "1.1.102", "true", "true")

    def test_a_release_candidate_pypi_has_is_not_published_again_and_does_not_fail(self, release: Release) -> None:
        """A docs push to main after the rc must not turn the release run red."""
        PypiHandler.known = ["2.0.0rc1"]

        decision = release.decide("2.0.0rc1")

        assert (decision.exit_code, decision.outputs["publish"]) == (0, "false")

    def test_pypi_failing_stops_the_run_instead_of_guessing(self, release: Release) -> None:
        PypiHandler.broken = True

        assert release.decide("2.0.0rc1").exit_code != 0

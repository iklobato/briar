"""Profile resolver tests — env / cli / active-file precedence."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from briar.profile import resolve_profile


class ResolveProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = os.environ.pop("BRIAR_PROFILE", None)

    def tearDown(self) -> None:
        os.environ.pop("BRIAR_PROFILE", None)
        if self._env_backup is not None:
            os.environ["BRIAR_PROFILE"] = self._env_backup

    def test_default_when_nothing_set(self) -> None:
        with mock.patch("briar.profile.ACTIVE_FILE") as af:
            af.read_text.side_effect = FileNotFoundError
            self.assertEqual(resolve_profile(None), "default")

    def test_cli_wins(self) -> None:
        os.environ["BRIAR_PROFILE"] = "env-pick"
        self.assertEqual(resolve_profile("cli-pick"), "cli-pick")

    def test_env_wins_over_active_file(self) -> None:
        os.environ["BRIAR_PROFILE"] = "from-env"
        with mock.patch("briar.profile.ACTIVE_FILE") as af:
            af.read_text.return_value = "from-file"
            self.assertEqual(resolve_profile(None), "from-env")

    def test_active_file_when_no_env(self) -> None:
        with mock.patch("briar.profile.ACTIVE_FILE") as af:
            af.read_text.return_value = "from-file"
            self.assertEqual(resolve_profile(None), "from-file")


if __name__ == "__main__":
    unittest.main()

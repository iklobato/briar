"""The adapter that checks a source when no extractor of a run names it (spec 005 PR 3):
the configured sources of `briar doctor` that no run uses (J10, J11)."""

from __future__ import annotations

from typing import Callable, ClassVar, List, Mapping, Sequence

from briar.auth.check import LoginTarget
from briar.auth.logins import CompanyLogins


class SourceProbes:
    """One default adapter per login source, built with the company logins."""

    # The factory of each source, by method name.
    FACTORIES: ClassVar[Mapping[str, str]] = {
        "github": "_repo_host",
        "bitbucket": "_repo_host",
        "jira": "_tracker",
        "linear": "_tracker",
        "fireflies": "_meeting",
        "aws": "_cloud",
        "gcp": "_cloud",
        "azure": "_cloud",
    }

    def __init__(self, logins: CompanyLogins) -> None:
        self._logins = logins

    def target(self, source: str) -> LoginTarget:
        factory: Callable[[str], LoginTarget] = getattr(self, self.FACTORIES[source])
        return factory(source)

    def for_sources(self, sources: Sequence[str], run_targets: Sequence[LoginTarget]) -> List[LoginTarget]:
        """The targets of `sources`: those the run reads (with their resources, J11), then a
        default adapter for each source no run uses ("who am I" only)."""
        from_run = [target for target in run_targets if target.probe.login_source in sources]
        covered = {target.probe.login_source for target in from_run}
        return from_run + [self.target(source) for source in sources if source not in covered]

    def _repo_host(self, source: str) -> LoginTarget:
        from briar.extract._providers import make_provider

        return LoginTarget(make_provider(source, self._logins))

    def _tracker(self, source: str) -> LoginTarget:
        from briar.extract._trackers import make_tracker

        return LoginTarget(make_tracker(source, self._logins))

    def _meeting(self, source: str) -> LoginTarget:
        from briar.extract._meetings import make_meeting

        return LoginTarget(make_meeting(source, self._logins))

    def _cloud(self, source: str) -> LoginTarget:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud(source, self._logins)
        return LoginTarget(cloud, cloud.scope())


__all__ = ["SourceProbes"]

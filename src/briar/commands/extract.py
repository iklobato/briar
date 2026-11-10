"""`briar extract`: run one or more knowledge extractors and merge their
sections into the blob `knowledge:<company>` of the file store.

Since 2.0 the command line takes only `--company`, `--include` and `--repo`
(decision C12). Every other extractor value comes from the `[extract]` and
`[extract.<name>]` tables of the project config, with the short key names of
docs/decisions/2026-11-10-briar-2-extract-config.md (E1 to E16)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Dict, List, Mapping, Optional, Tuple

from briar.commands.base import Command
from briar.errors import CliError
from briar.extract import EXTRACTORS
from briar.extract.base import ExtractedSection, KnowledgeExtractor
from briar.extract.canonical import SHARED_CONCEPTS, OptionConcept, _ReplaceDefaultAppend, apply_canonical
from briar.extract.composer import KnowledgeComposer, render_markdown
from briar.settings import BriarSettings
from briar.storage import KnowledgeRef, make_store

DEFAULT_ROOT = "./knowledge"


def _status(message: str) -> None:
    """Human progress/status → stderr, so stdout stays clean for piping."""
    print(message, file=sys.stderr)


class ExtractorKeys:
    """The `[extract.<name>]` keys of one extractor: each option's short name (E1 to E4).
    A repo list has no key (E8): it comes from `--repo`, `repos` or `[repo]`."""

    SOURCE_KINDS: ClassVar[Tuple[str, ...]] = ("provider", "tracker", "meeting", "cloud")
    PROJECT_DESTS: ClassVar[Tuple[str, ...]] = ("ticket_project", "ticket_archaeology_project")
    PROJECTS = "projects"
    REPO = "repo"
    # E3: the options with no shared concept drop the extractor prefix.
    NICHE: ClassVar[Mapping[str, str]] = {
        "aws_extract_profile": "profile",
        "aws_extract_region": "region",
        "aws_extract_service": "services",
        "meeting_attendee_allow": "attendee_allow",
        "prhygiene_large_loc": "large_loc",
        "gov_branch": "branch",
        "stale_days": "days",
    }

    def __init__(self, extractor: KnowledgeExtractor) -> None:
        self._extractor = extractor

    def parser(self) -> argparse.ArgumentParser:
        seed = argparse.ArgumentParser(add_help=False)
        self._extractor.add_arguments(seed)
        return seed

    def dests(self) -> Dict[str, argparse.Action]:
        return {action.dest: action for action in self.parser()._actions}

    def options(self) -> Dict[str, argparse.Action]:
        """Short name → the option it sets."""
        named = {self.short_name(action.dest): action for action in self.parser()._actions}
        return {name: action for name, action in named.items() if name}

    def short_name(self, dest: str) -> str:
        """`dest`'s key in `[extract.<name>]`; empty when it has none (a repo list)."""
        if dest in self.SOURCE_KINDS:
            return dest
        if dest in self.PROJECT_DESTS:
            return self.PROJECTS
        concept = OptionConcept(dest).concept() or ""
        return self.NICHE.get(dest, "" if concept == self.REPO else concept)


class ExtractConfig:
    """`briar extract` values from the project config (C12, research R-5, E6 to E11).

    `[extract]` holds values for every included extractor: a shared concept or a
    source kind. `[extract.<name>]` holds one extractor's short-named options.
    Precedence, lowest first: the extractor default, the `tracker` and
    `[repo] provider` settings, `[extract]`, `[extract.<name>]`. An unknown key,
    a 1.x key name or a value outside a fixed list stops the run, naming it.
    Spec 004 replaces this class with a pydantic model per extractor (D32)."""

    TABLE = "extract"
    REPO_HINT = "the repo list comes from --repo, `repos` or [repo]"
    LIST_HINT = "not a `briar extract` setting; `briar extract -h` lists the keys"

    def __init__(self, project_config: Mapping[str, object], settings: BriarSettings, config_path: Optional[Path]) -> None:
        self._project_config = project_config
        self._settings = settings
        self._config_path = config_path

    def root(self) -> str:
        return self._settings.root or DEFAULT_ROOT

    def check(self) -> None:
        for key, entry in self._table().items():
            if isinstance(entry, Mapping):
                self._check_own(key, entry)
                continue
            self._check_shared(key, entry)

    def namespace_for(self, extractor: KnowledgeExtractor, command_line: argparse.Namespace) -> argparse.Namespace:
        """`extractor`'s options at their defaults, then the settings, `[extract]` and
        `[extract.<name>]`, each one beating the one before. Call `check()` first."""
        keys = ExtractorKeys(extractor)
        namespace = keys.parser().parse_args([])
        vars(namespace).update(vars(command_line))
        vars(namespace).update(self._shared())
        apply_canonical(namespace, extractor)
        options = keys.options()
        for name, setting in self._own(extractor.name).items():
            setattr(namespace, options[name].dest, setting)
        return namespace

    def _table(self) -> Mapping[str, object]:
        table = self._project_config.get(self.TABLE)
        return table if isinstance(table, Mapping) else {}

    def _own(self, name: str) -> Mapping[str, object]:
        table = self._table().get(name)
        return table if isinstance(table, Mapping) else {}

    def _shared(self) -> Dict[str, object]:
        settings = {"tracker": self._settings.tracker, "provider": self._settings.repo.provider}
        shared = {key: entry for key, entry in self._table().items() if not isinstance(entry, Mapping)}
        return {**{key: entry for key, entry in settings.items() if entry is not None}, **shared}

    def _check_shared(self, key: str, setting: object) -> None:
        if key in SHARED_CONCEPTS:
            return
        if key not in ExtractorKeys.SOURCE_KINDS:
            raise self._error(key, self.REPO_HINT if key == ExtractorKeys.REPO else self.LIST_HINT)
        self._check_choice(key, self._source_option(key), setting)

    def _check_own(self, name: str, table: Mapping[str, object]) -> None:
        extractor = EXTRACTORS.get(name)
        if extractor is None:
            raise self._error(name, "not an extractor name; `briar extract -h` lists them")
        keys = ExtractorKeys(extractor)
        options = keys.options()
        for key, setting in table.items():
            if key not in options:
                raise self._error(f"{name}.{key}", self._own_hint(keys, key))
            self._check_choice(f"{name}.{key}", options[key], setting)

    def _own_hint(self, keys: ExtractorKeys, key: str) -> str:
        if key == ExtractorKeys.REPO:
            return self.REPO_HINT
        if key not in keys.dests():
            return self.LIST_HINT
        short = keys.short_name(key)
        return f"that is the 1.x name; use `{short}`" if short else self.REPO_HINT

    def _source_option(self, key: str) -> Optional[argparse.Action]:
        for extractor in EXTRACTORS.values():
            option = ExtractorKeys(extractor).options().get(key)
            if option is not None:
                return option
        return None

    def _check_choice(self, key: str, option: Optional[argparse.Action], setting: object) -> None:
        if option is None or not option.choices:
            return
        bad = [item for item in (setting if isinstance(setting, list) else [setting]) if item not in option.choices]
        if bad:
            raise self._error(key, f"{bad[0]!r} is not one of {', '.join(map(str, option.choices))}")

    def _error(self, key: str, hint: str) -> CliError:
        where = self._config_path or "the project config"
        return CliError(f"`{self.TABLE}.{key}` in {where}: {hint}")


class ExtractKeyTable:
    """Every extractor's `[extract.<name>]` keys with their default and allowed values,
    for `briar extract -h` (E14) and agents/extract.md (E15)."""

    def __init__(self, extractors: Mapping[str, KnowledgeExtractor]) -> None:
        self._extractors = extractors

    def rows(self) -> List[Tuple[str, str, str, str]]:
        """(extractor, key, default, allowed values), sorted by extractor then key."""
        rows: List[Tuple[str, str, str, str]] = []
        for name in sorted(self._extractors):
            options = ExtractorKeys(self._extractors[name]).options()
            rows.extend((name, key, self._shown(options[key].default), ", ".join(map(str, options[key].choices or ()))) for key in sorted(options))
        return rows

    def help_text(self) -> str:
        shared = ", ".join(SHARED_CONCEPTS + ExtractorKeys.SOURCE_KINDS)
        by_extractor: Dict[str, List[str]] = {}
        for name, key, default, allowed in self.rows():
            by_extractor.setdefault(name, []).append(f"{key}={default}" + (f" ({allowed})" if allowed else ""))
        lines = [f"extractor settings, in .briar.toml:\n  [extract] (every extractor): {shared}\n  [extract.<name>] (one extractor), key=default:"]
        lines.extend(f"    {name}: {', '.join(keys)}" for name, keys in by_extractor.items())
        return "\n".join(lines)

    def markdown(self) -> str:
        lines = ["| Extractor | Key | Default | Allowed values |", "|---|---|---|---|"]
        lines.extend(f"| `{name}` | `{key}` | `{default}` | {allowed} |" for name, key, default, allowed in self.rows())
        return "\n".join(lines)

    def _shown(self, default: object) -> str:
        return "unset" if default is None else json.dumps(default)


@dataclass(frozen=True)
class ExtractRun:
    """What one run produced: the sections with data, and the headings of every extractor
    that ran (their old sections are replaced, or dropped when empty)."""

    sections: List[ExtractedSection] = field(default_factory=list)
    refreshed: List[str] = field(default_factory=list)


class CommandExtract(Command):
    name = "extract"
    help = "Mine the live state of GitHub / AWS / etc. into a markdown " "knowledge blob written to local disk."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.formatter_class = argparse.RawDescriptionHelpFormatter
        parser.epilog = ExtractKeyTable(EXTRACTORS).help_text()
        parser.add_argument("--company", required=True, help="Company name (drives the markdown title + blob name)")
        parser.add_argument(
            "--include",
            action="append",
            default=[],
            choices=sorted(EXTRACTORS.keys()),
            help="Which extractor(s) to run (repeatable; default: all available). Sections of other extractors already in the blob are kept.",
        )
        parser.add_argument(
            "--repo",
            action=_ReplaceDefaultAppend,
            default=[],
            metavar="OWNER/REPO",
            help="Repository (or tracker project) to mine. Repeatable. Feeds every included extractor's repo list.",
        )

    def run(self, args: argparse.Namespace) -> int:
        config = self._config()
        config.check()
        outcome = self._extract(config, args)
        if not outcome.sections:
            raise CliError("nothing extracted — every enabled extractor returned empty")
        ref = self._write_blob(config.root(), args.company, outcome)
        _status(f"\nwrote blob '{ref.name}' ({ref.byte_count} bytes, {len(outcome.sections)} sections) under {config.root()}")
        return 0

    def _config(self) -> ExtractConfig:
        from briar.config import find_config_file, load_project_config
        from briar.settings import SettingsResolver

        project_config = load_project_config()
        return ExtractConfig(project_config, SettingsResolver.from_project(project_config).settings(), find_config_file())

    def _extract(self, config: ExtractConfig, args: argparse.Namespace) -> ExtractRun:
        outcome = ExtractRun()
        for name in args.include or list(EXTRACTORS.keys()):
            extractor = EXTRACTORS[name]
            self._run_one(extractor, config.namespace_for(extractor, args), outcome)
        return outcome

    def _run_one(self, extractor: KnowledgeExtractor, namespace: argparse.Namespace, outcome: ExtractRun) -> None:
        if not extractor.is_available(namespace):
            _status(f"  skipped {extractor.name}  (not available in this env)")
            return
        _status(f"  running {extractor.name} ...")
        outcome.refreshed.append(extractor.heading)
        section = extractor.extract(namespace)
        if section.is_empty:
            _status(f"  {extractor.name}: no data")
            return
        outcome.sections.append(section)

    def _write_blob(self, root: str, company: str, outcome: ExtractRun) -> KnowledgeRef:
        """Merge the run into the stored blob and write it back. Sections of extractors
        that did not run stay."""
        store = make_store("file", file_root=Path(root))
        blob_name = f"knowledge:{company}"
        fresh = render_markdown(company=company, sections=outcome.sections)
        merged = KnowledgeComposer.merge_markdown(company=company, existing=store.get(blob_name), fresh=fresh, refreshed=outcome.refreshed)
        return store.put(blob_name, merged, category="knowledge")

"""Project-config precedence: CLI flag > env > config > built-in default."""

from __future__ import annotations

import argparse


from briar.config import apply_config_defaults, find_config_file, load_project_config


def _write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    return path


def test_find_and_load_dedicated_briar_toml(tmp_path):
    _write(tmp_path, ".briar.toml", 'company = "acme"\nstore = "postgres"\n')
    found = find_config_file(tmp_path)
    assert found is not None and found.name == ".briar.toml"
    assert load_project_config(tmp_path) == {"company": "acme", "store": "postgres"}


def test_load_pyproject_tool_briar_section(tmp_path):
    _write(tmp_path, "pyproject.toml", '[tool.briar]\ncompany = "acme"\n[tool.other]\nx = 1\n')
    assert load_project_config(tmp_path) == {"company": "acme"}


def test_dedicated_file_wins_over_pyproject(tmp_path):
    _write(tmp_path, "pyproject.toml", '[tool.briar]\ncompany = "from-pyproject"\n')
    _write(tmp_path, ".briar.toml", 'company = "from-dotfile"\n')
    assert load_project_config(tmp_path)["company"] == "from-dotfile"


def test_search_walks_up_to_parent(tmp_path):
    _write(tmp_path, ".briar.toml", 'company = "root"\n')
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert load_project_config(nested)["company"] == "root"


def test_no_config_returns_empty(tmp_path):
    assert load_project_config(tmp_path) == {}
    assert find_config_file(tmp_path) is None


def _parser_with_required_company():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True)
    parser.add_argument("--store", default="file")
    return parser


def test_config_satisfies_required_flag():
    parser = _parser_with_required_company()
    satisfied = apply_config_defaults(parser, {"company": "acme"})
    assert "company" in satisfied
    args = parser.parse_args([])  # no --company on the command line
    assert args.company == "acme"


def test_cli_flag_overrides_config():
    parser = _parser_with_required_company()
    apply_config_defaults(parser, {"company": "acme", "store": "postgres"})
    args = parser.parse_args(["--company", "other", "--store", "file"])
    assert args.company == "other"
    assert args.store == "file"


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("BRIAR_DEFAULT_STORE", "postgres")
    parser = _parser_with_required_company()
    apply_config_defaults(parser, {"company": "acme", "store": "file"})
    args = parser.parse_args([])
    assert args.store == "postgres"  # env beats config


def test_repo_section_feeds_owner_repo_provider():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--provider", default="github")
    apply_config_defaults(parser, {"repo": {"owner": "acme-co", "repo": "app", "provider": "bitbucket"}})
    args = parser.parse_args([])
    assert (args.owner, args.repo, args.provider) == ("acme-co", "app", "bitbucket")


def test_unrelated_dest_untouched():
    parser = argparse.ArgumentParser()
    parser.add_argument("--unrelated", default="keep")
    apply_config_defaults(parser, {"company": "acme"})
    assert parser.parse_args([]).unrelated == "keep"


def test_applies_through_subparsers():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    build = sub.add_parser("build")
    build.add_argument("--company", required=True)
    apply_config_defaults(parser, {"company": "acme"})
    args = parser.parse_args(["build"])
    assert args.company == "acme"

# Copyright 2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Audit W1 / finding 11: a project policy is honored, validated and overridable."""

import argparse
import json
from pathlib import Path

import pytest

from towel.cli import _build_parser
from towel.config import apply_project_defaults

# Literal inputs, as the python lane made the CLI tests: no argument is handed over.
from tests.test_cli_integration import LITERAL_DUPLICATES as DUPLICATES
from tests.test_cli_integration import invoke

FLAGS = ["--no-types", "--no-format", "--progress", "none"]


def strict_mode_flags() -> list[str]:
    """Keep parse-refusal regressions strict when the relaxed lane's parser lands.

    The integration base of this lane is strict-only and has no mode flag yet.
    """
    parser = _build_parser()
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return (
        ["--strict"]
        if any("--strict" in a.option_strings for a in subparsers.choices["preview"]._actions)
        else []
    )


def project(tmp_path: Path, settings: str) -> Path:
    (tmp_path / "pyproject.toml").write_text("[tool.towel]\n" + settings)
    source = tmp_path / "example.py"
    source.write_text(DUPLICATES)
    return source


def resolved(source: Path, *flags: str, command: str = "preview") -> argparse.Namespace:
    parser = _build_parser()
    argv = [command, str(source)]
    if command == "dry":
        argv.append(str(source.with_name("out.py")))
    argv.extend(flags)
    return apply_project_defaults(parser, parser.parse_args(argv), argv)


def test_w1_threshold_changes_preview_and_cli_or_no_config_restores_it(tmp_path: Path) -> None:
    source = project(tmp_path, "min-lines = 99\n")
    for extra, expected, configured in [
        ([], 0, True),
        (["--min-lines", "4"], 1, True),
        (["--no-config"], 1, False),
    ]:
        result = invoke(["preview", str(source), "--json", *FLAGS, *extra])
        assert result.status == 0, result.stderr
        document = json.loads(result.stdout)
        assert document["applied"] == expected
        assert document["config_path"] == (str(tmp_path / "pyproject.toml") if configured else None)
        assert result.stderr.count("Using [tool.towel] from") == int(configured)
    assert source.read_text() == DUPLICATES


@pytest.mark.parametrize("command", ["dry", "preview"])
def test_w1_cli_overrides_scalars_booleans_aliases_and_whole_exclude_list(
    tmp_path: Path, command: str
) -> None:
    source = project(
        tmp_path,
        'exclude = ["tests", "migrations"]\ntypes = false\n'
        "cross-module = true\nmax-refactorings = 8\n",
    )
    defaults = resolved(source, command=command)
    assert defaults.exclude == ["tests", "migrations"]
    assert defaults.types is False and defaults.cross_module is True
    actual = resolved(
        source,
        "--exclude=vendor",
        "--exclude",
        "build",
        "--types",
        "--no-cross-module",
        "--max-iterations=2",
        command=command,
    )
    assert actual.exclude == ["vendor", "build"]
    assert actual.types is True and actual.cross_module is False
    assert actual.max_refactorings == 2


@pytest.mark.parametrize(
    "setting,key",
    [
        ("min-lnes = 99", "min-lnes"),
        ('min-lines = "4"', "min-lines"),
        ("min-lines = true", "min-lines"),
        ("min-lines = 1.5", "min-lines"),
        ("min-lines = -1", "min-lines"),
        ('types = "false"', "types"),
        ('exclude = "tests"', "exclude"),
        ('exclude = ["tests", 2]', "exclude"),
        ('exclude = ["src/tests"]', "exclude"),
        ('progress = "oops"', "progress"),
        ("time-limit = inf", "time-limit"),
        ("time-limit = 0", "time-limit"),
        ("time-limit = 100000001", "time-limit"),
        ("progress = 2", "progress"),
        ("types = false\nno-types = true", "no-types"),
        ("json = true\nsummary = true", "summary"),
    ],
)
def test_w1_bad_policy_names_file_key_and_allowed_keys_even_if_cli_overrides(
    tmp_path: Path, setting: str, key: str
) -> None:
    source = project(tmp_path, setting)
    result = invoke(["preview", str(source), "--min-lines=4", *FLAGS])
    assert result.status == 2
    assert str(tmp_path / "pyproject.toml") in result.stderr
    assert key in result.stderr and "Accepted keys:" in result.stderr
    assert "min-lines" in result.stderr and "exclude" in result.stderr
    assert "Traceback" not in result.stderr


def test_w1_config_discovery_uses_target_ancestry_and_command_only_settings(tmp_path: Path) -> None:
    source = project(tmp_path, "interactive = false\nsummary = true\ntime-limit = 2\n")
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    nested_source = nested / source.name
    nested_source.write_text(DUPLICATES)
    args = resolved(nested_source, "--json")
    assert args.config_path == str(tmp_path / "pyproject.toml")
    assert args.summary is False and args.json is True
    assert args.time_limit == 2.0
    dry = resolved(nested_source, command="dry")
    assert dry.interactive is False and not hasattr(dry, "summary")


def test_w1_new_parser_options_need_no_loader_change(tmp_path: Path) -> None:
    source = project(tmp_path, "test-policy-flag = true\ntest-policy-count = 2\n")
    parser = _build_parser()
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for name in ("dry", "preview"):
        subparsers.choices[name].add_argument("--test-policy-flag", action="store_true")
        subparsers.choices[name].add_argument("--test-policy-count", type=int)
    argv = ["preview", str(source)]
    args = apply_project_defaults(parser, parser.parse_args(argv), argv)
    assert args.test_policy_flag is True and args.test_policy_count == 2


@pytest.mark.parametrize("contents", ["[tool.towel\n", "[tool]\ntowel = true\n"])
def test_w1_malformed_policy_errors_but_no_config_ignores_it(tmp_path: Path, contents: str) -> None:
    source = project(tmp_path, "")
    (tmp_path / "pyproject.toml").write_text(contents)
    result = invoke(["preview", str(source), *FLAGS])
    assert result.status == 2 and str(tmp_path / "pyproject.toml") in result.stderr
    assert resolved(source, "--no-config").config_path is None


def test_w1_dry_honors_policy_and_records_it_in_json(tmp_path: Path) -> None:
    source = project(tmp_path, "min-lines = 99\ninteractive = false\njson = true\n")
    output = tmp_path / "out.py"
    result = invoke(["dry", str(source), str(output), *FLAGS])
    assert result.status == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["applied"] == 0
    assert document["config_path"] == str(tmp_path / "pyproject.toml")
    assert output.read_text() == DUPLICATES


def test_w1_failed_run_records_loaded_policy(tmp_path: Path) -> None:
    source = project(tmp_path, "types = false\n")
    source.write_text("def broken(:\n")
    result = invoke(["preview", str(source), "--json", *FLAGS, *strict_mode_flags()])
    assert result.status == 1, result.stderr
    assert json.loads(result.stdout)["config_path"] == str(tmp_path / "pyproject.toml")


def test_w1_retired_non_interactive_alias_keeps_dry_semantics(tmp_path: Path) -> None:
    source = project(tmp_path, "non-interactive = true\n")
    assert resolved(source, command="dry").interactive is False
    assert resolved(source, "--interactive", command="dry").interactive is True


@pytest.mark.parametrize("boundary", ["pyproject.toml", ".git", ".hg"])
def test_subproject_ignores_formatter_configuration_above_its_boundary(
    tmp_path: Path, boundary: str
) -> None:
    """Cold read 7: a member must not silently inherit the enclosing checkout's tools."""
    from towel.formatting import _pre_commit_formatter, _ruff_configuration
    from towel.project_layout import find_project_root

    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    hook = "repos:\n  - repo: local\n    hooks:\n      - id: black\n"
    (tmp_path / ".pre-commit-config.yaml").write_text(hook)
    member = tmp_path / "packages/billing"
    module = member / "src/totals.py"
    module.parent.mkdir(parents=True)
    module.write_text("total = 0\n")
    if boundary == "pyproject.toml":
        (member / boundary).write_text('[project]\nname="billing"\nversion="1"\n')
    else:
        (member / boundary).mkdir()
    assert _pre_commit_formatter(module) is None
    assert _ruff_configuration(module) is None
    assert find_project_root(module).is_relative_to(member)

    (member / ".pre-commit-config.yaml").write_text(hook)
    (member / "ruff.toml").touch()
    assert _pre_commit_formatter(module) == "black"
    assert _ruff_configuration(module) == member / "ruff.toml"


@pytest.mark.parametrize("boundary", ["pyproject.toml", ".git", ".hg"])
def test_member_annotations_use_only_local_checker_settings(tmp_path: Path, boundary: str) -> None:
    """Cold read 7: a parent's mypy flags, CI command and checker targets cannot govern a member."""
    from towel.mypy_ci_policy import declared_mypy_root
    from towel.unification.annotation_wiring import declared_oldest_python, mypy_ladder_flags

    (tmp_path / "pyproject.toml").write_text(
        '[tool.mypy]\nstrict=true\npython_version="3.9"\n[tool.pyright]\npythonVersion="3.8"\n'
    )
    workflow = tmp_path / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("jobs:\n  lint:\n    steps:\n      - run: python -m mypy --strict .\n")
    member = tmp_path / "billing"
    member.mkdir()
    path = member / "amount.py"
    path.touch()
    if boundary == "pyproject.toml":
        (member / boundary).touch()
    else:
        (member / boundary).mkdir()
    assert declared_oldest_python(path) is None
    assert declared_mypy_root(path) is None
    assert not any(mypy_ladder_flags(path).values())

    (member / "mypy.ini").write_text("[mypy]\nstrict=true\npython_version=3.10\n")
    assert declared_oldest_python(path) == (3, 10)
    assert all(mypy_ladder_flags(path).values())

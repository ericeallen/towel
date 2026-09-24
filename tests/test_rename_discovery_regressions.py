# Copyright 2025-2026 Eric Allen
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

"""Renaming needs a complete consumer inventory and exact user selection."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_cli_integration import invoke
from towel.cli import _find_extracted_helpers
from towel.source_files import python_sources

HELPER = "def __extracted_func_0(value):\n    return value + 1\n"


def _mapping(root: Path, renames: dict[str, str]) -> Path:
    path = root / "renames.json"
    path.write_text(json.dumps(renames))
    return path


def _execute(path: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(path)], capture_output=True, text=True, timeout=5, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("file_filter", ["a.py", "./a.py"])
def test_file_selection_is_exact_and_still_updates_consumers(
    tmp_path: Path, file_filter: str
) -> None:
    (tmp_path / "a.py").write_text(HELPER)
    (tmp_path / "ba.py").write_text(HELPER)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "a.py").write_text(HELPER)
    main = tmp_path / "main.py"
    main.write_text(
        "from a import __extracted_func_0 as chosen\n"
        "from ba import __extracted_func_0 as other\n"
        "from pkg.a import __extracted_func_0 as nested\n"
        "print(chosen(2), other(3), nested(4))\n"
    )
    expected = _execute(main)
    mapping = _mapping(tmp_path, {"__extracted_func_0": "increment"})
    result = invoke(
        ["rename-helpers", str(tmp_path), "--file", file_filter, "--rename-file", str(mapping)]
    )
    assert result.status == 0, result.stderr
    assert "def increment(" in (tmp_path / "a.py").read_text()
    assert (tmp_path / "ba.py").read_text() == HELPER
    assert (package / "a.py").read_text() == HELPER
    assert "from a import increment as chosen" in main.read_text()
    assert _execute(main) == expected


@pytest.mark.parametrize(
    "filters",
    [
        ["--file", "main.py"],
        ["--function", "named_helper"],
        ["--file", "main.py", "--function", "named_helper"],
    ],
)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_filters_select_parameters_of_previously_named_helpers(
    tmp_path: Path, filters: list[str], asynchronous: bool
) -> None:
    main = tmp_path / "main.py"
    definition = "def named_helper(__param_0):\n    return __param_0 + 1\n"
    source = (
        "import asyncio\nasync " + definition + "print(asyncio.run(named_helper(__param_0=3)))\n"
        if asynchronous
        else definition + "print(named_helper(__param_0=3))\n"
    )
    main.write_text(source)
    expected = _execute(main)
    mapping = _mapping(tmp_path, {"named_helper.__param_0": "value"})
    result = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping), *filters])
    assert result.status == 0, (result.stdout, result.stderr)
    assert "named_helper(value)" in main.read_text()
    assert "__param_0" not in main.read_text()
    assert _execute(main) == expected


def test_default_inventory_still_only_offers_generated_helpers(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(HELPER + "def named_helper(value):\n    return value\n")
    helpers = _find_extracted_helpers(tmp_path, None, None)
    assert [name for _, name, _, _ in helpers] == ["__extracted_func_0"]


def test_discovery_supports_a_single_source_file(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text(HELPER)
    assert python_sources(source) == [source]
    alias = tmp_path / "alias.py"
    alias.symlink_to(source)
    assert python_sources(alias) == []


def test_missing_explicit_root_has_no_sources(tmp_path: Path) -> None:
    assert python_sources(tmp_path / "missing" / "project") == []


def test_mapping_outside_function_filter_is_refused_without_changes(tmp_path: Path) -> None:
    main = tmp_path / "main.py"
    source = HELPER + HELPER.replace("__extracted_func_0", "__extracted_func_1")
    main.write_text(source)
    mapping = _mapping(tmp_path, {"__extracted_func_1": "increment"})
    result = invoke(
        [
            "rename-helpers",
            str(tmp_path),
            "--function",
            "__extracted_func_0",
            "--rename-file",
            str(mapping),
            "--json",
        ]
    )
    assert result.status == 2
    assert json.loads(result.stdout)["applied"] is False
    assert main.read_text() == source


@pytest.mark.parametrize("directory", [".venv", "env", "custom_environment"])
def test_environment_fixtures_do_not_enter_rename_inventory(tmp_path: Path, directory: str) -> None:
    environment = tmp_path / directory
    environment.mkdir()
    (environment / "pyvenv.cfg").write_text("home = /not/an/interpreter\n")
    incompatible_fixture = 'print "Python 2 fixture in a dependency"\n'
    (environment / "fixture.py").write_text(incompatible_fixture)
    main = tmp_path / "main.py"
    main.write_text(HELPER + "print(__extracted_func_0(3))\n")
    expected = _execute(main)
    mapping = _mapping(tmp_path, {"__extracted_func_0": "increment"})
    result = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping)])
    assert result.status == 0, (result.stdout, result.stderr)
    assert "def increment(" in main.read_text()
    assert _execute(main) == expected
    assert (environment / "fixture.py").read_text() == incompatible_fixture


def test_an_unreadable_consumer_directory_aborts_before_any_rename(tmp_path: Path) -> None:
    definition = tmp_path / "a.py"
    definition.write_text(HELPER)
    restricted = tmp_path / "consumers"
    restricted.mkdir()
    (restricted / "__init__.py").write_text("")
    consumer = restricted / "use.py"
    source = "from a import __extracted_func_0\nVALUE = __extracted_func_0(3)\n"
    consumer.write_text(source)
    mapping = _mapping(tmp_path, {"a.py:__extracted_func_0": "increment"})
    # Search is allowed so probing pyvenv.cfg succeeds; listing is denied.
    # Mode 000 would fail before os.walk's historically swallowed scandir error.
    restricted.chmod(0o111)
    try:
        if os.access(restricted, os.R_OK):
            pytest.skip("The current user can bypass directory read permissions")
        with pytest.raises(PermissionError):
            python_sources(tmp_path)
        result = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping)])
        assert result.status != 0
        assert "Permission denied" in result.stderr
        assert definition.read_text() == HELPER
    finally:
        restricted.chmod(0o755)
    assert consumer.read_text() == source

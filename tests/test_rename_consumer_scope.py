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

"""A selected definition cannot leave an enclosing project's consumers behind."""

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from towel.changes import apply_changes
from towel.renaming import plan_renames
from tests.test_cli_integration import invoke

HELPER = "def __extracted_func_0(__param_0):\n    return __param_0 + 1\n"


def _project(root: Path, layout: str, consumer: str) -> Path:
    (root / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "0"\n')
    package = root / layout / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "from .a import __extracted_func_0\nfrom .a import __extracted_func_0 as exported\n"
    )
    helper = package / "a.py"
    helper.write_text(HELPER)
    source = "import sys\nsys.path.insert(0, " + repr(str(root / layout)) + ")\n" + consumer
    (root / "main.py").write_text(source)
    return helper


def _run(root: Path) -> bytes:
    completed = subprocess.run(
        [sys.executable, str(root / "main.py")], capture_output=True, timeout=10
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@pytest.mark.parametrize("layout", ["", "src"])
@pytest.mark.parametrize("target_kind", ["file", "package"])
@pytest.mark.parametrize(
    "consumer",
    [
        "from pkg.a import __extracted_func_0\nprint(__extracted_func_0(3))\n",
        "from pkg.a import __extracted_func_0 as run\nprint(run(3))\n",
        "import pkg.a\nprint(pkg.a.__extracted_func_0(3))\n",
        "from pkg import __extracted_func_0\nprint(__extracted_func_0(3))\n",
    ],
)
def test_outside_consumers_refuse_atomically_then_root_selection_succeeds(
    tmp_path: Path, layout: str, target_kind: str, consumer: str
) -> None:
    helper = _project(tmp_path, layout, consumer)
    target = helper if target_kind == "file" else helper.parent
    expected = _run(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.py")}
    with pytest.raises(ValueError, match="outside.*target|consumer.*scope"):
        plan_renames(target, [("__extracted_func_0", "increment", helper)])
    assert {path: path.read_bytes() for path in before} == before
    plan, count = plan_renames(tmp_path, [("__extracted_func_0", "increment", helper)])
    assert count > 0
    apply_changes(plan)
    assert "def increment(" in helper.read_text()
    assert _run(tmp_path) == expected == b"4\n"


@pytest.mark.parametrize("target_kind", ["file", "package"])
def test_outside_keyword_consumer_refuses_parameter_only_batch(
    tmp_path: Path, target_kind: str
) -> None:
    helper = _project(
        tmp_path, "", "from pkg.a import __extracted_func_0 as run\nprint(run(__param_0=3))\n"
    )
    target = helper if target_kind == "file" else helper.parent
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.py")}
    with pytest.raises(ValueError, match="outside.*target|consumer.*scope"):
        plan_renames(target, [("__extracted_func_0.__param_0", "value", None)])
    assert {path: path.read_bytes() for path in before} == before
    plan, _ = plan_renames(tmp_path, [("__extracted_func_0.__param_0", "value", helper)])
    apply_changes(plan)
    assert _run(tmp_path) == b"4\n"


def test_complete_file_scope_remains_supported(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text(HELPER + "print(__extracted_func_0(3))\n")
    plan, count = plan_renames(source, [("__extracted_func_0", "increment", None)])
    assert count == 2
    apply_changes(plan)
    assert _run(tmp_path) == b"4\n"


def test_preserved_export_alias_can_keep_outside_consumer_unchanged(tmp_path: Path) -> None:
    helper = _project(tmp_path, "", "from pkg import exported\nprint(exported(3))\n")
    # This stable user alias needs no edit in the outside consumer.
    package_init = helper.parent / "__init__.py"
    package_init.write_text("from .a import __extracted_func_0 as exported\n")
    main = tmp_path / "main.py"
    before = main.read_bytes()
    plan, _ = plan_renames(helper.parent, [("__extracted_func_0", "increment", helper)])
    apply_changes(plan)
    assert main.read_bytes() == before
    assert _run(tmp_path) == b"4\n"


def test_narrow_parameter_selection_leaves_other_definitions_alone(tmp_path: Path) -> None:
    chosen = tmp_path / "chosen.py"
    chosen.write_text(HELPER + "print(__extracted_func_0(__param_0=3))\n")
    outside = tmp_path / "outside.py"
    outside.write_text(HELPER)
    before = outside.read_bytes()
    plan, _ = plan_renames(chosen, [("__extracted_func_0.__param_0", "value", None)])
    apply_changes(plan)
    assert "def __extracted_func_0(value)" in chosen.read_text()
    assert outside.read_bytes() == before


def test_unmarked_namespace_requires_explicit_complete_consumer_scope(tmp_path: Path) -> None:
    """A bare namespace package cannot establish its enclosing project's boundary."""
    package = tmp_path / "pkg"
    package.mkdir()
    helper = package / "a.py"
    helper.write_text(HELPER)
    main = tmp_path / "main.py"
    main.write_text("from pkg.a import __extracted_func_0\nprint(__extracted_func_0(3))\n")
    mapping = tmp_path / "renames.json"
    mapping.write_text(json.dumps({"__extracted_func_0": "increment"}))
    before = {helper: helper.read_bytes(), main: main.read_bytes()}
    refused = invoke(["rename-helpers", str(package), "--rename-file", str(mapping), "--json"])
    assert refused.status == 2
    result = json.loads(refused.stdout)
    assert result["applied"] is False
    assert "--complete-scope" in result["error"]
    assert {path: path.read_bytes() for path in before} == before
    applied = invoke(
        ["rename-helpers", str(tmp_path), "--complete-scope", "--rename-file", str(mapping)]
    )
    assert applied.status == 0, (applied.stdout, applied.stderr)
    assert _run(tmp_path) == b"4\n"


def test_complete_scope_flag_does_not_hide_known_outside_consumers(tmp_path: Path) -> None:
    """An acknowledgement cannot bypass a discovered enclosing project's consumers."""
    helper = _project(
        tmp_path, "", "from pkg.a import __extracted_func_0\nprint(__extracted_func_0(3))\n"
    )
    mapping = tmp_path / "renames.json"
    mapping.write_text(json.dumps({"a.py:__extracted_func_0": "increment"}))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.py")}
    result = invoke(
        [
            "rename-helpers",
            str(helper.parent),
            "--complete-scope",
            "--rename-file",
            str(mapping),
            "--json",
        ]
    )
    assert result.status == 2
    assert "outside the selected target" in json.loads(result.stdout)["error"]
    assert {path: path.read_bytes() for path in before} == before


def test_unreadable_outside_subtree_cannot_appear_consumer_free(tmp_path: Path) -> None:
    """Broader read-only discovery fails loudly before a selected definition changes."""
    helper = _project(tmp_path, "", "from pkg import exported\nprint(exported(3))\n")
    restricted = tmp_path / "tests"
    restricted.mkdir()
    (restricted / "consumer.py").write_text("from pkg.a import __extracted_func_0\n")
    before = helper.read_bytes()
    restricted.chmod(0o111)
    try:
        if os.access(restricted, os.R_OK):
            pytest.skip("The current user can bypass directory read permissions")
        with pytest.raises(PermissionError):
            plan_renames(helper.parent, [("__extracted_func_0", "increment", helper)])
    finally:
        restricted.chmod(0o700)
    assert helper.read_bytes() == before


def test_relative_target_keeps_definition_selection_separate_from_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Canonical consumer paths must still match definitions selected relatively."""
    helper = _project(
        tmp_path, "", "from pkg.a import __extracted_func_0\nprint(__extracted_func_0(3))\n"
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="outside the selected target"):
        plan_renames(Path("pkg"), [("__extracted_func_0", "increment", helper)])
    plan, _ = plan_renames(Path("."), [("__extracted_func_0", "increment", helper)])
    apply_changes(plan)
    assert _run(tmp_path) == b"4\n"

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

"""The JSON inventory a naming assistant reads before proposing renames."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from towel.cli import _find_extracted_helpers, helper_inventory


def _project(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "a.py").write_text(
        "def __extracted_func_0(__param_0, __param_1, items):\n"
        "    if not __param_0():\n"
        "        raise ValueError(__param_1)\n"
        "    return [__param_1 for _ in items]\n"
        "class Box:\n"
        "    def _extracted_func_1(self, __param_0):\n"
        "        return __param_0(self.width)\n"
        "    def area(self):\n"
        "        return self._extracted_func_1(lambda w: w * self.height)\n"
        "def run(self, items):\n"
        "    return __extracted_func_0(lambda: self.email, 'missing', items)\n"
    )
    (tmp_path / "pkg" / "b.py").write_text(
        "from pkg.a import __extracted_func_0\n"
        "def other(obj, items):\n"
        "    return __extracted_func_0(lambda: obj.phone, 'absent', items)\n"
    )
    return tmp_path


def test_inventory_describes_scope_parameters_and_bindings(tmp_path: Path) -> None:
    target = _project(tmp_path)
    inventory = helper_inventory(target, _find_extracted_helpers(target, None, None))
    by_name = {entry["name"]: entry for entry in inventory["helpers"]}
    module_helper = by_name["__extracted_func_0"]
    assert module_helper["scope"] == "module" and module_helper["renameable"] is True
    assert module_helper["rename_key"] == "pkg/a.py:__extracted_func_0"
    parameters = {p["name"]: p for p in module_helper["parameters"]}
    assert parameters["__param_0"]["kind"] == "thunk"
    assert parameters["__param_1"]["kind"] == "value"
    assert parameters["items"]["kind"] == "value"
    assert parameters["__param_0"]["rename_key"] == "pkg/a.py:__extracted_func_0.__param_0"
    assert sorted(b["expression"] for b in parameters["__param_0"]["bindings"]) == [
        "obj.phone",
        "self.email",
    ]
    assert sorted(b["expression"] for b in parameters["__param_1"]["bindings"]) == [
        "'absent'",
        "'missing'",
    ]
    assert {call["file"] for call in module_helper["calls"]} == {"pkg/a.py", "pkg/b.py"}
    method = by_name["_extracted_func_1"]
    assert method["scope"] == "class:Box" and method["renameable"] is True
    kinds = [p["kind"] for p in method["parameters"]]
    assert kinds == ["receiver", "lifted"]
    assert method["parameters"][1]["bindings"][0]["expression"] == "lambda w: w * self.height"
    assert "def _extracted_func_1" in method["source"]


def test_cli_list_json_is_parseable(tmp_path: Path) -> None:
    target = _project(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-m", "towel.cli", "rename-helpers", str(target), "--list", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    assert {entry["name"] for entry in payload["helpers"]} == {
        "__extracted_func_0",
        "_extracted_func_1",
    }
    assert "parameter" in payload["mapping_format"]


def test_cli_rename_file_reports_structured_result_and_rejection(tmp_path: Path) -> None:
    target = _project(tmp_path)
    mapping = tmp_path / "renames.json"
    mapping.write_text(
        json.dumps(
            {
                "pkg/a.py:__extracted_func_0": "require_email",
                "pkg/a.py:__extracted_func_0.__param_0": "current_email",
                "_extracted_func_1": "_scaled_width",
            }
        )
    )
    command = [
        sys.executable,
        "-m",
        "towel.cli",
        "rename-helpers",
        str(target),
        "--rename-file",
        str(mapping),
        "--json",
    ]
    dry = subprocess.run([*command, "--preview"], capture_output=True, text=True, check=True)
    assert json.loads(dry.stdout)["applied"] is False and json.loads(dry.stdout)["changes"] > 0
    assert "__extracted_func_0" in (target / "pkg" / "a.py").read_text()
    applied = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(applied.stdout)["applied"] is True
    text = (target / "pkg" / "a.py").read_text() + (target / "pkg" / "b.py").read_text()
    assert "require_email(current_email, __param_1, items)" in text
    assert "if not current_email():" in text and "self._scaled_width(lambda w:" in text
    assert "__extracted_func_0" not in text and "_extracted_func_1" not in text
    mapping.write_text(json.dumps({"pkg/a.py:require_email.__param_1": "items"}))
    rejected = subprocess.run([*command, "--preview"], capture_output=True, text=True, check=False)
    assert rejected.returncode == 2
    payload = json.loads(rejected.stdout)
    assert payload["applied"] is False and "collide" in payload["error"]


def test_inventory_includes_before_after_from_the_dry_sidecar(tmp_path: Path) -> None:
    import contextlib
    import io

    from towel.cli import _change_sidecar_path, _write_change_sidecar
    from towel.unification.refactor_engine import UnificationRefactorEngine

    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "m.py").write_text(
        "def f(a):\n    x = a + 1\n    y = x * 2\n    z = y + compute(a)\n    return z\n\n"
        "def g(b):\n    x = b + 1\n    y = x * 2\n    z = y + compute(b)\n    return z\n\n"
        "def compute(v):\n    return v\n"
    )
    out = tmp_path / "cleaned"
    # A generated helper is what the sidecar names; ``g`` reusing ``f`` logs nothing.
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(src), str(out), progress="none")
        # The engine recorded the true original block and generated call per site.
        assert engine.change_log, "a refactoring should have been applied and logged"
        _write_change_sidecar(engine, str(out))

    assert _change_sidecar_path(out).is_file()

    inventory = helper_inventory(out, _find_extracted_helpers(out, None, None))
    entries = inventory["helpers"]
    assert entries, "the cleaned tree should contain at least one helper"
    changed = [e for e in entries if e["changes"]]
    assert changed, "at least one helper carries before/after changes from the sidecar"
    for change in changed[0]["changes"]:
        assert change["before"] and change["after"]
        # 'before' is the original block; 'after' is the generated call.
        assert "compute(" in change["before"]
        assert change["after"].startswith("return ") and "(" in change["after"]


def test_inventory_has_empty_changes_without_a_sidecar(tmp_path: Path) -> None:
    target = _project(tmp_path)
    inventory = helper_inventory(target, _find_extracted_helpers(target, None, None))
    for entry in inventory["helpers"]:
        assert entry["changes"] == []


def test_read_change_sidecar_accepts_only_its_own_shape(tmp_path: Path, caplog) -> None:
    import json
    import logging

    from towel.cli import _read_change_sidecar

    sidecar = tmp_path / "sidecar.json"
    record = {"file": "m.py", "line": 3, "before": "x = 1", "after": "x = h()"}

    sidecar.write_text(json.dumps({"helpers": {"h": [record]}}))
    assert _read_change_sidecar(sidecar) == {"h": [record]}

    assert _read_change_sidecar(tmp_path / "missing.json") == {}
    sidecar.write_text("{not json")
    assert _read_change_sidecar(sidecar) == {}
    sidecar.write_text(json.dumps({"helpers": [record]}))
    assert _read_change_sidecar(sidecar) == {}

    with caplog.at_level(logging.WARNING, logger="towel"):
        sidecar.write_text(json.dumps({"helpers": {"h": [{**record, "line": "3"}]}}))
        assert _read_change_sidecar(sidecar) == {}
    assert "malformed change records" in caplog.text


def test_the_sidecar_spells_each_file_relative_to_an_output_reached_through_a_link(
    tmp_path: Path,
) -> None:
    """Round-4 p2_sidecar_symlink_path: through macOS's ``/tmp`` link, every path climbed out of it."""
    import contextlib
    import io

    from towel.cli import _change_sidecar_path, _write_change_sidecar
    from towel.unification.refactor_engine import UnificationRefactorEngine

    real = tmp_path.resolve() / "real"
    (real / "src").mkdir(parents=True)
    (real / "src" / "m.py").write_text(
        "def f(a):\n    x = a + 1\n    y = x * 2\n    z = y + compute(a)\n    return z\n\n"
        "def g(b):\n    x = b + 1\n    y = x * 2\n    z = y + compute(b)\n    return z\n\n"
        "def compute(v):\n    return v\n"
    )
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(
            str(linked / "src"), str(linked / "src"), progress="none"
        )
        _write_change_sidecar(engine, str(linked / "src"))
    recorded = json.loads(_change_sidecar_path(real / "src").read_text())["helpers"]
    files = {change["file"] for changes in recorded.values() for change in changes}
    assert files == {"m.py"}, files

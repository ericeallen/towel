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

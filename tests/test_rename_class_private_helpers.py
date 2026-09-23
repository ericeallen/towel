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

"""Renaming a class-private helper, the only kind of method helper Towel writes.

A method helper is ``__extracted_func_0`` in its class's body, stored as
``_Box__extracted_func_0``, so it is named by its class,
``path.py:Box.__extracted_func_0``, and renamed with the references in that
class's body. The new name must be class-private too, since the privacy is
what keeps every subclass from overriding it. An explicit spelling of the
stored name anywhere else, the same private name in another class the
compiler would mangle alike, or a lookup by a computed name in the class's
body refuses the whole batch, and nothing is written.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, Mapping

import pytest

from towel.cli import _apply_rename_mappings, _find_extracted_helpers, helper_inventory
from towel.renaming import plan_renames

BOX = """
    class Box:
        def __init__(self, width, height):
            self.width = width
            self.height = height

        def area(self):
            return self.__extracted_func_0(lambda w: w * self.height, 1)

        def doubled(self):
            return self.__extracted_func_0(lambda w: w * self.height, 2)

        def __extracted_func_0(self, __param_0, __param_1):
            return __param_0(self.width) * __param_1


    class Crate:
        def __init__(self, depth):
            self.depth = depth

        def volume(self):
            return self.__extracted_func_0(3)

        def __extracted_func_0(self, __param_0):
            return self.depth * __param_0
    """

DRIVER = (
    "from pkg.a import Box, Crate\n"
    "print(Box(2, 3).area(), Box(2, 3).doubled(), Crate(4).volume())\n"
)


def _project(tmp_path: Path, files: Mapping[str, str]) -> Path:
    root = tmp_path / "project"
    for name, text in {"pkg/__init__.py": "", **files}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return root


def _run(root: Path, driver: str = DRIVER) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=root,
        capture_output=True,
        text=True,
        env={"PATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=60,
        check=False,
    )
    return completed.stdout + "|" + "".join(completed.stderr.strip().splitlines()[-1:])


def _snapshot(root: Path) -> Dict[Path, bytes]:
    return {path: path.read_bytes() for path in sorted(root.rglob("*.py"))}


def _rename(root: Path, mapping: Mapping[str, str]) -> int:
    return _apply_rename_mappings(root, mapping, False, quiet=True)


def test_a_class_private_helper_is_renamed_by_its_class(tmp_path: Path) -> None:
    root = _project(tmp_path, {"pkg/a.py": BOX})
    before = _run(root)
    changes = _rename(root, {"pkg/a.py:Box.__extracted_func_0": "__scaled_width"})
    assert changes == 3
    text = (root / "pkg" / "a.py").read_text()
    assert "def __scaled_width(self, __param_0, __param_1):" in text
    assert text.count("self.__scaled_width(lambda w:") == 2
    # Crate's helper of the same name is another attribute, _Crate__extracted_func_0.
    assert "return self.__extracted_func_0(3)" in text
    assert "def __extracted_func_0(self, __param_0):" in text
    assert _run(root) == before


def test_a_class_private_helpers_parameters_are_renamed_by_the_same_key(tmp_path: Path) -> None:
    root = _project(tmp_path, {"pkg/a.py": BOX})
    before = _run(root)
    _rename(
        root,
        {
            "pkg/a.py:Box.__extracted_func_0": "__scaled_width",
            "pkg/a.py:Box.__extracted_func_0.__param_0": "scale",
            "pkg/a.py:Box.__extracted_func_0.__param_1": "factor",
            "pkg/a.py:Crate.__extracted_func_0.__param_0": "multiplier",
        },
    )
    text = (root / "pkg" / "a.py").read_text()
    assert (
        "def __scaled_width(self, scale, factor):\n        return scale(self.width) * factor"
        in text
    )
    assert (
        "def __extracted_func_0(self, multiplier):\n        return self.depth * multiplier" in text
    )
    assert _run(root) == before


KEYWORDS = """
    class Box:
        def __init__(self, width):
            self.width = width

        def area(self):
            return self.__extracted_func_0(_Box__param_0=2)

        def broken(self):
            try:
                return self.__extracted_func_0(__param_0=2)
            except TypeError as error:
                return type(error).__name__

        def __extracted_func_0(self, __param_0):
            return self.width * __param_0
    """


def test_only_a_keyword_spelled_as_the_parameter_is_stored_follows_it(tmp_path: Path) -> None:
    """CPython mangles a private parameter's name but never a call's keyword.

    ``_Box__param_0=2`` reaches the parameter and is renamed with it;
    ``__param_0=2``, even in ``Box``'s body, never did, and raises as before.
    """
    root = _project(tmp_path, {"pkg/a.py": KEYWORDS})
    driver = "from pkg.a import Box\nprint(Box(5).area(), Box(5).broken())\n"
    before = _run(root, driver)
    assert before == "10 TypeError\n|"
    _rename(root, {"pkg/a.py:Box.__extracted_func_0.__param_0": "factor"})
    text = (root / "pkg" / "a.py").read_text()
    assert "(factor=2)" in text and "(__param_0=2)" in text
    assert _run(root, driver) == before


@pytest.mark.parametrize("new", ["scaled_width", "_scaled_width", "__scaled_width__", "___"])
def test_a_new_name_that_is_not_class_private_refuses_the_batch(tmp_path: Path, new: str) -> None:
    root = _project(
        tmp_path, {"pkg/a.py": BOX, "pkg/b.py": "def __extracted_func_1():\n    return 1\n"}
    )
    before = _snapshot(root)
    with pytest.raises(ValueError, match="class-private"):
        _rename(
            root,
            {"pkg/b.py:__extracted_func_1": "one", "pkg/a.py:Box.__extracted_func_0": new},
        )
    assert _snapshot(root) == before


@pytest.mark.parametrize(
    "consumer, message",
    [
        (
            "from pkg.a import Box\n\n\ndef reach(box):\n    return box._Box__extracted_func_0\n",
            "referenced as _Box__extracted_func_0",
        ),
        (
            "from pkg import a\n\n\nclass Box(a.Box):\n    def again(self):\n"
            "        return self.__extracted_func_0(len, 1)\n",
            "referenced as _Box__extracted_func_0",
        ),
        (
            "from pkg.a import Box\n\n\ndef reach(box):\n"
            "    return getattr(box, '_Box__extracted_func_0')\n",
            "in a string",
        ),
    ],
    ids=["explicit-mangled-spelling", "same-named-subclass", "string-spelling"],
)
def test_a_reference_outside_the_class_body_refuses_the_batch(
    tmp_path: Path, consumer: str, message: str
) -> None:
    root = _project(tmp_path, {"pkg/a.py": BOX, "pkg/b.py": consumer})
    before = _snapshot(root)
    with pytest.raises(ValueError, match=message):
        _rename(root, {"pkg/a.py:Box.__extracted_func_0": "__scaled_width"})
    assert _snapshot(root) == before


def test_a_lookup_by_computed_name_in_the_class_refuses_the_batch(tmp_path: Path) -> None:
    source = BOX.replace(
        "        def doubled(self):",
        "        def lookup(self, name):\n            return getattr(self, name)\n\n"
        "        def doubled(self):",
    )
    root = _project(tmp_path, {"pkg/a.py": source})
    before = _snapshot(root)
    with pytest.raises(ValueError, match="Dynamic attribute lookup"):
        _rename(root, {"pkg/a.py:Box.__extracted_func_0": "__scaled_width"})
    assert _snapshot(root) == before


def test_a_lookup_by_computed_name_elsewhere_cannot_reach_the_stored_name(tmp_path: Path) -> None:
    """``getattr(obj, name)`` outside ``Box`` would need the string ``_Box__...``, spelled nowhere."""
    consumer = "def reach(obj, name):\n    return getattr(obj, name)\n"
    root = _project(tmp_path, {"pkg/a.py": BOX, "pkg/b.py": consumer})
    before = _run(root)
    _rename(root, {"pkg/a.py:Box.__extracted_func_0": "__scaled_width"})
    assert "def __scaled_width" in (root / "pkg" / "a.py").read_text()
    assert _run(root) == before


@pytest.mark.parametrize(
    "addition",
    [
        "        def other(self):\n            self.__scaled_width = 1\n\n",
        "        def other(self):\n            __scaled_width = 1\n            return __scaled_width\n\n",
    ],
    ids=["attribute", "local"],
)
def test_a_new_name_the_class_already_stores_refuses_the_batch(
    tmp_path: Path, addition: str
) -> None:
    source = BOX.replace("        def doubled(self):", addition + "        def doubled(self):")
    root = _project(tmp_path, {"pkg/a.py": source})
    before = _snapshot(root)
    with pytest.raises(ValueError, match="already appears"):
        _rename(root, {"pkg/a.py:Box.__extracted_func_0": "__scaled_width"})
    assert _snapshot(root) == before


def test_a_nested_class_keeps_its_own_same_named_helper(tmp_path: Path) -> None:
    """``self.__extracted_func_0`` in a nested class's body is that class's, and is left alone."""
    source = BOX.replace(
        "    class Crate:",
        "    class Outer:\n"
        "        class Inner:\n"
        "            def __extracted_func_0(self):\n                return 'inner'\n\n"
        "            def call(self):\n                return self.__extracted_func_0()\n\n"
        "        def __extracted_func_0(self):\n            return 'outer'\n\n"
        "        def call(self):\n            return self.__extracted_func_0()\n\n\n"
        "    class Crate:",
    )
    root = _project(tmp_path, {"pkg/a.py": source})
    driver = DRIVER + "from pkg.a import Outer\nprint(Outer().call(), Outer.Inner().call())\n"
    before = _run(root, driver)
    _rename(root, {"pkg/a.py:Outer.__extracted_func_0": "__label"})
    text = (root / "pkg" / "a.py").read_text()
    assert "return self.__label()" in text and "def __label(self):" in text
    assert text.count("self.__extracted_func_0()") == 1  # Inner's own call
    assert _run(root, driver) == before
    _rename(root, {"pkg/a.py:Outer.Inner.__extracted_func_0": "__inner_label"})
    assert _run(root, driver) == before


def test_a_bare_key_for_a_class_private_helper_is_refused_with_its_class_key(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, {"pkg/a.py": BOX})
    before = _snapshot(root)
    with pytest.raises(ValueError, match="mangling"):
        _rename(root, {"__extracted_func_0": "__scaled_width"})
    assert _snapshot(root) == before


def test_a_class_named_like_another_that_stores_the_same_name_is_refused(
    tmp_path: Path,
) -> None:
    """Two classes named ``Box`` both store ``_Box__extracted_func_0``.

    If one derives from the other the two are one attribute, and a rename of
    either would change which one the other's methods reach; the classes are
    not resolved, so both keys are refused, the one without a file as naming
    two helpers.
    """
    root = _project(tmp_path, {"pkg/a.py": BOX, "pkg/c.py": BOX})
    before = _snapshot(root)
    with pytest.raises(ValueError, match="more than one"):
        plan_renames(root, [("Box.__extracted_func_0", "__scaled_width", None)])
    with pytest.raises(ValueError, match="another class named Box also defines"):
        plan_renames(root, [("Box.__extracted_func_0", "__scaled_width", root / "pkg" / "c.py")])
    assert _snapshot(root) == before


def test_the_inventory_keys_a_class_private_helper_by_its_class(tmp_path: Path) -> None:
    root = _project(tmp_path, {"pkg/a.py": BOX})
    inventory = helper_inventory(root, _find_extracted_helpers(root, None, None))
    entries = {(entry["scope"], entry["name"]): entry for entry in inventory["helpers"]}
    box = entries[("class:Box", "__extracted_func_0")]
    assert box["renameable"] is True
    assert box["rename_key"] == "pkg/a.py:Box.__extracted_func_0"
    assert [parameter["rename_key"] for parameter in box["parameters"]] == [
        "pkg/a.py:Box.__extracted_func_0.self",
        "pkg/a.py:Box.__extracted_func_0.__param_0",
        "pkg/a.py:Box.__extracted_func_0.__param_1",
    ]
    # Only Box's own calls: Crate's helper of the same name is another attribute.
    assert [call["line"] for call in box["calls"]] == [7, 10]
    assert [binding["expression"] for binding in box["parameters"][2]["bindings"]] == ["1", "2"]
    crate = entries[("class:Crate", "__extracted_func_0")]
    assert crate["rename_key"] == "pkg/a.py:Crate.__extracted_func_0"
    assert [call["line"] for call in crate["calls"]] == [21]
    assert "class-private" in inventory["mapping_format"]["notes"]


def test_the_cli_renames_a_class_private_helper_from_its_inventory_key(tmp_path: Path) -> None:
    root = _project(tmp_path, {"pkg/a.py": BOX})
    before = _run(root)
    inventory = json.loads(
        subprocess.run(
            [sys.executable, "-m", "towel.cli", "rename-helpers", str(root), "--list", "--json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    key = next(
        entry["rename_key"] for entry in inventory["helpers"] if entry["scope"] == "class:Box"
    )
    mapping = tmp_path / "renames.json"
    command = [
        sys.executable,
        "-m",
        "towel.cli",
        "rename-helpers",
        str(root),
        "--rename-file",
        str(mapping),
        "--json",
    ]
    mapping.write_text(json.dumps({key: "scaled_width"}))
    refused = subprocess.run(command, capture_output=True, text=True, check=False)
    assert refused.returncode == 2 and "class-private" in json.loads(refused.stdout)["error"]
    mapping.write_text(json.dumps({key: "__scaled_width"}))
    applied = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(applied.stdout)["applied"] is True
    assert "def __scaled_width" in (root / "pkg" / "a.py").read_text()
    assert _run(root) == before

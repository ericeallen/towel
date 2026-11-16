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

"""Round 2 defect 2: a project re-export must not hide a resource constructor."""

import ast
from pathlib import Path

import pytest

from tests.test_resource_guard import _refactored, _run
from towel.unification.import_graph import ImportGraphCache
from towel.unification.resource_guard import ResourceImports, resource_guard

pytestmark = pytest.mark.relaxed_mode

SOURCE = 'import os\n\nfrom pkg.compat import TemporaryDirectory\n\n\ndef first(x):\n    held = TemporaryDirectory()\n    backup = held\n    path = os.path.join(held.name, "data.txt")\n    held = None\n    with open(path, "w") as stream:\n        stream.write(str(x) * 3)\n    size = os.path.getsize(path)\n    print("first", os.path.exists(path), size)\n    return size\n\n\ndef second(x):\n    held = TemporaryDirectory()\n    backup = held\n    path = os.path.join(held.name, "data.txt")\n    held = None\n    with open(path, "w") as stream:\n        stream.write(str(x) * 3)\n    size = os.path.getsize(path)\n    if x:\n        print("second", os.path.exists(path), size)\n    return size\n\nfor v in (0, 1, 22):\n    print(first(v), second(v))\n'


@pytest.mark.parametrize("rebound", [True, False])
@pytest.mark.parametrize(
    "binding",
    [
        "from tempfile import TemporaryDirectory",
        "from tempfile import TemporaryDirectory as TD\nTemporaryDirectory = TD",
        "from .base import TemporaryDirectory",
        "from . import base\nTemporaryDirectory = base.TemporaryDirectory",
    ],
)
def test_project_reexports_preserve_resource_lifetime(
    tmp_path: Path, binding: str, rebound: bool
) -> None:
    """Review f_reexport: decline the rebound owner or return the stable owner."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "reexport"\nversion = "0"\n')
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "base.py").write_text("from tempfile import TemporaryDirectory\n")
    (package / "compat.py").write_text(binding + "\n")
    source = SOURCE if rebound else SOURCE.replace("    held = None\n", "")
    generated, original, changed = _refactored(tmp_path, source)
    assert _run(original)[0] == 0
    assert _run(changed) == _run(original), generated


@pytest.mark.parametrize(
    "statement, parameters, expected",
    [
        ("held = TD()", "", {"held"}),
        ("from pkg.compat import TD; held = TD()", "", {"held"}),
        ("held = TD()", "TD", set()),
        ("TD = make; held = TD()", "make", set()),
        ("watcher = ref(target)", "target", {"target"}),
    ],
)
def test_reexport_resolution_respects_local_bindings(
    tmp_path: Path, statement: str, parameters: str, expected: set[str]
) -> None:
    """Round 2 defect 2: following imports must retain lexical shadowing."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="probe"\nversion="0"\n')
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "compat.py").write_text(
        "from tempfile import TemporaryDirectory as TD\nfrom weakref import ref\n"
    )
    file = tmp_path / "work.py"
    source = f"from pkg.compat import TD, ref\ndef work({parameters}):\n    {statement}\n"
    file.write_text(source)
    module = ast.parse(source)
    function = module.body[1]
    assert isinstance(function, ast.FunctionDef)
    guard = resource_guard(function.body, module, ResourceImports(str(file), ImportGraphCache()))
    assert guard.refusal is None
    assert guard.names == expected


def test_reexport_cycles_terminate_and_reread_changed_sources(tmp_path: Path) -> None:
    """Round 2 defect 2: cyclic imports terminate and cached syntax follows current contents."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="probe"\nversion="0"\n')
    compat = tmp_path / "compat.py"
    compat.write_text("from base import TD\n")
    base = tmp_path / "base.py"
    base.write_text("from compat import TD\n")
    file = tmp_path / "work.py"
    source = "from compat import TD\ndef work():\n    held = TD()\n"
    file.write_text(source)
    module = ast.parse(source)
    function = module.body[1]
    assert isinstance(function, ast.FunctionDef)
    imports = ResourceImports(str(file), ImportGraphCache())
    assert resource_guard(function.body, module, imports).names == set()
    base.write_text("from tempfile import TemporaryDirectory as TD\n")
    assert resource_guard(function.body, module, imports).names == {"held"}

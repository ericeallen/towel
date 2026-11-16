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
import subprocess
import sys

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
        "from .base import *",
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


@pytest.mark.parametrize("strict", [False, True])
def test_directory_reexport_preserves_resource_lifetime(tmp_path: Path, strict: bool) -> None:
    """Review f_reexport: resolve constructors in the directory CLI's staged import model."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="probe"\nversion="0"\n')
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "compat.py").write_text("from tempfile import TemporaryDirectory\n")
    (package / "work.py").write_text(SOURCE.split("\nfor v in")[0])
    driver = tmp_path / "drive.py"
    driver.write_text(
        "import sys\nsys.path.insert(0, sys.argv[1])\nfrom pkg.work import first, second\n"
        "for v in (0, 1, 22):\n    print(first(v), second(v))\n"
    )
    output = tmp_path / "out" / "pkg"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel",
            "dry",
            str(package),
            str(output),
            "--no-types",
            "--no-format",
            "--no-interactive",
            "--progress",
            "none",
            "--strict" if strict else "--no-strict",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    runs = [
        subprocess.run(
            [sys.executable, str(driver), str(root)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        for root in (tmp_path, output.parent)
    ]
    assert runs[0].returncode == 0, runs[0].stderr
    assert (runs[0].returncode, runs[0].stdout, runs[0].stderr) == (
        runs[1].returncode,
        runs[1].stdout,
        runs[1].stderr,
    )
    if strict:
        assert (output / "work.py").read_bytes() == (package / "work.py").read_bytes()

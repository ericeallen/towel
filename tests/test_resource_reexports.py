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
from time import process_time
from unittest.mock import call, patch

import pytest

from tests.test_resource_guard import _refactored, _run
from towel.analysis_sources import sharing_analysis_sources
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


def test_generated_helper_ownership_follows_provider_changes(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="probe"\nversion="0"\n')
    client = tmp_path / "client.py"
    client.write_text(
        "from .provider import factory\n"
        "def __extracted_func():\n    return factory()\n"
        "value = __extracted_func()\n"
    )
    module = ast.parse(client.read_text())
    block = module.body[-1:]
    imports = ResourceImports(str(client), ImportGraphCache())
    provider = tmp_path / "provider.py"
    provider.write_text("def factory(): return 42\n")
    assert resource_guard(block, module, imports).refusal is None

    provider.write_text("from tempfile import TemporaryDirectory as factory\n")
    assert resource_guard(block, module, imports).refusal is not None


def test_stage_resolves_relative_reexports_per_importer(tmp_path: Path) -> None:
    from towel.unification.resource_guard import sharing_resource_answers

    (tmp_path / "pyproject.toml").write_text('[project]\nname="probe"\nversion="0"\n')
    for name, provider in (
        ("a", "from tempfile import TemporaryDirectory as factory\n"),
        ("b", "def factory(): return 42\n"),
    ):
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "p.py").write_text(provider)
        (package / "work.py").write_text("from .p import factory\nprint(factory())\n")

    graph = ImportGraphCache()
    with sharing_analysis_sources(), sharing_resource_answers(graph, tmp_path):
        for name, should_refuse in (("a", True), ("b", False)):
            client = tmp_path / name / "work.py"
            module = ast.parse(client.read_text())
            guard = resource_guard(module.body[-1:], module, ResourceImports(str(client), graph))
            assert (guard.refusal is not None) is should_refuse, name


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


@pytest.mark.parametrize("package_cycle", [False, True])
def test_branching_reexports_finish_within_timeout(tmp_path: Path, package_cycle: bool) -> None:
    """Two re-exports per layer give millions of paths to the same constructor."""
    if package_cycle:
        # As in toolz, a relative import can resolve through the package's own
        # star imports again, prepending ever more attributes to the same name.
        (tmp_path / "__init__.py").write_text("from .left0 import *\nfrom .right0 import *\n")
    layers = 2 if package_cycle else 23
    for layer in range(layers):
        for side in ("left", "right"):
            (tmp_path / f"{side}{layer}.py").write_text(
                f"from .left{layer + 1} import *\nfrom .right{layer + 1} import *\n"
                if layer + 1 < layers
                else "from tempfile import TemporaryDirectory as TD\n"
            )
    source = "from .left0 import TD\nheld = TD()\n"
    (tmp_path / "work.py").write_text(source)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import ast\n"
            "from pathlib import Path\n"
            "from towel.unification.import_graph import ImportGraphCache\n"
            "from towel.unification.resource_guard import ResourceImports, resource_guard\n"
            "file = Path('work.py').resolve()\n"
            "module = ast.parse(file.read_text())\n"
            "guard = resource_guard(module.body, module, ResourceImports(str(file), ImportGraphCache()))\n"
            "assert guard.names == {'held'} and guard.refusal is None, guard\n",
        ],
        cwd=tmp_path,
        timeout=10,
        check=True,
    )


def test_reexport_depth_counts_the_shortest_path(tmp_path: Path) -> None:
    """A constructor at hop 32 is visible, even if another path needs 33 hops."""
    for index in range(33):
        (tmp_path / f"link{index}.py").write_text(
            f"from .link{index + 1} import TD\n"
            if index < 32
            else "from tempfile import TemporaryDirectory as TD\n"
        )
    imports = ResourceImports(str(tmp_path / "work.py"), ImportGraphCache())
    constructor = "tempfile.TemporaryDirectory"
    for origins, expected in [
        ([".link0.TD"], False),
        ([".link0.TD", ".link1.TD"], True),
        ([".link1.TD"], True),
        ([".link0.TD"], False),
    ]:
        assert (constructor in imports.follow(frozenset(origins))) is expected


def test_generated_helper_checks_share_the_module_scan(tmp_path: Path) -> None:
    """A generated helper can occur in many overlapping candidate blocks."""
    source = "def __extracted_func():\n    return 42\n"
    module = ast.parse(source + "result = __extracted_func()\n")
    block = module.body[-1:]
    imports = ResourceImports(str(tmp_path / "work.py"), ImportGraphCache())
    with patch.object(ast, "walk", wraps=ast.walk) as walks:
        for _ in range(100):
            assert resource_guard(block, module, imports).refusal is None
    assert walks.call_args_list.count(call(module)) <= 16


def test_overlapping_blocks_share_call_lookups(tmp_path: Path) -> None:
    """Each block includes a large statement whose calls all resolve to a parameter."""
    source = "def work(f):\n    value = [\n" + "        f(1),\n" * 1000 + "    ]\n"
    module = ast.parse(source)
    function = module.body[0]
    assert isinstance(function, ast.FunctionDef)
    imports = ResourceImports(str(tmp_path / "work.py"), ImportGraphCache())
    start = process_time()
    for _ in range(450):
        assert resource_guard(function.body, module, imports).refusal is None
    # 450 checks took 0.04 CPU seconds under 3.13 coverage and over 4 s before sharing.
    assert process_time() - start < 0.22

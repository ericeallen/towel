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

"""An inert module body does not justify advancing its observable import."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import subprocess
import sys

import pytest

from towel.import_model import NameStatus
from towel.unification.import_graph import (
    ImportChange,
    ImportExtent,
    ImportGraphCache,
    _import_edges,
    import_change,
)
from towel.unification.refactor_engine import UnificationRefactorEngine


def _program(
    root: Path, source: str, *, initializer: str | None = None, regular: bool = True
) -> tuple[Path, Path]:
    (root / "pyproject.toml").write_text('[project]\nname="closed-imports"\nversion="0"\n')
    package = root / "pkg"
    package.mkdir()
    if regular:
        (package / "__init__.py").write_text("")
    (root / "run.py").write_text("import pkg.b\n")
    host_directory = package
    module = "a"
    if initializer is not None:
        host_directory = package / "nested"
        host_directory.mkdir()
        (host_directory / "__init__.py").write_text(initializer)
        module = "nested.a"
    host = host_directory / "a.py"
    host.write_text(source)
    borrower = package / "b.py"
    borrower.write_text(f"def second():\n    return 1\n\nimport pkg.{module}\n")
    return host, borrower


@pytest.mark.parametrize(
    "source",
    [
        '"module documentation"\npass\nVALUE = 3\n',
        "def first(value):\n    return value\n",
        'def first(value: "int") -> "int":\n    return value\n',
        "async def first():\n    import importlib\n    return importlib\n",
        "from __future__ import annotations\ndef first(value: Unbound):\n    return value\n",
        "import math\ndef first():\n    return 1\n",
        "from . import b\ndef first():\n    return 1\n",
        "VALUE = unbound\n",
        "VALUE = 1 / 0\n",
        "def first(value=unbound):\n    return value\n",
        "def first(value: Unbound):\n    return value\n",
        "@staticmethod\ndef first():\n    return 1\n",
        "class First:\n    pass\n",
        "print('host')\n",
    ],
    ids=[
        "literal-bindings",
        "plain-function",
        "quoted-annotations",
        "deferred-function-import",
        "standard-future",
        "ordinary-import",
        "borrower-import",
        "eager-name",
        "arithmetic-error",
        "eager-default",
        "eager-annotation",
        "decorator",
        "class-construction",
        "statement-effect",
    ],
)
def test_the_host_body_does_not_authorize_an_earlier_import(tmp_path: Path, source: str) -> None:
    host, borrower = _program(tmp_path, source)
    changed = import_change(str(host), str(borrower), ImportGraphCache())
    assert changed is ImportChange.IMPORT_ORDER


@pytest.mark.parametrize("initializer", ["VALUE = 1\n", "import math\n", "VALUE = unbound\n"])
def test_inert_package_initializers_do_not_authorize_an_earlier_import(
    tmp_path: Path, initializer: str
) -> None:
    host, borrower = _program(tmp_path, "def first():\n    return 1\n", initializer=initializer)
    changed = import_change(str(host), str(borrower), ImportGraphCache())
    assert changed is ImportChange.IMPORT_ORDER


@pytest.mark.parametrize("relative", [False, True])
def test_a_local_future_provider_cannot_qualify_as_an_inert_import(
    tmp_path: Path, relative: bool
) -> None:
    prefix = "." if relative else ""
    host, borrower = _program(tmp_path, f"from {prefix}__future__ import annotations\n")
    provider = host.parent if relative else tmp_path
    (provider / "__future__.py").write_text("annotations = 0\n")
    assert import_change(str(host), str(borrower), ImportGraphCache()) is ImportChange.IMPORT_ORDER


def test_a_quiet_name_read_can_depend_on_code_before_the_late_import(tmp_path: Path) -> None:
    """No call in the host is insufficient: its eager read can fail when advanced."""
    host, borrower = _program(tmp_path, "VALUE = towel_order_bound\n")
    original = (
        "import builtins\nclass Ready:\n"
        "    print('borrower ready')\n    builtins.towel_order_bound = 7\n"
        "import pkg.a\nprint(pkg.a.VALUE)\n"
    )

    def run(source: str) -> subprocess.CompletedProcess[str]:
        borrower.write_text(source)
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys; sys.path.insert(0, {str(tmp_path)!r}); import pkg.b",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    before = run(original)
    assert before.returncode == 0 and before.stdout == "borrower ready\n7\n", before.stderr
    assert import_change(str(host), str(borrower), ImportGraphCache()) is ImportChange.IMPORT_ORDER
    advanced = run(original.replace("import builtins\n", "import builtins\nimport pkg.a\n"))
    assert advanced.returncode != 0 and advanced.stdout == ""
    assert "NameError: name 'towel_order_bound' is not defined" in advanced.stderr


@pytest.mark.parametrize("external_binding", [False, True], ids=["initializer", "external-code"])
def test_importing_an_inert_host_still_overwrites_its_parent_binding(
    tmp_path: Path, external_binding: bool
) -> None:
    body = (
        "def {name}(seed):\n    total = seed + 1\n    result = total * 2\n"
        "    added = result + 3\n    return added * {factor}\n"
    )
    host, borrower = _program(tmp_path, body.format(name="first", factor=5))
    package = host.parent
    (package / "__init__.py").write_text("" if external_binding else "a = 7\n")
    borrower.write_text(
        "import pkg\n"
        + body.format(name="second", factor=7)
        + "print(pkg.a + 1)\nimport pkg.a\nprint(second(3), pkg.a.first(2))\n"
    )

    def run() -> subprocess.CompletedProcess[str]:
        external = "import pkg; pkg.a = 7; " if external_binding else ""
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys; sys.path.insert(0, {str(tmp_path)!r}); {external}import pkg.b",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    before = run()
    assert before.returncode == 0 and before.stdout == "8\n77 45\n", before.stderr
    assert import_change(str(host), str(borrower), ImportGraphCache()) is ImportChange.IMPORT_ORDER
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    after = run()
    assert after.returncode == 0 and after.stdout == before.stdout, after.stderr


@pytest.mark.parametrize("regular", [False, True], ids=["namespace", "regular"])
@pytest.mark.parametrize("statement", ["from pkg import a", "from . import a"])
def test_imported_members_are_possible_reach_but_not_guaranteed_module_loads(
    tmp_path: Path, regular: bool, statement: str
) -> None:
    host, borrower = _program(tmp_path, "def first():\n    return 1\n", regular=regular)
    borrower.write_text(f"{statement}\ndef second():\n    return 2\n")
    cache = ImportGraphCache()
    program = cache.program_for(borrower)
    guaranteed: tuple[ImportExtent, ...] = ("leading", "unconditionally")
    possible: tuple[ImportExtent, ...] = ("at_import", "everywhere")
    for extent in guaranteed:
        edges = _import_edges(borrower, program, cache, extent)
        assert edges is not None and host not in edges.files
    for extent in possible:
        edges = _import_edges(borrower, program, cache, extent)
        assert edges is not None and host in edges.files


@pytest.mark.parametrize("regular", [False, True], ids=["namespace", "regular"])
@pytest.mark.parametrize(
    "statement",
    ["import pkg.a", "import pkg.a as loaded", "from pkg.a import first", "from .a import first"],
)
def test_an_explicit_module_load_remains_sufficient_for_a_helper_import(
    tmp_path: Path, regular: bool, statement: str
) -> None:
    host, borrower = _program(tmp_path, "def first():\n    return 1\n", regular=regular)
    borrower.write_text(f"{statement}\ndef second():\n    return 2\n")
    assert import_change(str(host), str(borrower), ImportGraphCache()) is None


@pytest.mark.parametrize("regular", [False, True], ids=["namespace", "regular"])
@pytest.mark.parametrize("statement", ["from pkg import a", "import pkg", ""])
def test_a_new_submodule_import_cannot_replace_an_external_package_attribute(
    tmp_path: Path, regular: bool, statement: str
) -> None:
    body = (
        "def {name}(seed):\n    total = seed + 1\n    result = total * 2\n"
        "    added = result + 3\n    return added * {factor}\n"
    )
    host, borrower = _program(tmp_path, body.format(name="first", factor=5), regular=regular)
    borrower.write_text(statement + "\n" + body.format(name="second", factor=7))

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
                "import pkg; pkg.a = 7; import pkg.b; print(pkg.a + 1, pkg.b.second(3))",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    before = run()
    assert before.returncode == 0 and before.stdout == "8 77\n", before.stderr
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(
            str(host.parent), str(host.parent), progress="none"
        )
    after = run()
    assert after.returncode == 0 and after.stdout == before.stdout, after.stderr


@pytest.mark.parametrize("regular", [False, True], ids=["namespace", "regular"])
def test_explicitly_loaded_module_still_shares_a_helper_end_to_end(
    tmp_path: Path, regular: bool
) -> None:
    body = (
        "def {name}(seed):\n    total = seed + 1\n    result = total * 2\n"
        "    added = result + 3\n    return added * {factor}\n"
    )
    host, borrower = _program(tmp_path, body.format(name="first", factor=5), regular=regular)
    borrower.write_text("import pkg.a\n" + body.format(name="second", factor=7))
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(host.parent), str(host.parent), progress="none"
        )
    assert sum(count for count, _ in results.values()) > 0
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
            "import pkg.a; pkg.a = 7; import pkg.b; print(pkg.a + 1, pkg.b.second(3))",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and result.stdout == "8 77\n", result.stderr


def test_a_partially_initialized_import_does_not_prove_its_later_imports_ran(
    tmp_path: Path,
) -> None:
    body = (
        "def {name}(seed):\n    total = seed + 1\n    result = total * 2\n"
        "    added = result + 3\n    return added * {factor}\n"
    )
    host, borrower = _program(tmp_path, body.format(name="first", factor=5))
    package = host.parent
    (package / "__init__.py").write_text("a = 7\n")
    (package / "bridge.py").write_text(
        "import importlib\nimportlib.import_module('pkg.b')\nimport pkg.a\n"
    )
    borrower.write_text(
        "import pkg.bridge\nimport pkg\n"
        + body.format(name="second", factor=7)
        + "print(pkg.a + 1)\n"
    )

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
                "import pkg.bridge; import pkg.a; import pkg.b; "
                "print(pkg.b.second(3), pkg.a.first(2))",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    before = run()
    assert before.returncode == 0 and before.stdout == "8\n77 45\n", before.stderr
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    after = run()
    assert after.returncode == 0 and after.stdout == before.stdout, after.stderr


def test_ambiguous_absolute_imports_do_not_guarantee_all_candidate_locations(
    tmp_path: Path,
) -> None:
    host, borrower = _program(tmp_path, "def first():\n    return 1\n")
    copy = tmp_path / "build" / "lib" / "pkg"
    copy.mkdir(parents=True)
    (copy / "__init__.py").write_text("")
    copied_host = copy / "a.py"
    copied_host.write_text(host.read_text())
    borrower.write_text("import pkg.a\ndef second():\n    return 2\n")
    cache = ImportGraphCache()
    program = cache.program_for(borrower)
    assert program.model.names["pkg"].status is NameStatus.AMBIGUOUS
    guaranteed: tuple[ImportExtent, ...] = ("leading", "unconditionally")
    possible: tuple[ImportExtent, ...] = ("at_import", "everywhere")
    for extent in guaranteed:
        edges = _import_edges(borrower, program, cache, extent)
        assert edges is not None and edges.files.isdisjoint({host, copied_host})
    for extent in possible:
        edges = _import_edges(borrower, program, cache, extent)
        assert edges is not None and {host, copied_host} <= edges.files

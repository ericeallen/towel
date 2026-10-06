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

"""An existing function never starts calling another existing function.

Rewriting ``f2`` as ``return f1(...)`` because the two bodies are the same
made ``f2`` look ``f1`` up in its module at every call, so ``mock.patch("c.f1")``,
or any other rebinding of ``c.f1``, changed ``f2`` too (audit case r06:
``PATCHED PATCHED``). A duplicate that is the whole body of a function is
extracted like any other: both functions call a new helper, and each still
depends only on itself and on the helper, which nothing outside can know to
patch.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict

import pytest

from tests.test_helpers import (
    module_functions,
    refactor_to_fixed_point_silently,
    unparsed_body,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

SAME_BODIES = """
def f1(xs):
    total = 0
    for x in xs:
        total += x * 2
    print("done", total)
    return total


def f2(ys):
    total = 0
    for y in ys:
        total += y * 2
    print("done", total)
    return total
"""

PATCHING_DRIVER = """
from unittest import mock
import c

with mock.patch("c.f1", return_value="PATCHED"):
    print(c.f1([1]), c.f2([2]))
with mock.patch("c.f2", return_value="PATCHED"):
    print(c.f1([1]), c.f2([2]))
c.f1 = lambda xs: "REBOUND"
print(c.f1([1]), c.f2([2]))
"""


def _write(root: Path, files: Dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"))


def _run(root: Path, script: str) -> str:
    completed = subprocess.run(
        [sys.executable, script],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return completed.stdout


def _refactor_module(path: Path) -> int:
    """Refactor one module to a fixed point in place; how many refactorings applied."""
    final, applied = refactor_to_fixed_point_silently(str(path), min_lines=3)
    path.write_text(final)
    return applied


def _refactor_directory(target: Path) -> int:
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(target), str(target), progress="none"
        )
    return sum(applied for applied, _ in results.values())


def _assert_owned_helper_call(
    function: ast.FunctionDef, helper: ast.FunctionDef, parameters: tuple[str, ...]
) -> None:
    """The whole body transfers original arguments, then calls a fresh owned ABI."""
    assert len(function.body) == len(parameters) + 2
    storage, *deletions, invocation = function.body
    assert isinstance(storage, ast.Assign) and len(storage.targets) == 1
    assert isinstance(storage.targets[0], ast.Name)
    box = storage.targets[0].id
    expected_storage = ast.parse(f"{box} = [{tuple(reversed(parameters))!r}]").body[0]
    assert isinstance(expected_storage, ast.Assign)
    expected_storage.value = ast.List(
        elts=[
            ast.Tuple(
                elts=[ast.Name(id=name, ctx=ast.Load()) for name in reversed(parameters)],
                ctx=ast.Load(),
            )
        ],
        ctx=ast.Load(),
    )
    assert ast.dump(storage) == ast.dump(expected_storage)
    assert all(
        isinstance(deletion, ast.Delete)
        and len(deletion.targets) == 1
        and isinstance(deletion.targets[0], ast.Name)
        for deletion in deletions
    )
    assert [
        target.id
        for deletion in deletions
        if isinstance(deletion, ast.Delete)
        for target in deletion.targets
        if isinstance(target, ast.Name)
    ] == list(parameters)
    calls = [
        node
        for node in ast.walk(invocation)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == helper.name
    ]
    assert len(calls) == 1
    call = calls[0]
    assert len(call.args) == len(parameters) + 1 and not call.keywords
    expected_arguments = [
        ast.parse(f"{box}[0][{len(parameters)-1-index}]", mode="eval").body
        for index in range(len(parameters))
    ]
    expected_arguments.append(ast.parse(f"{box}.pop()", mode="eval").body)
    assert [ast.dump(argument) for argument in call.args] == [
        ast.dump(argument) for argument in expected_arguments
    ]
    helper_parameters = helper.args.posonlyargs + helper.args.args
    assert len(helper_parameters) == len(call.args)
    holder = helper_parameters[-1].arg
    assert holder.startswith("_towel_owner")
    assert not any(
        isinstance(node, ast.Name) and node.id == holder
        for statement in helper.body
        for node in ast.walk(statement)
    )


def test_patching_one_function_leaves_its_duplicate_alone(tmp_path: Path) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, {"c.py": SAME_BODIES, "drive.py": PATCHING_DRIVER})
    assert _refactor_module(after / "c.py") > 0
    assert _run(after, "drive.py") == _run(before, "drive.py")
    functions = module_functions((after / "c.py").read_text())
    helpers = [name for name in functions if name.startswith("__extracted_func")]
    assert len(helpers) == 1
    _assert_owned_helper_call(functions["f1"], functions[helpers[0]], ("xs",))
    _assert_owned_helper_call(functions["f2"], functions[helpers[0]], ("ys",))


def test_a_function_in_another_module_is_not_called_in_place_of_a_duplicate(
    tmp_path: Path,
) -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": """
            from builtins import print as emit

            def fa(items):
                total = 0
                for item in items:
                    total += item * 3
                emit("done", total)
                return total
            """,
        "pkg/b.py": """
            import pkg.a
            from builtins import print as emit


            def fb(values):
                total = 0
                for value in values:
                    total += value * 3
                emit("done", total)
                return total
            """,
        "run.py": """
            from unittest import mock
            import pkg.a
            import pkg.b

            with mock.patch("pkg.a.fa", return_value="PATCHED"):
                print(pkg.a.fa([1]), pkg.b.fb([2]))
            """,
    }
    before, after = tmp_path / "before", tmp_path / "after"
    _write(before, files)
    _write(after, files)
    assert _refactor_directory(after / "pkg") > 0
    assert _run(after, "run.py") == _run(before, "run.py")
    assert "fa(" not in unparsed_body(module_functions((after / "pkg" / "b.py").read_text())["fb"])


@pytest.mark.parametrize("helper_name", ["__extracted_func_0", "square_shifted"])
def test_two_whole_bodies_both_become_calls_of_the_new_helper(
    tmp_path: Path, helper_name: str
) -> None:
    """Even where one of them is a helper an earlier pass inserted.

    Were either redirected to the other, a later patch of the one would
    change the other; so both are rewritten, which leaves the earlier helper
    an ownership transfer followed by a call of the new one.
    """
    source = f"""
        def {helper_name}(result, x):
            y = x + 10
            z = y ** 2
            result.append(z)


        def other(result, x):
            y = x + 10
            z = y ** 2
            result.append(z)
        """
    target = tmp_path / "m.py"
    _write(tmp_path, {"m.py": source})
    assert _refactor_module(target) > 0
    functions = module_functions(target.read_text())
    new = [name for name in functions if name not in {helper_name, "other"}]
    assert len(new) == 1
    _assert_owned_helper_call(functions[helper_name], functions[new[0]], ("result", "x"))
    _assert_owned_helper_call(functions["other"], functions[new[0]], ("result", "x"))
    driver = f"""from unittest import mock
import m
with mock.patch('m.{helper_name}', return_value='patched'):
    values = []
    m.other(values, 2)
    print(values)
with mock.patch('m.other', return_value='patched'):
    values = []
    m.{helper_name}(values, 3)
    print(values)
"""
    _write(tmp_path, {"drive.py": driver})
    observed = _run(tmp_path, "drive.py")
    _write(tmp_path, {"m.py": source})
    assert observed == _run(tmp_path, "drive.py")

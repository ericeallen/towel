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

"""A differing expression over names the block binds is passed as a function of them.

The user-perspective audit of 1.949, cases 24 and 42 (engine map D2): twins
that differ in an attribute or a method of a loop variable were declined as
``unification_failed``, because no argument evaluated at the call can read a
variable that exists only inside the block. The parameter is now a function
of that variable, ``lambda item: item.weight_kg``, which the helper calls
where the block read the attribute. That is sound in both modes
(docs/DECISIONS.md, "Relaxed mode passes arguments by value").

The helpers here run a program before and after refactoring;
``tests/test_by_value_arguments.py`` uses them too.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
from typing import List, Tuple

import pytest

from towel.api import UnificationRefactorEngine
from towel.unification.thunk_inlining import reads_first
from tests.test_cli_integration import invoke

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
UNTYPED = ["--no-types", "--no-format", "--progress", "none"]


def run_script(script: Path, *arguments: str) -> Tuple[int, str, str]:
    """A script's exit status, its output, and the last line of its error output."""
    result = subprocess.run(
        [sys.executable, str(script), *arguments],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=script.parent,
    )
    return result.returncode, result.stdout, (result.stderr.strip().splitlines() or [""])[-1]


def refactored(tmp_path: Path, source: str, *, strict: bool = False) -> Tuple[str, bool]:
    """``source`` refactored in one mode, and whether it still behaves as the original does.

    Small safe transformations are allowed (``skip_trivial_helpers=False``),
    as in the hostile batteries, so that an example can be a few lines long.
    """
    directory = tmp_path / ("strict" if strict else "relaxed")
    directory.mkdir(parents=True, exist_ok=True)
    original = directory / "original.py"
    original.write_text(source)
    changed = directory / "changed.py"
    changed.write_text(source)
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False, strict=strict)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_to_fixed_point(str(changed))
    return changed.read_text(), run_script(original) == run_script(changed)


def helper_calls(source: str) -> List[str]:
    """Every call of a generated helper in ``source``, unparsed, in source order."""
    found = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and "extracted_func" in ast.unparse(node.func)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    ]
    return [ast.unparse(node) for node in sorted(found, key=lambda node: node.lineno)]


def refactored_scenario(tmp_path: Path, fixture: str, *, strict: bool = False) -> Tuple[str, bool]:
    """An audit case refactored by the command line: its generated code, and whether its driver agrees."""
    case = tmp_path / Path(fixture).name
    for path in sorted((FIXTURES / fixture).rglob("*.py.txt")):
        target = case / path.relative_to(FIXTURES / fixture).with_suffix("")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    source, output = case / "src", case / "out"
    mode = "--strict" if strict else "--no-strict"
    result = invoke(["dry", str(source), str(output), "--no-interactive", mode, *UNTYPED])
    assert result.status == 0, result.stderr
    generated = "\n".join(path.read_text() for path in sorted(output.rglob("*.py")))
    driver = case / "driver.py"
    return generated, run_script(driver, str(output)) == run_script(driver, str(source))


# -- The audit's cases ---------------------------------------------------------


def test_a_method_of_a_loop_variable_becomes_a_callback(tmp_path: Path) -> None:
    """Audit case 24 (engine map D2): ``value.strip()`` against ``.lower()`` and ``.title()``.

    ``value`` exists only inside the block, so each site passes a function
    of it, and the helper calls that where the block called the method.
    """
    generated, same = refactored_scenario(tmp_path, "by_value/loop_method")
    assert helper_calls(generated) == [
        "__extracted_func_0(lambda value: value.strip(), columns, rows)",
        "__extracted_func_0(lambda value: value.lower(), columns, rows)",
        "__extracted_func_0(lambda value: value.title(), columns, rows)",
    ]
    assert "new_row[column] = __param_0(value)" in generated
    assert same


def test_an_attribute_of_a_loop_variable_becomes_a_callback(tmp_path: Path) -> None:
    """Audit case 42 (engine map D2): ``item.weight_kg`` against ``item.volume_l`` in a loop."""
    generated, same = refactored_scenario(tmp_path, "by_value/typed_attr")
    assert helper_calls(generated) == [
        "__extracted_func_0(lambda item: item.weight_kg, items)",
        "__extracted_func_0(lambda item: item.volume_l, items)",
    ]
    assert "total += __param_0(item) * item.quantity" in generated
    assert same


# -- Hostile cases -------------------------------------------------------------

LATER_BOUND_NAME = """\
class Row:
    def __init__(self, alpha, beta):
        self.alpha = alpha
        self.beta = beta


def alphas(rows, step):
    out = []
    for row in rows:
        print("row")
        out.append(row.alpha[step])
        step = step + 1
    return out


def betas(rows, step):
    out = []
    for row in rows:
        print("row")
        out.append(row.beta)
        step = step + 1
    return out


rows = [Row("abc", 1), Row("def", 2)]
print(alphas(rows, 0), betas(rows, 0))
"""


@pytest.mark.parametrize("strict", [False, True])
def test_a_callback_never_reads_a_name_the_block_rebinds_from_the_caller(
    tmp_path: Path, strict: bool
) -> None:
    """``row.alpha[step]`` reads ``step``, which the loop rebinds after it.

    A callback of ``row`` alone would read the caller's ``step``, still 0
    on the second row. Whatever Towel does here must behave as before.
    """
    generated, same = refactored(tmp_path, LATER_BOUND_NAME, strict=strict)
    assert same
    assert "lambda row: row.alpha[step]" not in generated


COMPREHENSION_TARGET = """\
class Item:
    def __init__(self, name, price):
        self.name = name
        self.price = price


def names(items):
    print("collecting")
    found = [item.name for item in items if item.price]
    print("collected", len(found))
    return found


def prices(items):
    print("collecting")
    found = [item.price for item in items if item.price]
    print("collected", len(found))
    return found


stock = [Item("towel", 3), Item("free", 0)]
print(names(stock), prices(stock))
"""


@pytest.mark.parametrize("strict", [False, True])
def test_an_attribute_of_a_comprehension_target_becomes_a_callback(
    tmp_path: Path, strict: bool
) -> None:
    """Sound in both modes: the callback is called where the block read the attribute."""
    generated, same = refactored(tmp_path, COMPREHENSION_TARGET, strict=strict)
    assert "lambda item: item.name" in generated and "lambda item: item.price" in generated
    assert "[__param_0(item) for item in items if item.price]" in generated
    assert same


SKIPPED_READ = """\
def alpha(flag):
    if flag:
        value = 4
    print("start")
    print("middle")
    result = value if flag else 0
    print("end")
    return result


def beta(flag):
    if flag:
        value = 4
    print("start")
    print("middle")
    result = flag and value
    print("end")
    return result


for function in (alpha, beta):
    for flag in (False, True):
        try:
            print(function(flag))
        except Exception as error:
            print(type(error).__name__)
"""


@pytest.mark.parametrize("strict", [False, True])
def test_a_callback_never_reads_a_name_the_expression_may_skip(
    tmp_path: Path, strict: bool
) -> None:
    """Review of lane ``byvalue``, defect 2: ``callback(value)`` read ``value`` where the block did not.

    ``value if flag else 0`` and ``flag and value`` read ``value`` only when
    ``flag`` is true, and ``value`` is bound only then. A function of
    ``value`` called as ``__param_0(value)`` reads it on every path and
    raises ``UnboundLocalError`` for ``flag=False``.
    """
    generated, same = refactored(tmp_path, SKIPPED_READ, strict=strict)
    assert "(value)" not in generated
    assert same


@pytest.mark.parametrize(
    "text, names, first",
    [
        ("item.weight_kg", ["item"], True),
        ("value.strip()", ["value"], True),
        ("item.price * item.quantity", ["item"], True),
        ("a + b", ["a", "b"], True),
        ("a + b", ["b", "a"], False),  # the function would read b first
        ("value if flag else 0", ["value"], False),
        ("flag and value", ["value"], False),
        ("a.x + b.y", ["a", "b"], False),  # a.x may fail before b is read
        ("len(value)", ["value"], False),
        ("(item := other).name", ["item"], False),
    ],
)
def test_a_callback_takes_only_names_its_expression_reads_first(
    text: str, names: List[str], first: bool
) -> None:
    assert reads_first(ast.parse(text, mode="eval").body, names) is first

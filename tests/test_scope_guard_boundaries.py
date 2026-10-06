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

"""An unrelated scope constraint must not hide a sound smaller extraction.

These are capability requirements, paired with the existing hostile tests
for moved nonlocal bindings and private-name mangling. A whole-function
refusal must not be restored merely because it makes those guards simpler.
"""

from __future__ import annotations

import ast
import contextlib
import io
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.typed_fixtures import requires_mypy
from towel.type_inference import MypyInferrer
from towel.unification.pair_evaluation import _module_namespace_names
from towel.unification.refactor_engine import UnificationRefactorEngine


def _extract(tmp_path: Path, source: str, *, safe_arithmetic: bool = False) -> tuple[str, int]:
    if safe_arithmetic:
        untouched, refused = _extract(tmp_path / "original", source)
        assert refused == 0 and untouched == textwrap.dedent(source)
        source = source.replace(
            "total = value + 1\n                doubled = total * 2\n                print(doubled)\n                return doubled",
            "assert isinstance(value, int)\n                print((value + 1) * 2)\n                return (value + 1) * 2",
        )
        tmp_path = tmp_path / "companion"
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "before.py"
    original = textwrap.dedent(source)
    path.write_text(original)
    after = tmp_path / "after.py"
    engine = UnificationRefactorEngine(min_lines=3, annotate_helpers=False)
    with contextlib.redirect_stdout(io.StringIO()):
        result, count, _ = engine.refactor_to_fixed_point(
            str(path), progress="none", output_path=str(after)
        )
    assert path.read_text() == original
    runs = [
        subprocess.run([sys.executable, str(file)], capture_output=True, text=True, check=True)
        for file in (path, after)
    ]
    assert (runs[0].stdout, runs[0].stderr) == (runs[1].stdout, runs[1].stderr)
    return result, count


def test_nonlocal_declaration_does_not_block_an_unrelated_suffix(tmp_path: Path) -> None:
    result, count = _extract(
        tmp_path,
        """
        def outer():
            count = 0
            def first(value):
                nonlocal count
                count += 1
                total = value + 1
                doubled = total * 2
                print(doubled)
                return doubled
            def second(value):
                nonlocal count
                count += 2
                total = value + 1
                doubled = total * 2
                print(doubled)
                return doubled
            left, right = first(3), second(5)
            return left, right, count
        print(outer())
    """,
        safe_arithmetic=True,
    )
    assert count >= 1, result
    helpers = [
        node
        for node in ast.walk(ast.parse(result))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]
    assert helpers and all(
        not any(isinstance(node, ast.Nonlocal) for node in ast.walk(helper)) for helper in helpers
    )


def test_private_attribute_elsewhere_does_not_block_a_public_suffix(tmp_path: Path) -> None:
    result, count = _extract(
        tmp_path,
        """
        class First:
            __secret = 7
            def calculate(self, value):
                print(self.__secret)
                total = value + 1
                doubled = total * 2
                print(doubled)
                return doubled
        class Second:
            __secret = 11
            def calculate(self, value):
                print(self.__secret)
                total = value + 1
                doubled = total * 2
                print(doubled)
                return doubled
        print(First().calculate(3), Second().calculate(5))
    """,
        safe_arithmetic=True,
    )
    assert count >= 1, result
    helpers = [
        node
        for node in ast.walk(ast.parse(result))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]
    assert helpers and all("__secret" not in ast.unparse(helper) for helper in helpers)


@pytest.mark.parametrize(
    "source,expected",
    [
        ("def helper(value):\n    local: AnnotationOnly = value\n    return local", frozenset()),
        ("def helper(value):\n    value: AnnotationOnly\n    return value", frozenset()),
        (
            "def helper(value):\n    local: AnnotationOnly = factory(value)\n    return local",
            frozenset({"factory"}),
        ),
        (
            "def helper(value):\n    class Inner:\n        local: Evaluated = value\n    return Inner",
            frozenset({"Evaluated"}),
        ),
        (
            "def helper(value):\n    def inner(arg: Evaluated):\n        return arg\n    return inner(value)",
            frozenset({"Evaluated"}),
        ),
        (
            "def helper(value):\n    value[index()]: AnnotationOnly\n    return value",
            frozenset({"index"}),
        ),
    ],
)
def test_module_reads_distinguish_local_annotations_from_evaluated_names(
    source: str, expected: frozenset[str]
) -> None:
    """CPython's symbol table lists local annotation names even though no lookup runs.

    Keep real reads in values, annotation targets, class bodies and nested
    function signatures. The analysis must not mutate the caller-owned AST.
    """
    helper = ast.parse(source).body[0]
    assert isinstance(helper, ast.FunctionDef)
    original = ast.dump(helper)
    result = _module_namespace_names(helper)
    assert result == (expected, frozenset())
    assert ast.dump(helper) == original


@requires_mypy
@pytest.mark.parametrize("type_checking_only", [False, True])
def test_annotation_only_cross_module_type_is_not_a_runtime_argument(
    tmp_path: Path, type_checking_only: bool
) -> None:
    """A local TypedDict annotation must neither force a runtime import nor a parameter.

    This is the recorded uvicorn extraction defect. Require an applied typed
    extraction, unchanged runtime output, and absence of the spurious type
    argument; silently refusing the duplicate would not fix the capability.
    """
    project = tmp_path / "before"
    package = project / "pkg"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    (package / "__init__.py").write_text("")
    (package / "types.py").write_text(
        "from typing import TypedDict\nclass BodyEvent(TypedDict):\n    body: bytes\n"
    )
    template = (
        "from __future__ import annotations\nfrom builtins import print as emit\n{imports}\n"
        "def {name}(queue: list[BodyEvent]) -> list[BodyEvent]:\n"
        "    emit('start')\n"
        "    empty: BodyEvent = {{'body': {payload}}}\n"
        "    queue.append(empty)\n"
        "    return queue\n"
    )
    (package / "host.py").write_text(
        template.format(imports="from pkg.types import BodyEvent", name="respond", payload="b''")
    )
    annotation_import = "from pkg.types import BodyEvent"
    if type_checking_only:
        annotation_import = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg.types import BodyEvent"
    (package / "borrower.py").write_text(
        template.format(
            imports="import pkg.host\n" + annotation_import, name="fail", payload="b'oops'"
        )
    )
    originals = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
    output = tmp_path / "after" / "pkg"
    checker = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, cross_module_helpers=True, type_oracle=checker
        )
        with contextlib.redirect_stdout(io.StringIO()):
            result, reason = engine.refactor_directory_to_fixed_point(
                str(package), str(output), progress="none"
            )
    finally:
        checker.close()
    assert sum(count for count, _ in result.values()) > 0, reason
    assert {path: path.read_bytes() for path in originals} == originals
    for path in output.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name:
                assert all(argument.arg != "BodyEvent" for argument in node.args.args)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and "extracted_func" in node.func.id
            ):
                assert not any(
                    isinstance(argument, ast.Name) and argument.id == "BodyEvent"
                    for argument in node.args
                )
    scenario = (
        "from pkg.host import respond; from pkg.borrower import fail; print(respond([]), fail([]))"
    )
    runs = [
        subprocess.run(
            [sys.executable, "-c", scenario],
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
        )
        for directory in (project, output.parent)
    ]
    assert (runs[0].stdout, runs[0].stderr) == (runs[1].stdout, runs[1].stderr)

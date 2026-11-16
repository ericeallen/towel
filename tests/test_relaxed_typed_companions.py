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

"""Review item 5: relaxed extraction preserves runtime values and checked public contracts."""

import ast
import sys
from pathlib import Path
import textwrap
from collections.abc import Mapping

import pytest

from tests.test_relaxed_early_companions import _observe
from towel.type_inference import CheckResult, CheckSuccess, MypyInferrer, TypeDiagnostic
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.models import MethodKind

pytestmark = pytest.mark.relaxed_mode


def _checked_behavior(root: Path, source: str, driver: str, bad_use: str = "") -> None:
    path = root / "program.py"
    source = textwrap.dedent(source)
    (root / "pyproject.toml").write_text("[tool.mypy]\nstrict=true\n")
    path.write_text(source + "\n" + driver)
    before = _observe(path)
    assert before[0] == 0 and not before[2], before
    path.write_text(source)
    checker = MypyInferrer()
    try:
        assert checker.check(str(path), source) == CheckSuccess()
        engine = UnificationRefactorEngine(
            strict=False,
            skip_trivial_helpers=False,
            min_lines=2,
            reuse_existing_functions=False,
            type_oracle=checker,
        )
        final, _, _ = engine.refactor_to_fixed_point(str(path), progress="none")
        assert checker.check(str(path), final) == CheckSuccess()
        if bad_use:
            rejected = checker.check(str(path), final + "\n" + bad_use)
            assert isinstance(rejected, CheckSuccess) and rejected.errors
        path.write_text(final + "\n" + driver)
        assert _observe(path) == before
    finally:
        checker.close()


@pytest.mark.parametrize("bound", ["", ", bound=float"])
def test_indexed_generic_local_preserves_identity(tmp_path: Path, bound: str) -> None:
    source = f"""from typing import TypeVar
T = TypeVar('T'{bound})
U = TypeVar('U'{bound})
def first(items: list[T]) -> T:
    head = items[0]
    assert len(items) > 0
    result = [head]
    return result[0]
def second(items: list[U]) -> U:
    head = next(iter(items))
    if not items:
        raise ValueError('empty')
    result = [head]
    return result[0]
"""
    _checked_behavior(
        tmp_path,
        source,
        "value = 2.5\nprint(first([value]) is value, second([value]) is value)",
        "wrong: str = first([2.5])",
    )


@pytest.mark.parametrize(
    "name",
    [
        "test_inferred_local_callable_keeps_argument_and_result_correlation",
        "test_constrained_local_specializations_are_not_silently_narrowed",
        "test_the_type_the_block_saw_replaces_a_wider_declared_one",
    ],
)
def test_checked_local_expression_behavior(tmp_path: Path, name: str) -> None:
    from tests.relaxed_early_cases import CASES

    case = next(case for case in CASES if case.name == name)
    _checked_behavior(tmp_path, case.source, case.driver)


@pytest.mark.parametrize("method_kind", ["instance", "staticmethod", "classmethod"])
def test_checked_generic_method_dispatch(tmp_path: Path, method_kind: MethodKind) -> None:
    from tests.test_generic_methods import _ordinary_source

    _checked_behavior(
        tmp_path,
        _ordinary_source(method_kind),
        "class Derived(Calculator):\n    pass\ninstance = Derived()\nprint(instance.integers(2, 3), instance.strings('a', 'b'), instance.calls, Derived.calls, Calculator.calls)",
        "Calculator().integers(1, 'wrong')",
    )


@pytest.mark.parametrize("syntax", ["legacy", "pep695"])
@pytest.mark.parametrize("static", [False, True])
def test_generic_class_public_contract(tmp_path: Path, syntax: str, static: bool) -> None:
    if syntax == "pep695" and sys.version_info < (3, 12):
        pytest.skip("PEP 695 syntax requires Python 3.12")
    source = (
        'from typing import Generic, TypeVar\nT = TypeVar("T")\nclass Box(Generic[T]):\n'
        if syntax == "legacy"
        else "class Box[T]:\n"
    )
    source += "    def __init__(self, value: T) -> None:\n        self.value = value\n"
    for name in ("first", "second"):
        source += (
            f"    @staticmethod\n    def {name}(items: list[T]) -> T:\n        result = items[0]\n        return result\n"
            if static
            else f"    def {name}(self) -> T:\n        result = self.value\n        return result\n"
        )
    driver = (
        "value = object()\nprint(Box.first([value]) is value, Box.second([value]) is value)"
        if static
        else "value = object()\nbox = Box(value)\nprint(box.first() is value, box.second() is value)"
    )
    bad = 'wrong: int = Box.first(["wrong"])' if static else 'wrong: int = Box("wrong").first()'
    _checked_behavior(tmp_path, source, driver, bad)


def test_existing_type_error_is_preserved(tmp_path: Path) -> None:
    from tests.test_differential_baseline import TWINS, _Oracle

    program, consumer = tmp_path / "program.py", tmp_path / "consumer.py"
    program.write_text(TWINS)
    consumer.write_text('broken: int = "wrong"\n')
    existing = TypeDiagnostic(str(consumer.resolve()), "Incompatible types [assignment]", 1)
    oracle = _Oracle(lambda _: CheckSuccess((existing,)))
    engine = UnificationRefactorEngine(strict=False, skip_trivial_helpers=False, type_oracle=oracle)
    proposals = engine.analyze_files([str(program), str(consumer)])
    assert proposals
    before = program.read_text()
    driver = "\nprint(first(2), second(3))\n"
    program.write_text(before + driver)
    expected = _observe(program)
    program.write_text(before)
    final = engine.apply_refactoring(str(program), proposals[0])
    program.write_text(final + driver)
    assert _observe(program) == expected
    assert expected[0] == 0 and oracle.inferences > 0
    assert consumer.read_text() == 'broken: int = "wrong"\n'


@pytest.mark.parametrize("fallback", ["unannotated", "any"])
def test_rejected_generic_declarations_do_not_leak(tmp_path: Path, fallback: str) -> None:
    from tests.test_generic_extraction import ADDITION, _project
    from tests.test_generic_verification import _declarations, _helper, _error
    from tests.test_type_run_policy import _Oracle

    path = _project(tmp_path, ADDITION + "\n    def legacy(value):\n        return value\n")
    source = path.read_text()

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        proposed = sources[str(path)]
        if proposed == source:
            return CheckSuccess()
        if _declarations(proposed):
            return _error(path)
        helper = _helper(proposed)
        annotations = [arg.annotation for arg in helper.args.args] + [helper.returns]
        accepted = all(annotation is None for annotation in annotations)
        if fallback == "any":
            accepted = all(
                isinstance(annotation, ast.Attribute) and annotation.attr == "Any"
                for annotation in annotations
            )
        return CheckSuccess() if accepted else _error(path)

    oracle = _Oracle(verdict)
    engine = UnificationRefactorEngine(
        strict=False,
        min_lines=2,
        skip_trivial_helpers=False,
        reuse_existing_functions=False,
        type_oracle=oracle,
    )
    proposal = engine.analyze_file(str(path))[0]
    final = engine.apply_refactoring(str(path), proposal)
    assert not _declarations(final)
    assert engine.apply_refactoring(str(path), proposal) == final
    driver = '\nprint(integers(2, 3), strings("a", "b"))\n'
    path.write_text(source + driver)
    before = _observe(path)
    path.write_text(final + driver)
    assert before[0] == 0 and not before[2], before
    assert _observe(path) == before


def test_existing_mypy_error_moves_without_changing_values(tmp_path: Path) -> None:
    from tests.test_differential_baseline import TWINS, _mypy_run

    source = TWINS + "\nWRONG: str = first(1)\n"
    path = tmp_path / "m.py"
    path.write_text(source + "\nprint(WRONG, first(3), second(4))\n")
    expected = _observe(path)
    final, applied = _mypy_run(tmp_path, source)
    assert applied > 0
    checker = MypyInferrer()
    try:
        before = checker.check(str(path), source)
        after = checker.check(str(path), final)
        assert isinstance(before, CheckSuccess) and isinstance(after, CheckSuccess)
        assert [error.message for error in after.errors] == [
            error.message for error in before.errors
        ]
        assert before.errors
    finally:
        checker.close()
    path.write_text(final + "\nprint(WRONG, first(3), second(4))\n")
    assert expected[0] == 0 and _observe(path) == expected


def test_unresolved_import_still_refuses_unchecked_changes(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys
    from tests.test_differential_baseline import PACKAGING, CLI_CALCULATIONS

    (tmp_path / "pyproject.toml").write_text(PACKAGING)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    blind = package / "blind.py"
    blind.write_text("import towel_absent_module\n\n" + CLI_CALCULATIONS)
    before = blind.read_text()
    seen = package / "seen.py"
    seen.write_text(CLI_CALCULATIONS)
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "from towel.cli import main; main()",
            "dry",
            "pkg",
            "pkg",
            "--no-format",
            "--no-interactive",
            "--progress",
            "none",
            "--no-strict",
        ],
        cwd=tmp_path,
        env={**os.environ, "TOWEL_WORKERS": "2"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert blind.read_text() == before
    assert "Cannot find implementation or library stub" in run.stderr
    driver = "\nprint(first(), second(), third())\n"
    final = seen.read_text()
    seen.write_text(CLI_CALCULATIONS + driver)
    expected = _observe(seen)
    seen.write_text(final + driver)
    assert expected[0] == 0 and _observe(seen) == expected


def test_unannotated_helper_does_not_enable_checking_an_unchecked_body(tmp_path: Path) -> None:
    from tests.test_argument_ownership import SOURCE

    source = (
        SOURCE.replace("events=[]", "events: list[str]=[]")
        .replace("return 11", "return opaque.missing(11)")
        .replace("return 12", "return opaque.missing(12)")
    )
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\ncheck_untyped_defs=false\n")
    path = tmp_path / "subject.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        strict=False, min_lines=2, skip_trivial_helpers=False, reuse_existing_functions=False
    )
    final, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert applied > 0
    checker = MypyInferrer()
    try:
        assert checker.check(str(path), source) == checker.check(str(path), final) == CheckSuccess()
    finally:
        checker.close()
    driver = "\nfrom types import SimpleNamespace\nopaque = SimpleNamespace(missing=lambda value: value)\nprint(f(None), g(None))\n"
    path.write_text(source + driver)
    before = _observe(path)
    path.write_text(final + driver)
    assert before[0] == 0 and not before[2]
    assert _observe(path) == before

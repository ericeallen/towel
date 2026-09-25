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

"""Method generalization retains class contracts and checks the actual host class."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
import sys
import textwrap

import pytest

from tests.test_generic_methods import _annotation, _project
from tests.test_generic_verification import _RecordingOracle, _declarations
from towel.type_inference import CheckSuccess, MypyInferrer, PyrightOracle, TypeOracle
from towel.unification.exceptions import RefactoringError
from towel.unification.models import is_generated_helper_name
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.fixture(params=["mypy", "pyright"])
def checker(request: pytest.FixtureRequest) -> Iterator[TypeOracle]:
    pytest.importorskip(request.param)
    oracle: TypeOracle = MypyInferrer() if request.param == "mypy" else PyrightOracle()
    try:
        yield oracle
    finally:
        oracle.close()


def _engine(checker: TypeOracle) -> UnificationRefactorEngine:
    return UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=checker
    )


def _method(source: str, owner: str) -> ast.FunctionDef:
    host = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == owner
    )
    return next(
        node
        for node in host.body
        if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
    )


def _module_function(source: str) -> ast.FunctionDef:
    return next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
    )


@pytest.mark.parametrize("syntax", ["legacy", "pep695"])
@pytest.mark.parametrize("method_kind", ["instance", "staticmethod"])
def test_class_bound_result_keeps_the_public_contract(
    tmp_path: Path, checker: TypeOracle, syntax: str, method_kind: str
) -> None:
    if syntax == "pep695" and sys.version_info < (3, 12):
        pytest.skip("PEP 695 source requires Python 3.12")
    header = (
        'from typing import Generic, TypeVar\nT = TypeVar("T")\nclass Box(Generic[T]):\n'
        if syntax == "legacy"
        else "class Box[T]:\n"
    )
    methods = ""
    for name in ("first", "second"):
        if method_kind == "staticmethod":
            methods += (
                f"    @staticmethod\n    def {name}(items: list[T]) -> T:\n"
                "        result = items[0]\n        return result\n"
            )
        else:
            methods += (
                f"    def {name}(self) -> T:\n"
                "        result = self.value\n        return result\n"
            )
    source = (
        header + "    def __init__(self, value: T) -> None:\n        self.value = value\n" + methods
    )
    path = _project(tmp_path, source)
    assert checker.check(str(path), source) == CheckSuccess()
    engine = _engine(checker)
    # A helper that dispatches on nothing is a module function (see
    # `test_receiver_dependency.py`); the class's contract must survive either way.
    static = method_kind == "staticmethod"
    proposal = next(
        proposal
        for proposal in engine.analyze_file(str(path))
        if proposal.insert_into_class == (None if static else "Box")
    )
    assert proposal.method_kind == (None if static else method_kind)
    rendered = engine.apply_refactoring(str(path), proposal)
    helper = _module_function(rendered) if static else _method(rendered, "Box")
    assert "Any" not in rendered
    assert checker.check(str(path), rendered) == CheckSuccess()
    assert path.read_text() == source
    if method_kind == "staticmethod":
        binder = _annotation(helper.returns)
        if binder == "T":
            assert _declarations(rendered) == _declarations(source)
        else:
            assert _declarations(rendered) != _declarations(source)
        assert _annotation(helper.args.args[0].annotation) == f"list[{binder}]"
        invalid = checker.check(str(path), rendered + '\nbad = Box[int].first(["x"])\n')
        assert isinstance(invalid, CheckSuccess) and invalid.errors
        invalid_helper_use = checker.check(
            str(path), rendered + f'\nbad_use: int = {helper.name}(["x"])\n'
        )
        assert isinstance(invalid_helper_use, CheckSuccess) and invalid_helper_use.errors
    else:
        assert _annotation(helper.returns) == "T"
        assert _declarations(rendered) == _declarations(
            source
        ), "Class binders need no fresh declaration"
        assert len(helper.args.args) == 1
        receiver = helper.args.args[0].annotation
        assert receiver is None or _annotation(receiver) == "Box[T]"


@pytest.mark.parametrize("descriptor", ["instance", "classmethod"])
def test_explicit_source_receiver_contract_is_not_silently_erased(
    tmp_path: Path, checker: TypeOracle, descriptor: str
) -> None:
    receiver = "self" if descriptor == "instance" else "cls"
    decorate = "" if descriptor == "instance" else "    @classmethod\n"
    methods = ""
    for name, kind in (("first", "int"), ("second", "str")):
        receiver_type = f"Box[{kind}]"
        if descriptor == "classmethod":
            receiver_type = f"type[{receiver_type}]"
        methods += (
            decorate
            + f'    def {name}({receiver}: "{receiver_type}", items: list[{kind}]) -> {kind}:\n'
            + f"        result = items[{receiver}.index]\n        return result\n"
        )
    # The methods read the receiver, so the helper stays a method of Box and the
    # contract this test is about is live. A block that never touches the
    # receiver gets a static helper instead, which has no receiver to contract
    # and may freshen the class variable (see `test_receiver_dependency.py`).
    source = (
        'from typing import Generic, TypeVar\nT = TypeVar("T")\nclass Box(Generic[T]):\n'
        "    index: int = 0\n" + methods
    )
    path = _project(tmp_path, source)
    assert checker.check(str(path), source) == CheckSuccess()
    oracle = _RecordingOracle(checker)
    engine = _engine(oracle)
    proposal = next(
        proposal
        for proposal in engine.analyze_file(str(path))
        if proposal.insert_into_class == "Box"
    )
    try:
        rendered = engine.apply_refactoring(str(path), proposal)
    except RefactoringError as error:
        assert "Every helper annotation variant" in str(error)
    else:
        assert checker.check(str(path), rendered) == CheckSuccess()
    assert oracle.checks
    assert all(
        _declarations(files[str(path)]) == _declarations(source) for files, _ in oracle.checks
    )
    assert path.read_text() == source


SIBLINGS = {
    # The shared block reads a member the classes inherit from their base.
    "inherited-member": """
        class Base:
            calls: int = 0
        class First(Base):
            def first(self, values: list[int]) -> int:
                self.calls += 1
                result = values[0]
                return result
        class Second(Base):
            def second(self, values: list[str]) -> str:
                self.calls += 1
                result = values[0]
                return result
        """,
    # The shared block reads a member each class declares for itself, typed
    # differently; the base has none.
    "own-members": """
        class Base:
            pass
        class First(Base):
            items: list[int] = [1]
            def first(self, value: int) -> list[int]:
                result = self.items.copy()
                result.append(value)
                return result
        class Second(Base):
            items: list[str] = ["a"]
            def second(self, value: str) -> list[str]:
                result = self.items.copy()
                result.append(value)
                return result
        """,
}


@pytest.mark.parametrize("program", sorted(SIBLINGS))
def test_a_block_sibling_classes_share_is_a_module_function_the_checker_accepts(
    tmp_path: Path, checker: TypeOracle, program: str
) -> None:
    """Methods of two classes share a helper only as a module function taking the receiver.

    The base the classes have in common used to take it as a generic method,
    which made it a member of every subclass of ``Base``; no class gains one
    now (docs/DECISIONS.md). The module function must pass the project's
    checker, or the extraction is declined and nothing changes.
    """
    path = _project(tmp_path, textwrap.dedent(SIBLINGS[program]))
    source = path.read_text()
    assert checker.check(str(path), source) == CheckSuccess()
    engine = _engine(checker)
    proposals = engine.analyze_file(str(path))
    assert proposals and all(proposal.insert_into_class is None for proposal in proposals)
    try:
        rendered = engine.apply_refactoring(str(path), proposals[0])
    except RefactoringError as error:
        assert "Every helper annotation variant" in str(error)
        assert engine.change_log == ()
    else:
        helper = _module_function(rendered)
        assert helper.args.args[0].arg == "self"
        assert checker.check(str(path), rendered) == CheckSuccess()
        arguments = ("[1]", '["x"]') if program == "inherited-member" else ("2", '"b"')
        driver = (
            "\nfirst, second = First(), Second()\n"
            f"results = (first.first({arguments[0]}), second.second({arguments[1]}),"
            f" first.first({arguments[0]}), vars(first), vars(second))\n"
        )
        assert _results(rendered + driver) == _results(source + driver)
    assert path.read_text() == source


def _results(program: str) -> object:
    """What ``program`` leaves in ``results`` when run in a namespace of its own."""
    namespace: dict[str, object] = {}
    exec(compile(program, "program.py", "exec"), namespace)
    return namespace["results"]


def test_class_attribute_names_do_not_capture_generated_type_variables(
    tmp_path: Path, checker: TypeOracle
) -> None:
    path = _project(
        tmp_path,
        textwrap.dedent("""
            class Calculator:
                _TowelT0 = 42
                _towel_typevar = "preserved"
                calls: int = 0
                def first(self, left: int, right: int) -> int:
                    self.calls += 1
                    result = left + right
                    return result
                def second(self, left: str, right: str) -> str:
                    self.calls += 1
                    result = left + right
                    return result
            """),
    )
    source = path.read_text()
    assert checker.check(str(path), source) == CheckSuccess()
    engine = _engine(checker)
    proposal = engine.analyze_file(str(path))[0]
    rendered = engine.apply_refactoring(str(path), proposal)
    helper = _method(rendered, "Calculator")
    assert _annotation(helper.returns) != "_TowelT0"
    assert "Any" not in rendered and _declarations(rendered)
    assert checker.check(str(path), rendered) == CheckSuccess()
    exec(
        compile(
            rendered
            + "\nassert Calculator._TowelT0 == 42\n"
            + 'assert Calculator._towel_typevar == "preserved"\n'
            + "assert Calculator().first(1, 2) == 3\n"
            + 'assert Calculator().second("a", "b") == "ab"\n',
            str(path),
            "exec",
        ),
        {},
    )

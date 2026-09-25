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

"""Generic method extraction keeps descriptor dispatch and scoped type relationships."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
import sys

import pytest

from towel.type_inference import CheckSuccess, MypyInferrer, PyrightOracle, TypeOracle
from towel.unification.models import MethodKind, is_generated_helper_name
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.fixture(params=["mypy", "pyright"])
def oracle(request: pytest.FixtureRequest) -> Iterator[TypeOracle]:
    pytest.importorskip(request.param)
    checker: TypeOracle = MypyInferrer() if request.param == "mypy" else PyrightOracle()
    try:
        yield checker
    finally:
        checker.close()


def _project(tmp_path: Path, source: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mypy]\nstrict = true\n"
        f'python_version = "{sys.version_info.major}.{sys.version_info.minor}"\n'
        '[tool.pyright]\ntypeCheckingMode = "strict"\n'
        f'pythonVersion = "{sys.version_info.major}.{sys.version_info.minor}"\n'
    )
    path = tmp_path / "program.py"
    path.write_text(source)
    return path


def _annotation(node: ast.expr | None) -> str:
    assert node is not None
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else ast.unparse(node)
    )


def _extract(
    path: Path, oracle: TypeOracle, host: str, method_kind: MethodKind
) -> tuple[str, ast.FunctionDef]:
    """The rendered module and its helper: a method of ``host``, or a module function.

    A helper that dispatches on nothing is a module-level function, since
    nothing a method can spell is sure to reach its class; the type
    relationships these tests are about must hold for it all the same.
    """
    source = path.read_text()
    assert oracle.check(str(path), source) == CheckSuccess()
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=oracle
    )
    proposals = engine.analyze_file(str(path))
    static = method_kind == "staticmethod"
    proposal = next(
        proposal
        for proposal in proposals
        if proposal.insert_into_class == (None if static else host)
    )
    assert proposal.method_kind == (None if static else method_kind)
    original_proposal = ast.dump(proposal.extracted_function, include_attributes=True)
    rendered = engine.apply_refactoring(str(path), proposal)
    assert path.read_text() == source
    assert ast.dump(proposal.extracted_function, include_attributes=True) == original_proposal
    assert oracle.check(str(path), rendered) == CheckSuccess(), rendered
    module = ast.parse(rendered)
    owner = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == host
    )
    module_helpers = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
    ]
    original_assignments = {
        ast.dump(node) for node in ast.parse(source).body if isinstance(node, ast.Assign)
    }
    declarations = [
        node
        for node in module.body
        if isinstance(node, ast.Assign) and ast.dump(node) not in original_assignments
    ]
    assert "Any" not in rendered
    if static:
        assert len(module_helpers) == 1, rendered
        helper = module_helpers[0]
        assert not helper.decorator_list, rendered
        assert all(node.lineno < helper.lineno for node in declarations)
        return rendered, helper
    helper = next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
    )
    assert not module_helpers, "This regression requires a method, not an independent module helper"
    assert declarations, "The method must have fresh type-variable declarations"
    assert all(node.lineno < owner.lineno for node in declarations)
    assert not any(
        isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) for node in owner.body
    ), "Method type variables belong in the module, not the class namespace"
    decorators = [ast.unparse(node) for node in helper.decorator_list]
    assert decorators == ([] if method_kind == "instance" else [method_kind])
    return rendered, helper


def _ordinary_source(method_kind: MethodKind) -> str:
    decorator = "" if method_kind == "instance" else f"    @{method_kind}\n"
    receiver = "self" if method_kind == "instance" else "cls"
    prefix = "" if method_kind == "staticmethod" else f"{receiver}, "
    mutate = "" if method_kind == "staticmethod" else f"        {receiver}.calls += 1\n"
    return "class Calculator:\n    calls: int = 0\n\n" + "\n".join(
        decorator
        + f"    def {name}({prefix}left: {kind}, right: {kind}) -> {kind}:\n"
        + mutate
        + "        result = left + right\n"
        + "        return result\n"
        for name, kind in (("integers", "int"), ("strings", "str"))
    )


def _run(source: str, statements: str) -> object:
    namespace: dict[str, object] = {}
    exec(compile(source + "\n" + statements, "program.py", "exec"), namespace)
    return namespace["observed"]


@pytest.mark.parametrize("method_kind", ["instance", "staticmethod", "classmethod"])
def test_constrained_generic_methods_preserve_dispatch_and_reject_mixed_arguments(
    tmp_path: Path, oracle: TypeOracle, method_kind: MethodKind
) -> None:
    source = _ordinary_source(method_kind)
    path = _project(tmp_path, source)
    rendered, helper = _extract(path, oracle, "Calculator", method_kind)
    explicit = [argument for argument in helper.args.args if argument.arg not in {"self", "cls"}]
    assert len(explicit) == 2
    binder = _annotation(explicit[0].annotation)
    assert _annotation(explicit[1].annotation) == binder
    assert _annotation(helper.returns) == binder
    assert binder not in {"int", "str", "int | str", "Any"}
    # A method helper is called from a method appended to the class; a module
    # helper, from a function appended to the module.
    via, indent, receiver = (
        ("", "", "") if method_kind == "staticmethod" else ("self.", "    ", "self")
    )
    valid = rendered + (
        f"\n{indent}def valid({receiver}) -> None:\n"
        f"{indent}    {via}{helper.name}(1, 2)\n"
        f'{indent}    {via}{helper.name}("a", "b")\n'
    )
    assert oracle.check(str(path), valid) == CheckSuccess()
    invalid = rendered + (
        f"\n{indent}def invalid({receiver}) -> None:\n" f'{indent}    {via}{helper.name}(1, "x")\n'
    )
    rejected = oracle.check(str(path), invalid)
    assert isinstance(rejected, CheckSuccess) and rejected.errors
    assert all("private" not in error.message.lower() for error in rejected.errors)
    scenario = (
        "class Derived(Calculator):\n    pass\n"
        "instance = Derived()\n"
        "observed = (instance.integers(2, 3), instance.strings('a', 'b'), "
        "instance.calls, Derived.calls, Calculator.calls)\n"
    )
    expected = {
        "instance": (5, "ab", 2, 0, 0),
        "staticmethod": (5, "ab", 0, 0, 0),
        "classmethod": (5, "ab", 2, 2, 0),
    }
    assert _run(source, scenario) == _run(rendered, scenario) == expected[method_kind]


def _generic_source(style: str, method_kind: MethodKind) -> str:
    prefix = (
        'from typing import Generic, TypeVar\nT = TypeVar("T")\n'
        'U = TypeVar("U")\nV = TypeVar("V")\n\nclass Box(Generic[T]):\n'
        if style == "legacy"
        else "class Box[T]:\n"
    )
    prefix += (
        "    calls: int = 0\n\n"
        "    def __init__(self, value: T) -> None:\n"
        "        self.value = value\n\n"
    )
    decorator = "" if method_kind == "instance" else f"    @{method_kind}\n"
    receiver = "self" if method_kind == "instance" else "cls"
    arguments = "" if method_kind == "staticmethod" else f"{receiver}, "
    return prefix + "\n".join(
        decorator
        + f'    def {name}{f"[{parameter}]" if style == "pep695" else ""}'
        + f"({arguments}items: list[{parameter}]) -> "
        + (f"tuple[T, {parameter}]" if method_kind == "instance" else parameter)
        + ":\n"
        + ("        cls.calls += 1\n" if method_kind == "classmethod" else "")
        + "        item = items[0]\n"
        + ("        result = (self.value, item)\n" if method_kind == "instance" else "")
        + ("        return result\n" if method_kind == "instance" else "        return item\n")
        for name, parameter in (("first", "U"), ("second", "V"))
    )


@pytest.mark.parametrize("method_kind", ["instance", "staticmethod", "classmethod"])
@pytest.mark.parametrize("style", ["legacy", "pep695"])
def test_method_parameters_are_fresh_while_generic_host_parameters_keep_their_scope(
    tmp_path: Path, oracle: TypeOracle, style: str, method_kind: MethodKind
) -> None:
    if style == "pep695" and sys.version_info < (3, 12):
        pytest.skip("PEP 695 input requires Python 3.12")
    source = _generic_source(style, method_kind)
    path = _project(tmp_path, source)
    rendered, helper = _extract(path, oracle, "Box", method_kind)
    items = next(argument for argument in helper.args.args if argument.arg == "items")
    item_type = _annotation(items.annotation)
    assert item_type.startswith("list[") and item_type.endswith("]")
    binder = item_type[5:-1]
    assert binder not in {"T", "U", "V", "Any"}
    assert _annotation(helper.returns) == (
        f"tuple[T, {binder}]" if method_kind == "instance" else binder
    )
    if style == "pep695":
        assert "class Box[T]" in rendered
        assert "def first[U]" in rendered and "def second[V]" in rendered
    scenario = "box = Box(7)\n" "observed = (box.first(['a']), box.second([3.5]), box.calls)\n"
    expected = (
        ((7, "a"), (7, 3.5), 0)
        if method_kind == "instance"
        else ("a", 3.5, 2 if method_kind == "classmethod" else 0)
    )
    assert _run(source, scenario) == _run(rendered, scenario) == expected


@pytest.mark.parametrize("method_kind", ["staticmethod", "classmethod"])
@pytest.mark.parametrize("style", ["legacy", "pep695"])
def test_qualified_method_calls_preserve_both_class_and_method_type_relationships(
    tmp_path: Path, oracle: TypeOracle, style: str, method_kind: MethodKind
) -> None:
    if style == "pep695" and sys.version_info < (3, 12):
        pytest.skip("PEP 695 input requires Python 3.12")
    source = _generic_source(style, method_kind)
    for parameter in ("U", "V"):
        source = source.replace(
            f"items: list[{parameter}]) -> {parameter}:",
            f"base: T, items: list[{parameter}]) -> tuple[T, {parameter}]:",
        )
    source = source.replace("return item", "return base, item")
    path = _project(tmp_path, source)
    rendered, helper = _extract(path, oracle, "Box", method_kind)
    arguments = {argument.arg: argument.annotation for argument in helper.args.args}
    base_type = _annotation(arguments["base"])
    items_type = _annotation(arguments["items"])
    assert items_type.startswith("list[") and items_type.endswith("]")
    item_type = items_type[5:-1]
    assert item_type not in {"T", "U", "V", "Any", base_type}
    assert _annotation(helper.returns) == f"tuple[{base_type}, {item_type}]"
    if method_kind == "classmethod":
        assert base_type == "T", "The bound cls receiver retains the host's parameter"
    # A static helper reached through bare Box has no specialized receiver.
    # It can quantify T independently and infer the same result from base.
    assert base_type not in {"U", "V", "Any"}
    scenario = (
        "box = Box(7)\n" "observed = (box.first(11, ['a']), box.second(13, [3.5]), box.calls)\n"
    )
    expected = ((11, "a"), (13, 3.5), 2 if method_kind == "classmethod" else 0)
    assert _run(source, scenario) == _run(rendered, scenario) == expected

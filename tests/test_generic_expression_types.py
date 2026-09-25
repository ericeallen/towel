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

"""Real checker expression types retain generic relationships through extraction."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
import textwrap

import pytest

from towel.type_inference import (
    CheckSuccess,
    MypyInferrer,
    PyrightOracle,
    RevealKey,
    RevealRequest,
    TypeOracle,
    _BuildMessages,
)
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.type_bindings import TypeResolver, render_type, required_imports


@pytest.fixture(params=["mypy", "pyright"])
def measured_oracle(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TypeOracle, dict[RevealKey, str]]]:
    pytest.importorskip(request.param)
    oracle: TypeOracle = MypyInferrer() if request.param == "mypy" else PyrightOracle()
    observed: dict[RevealKey, str] = {}
    reveal = oracle.reveal

    def record(requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        result = reveal(requests)
        observed.update(result)
        return result

    monkeypatch.setattr(oracle, "reveal", record)
    try:
        yield oracle, observed
    finally:
        oracle.close()


def project(tmp_path: Path, source: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mypy]\nstrict=true\n[tool.pyright]\ntypeCheckingMode="strict"\n'
    )
    path = tmp_path / "program.py"
    path.write_text(textwrap.dedent(source))
    return path


def extract(path: Path, oracle: TypeOracle) -> tuple[str, ast.FunctionDef]:
    source = path.read_text()
    assert oracle.check(str(path), source) == CheckSuccess()
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=oracle
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    changed = engine.apply_refactoring(str(path), proposals[0])
    assert path.read_text() == source
    assert oracle.check(str(path), changed) == CheckSuccess(), changed
    helpers = [
        node
        for node in ast.parse(changed).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]
    assert len(helpers) == 1
    return changed, helpers[0]


def annotation(node: ast.expr | None) -> str:
    assert node is not None
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else ast.unparse(node)
    )


@pytest.mark.parametrize("bound", ["", ", bound=float"])
def test_unannotated_indexed_locals_preserve_free_or_bounded_parameters(
    tmp_path: Path,
    measured_oracle: tuple[TypeOracle, dict[RevealKey, str]],
    bound: str,
) -> None:
    oracle, observed = measured_oracle
    path = project(
        tmp_path,
        f"""
        from typing import TypeVar
        T = TypeVar("T"{bound})
        U = TypeVar("U"{bound})
        def first(items: list[T]) -> T:
            head = items[0]
            assert len(items) > 0
            result = [head]
            return result[0]
        def second(items: list[U]) -> U:
            head = next(iter(items))
            if not items:
                raise ValueError("empty")
            result = [head]
            return result[0]
    """,
    )
    changed, helper = extract(path, oracle)
    assert observed, "Local expression types must be obtained from the real checker"
    kind = annotation(helper.args.args[0].annotation)
    assert kind.startswith("_TowelT") and annotation(helper.returns) == kind
    assert "Any" not in changed
    declaration = next(
        node
        for node in ast.parse(changed).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == kind
    )
    assert isinstance(declaration.value, ast.Call)
    assert [(keyword.arg, annotation(keyword.value)) for keyword in declaration.value.keywords] == (
        [("bound", "float")] if bound else []
    )
    namespace: dict[str, object] = {}
    exec(compile(changed, str(path), "exec"), namespace)
    value: object = 2.5 if bound else object()
    first, second = namespace["first"], namespace["second"]
    assert callable(first) and callable(second)
    assert first([value]) is value and second([value]) is value


def test_inferred_local_callable_keeps_argument_and_result_correlation(
    tmp_path: Path,
    measured_oracle: tuple[TypeOracle, dict[RevealKey, str]],
) -> None:
    oracle, observed = measured_oracle
    path = project(
        tmp_path,
        """
        from typing import Callable, TypeVar
        T = TypeVar("T")
        U = TypeVar("U")
        def first(items: list[T], functions: list[Callable[[T], T]]) -> T:
            callback = functions[0]
            head = items[0]
            assert len(items) > 0
            result = callback(head)
            wrapped = [result]
            return wrapped[0]
        def second(items: list[U], functions: list[Callable[[U], U]]) -> U:
            callback = next(iter(functions))
            head = next(iter(items))
            if not items:
                raise ValueError("empty")
            result = callback(head)
            wrapped = [result]
            return wrapped[0]
    """,
    )
    changed, helper = extract(path, oracle)
    assert any(" -> " in kind for kind in observed.values()), observed
    kinds = {parameter.arg: annotation(parameter.annotation) for parameter in helper.args.args}
    assert kinds["callback"] == f'Callable[[{kinds["head"]}], {kinds["head"]}]'
    assert annotation(helper.returns) == kinds["head"] and "Any" not in changed
    arguments = {"callback": "integer_callback", "head": '"wrong"'}
    bad = changed + "\ndef integer_callback(value: int) -> int:\n    return value + 1\n"
    bad += f'\nwrong = {helper.name}({", ".join(arguments[arg.arg] for arg in helper.args.args)})\n'
    rejected = oracle.check(str(path), bad)
    assert isinstance(rejected, CheckSuccess) and rejected.errors
    namespace: dict[str, object] = {}
    exec(compile(changed, str(path), "exec"), namespace)
    first, second = namespace["first"], namespace["second"]
    assert callable(first) and callable(second)
    assert first([3], [lambda value: value * 2]) == 6
    assert second(["word"], [str.upper]) == "WORD"


def test_real_inferred_literal_spelling_resolves_without_new_imports(
    tmp_path: Path,
    measured_oracle: tuple[TypeOracle, dict[RevealKey, str]],
) -> None:
    """mypy marks the literal it inferred for a Final with ``?``; pyright marks none.

    Only a marked literal is widened. Unmarked, it may be the program's own
    declaration, which widening would change, so it stays exact and is
    spelled with the import it needs.
    """
    oracle, _ = measured_oracle
    path = project(
        tmp_path,
        """
        from typing import Final
        VALUE: Final = 3
        def example() -> int:
            return VALUE
    """,
    )
    source = path.read_text()
    assert oracle.check(str(path), source) == CheckSuccess()
    revealed = oracle.reveal([RevealRequest(str(path), source, 5, "    ", ("VALUE",))])
    text = revealed[(str(path), 5, 0)]
    assert text.startswith("Literal[3]"), text
    term = TypeResolver(source, str(path), 5, source, str(path)).resolve_revealed(text)
    assert term is not None
    if isinstance(oracle, MypyInferrer):
        assert text == "Literal[3]?"
        assert ast.unparse(render_type(term)) == "int" and not required_imports([term])
    else:
        assert text == "Literal[3]"
        assert ast.unparse(render_type(term)) == "Literal[3]"
        assert required_imports([term]) == (("typing", "Literal"),)


@pytest.mark.parametrize(
    "notes,expected",
    [
        (("int", "int"), "int"),
        (("int", "str"), None),
        (("str", "int"), None),
        (("int", "str", "int"), None),
    ],
)
def test_mypy_repeated_reveals_require_one_unambiguous_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    notes: tuple[str, ...],
    expected: str | None,
) -> None:
    pytest.importorskip("mypy")
    path = project(tmp_path, "def example(value: int) -> int:\n    return value\n")
    oracle = MypyInferrer()
    messages = _BuildMessages(tuple(f'{path}:2: note: Revealed type is "{kind}"' for kind in notes))
    monkeypatch.setattr(oracle, "_build_errors", lambda *args, **kwargs: messages)
    try:
        result = oracle.reveal([RevealRequest(str(path), path.read_text(), 2, "    ", ("value",))])
    finally:
        oracle.close()
    assert result == ({(str(path), 2, 0): expected} if expected is not None else {})


def test_constrained_local_specializations_are_not_silently_narrowed(
    tmp_path: Path,
    measured_oracle: tuple[TypeOracle, dict[RevealKey, str]],
) -> None:
    oracle, _ = measured_oracle
    path = project(
        tmp_path,
        """
        from typing import TypeVar
        T = TypeVar("T", int, str)
        U = TypeVar("U", int, str)
        def first(items: list[T]) -> T:
            head = items[0]
            assert len(items) > 0
            result = [head]
            return result[0]
        def second(items: list[U]) -> U:
            head = next(iter(items))
            if not items:
                raise ValueError("empty")
            result = [head]
            return result[0]
    """,
    )
    source = path.read_text()
    assert oracle.check(str(path), source) == CheckSuccess()
    lines = [
        number for number, line in enumerate(source.splitlines(), 1) if "result = [head]" in line
    ]
    reveals = oracle.reveal(
        [RevealRequest(str(path), source, line, "    ", ("head",)) for line in lines]
    )
    if isinstance(oracle, MypyInferrer):
        assert reveals == {}, "Distinct int/str specializations must not retain only str"
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        proposals = engine.analyze_file(str(path))
        assert proposals
        with pytest.raises(RefactoringError, match="annotation variant"):
            engine.apply_refactoring(str(path), proposals[0])
        assert path.read_text() == source
    else:
        assert set(reveals.values()) == {"T@first", "U@second"}
        changed, helper = extract(path, oracle)
        kind = annotation(helper.args.args[0].annotation)
        assert kind.startswith("_TowelT") and annotation(helper.returns) == kind
        declaration = next(
            node
            for node in ast.parse(changed).body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == kind
        )
        assert isinstance(declaration.value, ast.Call)
        assert [annotation(argument) for argument in declaration.value.args[1:]] == ["int", "str"]

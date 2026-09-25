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

"""Local annotations must follow the same scoped binders as generic signatures."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
import sys
import textwrap

import pytest

from towel.type_inference import (
    CheckResult,
    CheckSuccess,
    MypyInferrer,
    PyrightOracle,
    RevealKey,
    RevealRequest,
    Subtyping,
    TypeOracle,
)
from towel.unification.annotations import ApplySite
from towel.unification.generic_annotations import generic_helpers
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.probe_answers import answer_probes, only_probes


class _DeclaredTypesOnly(TypeOracle):
    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        if not only_probes(requests):
            raise AssertionError("These complete source signatures need no speculative reveals")
        return answer_probes(requests)

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[tuple[str, str]]
    ) -> Sequence[Subtyping]:
        raise AssertionError("Structural candidate generation does not prove subtyping")

    def check(self, file_path: str, source: str) -> CheckResult:
        raise AssertionError("Candidate generation cannot authorize unchecked materialization")

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        raise AssertionError("Candidate generation cannot authorize unchecked materialization")

    def close(self) -> None:
        pass


@pytest.fixture(params=["mypy", "pyright"])
def oracle(request: pytest.FixtureRequest) -> Iterator[TypeOracle]:
    pytest.importorskip(request.param)
    checker: TypeOracle = MypyInferrer() if request.param == "mypy" else PyrightOracle()
    try:
        yield checker
    finally:
        checker.close()


def _annotation(node: ast.expr | None) -> str:
    assert node is not None
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else ast.unparse(node)
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 input requires Python 3.12")
@pytest.mark.parametrize("second_parameter", ["T", "U"])
@pytest.mark.parametrize("quoted", [False, True])
def test_scoped_local_annotations_follow_the_fresh_signature_binder(
    tmp_path: Path, oracle: TypeOracle, second_parameter: str, quoted: bool
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mypy]\nstrict = true\n"
        f'python_version = "{sys.version_info.major}.{sys.version_info.minor}"\n'
        '[tool.pyright]\ntypeCheckingMode = "strict"\n'
        f'pythonVersion = "{sys.version_info.major}.{sys.version_info.minor}"\n'
    )
    path = tmp_path / "program.py"
    quote = '"' if quoted else ""
    original = "\n".join(
        f"def {name}[{parameter}](items: list[{parameter}]) -> list[{parameter}]:\n"
        "    count: int = len(items)\n"
        f"    result: {quote}list[{parameter}]{quote} = items[:count]\n"
        "    return result\n"
        for name, parameter in (("first", "T"), ("second", second_parameter))
    )
    path.write_text(original)
    assert oracle.check(str(path), original) == CheckSuccess()
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=oracle
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    proposal = proposals[0]
    before_helper = ast.dump(proposal.extracted_function, include_attributes=True)
    rendered = engine.apply_refactoring(str(path), proposal)
    assert oracle.check(str(path), rendered) == CheckSuccess(), rendered
    assert path.read_text() == original
    assert ast.dump(proposal.extracted_function, include_attributes=True) == before_helper
    helper = next(
        node
        for node in ast.parse(rendered).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    annotations = {
        node.target.id: node.annotation
        for node in ast.walk(helper)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert ast.dump(annotations["count"]) == ast.dump(ast.Name(id="int", ctx=ast.Load()))
    result_type = _annotation(annotations["result"])
    assert result_type == _annotation(helper.args.args[0].annotation)
    assert result_type == _annotation(helper.returns)
    assert result_type not in {"list[T]", "list[U]", "Any"}
    namespace: dict[str, object] = {}
    exec(compile(rendered, str(path), "exec"), namespace)
    first, second = namespace["first"], namespace["second"]
    assert callable(first) and callable(second)
    values = [1, 2]
    assert first(values) == values and first(values) is not values
    assert second(["a", "b"]) == ["a", "b"]


def _declared_sites(source: str, path: str) -> tuple[ApplySite, ...]:
    sites: list[ApplySite] = []
    for function in ast.parse(source).body:
        if not isinstance(function, ast.FunctionDef):
            continue
        statement = ast.parse("return helper(items)").body[0]
        assert isinstance(statement, ast.Return) and isinstance(statement.value, ast.Call)
        sites.append(
            ApplySite(
                path,
                source,
                function.body[0].lineno,
                function.end_lineno or function.lineno,
                "    ",
                statement,
                statement.value,
                function.returns,
            )
        )
    return tuple(sites)


DECLARED = textwrap.dedent("""\
    from typing import TypeVar
    T = TypeVar("T")
    U = TypeVar("U")

    def first(items: list[T]) -> list[T]:
        count: int = len(items)
        result: list[T] = items[:count]
        return result

    def second(items: list[U]) -> list[U]:
        count: int = len(items)
        result: list[U] = items[:count]
        return result
    """)


def _helper(body: str) -> ast.FunctionDef:
    helper = ast.parse(
        "def helper(items):\n" + textwrap.indent(textwrap.dedent(body), "    ")
    ).body[0]
    assert isinstance(helper, ast.FunctionDef)
    return helper


def test_representative_body_can_come_from_either_original_scope(tmp_path: Path) -> None:
    path = str(tmp_path / "program.py")
    helper = _helper("count: int = len(items)\nresult: list[U] = items[:count]\nreturn result\n")
    before = ast.dump(helper, include_attributes=True)
    candidates = list(
        generic_helpers(
            helper, _declared_sites(DECLARED, path), path, DECLARED, (), _DeclaredTypesOnly()
        )
    )
    assert len(candidates) == 1
    candidate = candidates[0].helper
    local = [node for node in ast.walk(candidate) if isinstance(node, ast.AnnAssign)]
    assert _annotation(local[1].annotation) == _annotation(candidate.returns)
    assert ast.dump(local[0].annotation) == "Name(id='int', ctx=Load())"
    assert ast.dump(helper, include_attributes=True) == before


def test_concrete_local_type_columns_follow_the_same_signature_generalization(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mypy]\nstrict = true\n[tool.pyright]\ntypeCheckingMode = "strict"\n'
    )
    path = tmp_path / "program.py"
    source = "\n".join(
        f"def {name}(items: list[{kind}]) -> list[{kind}]:\n"
        "    count: int = len(items)\n"
        f"    result: list[{kind}] = items[:count]\n"
        "    return result\n"
        for name, kind in (("first", "int"), ("second", "str"))
    )
    path.write_text(source)
    assert oracle.check(str(path), source) == CheckSuccess()
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=oracle
    )
    proposal = engine.analyze_file(str(path))[0]
    rendered = engine.apply_refactoring(str(path), proposal)
    assert oracle.check(str(path), rendered) == CheckSuccess(), rendered
    helper = next(
        node
        for node in ast.parse(rendered).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    locals_ = [node for node in ast.walk(helper) if isinstance(node, ast.AnnAssign)]
    assert _annotation(locals_[1].annotation) == _annotation(helper.args.args[0].annotation)
    assert _annotation(locals_[1].annotation) == _annotation(helper.returns)
    assert "TypeVar" in rendered and "Any" not in rendered
    assert ast.dump(locals_[0].annotation) == "Name(id='int', ctx=Load())"
    assert path.read_text() == source


def test_annotation_free_lambda_does_not_block_body_rebinding(tmp_path: Path) -> None:
    path = str(tmp_path / "program.py")
    source = DECLARED.replace("items[:count]", "(lambda: items[:count])()")
    helper = _helper(
        "count: int = len(items)\nresult: list[T] = (lambda: items[:count])()\nreturn result\n"
    )
    candidates = list(
        generic_helpers(
            helper, _declared_sites(source, path), path, source, (), _DeclaredTypesOnly()
        )
    )
    assert len(candidates) == 1
    assert any(isinstance(node, ast.Lambda) for node in ast.walk(candidates[0].helper))


@pytest.mark.parametrize(
    "body",
    [
        "count: int = len(items)\nresult: list[V] = items[:count]\nreturn result\n",
        "count: int = len(items)\nresult: dict[str, T] = {}\nreturn result\n",
        "def nested(value: T) -> T:\n    return value\nreturn items\n",
        "class Nested:\n    value: T\nreturn items\n",
    ],
)
def test_unproven_provenance_and_nested_binders_decline_without_mutating_inputs(
    tmp_path: Path, body: str
) -> None:
    path = str(tmp_path / "program.py")
    helper = _helper(body)
    before = ast.dump(helper, include_attributes=True)
    assert (
        list(
            generic_helpers(
                helper, _declared_sites(DECLARED, path), path, DECLARED, (), _DeclaredTypesOnly()
            )
        )
        == []
    )
    assert ast.dump(helper, include_attributes=True) == before


def test_body_binder_not_represented_in_signature_is_not_guessed(tmp_path: Path) -> None:
    path = str(tmp_path / "program.py")
    source = DECLARED.replace("result: list[T]", "result: list[U]")
    helper = _helper("count: int = len(items)\nresult: list[U] = items[:count]\nreturn result\n")
    assert (
        list(
            generic_helpers(
                helper, _declared_sites(source, path), path, source, (), _DeclaredTypesOnly()
            )
        )
        == []
    )


def test_differing_concrete_body_annotations_are_not_silently_rewritten(tmp_path: Path) -> None:
    path = str(tmp_path / "program.py")
    source = DECLARED.replace("count: int = len(items)", "count: float = len(items)", 1)
    helper = _helper("count: int = len(items)\nresult: list[U] = items[:count]\nreturn result\n")
    assert (
        list(
            generic_helpers(
                helper, _declared_sites(source, path), path, source, (), _DeclaredTypesOnly()
            )
        )
        == []
    )

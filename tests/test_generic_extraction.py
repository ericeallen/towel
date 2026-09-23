"""Parametric extraction preserves relationships under real project checkers."""

from __future__ import annotations

import ast
from collections.abc import Iterator
import logging
from pathlib import Path
import sys
import textwrap

import pytest

from towel.type_inference import CheckSuccess, MypyInferrer, PyrightOracle, TypeOracle
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.exceptions import RefactoringError


@pytest.fixture(params=["mypy", "pyright"])
def oracle(request: pytest.FixtureRequest) -> Iterator[TypeOracle]:
    pytest.importorskip(request.param)
    checker: TypeOracle = MypyInferrer() if request.param == "mypy" else PyrightOracle()
    try:
        yield checker
    finally:
        checker.close()


def _project(tmp_path: Path, code: str, *, pyright_mode: str = "strict") -> Path:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mypy]\nstrict = true\n"
        f'python_version = "{sys.version_info.major}.{sys.version_info.minor}"\n'
        f'[tool.pyright]\ntypeCheckingMode = "{pyright_mode}"\n'
        f'pythonVersion = "{sys.version_info.major}.{sys.version_info.minor}"\n'
    )
    path = tmp_path / "program.py"
    path.write_text(textwrap.dedent(code))
    return path


def _extract(path: Path, oracle: TypeOracle) -> tuple[str, ast.FunctionDef]:
    original = path.read_text()
    assert oracle.check(str(path), original) == CheckSuccess()
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=oracle
    )
    proposals = engine.analyze_file(str(path))
    assert proposals, original
    rendered = engine.apply_refactoring(str(path), proposals[0])
    assert path.read_text() == original, "materialization must leave source files untouched"
    assert oracle.check(str(path), rendered) == CheckSuccess(), rendered
    helper = next(
        node
        for node in ast.parse(rendered).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    return rendered, helper


def _annotation(node: ast.expr | None) -> str:
    assert node is not None
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else ast.unparse(node)
    )


ADDITION = """
    def integers(left: int, right: int) -> int:
        result = left + right
        return result

    def strings(left: str, right: str) -> str:
        result = left + right
        return result
"""


def test_addition_gets_a_constrained_parameter_and_rejects_mixed_calls(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    path = _project(tmp_path, ADDITION)
    rendered, helper = _extract(path, oracle)
    assert "TypeVar" in rendered and "Any" not in rendered
    kinds = [_annotation(arg.annotation) for arg in helper.args.args]
    assert len(set(kinds)) == 1
    assert _annotation(helper.returns) == kinds[0]
    rejected = oracle.check(str(path), rendered + f'\nwrong = {helper.name}(1, "x")\n')
    assert isinstance(rejected, CheckSuccess) and rejected.errors
    namespace: dict[str, object] = {}
    exec(compile(rendered, str(path), "exec"), namespace)
    integers, strings = namespace["integers"], namespace["strings"]
    assert callable(integers) and callable(strings)
    assert integers(2, 3) == 5
    assert strings("a", "b") == "ab"


def test_invariant_collections_share_element_and_result_parameters(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    path = _project(
        tmp_path,
        """
        def integers(items: list[int], value: int) -> list[int]:
            result = items.copy()
            result.append(value)
            return result

        def strings(items: list[str], value: str) -> list[str]:
            result = items.copy()
            result.append(value)
            return result
    """,
    )
    rendered, helper = _extract(path, oracle)
    assert "TypeVar" in rendered and "Any" not in rendered
    kinds = {argument.arg: _annotation(argument.annotation) for argument in helper.args.args}
    assert kinds["items"] == f'list[{kinds["value"]}]'
    assert _annotation(helper.returns) == kinds["items"]
    wrong_arguments = {"items": "[1]", "value": '"x"'}
    bad_call = ", ".join(wrong_arguments[argument.arg] for argument in helper.args.args)
    rejected = oracle.check(str(path), rendered + f"\nwrong = {helper.name}({bad_call})\n")
    assert isinstance(rejected, CheckSuccess) and rejected.errors
    namespace: dict[str, object] = {}
    exec(compile(rendered, str(path), "exec"), namespace)
    integers = namespace["integers"]
    assert callable(integers)
    original = [1]
    assert integers(original, 2) == [1, 2] and original == [1]


def test_two_independent_columns_keep_distinct_parameters(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    path = _project(
        tmp_path,
        """
        def first(left: list[int], right: list[str]) -> tuple[int, str]:
            a = left[0]
            b = right[0]
            return a, b

        def second(left: list[float], right: list[bytes]) -> tuple[float, bytes]:
            a = left[0]
            b = right[0]
            return a, b
    """,
    )
    rendered, helper = _extract(path, oracle)
    kinds = [_annotation(argument.annotation) for argument in helper.args.args]
    assert len(set(kinds)) == 2
    assert all(kind.startswith("list[") for kind in kinds)
    assert _annotation(helper.returns) == f"tuple[{kinds[0][5:-1]}, {kinds[1][5:-1]}]"
    assert "Any" not in rendered


def test_generic_retry_after_a_fully_annotated_union_fails_keeps_calls_bound(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    path = _project(
        tmp_path,
        """
        def first(items: list[int], value: int) -> None:
            items.append(value)
            items.reverse()

        def second(items: list[str], value: str) -> None:
            items.append(value)
            items.reverse()
    """,
    )
    rendered, helper = _extract(path, oracle)
    assert "TypeVar" in rendered and "Any" not in rendered
    assert _annotation(helper.returns) == "None"
    namespace: dict[str, object] = {}
    exec(compile(rendered, str(path), "exec"), namespace)
    first = namespace["first"]
    assert callable(first)
    items = [1, 2]
    assert first(items, 3) is None and items == [3, 2, 1]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 input requires Python 3.12")
def test_function_scoped_free_parameters_are_rebound_in_the_helper(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    path = _project(
        tmp_path,
        """
        def first[T](items: list[T]) -> T:
            result = items[0]
            return result

        def second[T](items: list[T]) -> T:
            result = items[0]
            return result
    """,
    )
    rendered, helper = _extract(path, oracle)
    result = _annotation(helper.returns)
    assert result != "T" and _annotation(helper.args.args[0].annotation) == f"list[{result}]"
    assert "TypeVar" in rendered and "Any" not in rendered
    assert "def first[T]" in rendered and "def second[T]" in rendered
    namespace: dict[str, object] = {}
    exec(compile(rendered, str(path), "exec"), namespace)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 input requires Python 3.12")
def test_class_scoped_parameters_can_flow_to_an_independent_module_helper(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    path = _project(
        tmp_path,
        """
        class First[T]:
            def head(self, items: list[T]) -> T:
                result = items[0]
                return result

        class Second[U]:
            def head(self, items: list[U]) -> U:
                result = items[0]
                return result
    """,
    )
    rendered, helper = _extract(path, oracle)
    result = _annotation(helper.returns)
    assert result not in {"T", "U", "Any"}
    assert _annotation(helper.args.args[0].annotation) == f"list[{result}]"
    assert "class First[T]" in rendered and "class Second[U]" in rendered


def test_free_legacy_parameters_with_different_names_are_freshened(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    path = _project(
        tmp_path,
        """
        from typing import TypeVar
        T = TypeVar("T")
        U = TypeVar("U")

        def first(items: list[T]) -> T:
            result = items[0]
            return result

        def second(items: list[U]) -> U:
            result = items[0]
            return result
    """,
    )
    rendered, helper = _extract(path, oracle)
    result = _annotation(helper.returns)
    assert result not in {"T", "U", "Any"}
    assert _annotation(helper.args.args[0].annotation) == f"list[{result}]"
    assert 'T = TypeVar("T")' in rendered and 'U = TypeVar("U")' in rendered


def test_no_checker_does_not_invent_generic_contracts(tmp_path: Path) -> None:
    path = _project(tmp_path, ADDITION)
    engine = UnificationRefactorEngine(min_lines=2, reuse_existing_functions=False)
    proposal = engine.analyze_file(str(path))[0]
    assert "TypeVar" not in engine.apply_refactoring(str(path), proposal)


@pytest.mark.parametrize("domain", ["bound=str", "str, bytes"])
def test_free_parameter_domains_are_preserved(
    tmp_path: Path, oracle: TypeOracle, domain: str
) -> None:
    path = _project(
        tmp_path,
        f"""
        from typing import TypeVar
        T = TypeVar("T", {domain})
        U = TypeVar("U", {domain})

        def first(items: list[T]) -> T:
            result = items[0]
            return result

        def second(items: list[U]) -> U:
            result = items[0]
            return result
        """,
    )
    rendered, helper = _extract(path, oracle)
    binder_name = _annotation(helper.returns)
    declarations = [
        node
        for node in ast.parse(rendered).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == binder_name for target in node.targets
        )
    ]
    assert len(declarations) == 1
    call = declarations[0].value
    assert isinstance(call, ast.Call)
    if domain.startswith("bound"):
        assert len(call.args) == 1
        assert len(call.keywords) == 1 and call.keywords[0].arg == "bound"
        assert _annotation(call.keywords[0].value) == "str"
    else:
        assert [_annotation(argument) for argument in call.args[1:]] == ["str", "bytes"]


def test_existing_names_do_not_capture_generated_binders_or_the_constructor(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    path = _project(
        tmp_path,
        "TypeVar = 1\n_TowelT0 = 2\n_towel_typevar = 3\n" + textwrap.dedent(ADDITION),
    )
    rendered, helper = _extract(path, oracle)
    assert _annotation(helper.returns) != "_TowelT0"
    namespace: dict[str, object] = {}
    exec(compile(rendered, str(path), "exec"), namespace)
    assert {name: namespace[name] for name in ("TypeVar", "_TowelT0", "_towel_typevar")} == {
        "TypeVar": 1,
        "_TowelT0": 2,
        "_towel_typevar": 3,
    }


@pytest.mark.parametrize("pyright_mode", ["basic", "strict"])
def test_cross_file_generic_helper_respects_project_rules_and_unchanged_consumers(
    tmp_path: Path, oracle: TypeOracle, pyright_mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    _project(tmp_path, "", pyright_mode=pyright_mode)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    first, second, consumer = (package / f"{name}.py" for name in ("first", "second", "consumer"))
    first.write_text(
        "def integers(left: int, right: int) -> int:\n    result = left + right\n    return result\n"
    )
    second.write_text(
        "def strings(left: str, right: str) -> str:\n    result = left + right\n    return result\n"
    )
    consumer.write_text(
        'from .first import integers\nfrom .second import strings\nn: int = integers(1, 2)\ns: str = strings("a", "b")\n'
    )
    original_consumer = consumer.read_text()
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=oracle, cross_module_helpers=True
    )
    proposals = engine.analyze_files([str(first), str(second), str(consumer)])
    proposal = next(
        proposal for proposal in proposals if len({r.file_path for r in proposal.replacements}) == 2
    )
    if isinstance(oracle, PyrightOracle) and pyright_mode == "strict":
        # Towel's existing cross-module helper names are private. Strict
        # Pyright forbids that import even when the generic contract is sound.
        # Generic inference must honor this policy rather than suppress it.
        caplog.set_level(logging.DEBUG, logger="towel.types")
        with pytest.raises(RefactoringError, match="Every helper annotation variant"):
            engine.apply_refactoring_multi_file(proposal)
        assert "reportPrivateUsage" in caplog.text
        assert engine.change_log == ()
    else:
        modified = engine.apply_refactoring_multi_file(proposal)
        assert any("TypeVar" in text for text in modified.values())
        assert oracle.check_project(modified) == CheckSuccess()
    assert consumer.read_text() == original_consumer


def test_precise_subclass_result_is_not_promoted_to_a_constraint(
    tmp_path: Path, oracle: TypeOracle
) -> None:
    path = _project(
        tmp_path,
        """
        class Text(str):
            pass

        def first(items: list[Text]) -> Text:
            result = items[0]
            return result

        def second(items: list[str]) -> str:
            result = items[0]
            return result
    """,
    )
    rendered, helper = _extract(path, oracle)
    assert "TypeVar" in rendered
    assert "bound=" not in rendered
    result = _annotation(helper.returns)
    assert _annotation(helper.args.args[0].annotation) == f"list[{result}]"
    assert (
        oracle.check(str(path), rendered + f"\nprecise: Text = {helper.name}([Text('a')])\n")
        == CheckSuccess()
    )

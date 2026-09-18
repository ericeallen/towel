"""Behavioral checks for lexical scope and caller-owned AST boundaries."""

import ast
import contextlib
import io
from pathlib import Path
from typing import Callable, cast

import pytest

from towel.unification.extractor import HygienicExtractor
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import has_external_loop_control, uses_class_private_names
from towel.unification.substitution import Substitution


def _rewrites(tmp_path: Path, source: str) -> list[str]:
    path = tmp_path / "subject.py"
    path.write_text(source, encoding="utf-8")
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        proposals = engine.analyze_files([str(path)], progress="none")
    return [engine.apply_refactoring(str(path), proposal) for proposal in proposals]


@pytest.mark.parametrize(
    "comprehension",
    [
        "[xs for xs in xs]",
        "{xs for xs in xs}",
        "{xs: xs + 1 for xs in xs}",
        "list(xs for xs in xs)",
        "[x for x in xs] + [xs for xs in ys]",
        "[(x, y) for x in xs for y in ys]",
    ],
)
def test_comprehension_iterable_uses_containing_scope(tmp_path: Path, comprehension: str) -> None:
    body = f"    result = {comprehension}\n    count = len(result)\n    value = count + 1\n    return value\n"
    source = "".join(f"def {name}(xs, ys):\n{body}" for name in ("first", "second"))
    original: dict[str, object] = {}
    exec(source, original)
    expected = cast(Callable[..., object], original["first"])([1, 2], [3, 4])
    outputs = _rewrites(tmp_path, source)
    assert outputs, "Self-contained comprehension extraction should remain useful"
    for output in outputs:
        namespace: dict[str, object] = {}
        exec(output, namespace)
        for name in ("first", "second"):
            assert cast(Callable[..., object], namespace[name])([1, 2], [3, 4]) == expected


def test_comprehension_target_does_not_replace_enclosing_parameter_binding() -> None:
    module = ast.parse("def f(xs):\n    return [xs for xs in xs]\n")
    function = cast(ast.FunctionDef, module.body[0])
    comprehension = cast(ast.ListComp, cast(ast.Return, function.body[0]).value)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    iterable = cast(ast.Name, comprehension.generators[0].iter)
    parameter_binding = analyzer.get_binding_for_name(iterable)
    assert parameter_binding is not None
    assert parameter_binding.node is function.args.args[0]
    target_binding = analyzer.get_binding_for_name(cast(ast.Name, comprehension.elt))
    assert target_binding is not None
    assert target_binding.scope_id != parameter_binding.scope_id


@pytest.mark.parametrize("inherited", [False, True])
def test_private_attribute_extraction_preserves_source_class(
    tmp_path: Path, inherited: bool
) -> None:
    prefix = "class Base:\n    pass\n" if inherited else ""
    base = "(Base)" if inherited else ""
    source = prefix + "".join(
        f"class {name}{base}:\n"
        "    __slots__ = ()\n"
        "    @property\n"
        f"    def __value(self):\n        return {value}\n"
        "    def run(self):\n"
        "        x = self.__value\n        y = x + 1\n        z = y * 2\n        return z\n"
        for name, value in (("A", 10), ("B", 20))
    )
    for output in [source, *_rewrites(tmp_path, source)]:
        namespace: dict[str, object] = {}
        exec(output, namespace)
        exec("observed = (A().run(), B().run())", namespace)
        assert namespace["observed"] == (22, 42)


def test_private_predicate_distinguishes_protocol_names() -> None:
    assert uses_class_private_names(ast.parse("result = self.__value").body)
    assert uses_class_private_names(ast.parse("result = __value").body)
    assert not uses_class_private_names(ast.parse("result = self.__len__()").body)
    assert not uses_class_private_names(ast.parse("result = self._value").body)


def test_same_class_private_extraction_remains_eligible(tmp_path: Path) -> None:
    source = (
        "class C:\n    __slots__ = ()\n    @property\n"
        "    def __value(self):\n        return 10\n"
        + "".join(
            f"    def {name}(self):\n"
            "        x = self.__value\n        y = x + 1\n        z = y * 2\n        return z\n"
            for name in ("first", "second")
        )
    )
    outputs = _rewrites(tmp_path, source)
    assert outputs
    for output in outputs:
        namespace: dict[str, object] = {}
        exec(output, namespace)
        exec("observed = (C().first(), C().second())", namespace)
        assert namespace["observed"] == (22, 22)


@pytest.mark.parametrize("callee", [False, True])
def test_generated_calls_do_not_mutate_or_alias_substitution_expressions(callee: bool) -> None:
    expression = ast.Name(id="value", ctx=ast.Load())
    substitution = Substitution()
    substitution.add_mapping(0, expression, "__param_0")
    if callee:
        substitution.params_used_as_callee.add("__param_0")
    before = ast.dump(expression, include_attributes=True)
    call = HygienicExtractor().generate_call(
        function_name="helper",
        block_idx=0,
        substitution=substitution,
        param_order={"__param_0": 0},
        free_variables=set(),
        is_value_producing=False,
    )
    assert ast.dump(expression, include_attributes=True) == before
    assert all(node is not expression for node in ast.walk(call))
    for node in ast.walk(call):
        if isinstance(node, ast.Name) and node.id == "value":
            node.id = "changed"
    assert expression.id == "value"


@pytest.mark.parametrize("control", ["break", "continue"])
def test_loop_control_requires_its_own_enclosing_loop(control: str) -> None:
    statement = ast.parse(f"if value:\n    {control}\n").body
    assert has_external_loop_control(statement)
    loop = ast.parse(f"for value in values:\n    if value:\n        {control}\n").body
    assert not has_external_loop_control(loop)
    loop_else = ast.parse(f"for value in values:\n    pass\nelse:\n    {control}\n").body
    assert has_external_loop_control(loop_else)
    enclosing_loop = ast.parse(
        f"for outer in values:\n    for inner in outer:\n        pass\n    else:\n        {control}\n"
    ).body
    assert not has_external_loop_control(enclosing_loop)


def test_complete_loop_extraction_retains_break_behavior(tmp_path: Path) -> None:
    body = (
        "    output = []\n    for value in values:\n"
        "        if value < 0:\n            break\n"
        "        output.append(value * 2)\n    return output\n"
    )
    source = "".join(f"def {name}(values):\n{body}" for name in ("first", "second"))
    outputs = _rewrites(tmp_path, source)
    assert outputs, "Complete loops should remain eligible for extraction"
    for output in outputs:
        namespace: dict[str, object] = {}
        exec(output, namespace)
        for name in ("first", "second"):
            assert cast(Callable[..., object], namespace[name])([1, 2, -1, 3]) == [2, 4]


def test_comprehension_walrus_preserves_containing_binding(tmp_path: Path) -> None:
    body = (
        "    result = [last := x for x in xs]\n"
        "    size = len(result)\n    value = last + size\n    return value\n"
    )
    source = "".join(f"def {name}(xs):\n{body}" for name in ("first", "second"))
    for output in [source, *_rewrites(tmp_path, source)]:
        namespace: dict[str, object] = {}
        exec(output, namespace)
        assert cast(Callable[..., object], namespace["first"])([1, 2]) == 4
        assert cast(Callable[..., object], namespace["second"])([3, 4]) == 6


def test_import_bindings_follow_python_dotted_import_rules() -> None:
    tree = ast.parse("import os.path\nfrom math import pi as value\nresult = (os, value)\n")
    analyzer = ScopeAnalyzer()
    scope = analyzer.analyze(tree)
    assert isinstance(scope.bindings["os"].node, ast.Import)
    assert isinstance(scope.bindings["value"].node, ast.ImportFrom)
    assert "os.path" not in scope.bindings


@pytest.mark.parametrize("closure", [False, True])
def test_external_rebinding_is_not_snapshotted(tmp_path: Path, closure: bool) -> None:
    body = "    before = value\n    update()\n    after = value\n    return before, after\n"
    functions = "".join(f"def {name}():\n{body}" for name in ("first", "second"))
    if closure:
        inner = "value = 1\ndef update():\n    nonlocal value\n    value = 42\n" + functions
        source = "def outer():\n" + "".join("    " + line + "\n" for line in inner.splitlines())
        source += "    return first(), second()\n"
        observed = "observed = outer()"
        expected: tuple[tuple[float, int], tuple[int, int]] = ((1, 42), (42, 42))
    else:
        source = (
            "from math import pi as value\ndef update():\n    global value\n    value = 42\n"
            + functions
        )
        observed = "observed = (first(), second())"
        import math

        expected = ((math.pi, 42), (42, 42))
    for output in [source, *_rewrites(tmp_path, source)]:
        namespace: dict[str, object] = {}
        exec(output, namespace)
        exec(observed, namespace)
        assert namespace["observed"] == expected


@pytest.mark.parametrize(
    "hazard", ["", "def update():\n    global value\n    value = 42\n", "globals()['value'] = 42\n"]
)
def test_visible_external_rebinding_guard(hazard: str) -> None:
    from towel.unification.semantic_safety import snapshots_rebound_external_names

    tree = ast.parse(
        "from math import pi as value\n" + hazard + "def first():\n    return value + 1\n"
    )
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = tree.body[-1]
    assert isinstance(function, ast.FunctionDef)
    assert snapshots_rebound_external_names(analyzer, function, function.body) == bool(hazard)


def test_readonly_closure_remains_extractable(tmp_path: Path) -> None:
    source = (
        "def outer():\n    value = 5\n"
        + "".join(
            f"    def {name}():\n        x = value + 1\n        y = x * 2\n        z = y + 3\n        return z\n"
            for name in ("first", "second")
        )
        + "    return first(), second()\n"
    )
    outputs = _rewrites(tmp_path, source)
    assert outputs, "Readonly closure values should remain extractable"
    for output in outputs:
        namespace: dict[str, object] = {}
        exec(output, namespace)
        assert cast(Callable[..., object], namespace["outer"])() == (15, 15)


def test_caller_parameter_captured_by_mutating_closure(tmp_path: Path) -> None:
    body = (
        "    def update():\n        nonlocal x\n        x += 1\n"
        "    update()\n    y = x + 1\n    return y\n"
    )
    source = "".join(f"def {name}(x):\n{body}" for name in ("first", "second"))
    for output in [source, *_rewrites(tmp_path, source)]:
        namespace: dict[str, object] = {}
        exec(output, namespace)
        for name in ("first", "second"):
            assert cast(Callable[..., object], namespace[name])(2) == 4


def test_external_hazard_summary_is_owned_reused_and_reset() -> None:
    """The hazard summary is taken once per analysis, reused by every query, and replaced by the next."""
    from dataclasses import FrozenInstanceError

    from towel.unification.semantic_safety import snapshots_rebound_external_names

    tree = ast.parse("from math import pi as value\ndef first():\n    return value + 1\n")
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = tree.body[-1]
    assert isinstance(function, ast.FunctionDef)
    summary = analyzer.external_binding_hazards
    assert summary is not None
    assert not snapshots_rebound_external_names(analyzer, function, function.body)

    # A rebinding added to the tree after analysis is invisible until the tree
    # is analyzed again: the queries read the snapshot, they do not rescan.
    tree.body.extend(ast.parse("def update():\n    global value\n    value = 42\n").body)
    for _ in range(10):
        assert not snapshots_rebound_external_names(analyzer, function, function.body)
        assert analyzer.external_binding_hazards is summary
    with pytest.raises(FrozenInstanceError):
        setattr(summary, "reflective", True)

    analyzer.analyze(tree)
    assert analyzer.external_binding_hazards is not summary
    assert snapshots_rebound_external_names(analyzer, function, function.body)

    replacement = ast.parse("from math import pi as value\ndef first():\n    return value + 1\n")
    analyzer.analyze(replacement)
    replacement_function = replacement.body[-1]
    assert isinstance(replacement_function, ast.FunctionDef)
    assert not snapshots_rebound_external_names(
        analyzer, replacement_function, replacement_function.body
    )
    assert not analyzer.global_vars
    assert function not in analyzer.node_scopes
    fresh = ScopeAnalyzer()
    fresh.analyze(replacement)
    assert snapshots_rebound_external_names(
        analyzer, replacement_function, replacement_function.body
    ) == snapshots_rebound_external_names(fresh, replacement_function, replacement_function.body)

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

"""The pure parts of the annotation ladder: what each rung writes, and what ends the ladder.

The end-to-end tests run the real checker over small projects; these pin each
rule against a helper and a refusal written out by hand, so a rule's edge is
tested where it is decided.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from towel.type_inference import TypeDiagnostic
from towel.unification.annotation_ladder import (
    Rejection,
    narrowing_refused_in_thunk,
    partial_type_passed,
    self_as_type_variable,
    targeted_any,
    unannotated_function,
    used_imports,
    without_quoted_none,
)
from towel.unification.annotation_wiring import mypy_ladder_flags
from towel.unification.exceptions import Untypeable
from towel.unification.placement import method_helper_position


def _function(source: str, index: int = 0) -> ast.FunctionDef:
    """The ``index``-th statement of ``source``, which must be a function definition."""
    node = ast.parse(textwrap.dedent(source)).body[index]
    assert isinstance(node, ast.FunctionDef)
    return node


# -- The judgments made from checker diagnostics ------------------------------------


def _call(source: str) -> ast.Call:
    node = ast.parse(source, mode="eval").body
    assert isinstance(node, ast.Call)
    return node


_THUNK_MODULE = textwrap.dedent("""
    def __extracted_func_0(__param_0, __param_1, __param_2):
        value = __param_1() if __param_0 else __param_2()
        return value


    def caller(self) -> float:
        return __extracted_func_0(self.total, lambda: self.total / 2, lambda: 0)
    """).lstrip()


def _thunk_refusal(message: str) -> Rejection:
    return Rejection(
        (TypeDiagnostic("/p/m.py", message, 7),),
        "/p/m.py",
        "__extracted_func_0",
        _THUNK_MODULE,
        {"/p/m.py": _THUNK_MODULE},
    )


def test_the_checker_says_when_a_truth_test_narrowed_what_a_lambda_reads() -> None:
    helper = _function(_THUNK_MODULE)
    refused = _thunk_refusal('Unsupported operand types for / ("None" and "int")')
    verdict = narrowing_refused_in_thunk(helper, refused)
    assert verdict is not None and verdict.reason is Untypeable.NARROWING_READ_IN_THUNK
    argument = _thunk_refusal(
        'Argument 1 to "__extracted_func_0" has incompatible type "int | None"; expected "int"'
    )
    assert narrowing_refused_in_thunk(helper, argument) is None


def _partial(source: str, *, checked_untyped: bool = False) -> object:
    text = textwrap.dedent(source).lstrip()
    module = ast.parse(text)
    call = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "helper"
    )
    return partial_type_passed([("m.py", text, call.lineno, call)], lambda _: checked_untyped)


def test_an_empty_collection_passed_before_anything_fills_it_is_named() -> None:
    verdict = _partial("""
        def f(options: dict[str, int]) -> None:
            attrs = {}
            helper(attrs, options)
        """)
    assert getattr(verdict, "reason", None) is Untypeable.PARTIAL_TYPE


def test_a_collection_filled_or_declared_before_the_call_is_not_partial() -> None:
    filled = """
        def f(options: dict[str, int]) -> None:
            attrs = {}
            attrs["a"] = 1
            helper(attrs, options)
        """
    declared = """
        def f(options: dict[str, int]) -> None:
            attrs: dict[str, int] = {}
            helper(attrs, options)
        """
    parameter = """
        def f(attrs: dict[str, int]) -> None:
            helper(attrs, attrs)
        """
    for source in (filled, declared, parameter):
        assert _partial(source) is None, source


def test_a_body_mypy_does_not_check_is_left_alone() -> None:
    source = """
        def f(options):
            attrs = {}
            helper(attrs, options)
        """
    assert _partial(source) is None
    assert (
        getattr(_partial(source, checked_untyped=True), "reason", None) is Untypeable.PARTIAL_TYPE
    )


# -- Where the helper goes ---------------------------------------------------------

_METHOD_HELPER = _function("""
    def __extracted_func_0(self, value: int) -> None:
        self.value = value
        self.twice = value * 2
    """)


def _position(body: str) -> object:
    source = "class Box:\n" + textwrap.indent(textwrap.dedent(body).lstrip(), "    ")
    return method_helper_position(source, "Box", _METHOD_HELPER, "self")


def test_no_later_assignment_leaves_the_helper_at_the_end_of_the_class() -> None:
    assert _position("""
            def __init__(self, value: int) -> None:
                self.__extracted_func_0(value)

            def reset(self) -> None:
                self.__extracted_func_0(0)
            """) is None


def test_an_assignment_before_the_first_call_keeps_the_declaration_where_it_was() -> None:
    assert _position("""
            def __init__(self) -> None:
                self.value = 0
                self.twice = 0

            def set(self, value: int) -> None:
                self.__extracted_func_0(value)

            def clear(self) -> None:
                self.value = -1
            """) is None


def test_a_later_assignment_puts_the_helper_right_after_the_first_caller() -> None:
    # __init__ ends on line 3 of the module ("class Box:" is line 1).
    assert _position("""
            def __init__(self, value: int) -> None:
                self.__extracted_func_0(value)

            def restore(self, state: object) -> None:
                self.value, self.twice = state  # type: ignore[misc]
            """) == 3


def test_the_first_callers_own_later_assignment_puts_the_helper_before_it() -> None:
    assert _position("""
            def __init__(self, value: int) -> None:
                self.__extracted_func_0(value)
                self.twice = value
            """) == 1


def test_attributes_pulling_both_ways_leave_the_helper_where_it_was() -> None:
    assert _position("""
            def __init__(self, value: int) -> None:
                self.value = value
                self.__extracted_func_0(value)
                self.twice = value
            """) is None


# -- The targeted rung ---------------------------------------------------------------

_MODULE = textwrap.dedent("""
    from typing import Any, Callable


    def __extracted_func_0(flag: bool, make: Callable[[], str | int], value: int | None) -> str:
        if flag:
            return make().upper()
        return str(value + 1)


    def caller(flag: bool) -> str:
        return __extracted_func_0(flag, lambda: "a", None)
    """).lstrip()
_TARGETED = _function(_MODULE, 1)


def _refusal(*errors: tuple[int, str]) -> Rejection:
    return Rejection(
        tuple(TypeDiagnostic("/p/m.py", message, line) for line, message in errors),
        "/p/m.py",
        "__extracted_func_0",
        _MODULE,
        {"/p/m.py": _MODULE},
    )


def test_a_thunk_whose_result_is_at_fault_keeps_its_shape() -> None:
    loosened = targeted_any(
        _TARGETED, _refusal((6, 'Item "int" of "str | int" has no attribute "upper"')), None
    )
    assert loosened is not None
    assert ast.unparse(loosened.args) == ("flag: bool, make: Callable[[], Any], value: int | None")
    assert loosened.returns is not None and ast.unparse(loosened.returns) == "str"


def test_an_argument_error_at_the_call_names_its_parameter() -> None:
    loosened = targeted_any(
        _TARGETED,
        _refusal(
            (11, 'Argument 3 to "__extracted_func_0" has incompatible type "None"; expected "int"')
        ),
        None,
    )
    assert loosened is not None
    assert ast.unparse(loosened.args) == "flag: bool, make: Callable[[], str | int], value: Any"


def test_returning_any_inside_the_helper_keeps_the_declared_type_beside_any() -> None:
    loosened = targeted_any(
        _TARGETED, _refusal((6, 'Returning Any from function declared to return "str"')), None
    )
    assert loosened is not None and loosened.returns is not None
    assert ast.unparse(loosened.returns) == "str | Any"


def test_an_error_that_points_nowhere_makes_no_rung() -> None:
    assert targeted_any(_TARGETED, _refusal((12, "Unrelated error elsewhere")), None) is None


# -- None and Self -----------------------------------------------------------------


def test_the_string_none_is_written_bare_everywhere_but_inside_another_string() -> None:
    helper = ast.parse(textwrap.dedent("""
        def f(a: 'None', b: "Literal['None']") -> '  None':
            c: 'None' = None
        """)).body[0]
    assert isinstance(helper, ast.FunctionDef)
    rewritten = without_quoted_none(helper)
    assert ast.unparse(rewritten).splitlines() == [
        "def f(a: None, b: \"Literal['None']\") -> None:",
        "    c: None = None",
    ]


def test_self_in_a_module_helper_becomes_a_variable_bound_to_the_sites_classes() -> None:
    helper = ast.parse("def f(cls: type[Self], d: int) -> Any: ...").body[0]
    assert isinstance(helper, ast.FunctionDef)
    declared = [ast.Name(id="Self", ctx=ast.Load()), ast.Name(id="Self", ctx=ast.Load())]
    spelled = self_as_type_variable(helper, ["A", "B", "A"], {"f"}, list(declared), True)
    assert spelled is not None
    rewritten, declarations = spelled
    assert ast.unparse(rewritten) == "def f(cls: type[_TowelSelf], d: int) -> _TowelSelf:\n    ..."
    assert ast.unparse(declarations[1]) == (
        "_TowelSelf = _towel_typevar('_TowelSelf', bound='A | B')"
    )


def test_no_self_in_a_parameter_makes_no_variable() -> None:
    helper = ast.parse("def f(d: int) -> Self: ...").body[0]
    assert isinstance(helper, ast.FunctionDef)
    assert self_as_type_variable(helper, ["A"], set(), [None], False) is None


# -- What the ladder knows before checking -------------------------------------------


def test_the_ladder_reads_strict_and_the_flags_it_sets_per_module(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mypy]\nstrict = true\n\n"
        "[[tool.mypy.overrides]]\nmodule = 'pkg.loose'\nwarn_return_any = false\n"
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "loose.py").write_text("")
    (package / "tight.py").write_text("")
    assert mypy_ladder_flags(package / "tight.py") == {
        "disallow_untyped_defs": True,
        "warn_return_any": True,
        "check_untyped_defs": True,
    }
    assert mypy_ladder_flags(package / "loose.py")["warn_return_any"] is False
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\ncheck_untyped_defs = true\n")
    assert mypy_ladder_flags(package / "tight.py") == {
        "disallow_untyped_defs": False,
        "warn_return_any": False,
        "check_untyped_defs": True,
    }


# -- A fully annotated module --------------------------------------------------------


def test_a_module_is_fully_annotated_as_mypy_strict_counts_it() -> None:
    annotated = ast.parse(textwrap.dedent("""
        class Box:
            def __init__(self, value: int):
                self.value = value

            @staticmethod
            def make(value: int) -> "Box":
                return Box(value)

            @classmethod
            def empty(cls) -> "Box":
                def zero() -> int:
                    return 0
                return cls(zero())


        def double(values: list[int], *rest: int, **named: int) -> int:
            return sum(map(lambda v: v * 2, values))
        """))
    assert unannotated_function(annotated) is None
    for source, name in [
        ("def f(x: int):\n    return x\n", "f"),
        ("class A:\n    def m(self, x):\n        return x\n", "m"),
        ("class A:\n    @staticmethod\n    def s(x) -> int:\n        return 1\n", "s"),
        ("class A:\n    def __init__(self):\n        pass\n", "__init__"),
        ("def f() -> None:\n    def g(y):\n        pass\n", "g"),
    ]:
        assert unannotated_function(ast.parse(source)) == name, source


# -- Imports and keys after a rung rewrites the signature ----------------------------


def test_an_import_a_rung_no_longer_spells_is_dropped() -> None:
    helper = _function("""
        def f(item: 'Item', count: Any) -> 'dict[Item, _TowelT0]':
            local: 'Box' = make()
        """)
    declarations = tuple(
        ast.parse("_TowelT0 = _towel_typevar('_TowelT0', bound='Tag | None')").body
    )
    imports = [
        ("pkg.models", "Item"),
        ("pkg.models", "Box"),
        ("pkg.tags", "Tag"),
        ("pkg.models", "Gone"),
        ("typing", "Any"),
    ]
    assert used_imports(imports, helper, declarations) == (
        ("pkg.models", "Item"),
        ("pkg.models", "Box"),
        ("pkg.tags", "Tag"),
        ("typing", "Any"),
    )


def _helper_operand_refusal(
    expression: str,
    message: str,
    *,
    original: str = "helper(lambda: task.total is not None, lambda: task.total, fallback)",
    line: int | None = 2,
    path: str = "/p/m.py",
) -> object:
    module = (
        "def helper(predicate, result, fallback):\n"
        f"    return {expression}\n"
        "\ndef caller(task, fallback):\n"
        "    return helper(wrap_predicate(task), wrap_result(task), fallback)\n"
    )
    helper = _function(module)
    rejection = Rejection(
        (TypeDiagnostic(path, message, line),),
        "/p/m.py",
        "helper",
        module,
        {"/p/m.py": module},
    )
    call = _call(original)
    before = (ast.dump(helper), ast.dump(call))
    verdict = narrowing_refused_in_thunk(helper, rejection, [call])
    assert before == (ast.dump(helper), ast.dump(call))
    return verdict


def test_a_nullable_operand_in_the_helper_uses_the_original_unwrapped_call() -> None:
    diagnostic = 'Argument 1 to "consume" has incompatible type "float | None"; expected "float"'
    verdict = _helper_operand_refusal("consume(result()) if predicate() else fallback", diagnostic)
    assert getattr(verdict, "reason", None) is Untypeable.NARROWING_READ_IN_THUNK
    assert "task.total is not None" in getattr(verdict, "detail", "")


def test_a_nullable_binary_operand_consumes_the_governed_result() -> None:
    diagnostic = 'Unsupported operand types for / ("None" and "int")'
    assert (
        _helper_operand_refusal("result() / 2 if predicate() else fallback", diagnostic) is not None
    )
    assert _helper_operand_refusal("2 / result() if predicate() else fallback", diagnostic) is None
    opposite = 'Unsupported operand types for / ("int" and "None")'
    assert (
        _helper_operand_refusal("2 / result() if predicate() else fallback", opposite) is not None
    )


def test_nullable_diagnostics_must_identify_the_exact_governed_operand() -> None:
    diagnostic = 'Argument 1 to "consume" has incompatible type "float | None"; expected "float"'
    for expression in (
        "consume(other()) if predicate() else result()",
        "consume(other()) + result() if predicate() else fallback",
        "consume(result()) + consume(other()) if predicate() else fallback",
        "consume(convert(result())) if predicate() else fallback",
        "consume(result()) + (other() if predicate() else fallback)",
        "consume(result(0)) if predicate() else fallback",
        "consume(result(unused=0)) if predicate() else fallback",
        "consume(result.__call__()) if predicate() else fallback",
        "consume(result()) if unrelated() else fallback",
    ):
        assert _helper_operand_refusal(expression, diagnostic) is None, expression
    expression = "consume(result()) if predicate() else fallback"
    for actual in ("float", "list[float | None]", "Callable[[], float | None]", "Literal['None']"):
        error = f'Argument 1 to "consume" has incompatible type "{actual}"; expected "float | None"'
        assert _helper_operand_refusal(expression, error) is None, actual
    for message in (
        'Argument 2 to "consume" has incompatible type "float | None"; expected "float"',
        'Argument 1 to "different" has incompatible type "float | None"; expected "float"',
        'Argument 1 to "consume" has incompatible type "float | None"; expected "str | None"',
        'Argument 1 to "consume" has incompatible type "float | None"; expected "Optional[str]"',
        'Argument 1 to "consume" has incompatible type "str"; expected "int"',
        'No overload variant of "consume" matches argument type "_TowelT0"',
        'Incompatible return value type (got "float | None", expected "float")',
        'Returning Any from function declared to return "float"',
    ):
        assert _helper_operand_refusal(expression, message) is None, message
    for line in (None, 1, 5, 900):
        assert _helper_operand_refusal(expression, diagnostic, line=line) is None
    assert _helper_operand_refusal(expression, diagnostic, path="/p/other.py") is None


def test_helper_refinement_requires_a_direct_original_subject_read() -> None:
    expression = "consume(result()) if predicate() else fallback"
    diagnostic = 'Argument 1 to "consume" has incompatible type "float | None"; expected "float"'
    for original in (
        "helper(lambda: task.total is not None, lambda: convert(task.total), fallback)",
        "helper(lambda: task.total is not None, lambda: task.completed, fallback)",
        "helper(lambda: task.total is not None, lambda: task.total.optional_attribute, fallback)",
        "helper(lambda: task.finished, lambda: task.total, fallback)",
        "helper(lambda unused: task.total is not None, lambda: task.total, fallback)",
    ):
        assert _helper_operand_refusal(expression, diagnostic, original=original) is None, original
    assert (
        _helper_operand_refusal(
            expression,
            diagnostic,
            original=(
                "helper(predicate=lambda: task.total is not None, result=lambda: task.total, fallback=0)"
            ),
        )
        is not None
    )


def test_a_caller_nullable_argument_error_is_not_a_signature_error() -> None:
    module = (
        "def helper(predicate, result):\n"
        "    return result() if predicate() else 0\n"
        "\ndef caller(task):\n"
        "    return helper(lambda: task.total is not None, lambda: consume(task.total))\n"
    )
    helper = _function(module)
    diagnostic = 'Argument 1 to "consume" has incompatible type "float | None"; expected "float"'
    rejection = Rejection(
        (TypeDiagnostic("/p/m.py", diagnostic, 5),),
        "/p/m.py",
        "helper",
        module,
        {"/p/m.py": module},
    )
    assert narrowing_refused_in_thunk(helper, rejection) is not None
    for expression in (
        "consume(convert(task.total))",
        "consume(task.completed) + task.total",
        "consume(task.total) + consume(task.completed)",
    ):
        rewritten = module.replace("consume(task.total)", expression)
        rejection = Rejection(
            (TypeDiagnostic("/p/m.py", diagnostic, 5),),
            "/p/m.py",
            "helper",
            rewritten,
            {"/p/m.py": rewritten},
        )
        assert narrowing_refused_in_thunk(helper, rejection) is None, expression


def test_a_nullable_helper_alias_requires_its_same_suite_reaching_assignment() -> None:
    diagnostic = 'Argument 1 to "consume" has incompatible type "float | None"; expected "float"'
    for body, expected in (
        ("alias = result()\n    return consume(alias)", True),
        ("first = result()\n    alias = first\n    return consume(alias)", True),
        ("alias: float | None = result()\n    return consume(alias)", True),
        ("alias = result()\n    alias = other()\n    return consume(alias)", False),
        ("alias = convert(result())\n    return consume(alias)", False),
        (
            "alias = result()\n    if other():\n        alias = other()\n    return consume(alias)",
            False,
        ),
        ("if other():\n        alias = result()\n    return consume(alias)", False),
        ("alias = first\n    first = result()\n    return consume(alias)", False),
        ("alias = alias\n    return consume(alias)", False),
        ("alias = result()\n    del alias\n    return consume(alias)", False),
    ):
        source = (
            "def helper(predicate, result, fallback):\n    if predicate():\n        "
            + (body.replace("\n", "\n    "))
            + "\n    return fallback\n"
        )
        helper = _function(source)
        line = next(i for i, text in enumerate(source.splitlines(), 1) if "consume(alias)" in text)
        rejection = Rejection(
            (TypeDiagnostic("/p/m.py", diagnostic, line),),
            "/p/m.py",
            "helper",
            source,
            {"/p/m.py": source},
        )
        call = _call("helper(lambda: task.total is not None, lambda: task.total, fallback)")
        verdict = narrowing_refused_in_thunk(helper, rejection, [call])
        assert (verdict is not None) is expected, source


def _pyright_argument_diagnostic(
    actual: str = "float | None",
    expected: str = "ConvertibleToInt",
    concrete: str = "float | None",
    none: str = "None",
) -> str:
    return (
        f'pyright: reportArgumentType: Argument of type "{actual}" cannot be assigned to '
        f'parameter "x" of type "{expected}" in function "__new__"\n'
        f'  Type "{concrete}" is not assignable to type "{expected}"\n'
        f'    Type "{none}" is not assignable to type "{expected}"'
    )


def test_pyrights_nullable_operand_witness_survives_a_constrained_type_variable() -> None:
    expression = "convert(result()) if predicate() else fallback"
    assert _helper_operand_refusal(expression, _pyright_argument_diagnostic()) is not None
    assert (
        _helper_operand_refusal(
            expression,
            _pyright_argument_diagnostic(
                actual="_TowelT0@helper", concrete="float* | None", none="None*"
            ),
        )
        is not None
    )
    for arguments in (
        {"actual": "_TowelT0@helper", "concrete": "object*"},
        {"actual": "list[float | None]", "concrete": "list[float | None]"},
        {"actual": "Callable[[], float | None]", "concrete": "Callable[[], float | None]"},
        {"expected": "str | None"},
        {"expected": "Optional[str]"},
        {"none": "float"},
    ):
        assert (
            _helper_operand_refusal(expression, _pyright_argument_diagnostic(**arguments)) is None
        )
    # A nested incompatibility has a different expected type, so cannot prove
    # that the outer operand itself was nullable.
    nested = _pyright_argument_diagnostic(
        actual="list[float | None]", concrete="list[float | None]", expected="list[float]"
    )
    nested += '\n  Type "float | None" is not assignable to type "float"'
    assert _helper_operand_refusal(expression, nested) is None


def test_pyrights_implementation_callee_requires_a_unique_argument() -> None:
    diagnostic = _pyright_argument_diagnostic()
    for expression in (
        "convert(result()) + other(other_result()) if predicate() else fallback",
        "convert(result()) + __new__(other_result()) if predicate() else fallback",
        "__new__(result()) + convert(other_result()) if predicate() else fallback",
        "convert(result(), fallback) if predicate() else fallback",
        "convert(result(), unused=0) if predicate() else fallback",
        "convert(value=result()) if predicate() else fallback",
    ):
        assert _helper_operand_refusal(expression, diagnostic) is None, expression
    assert (
        _helper_operand_refusal("convert(x=result()) if predicate() else fallback", diagnostic)
        is not None
    )

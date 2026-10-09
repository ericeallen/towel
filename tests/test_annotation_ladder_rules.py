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
    OriginalThunkCall,
    Rejection,
    Unanswerable,
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


def test_named_mypy_arguments_identify_only_the_matching_keyword_operand() -> None:
    diagnostic = (
        'Argument "value" to "consume" has incompatible type "float | None"; expected "float"'
    )
    assert (
        _helper_operand_refusal("consume(value=result()) if predicate() else fallback", diagnostic)
        is not None
    )
    for expression in (
        "consume(other=result()) if predicate() else fallback",
        "consume(result()) if predicate() else fallback",
        "consume(value=other()) + result() if predicate() else fallback",
        "consume(value=convert(result())) if predicate() else fallback",
        "consume(value=result()) + consume(value=other()) if predicate() else fallback",
    ):
        assert _helper_operand_refusal(expression, diagnostic) is None, expression
    accepting_none = diagnostic.replace('expected "float"', 'expected "str | None"')
    assert (
        _helper_operand_refusal(
            "consume(value=result()) if predicate() else fallback", accepting_none
        )
        is None
    )


def test_pyrights_primitive_none_rejection_is_the_same_nullable_argument_witness() -> None:
    diagnostic = (
        'pyright: reportArgumentType: Argument of type "_TowelT0@helper" cannot be assigned '
        'to parameter "value" of type "float" in function "consume"\n'
        '  Type "float* | None" is not assignable to type "float"\n'
        '    "None*" is not assignable to "float"'
    )
    expression = "consume(value=result()) if predicate() else fallback"
    assert _helper_operand_refusal(expression, diagnostic) is not None
    assert (
        _helper_operand_refusal(expression, diagnostic.replace('"None*" is', '"str*" is')) is None
    )
    assert (
        _helper_operand_refusal(
            expression, diagnostic.replace('"float* | None"', '"list[float* | None]"')
        )
        is None
    )


def test_pyrights_operator_constraint_identifies_the_actual_nullable_side() -> None:
    diagnostic = (
        'pyright: reportOperatorIssue: Operator "/" not supported for types '
        '"_TowelT0@helper" and "Literal[2]"\n'
        '  Operator "/" not supported for types "None*" and "Literal[2]"'
    )
    expression = "result() / 2 if predicate() else fallback"
    assert _helper_operand_refusal(expression, diagnostic) is not None
    for rewritten in (
        diagnostic.split("\n")[0],
        diagnostic.replace('"None*"', '"float*"'),
        diagnostic.replace('"_TowelT0@helper"', '"list[_TowelT0@helper]"'),
        diagnostic.replace('"_TowelT0@helper"', '"Callable[[], _TowelT0@helper]"'),
        diagnostic.replace('"None*" and "Literal[2]"', '"None*" and "str"'),
        diagnostic.replace('  Operator "/"', '  Operator "*"'),
    ):
        assert _helper_operand_refusal(expression, rewritten) is None, rewritten
    assert (
        _helper_operand_refusal(
            "result() / 2 + other() / 2 if predicate() else fallback", diagnostic
        )
        is None
    )
    assert _helper_operand_refusal("2 / result() if predicate() else fallback", diagnostic) is None
    direct = (
        'pyright: reportOperatorIssue: Operator "/" not supported for types '
        '"float | None" and "Literal[2]"'
    )
    assert _helper_operand_refusal(expression, direct) is not None
    assert (
        _helper_operand_refusal(
            expression, direct.replace('"float | None"', '"list[float | None]"')
        )
        is None
    )


def test_pyrights_optional_operator_needs_a_unique_nonliteral_side() -> None:
    diagnostic = 'pyright: reportOptionalOperand: Operator "/" not supported for "None"'
    assert (
        _helper_operand_refusal("result() / 2 if predicate() else fallback", diagnostic) is not None
    )
    assert (
        _helper_operand_refusal("2 / result() if predicate() else fallback", diagnostic) is not None
    )
    for expression in (
        "result() / other() if predicate() else fallback",
        "result() / None if predicate() else fallback",
        "2 / 3 if predicate() else result()",
        "result() + 2 if predicate() else fallback",
        "result() / 2 + other() / 2 if predicate() else fallback",
        "convert(result()) / 2 if predicate() else fallback",
    ):
        assert _helper_operand_refusal(expression, diagnostic) is None, expression


def test_mypy_reflected_comparisons_reverse_the_diagnosed_operand_order() -> None:
    reflected = 'Unsupported operand types for <= ("int" and "None")'
    direct = 'Unsupported operand types for >= ("None" and "int")'
    expression = "result() >= 2 if predicate() else fallback"
    assert _helper_operand_refusal(expression, reflected) is not None
    assert _helper_operand_refusal(expression, direct) is not None
    wrong_side = 'Unsupported operand types for <= ("None" and "int")'
    assert _helper_operand_refusal(expression, wrong_side) is None
    assert _helper_operand_refusal("result() < 2 if predicate() else fallback", reflected) is None
    assert (
        _helper_operand_refusal(
            "result() >= 2 and 2 <= other() if predicate() else fallback", reflected
        )
        is None
    )
    assert (
        _helper_operand_refusal("result() >= 2 < other() if predicate() else fallback", reflected)
        is not None
    )


def test_pyright_comparisons_do_not_use_mypys_reflected_order() -> None:
    diagnostic = (
        'pyright: reportOperatorIssue: Operator ">=" not supported for types '
        '"_TowelT0@helper" and "Literal[2]"\n'
        '  Operator ">=" not supported for types "None*" and "Literal[2]"'
    )
    assert (
        _helper_operand_refusal("result() >= 2 if predicate() else fallback", diagnostic)
        is not None
    )
    assert _helper_operand_refusal("result() <= 2 if predicate() else fallback", diagnostic) is None
    assert _helper_operand_refusal("2 >= result() if predicate() else fallback", diagnostic) is None


def test_pyright_protocol_rejection_tracks_the_instantiated_target() -> None:
    diagnostic = (
        'pyright: reportArgumentType: Argument of type "_TowelT0@helper" cannot be assigned '
        'to parameter "x" of type "Protocol[_T@consumer]" in function "consumer"\n'
        '  Type "float* | None" is not assignable to type "Protocol[float]"\n'
        '    "None*" is incompatible with protocol "Protocol[float]"'
    )
    expression = "consumer(result()) if predicate() else fallback"
    assert _helper_operand_refusal(expression, diagnostic) is not None
    for rewritten in (
        diagnostic.replace('"Protocol[float]"\n', '"Different[float]"\n'),
        diagnostic.replace(
            'incompatible with protocol "Protocol[float]"',
            'incompatible with protocol "Different[float]"',
        ),
        diagnostic.replace('"Protocol[_T@consumer]"', '"Protocol[_T@consumer] | None"'),
        diagnostic.replace('"float* | None"', '"list[float* | None]"'),
        diagnostic.replace('"None*" is incompatible', '"str*" is incompatible'),
    ):
        assert _helper_operand_refusal(expression, rewritten) is None, rewritten


def test_pyright_optional_unary_operands_are_unique_across_matching_operations() -> None:
    diagnostic = 'pyright: reportOptionalOperand: Operator "-" not supported for "None"'
    assert _helper_operand_refusal("-result() if predicate() else fallback", diagnostic) is not None
    for expression in (
        "+result() if predicate() else fallback",
        "-result() + -other() if predicate() else fallback",
        "-result() + (other() - another()) if predicate() else fallback",
        "-convert(result()) if predicate() else fallback",
        "-other() + result() if predicate() else fallback",
    ):
        assert _helper_operand_refusal(expression, diagnostic) is None, expression
    assert (
        _helper_operand_refusal("-result() + (2 - 3) if predicate() else fallback", diagnostic)
        is not None
    )


def test_pyright_optional_member_names_select_receiver_operands_without_name_semantics() -> None:
    diagnostic = 'pyright: reportOptionalMemberAccess: "custom" is not a known attribute of "None"'
    assert (
        _helper_operand_refusal("result().custom() if predicate() else fallback", diagnostic)
        is not None
    )
    for expression in (
        "result().other() if predicate() else fallback",
        "other().custom() + result() if predicate() else fallback",
        "convert(result()).custom() if predicate() else fallback",
        "result().custom() + other().custom() if predicate() else fallback",
        "result().nested.custom() if predicate() else fallback",
    ):
        assert _helper_operand_refusal(expression, diagnostic) is None, expression


def test_pyright_optional_subscripts_identify_the_receiver_and_not_the_index() -> None:
    diagnostic = 'pyright: reportOptionalSubscript: Object of type "None" is not subscriptable'
    assert (
        _helper_operand_refusal("result()[0] if predicate() else fallback", diagnostic) is not None
    )
    for expression in (
        "other()[result()] if predicate() else fallback",
        "result()[0] + other()[0] if predicate() else fallback",
        "convert(result())[0] if predicate() else fallback",
        "result()[0][1] if predicate() else fallback",
    ):
        assert _helper_operand_refusal(expression, diagnostic) is None, expression


def test_unknown_pyright_argument_positions_block_other_calls_appearing_unique() -> None:
    diagnostic = _pyright_argument_diagnostic()
    expression = "convert(result()) + other(first(), second()) if predicate() else fallback"
    assert _helper_operand_refusal(expression, diagnostic) is None


def _value_refusal(
    body: str,
    message: str,
    *,
    returns: str = "float",
    fallback: str = "float",
    original: str = "helper(lambda: task.total is not None, lambda: task.total, fallback)",
    path: str = "/p/m.py",
    line: int | None = None,
    caller_returns: str | None = "float",
    original_body: str | None = None,
) -> Unanswerable | None:
    module = (
        f"def helper(predicate, result, fallback: {fallback}) -> {returns}:\n"
        + "\n".join("    " + statement for statement in body.splitlines())
        + "\n\ndef caller(task, fallback):\n"
        + "    return helper(wrap_predicate(task), wrap_result(task), fallback)\n"
    )
    helper = _function(module)
    call = _call(original)
    before = ast.dump(helper), ast.dump(call)
    rejection = Rejection(
        (TypeDiagnostic(path, message, line if line is not None else len(body.splitlines()) + 1),),
        "/p/m.py",
        "helper",
        module,
        {"/p/m.py": module},
    )
    original_body = (
        original_body
        if original_body is not None
        else body.replace("result()", "task.total").replace("predicate()", "task.total is not None")
    )
    statements = tuple(ast.parse(textwrap.dedent(original_body)).body)
    context = OriginalThunkCall(
        call,
        ast.parse(caller_returns, mode="eval").body if caller_returns is not None else None,
        statements,
    )
    result = narrowing_refused_in_thunk(helper, rejection, [call], [context])
    assert (ast.dump(helper), ast.dump(call)) == before
    return result


_VALUE_RETURN = 'Incompatible return value type (got "float | None", expected "float")'
_VALUE_ASSIGNMENT = (
    'Incompatible types in assignment (expression has type "float | None", '
    'variable has type "float")'
)


def test_nullable_return_and_assignment_witness_selected_thunk_values() -> None:
    selected = "result() if predicate() else fallback"
    for body in (
        f"value = {selected}\nreturn value",
        f"value = {selected}\nother = value\nreturn other",
        f"return {selected}",
        "if predicate():\n    return result()\nreturn fallback",
    ):
        line = 3 if body.startswith("if ") else None
        assert _value_refusal(body, _VALUE_RETURN, line=line) is not None, body
    assert _value_refusal(f"value: float = {selected}\nreturn value + 1", _VALUE_ASSIGNMENT, line=2)


def test_nullable_value_witness_requires_the_declaration_and_original_call() -> None:
    body = "value = result() if predicate() else fallback\nreturn value"
    assert _value_refusal(body, _VALUE_RETURN, returns="str") is None
    assert _value_refusal(body, _VALUE_RETURN, path="/elsewhere/m.py") is None
    assert _value_refusal(body, _VALUE_RETURN, line=2) is None
    assert _value_refusal(body, _VALUE_RETURN, line=99) is None
    for original in (
        "helper(lambda: task.other is not None, lambda: task.total, fallback)",
        "helper(lambda: task.total is not None, lambda: convert(task.total), fallback)",
        "helper(lambda: task.total is not None, lambda: task.other, fallback)",
    ):
        assert _value_refusal(body, _VALUE_RETURN, original=original) is None
    assert (
        _value_refusal(body, 'Incompatible return value type (got "_T | float", expected "float")')
        is None
    )
    assert (
        _value_refusal(
            body, 'Incompatible return value type (got "list[float | None]", expected "float")'
        )
        is None
    )
    nullable = 'Incompatible return value type (got "float | None", expected "float | None")'
    assert _value_refusal(body, nullable, returns="float | None") is None
    assert (
        _value_refusal("value: str = result() if predicate() else fallback", _VALUE_ASSIGNMENT)
        is None
    )


def test_selected_value_alternatives_require_a_nonnullable_proof() -> None:
    body = "value = result() if predicate() else fallback\nreturn value"
    for annotation in ("Any", "object", "_T", "Alias", "float | None", "Callable[[], float]"):
        assert _value_refusal(body, _VALUE_RETURN, fallback=annotation) is None, annotation
    for body in (
        "value = result() if predicate() else other()\nreturn value",
        "value = result() if predicate() else unknown\nreturn value",
        "value = result() if predicate() else alias\nreturn value",
        "value = result() if predicate() else None\nreturn value",
        "fallback = unknown\nvalue = result() if predicate() else fallback\nreturn value",
        "if condition:\n    fallback = unknown\nvalue = result() if predicate() else fallback\nreturn value",
        "value = convert(result()) if predicate() else fallback\nreturn value",
        "value = result() if unrelated() else fallback\nreturn value",
        "value = result() if predicate() else fallback\nvalue = other()\nreturn value",
        "value = result() if predicate() else fallback\nif condition:\n    value = other()\nreturn value",
        "return result(); return other()",
    ):
        assert _value_refusal(body, _VALUE_RETURN) is None, body


def test_fixed_tuple_return_types_identify_the_exact_nullable_component() -> None:
    body = "value = result() if predicate() else fallback\nreturn value, 1"
    message = 'Incompatible return value type (got "tuple[float | None, int]", expected "tuple[float, int]")'
    assert (
        _value_refusal(
            body, message, returns="tuple[float, int]", caller_returns="tuple[float, int]"
        )
        is not None
    )
    reversed_message = 'Incompatible return value type (got "tuple[int, float | None]", expected "tuple[int, float]")'
    assert _value_refusal(
        body.replace("value, 1", "1, value"),
        reversed_message,
        returns="tuple[int, float]",
        caller_returns="tuple[int, float]",
    )
    for actual, expected, expression in (
        ("tuple[int, float | None]", "tuple[float, int]", "value, 1"),
        ("tuple[float | None, int | None]", "tuple[float, int]", "value, other()"),
        ("list[float | None]", "list[float]", "[value]"),
        ("tuple[float | None, ...]", "tuple[float, ...]", "value, 1"),
        ("tuple[float | None, int]", "tuple[float | None, int]", "value, 1"),
        ("tuple[float | None, int]", "tuple[float, int]", "other(), value"),
        ("tuple[float | None, int]", "tuple[float, int]", "convert(value), 1"),
    ):
        diagnostic = f'Incompatible return value type (got "{actual}", expected "{expected}")'
        assert (
            _value_refusal(
                body.replace("value, 1", expression),
                diagnostic,
                returns=expected,
                caller_returns=expected,
            )
            is None
        )


def test_pyright_nullable_value_details_match_the_exact_tuple_target() -> None:
    body = "value = result() if predicate() else fallback\nreturn value, 1"
    message = (
        'pyright: reportReturnType: Type "tuple[_T@helper | float, Literal[1]]" '
        'is not assignable to return type "tuple[float, int]"\n'
        '  Type "float* | float | None" is not assignable to type "float"\n'
        '    "None*" is not assignable to "float"'
    )
    assert (
        _value_refusal(
            body, message, returns="tuple[float, int]", caller_returns="tuple[float, int]"
        )
        is not None
    )
    for diagnostic, annotation in (
        (message.replace('"float* | float | None"', '"object* | float"'), "tuple[float, int]"),
        (message.replace('"float* | float | None"', '"list[float* | None]"'), "tuple[float, int]"),
        (message.replace('type "float"\n', 'type "str"\n'), "tuple[float, int]"),
        (message.replace('"None*"', '"str*"'), "tuple[float, int]"),
        (
            message.replace(
                "tuple[_T@helper | float, Literal[1]]", "tuple[float, _T@helper | int]"
            ),
            "tuple[float, int]",
        ),
        (
            message.replace(
                "tuple[_T@helper | float, Literal[1]]",
                "tuple[_T@helper | float, _U@helper | float]",
            ).replace("tuple[float, int]", "tuple[float, float]"),
            "tuple[float, float]",
        ),
        (
            message.replace("tuple[float, int]", "tuple[float | None, int]"),
            "tuple[float | None, int]",
        ),
    ):
        assert (
            _value_refusal(body, diagnostic, returns=annotation, caller_returns=annotation) is None
        ), diagnostic
    assignment = (
        'pyright: reportAssignmentType: Type "_T@helper | float" '
        'is not assignable to declared type "float"\n'
        '  Type "float* | float | None" is not assignable to type "float"\n'
        '    "None*" is not assignable to "float"'
    )
    assert _value_refusal("value: float = result() if predicate() else fallback", assignment)


def test_provisional_helper_return_does_not_invent_an_original_nonnullable_obligation() -> None:
    body = "value = result() if predicate() else fallback\nreturn value"
    for annotation in (None, "float | None", "object", "Any", "_T", "Alias"):
        assert _value_refusal(body, _VALUE_RETURN, caller_returns=annotation) is None
    message = 'Incompatible return value type (got "tuple[float | None, int]", expected "tuple[float, int]")'
    assert (
        _value_refusal(
            body.replace("return value", "return value, 1"),
            message,
            returns="tuple[float, int]",
            caller_returns="tuple[float | None, int]",
        )
        is None
    )
    # An explicit original annotated assignment still obliges its float target,
    # even when the enclosing function accepts a nullable return.
    assert (
        _value_refusal(
            "value: float = result() if predicate() else fallback",
            _VALUE_ASSIGNMENT,
            caller_returns="float | None",
        )
        is not None
    )


def test_original_return_obligation_follows_the_exact_original_output_component() -> None:
    body = "value = result() if predicate() else fallback\nreturn value"
    assert (
        _value_refusal(
            body,
            _VALUE_RETURN,
            original_body="value = task.total if task.total is not None else fallback",
        )
        is None
    )
    assert (
        _value_refusal(
            body,
            _VALUE_RETURN,
            original_body="value = task.total if task.total is not None else fallback\nreturn other",
        )
        is None
    )
    message = 'Incompatible return value type (got "tuple[float | None, int]", expected "tuple[float, int]")'
    assert (
        _value_refusal(
            body.replace("return value", "return value, 1"),
            message,
            returns="tuple[float, int]",
            caller_returns="tuple[float, int]",
            original_body="value = task.total if task.total is not None else fallback\nreturn 1, value",
        )
        is None
    )
    assert (
        _value_refusal(
            "value: float = result() if predicate() else fallback",
            _VALUE_ASSIGNMENT,
            original_body="value: float = other if task.total is not None else fallback",
        )
        is None
    )

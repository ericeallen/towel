"""Refactoring preserves hidden type-parameter scopes and unbound lexical names.

These programs came from the 1.732 pre-release audit. Run original and transformed
programs in separate interpreters: successful compilation cannot detect a helper
reading a different binding, or reading the right binding on an untaken path.
"""

from __future__ import annotations

from pathlib import Path
import sys
import textwrap

import pytest

from tests.hostile_execution import observe
from towel.diagnostics import Settings
from towel.unification.refactor_engine import UnificationRefactorEngine

SERIAL = Settings.from_environ({"TOWEL_WORKERS": "1"})
REQUIRES_TYPE_PARAMETERS = pytest.mark.skipif(
    sys.version_info < (3, 12), reason="PEP 695 syntax requires Python 3.12"
)


def _assert_preserved(source: str, expected: str, tmp_path: Path) -> None:
    before = tmp_path / "before.py"
    before.write_text(textwrap.dedent(source))
    engine = UnificationRefactorEngine(settings=SERIAL)
    final, applied, _ = engine.refactor_to_fixed_point(str(before), progress="none")
    assert applied > 0, "The regression must exercise a real transformation"
    (tmp_path / "after.py").write_text(final)
    original = observe("before.py", tmp_path)
    assert original == (0, expected, [])
    assert observe("after.py", tmp_path) == original


@REQUIRES_TYPE_PARAMETERS
@pytest.mark.parametrize("declaration", ["T", "*T", "**T"])
@pytest.mark.parametrize("site_count", [2, 3])
def test_generic_functions_keep_their_own_type_parameter_identity(
    declaration: str, site_count: int, tmp_path: Path
) -> None:
    names = ("first", "second", "third")[:site_count]
    source = "\n".join(f"""def {name}[{declaration}](x):
    a = T
    b = x + 1
    c = b * 3
    return a, c
""" for name in names)
    source += "\n".join(f"print({name}(1)[0] is {name}.__type_params__[0])" for name in names)
    _assert_preserved(source, "True\n" * site_count, tmp_path)


@REQUIRES_TYPE_PARAMETERS
def test_partial_generic_function_bodies_keep_type_parameter_bindings(tmp_path: Path) -> None:
    _assert_preserved(
        """
        def first[T](x):
            assert x
            a = T.__name__
            b = a + str(x)
            c = b.upper()
            return c

        def second[T](x):
            if x < 0:
                raise ValueError(x)
            a = T.__name__
            b = a + str(x)
            c = b.upper()
            return c

        print(first(1), second(2))
        """,
        "T1 T2\n",
        tmp_path,
    )


@REQUIRES_TYPE_PARAMETERS
@pytest.mark.parametrize("parameter", ["T", "len"])
def test_unrelated_generic_classes_pass_their_own_type_parameters(
    parameter: str, tmp_path: Path
) -> None:
    source = "\n".join(f"""class {name}[{parameter}]:
    def value(self, x):
        a = {parameter}
        b = x + 1
        c = b * 3
        return a, c
""" for name in ("First", "Second"))
    source += "\n".join(
        f"print({name}().value(1)[0] is {name}.__type_params__[0])" for name in ("First", "Second")
    )
    _assert_preserved(source, "True\nTrue\n", tmp_path)


@REQUIRES_TYPE_PARAMETERS
def test_nested_functions_keep_an_enclosing_generic_parameter(tmp_path: Path) -> None:
    _assert_preserved(
        """
        def outer[T]():
            def first(x):
                a = T
                b = x + 1
                c = b * 3
                return a, c
            def second(x):
                a = T
                b = x + 1
                c = b * 3
                return a, c
            return first(1)[0], second(2)[0]
        print(all(value is outer.__type_params__[0] for value in outer()))
        """,
        "True\n",
        tmp_path,
    )


@pytest.mark.parametrize("name", ["len", "value"])
def test_unbound_local_shadows_builtins_and_module_bindings(name: str, tmp_path: Path) -> None:
    _assert_preserved(
        f"""
        value = 100
        def first(flag):
            if flag:
                {name} = 7
            if flag:
                a = {name} + 1
            else:
                a = 5
            b = a * 2
            c = b - 3
            return c

        def second(flag):
            for index in range(int(flag)):
                {name} = 8
            if flag:
                a = {name} + 1
            else:
                a = 5
            b = a * 2
            c = b - 3
            return c
        print(first(False), second(False), first(True), second(True))
        """,
        "7 7 13 15\n",
        tmp_path,
    )


@pytest.mark.parametrize("name", ["len", "value"])
def test_unbound_closure_cell_shadows_builtins_and_module_bindings(
    name: str, tmp_path: Path
) -> None:
    _assert_preserved(
        f"""
        value = 100
        def outer(flag):
            if flag:
                {name} = 7
            def first():
                if flag:
                    a = {name} + 1
                else:
                    a = 5
                b = a * 2
                c = b - 3
                return c
            def second():
                if flag:
                    a = {name} + 1
                else:
                    a = 5
                b = a * 2
                c = b - 3
                return c
            return first(), second()
        print(outer(False), outer(True))
        """,
        "(7, 7) (13, 13)\n",
        tmp_path,
    )


@REQUIRES_TYPE_PARAMETERS
def test_unbound_function_local_shadows_its_type_parameter(tmp_path: Path) -> None:
    source = "\n".join(f"""def {name}[T](flag):
    {binding}
        T = 7
    if flag:
        a = T + 1
    else:
        a = 5
    b = a * 2
    c = b - 3
    return c
""" for name, binding in (("first", "if flag:"), ("second", "for _ in range(int(flag)):")))
    source += "print(first(False), second(False), first(True), second(True))\n"
    _assert_preserved(source, "7 7 13 13\n", tmp_path)

"""A class passed to a helper is ``type[C]``, not the signature of its constructor.

A checker shows a reference to a class as its constructor's signature, the same
shape it uses for a function. Written down, that says the parameter takes
something callable, and the class is then rejected where a ``type[T]`` is
wanted -- by ``isinstance``, or by a constructor-typed parameter.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import textwrap

import pytest

from towel.type_inference import MypyInferrer
from towel.unification.annotations import class_object_revealed
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


@pytest.mark.parametrize(
    "expression, revealed, expected",
    [
        # The probed name is the class's own, so the constructed type is it.
        ("Klass", "def (name: str) -> m.Klass", "type[m.Klass]"),
        ("nodes.reference", "def () -> docutils.nodes.reference", "type[docutils.nodes.reference]"),
        # A function: its name is not the name of what it returns.
        ("make_thing", "def () -> m.Thing", "def () -> m.Thing"),
        ("build", "def (a: int) -> builtins.str", "def (a: int) -> builtins.str"),
        # An alias hides the class's name, so it is left as the checker put it.
        ("alias", "def (name: str) -> m.Klass", "def (name: str) -> m.Klass"),
        # Not a constructor display at all.
        ("Klass", "type[m.Klass]", "type[m.Klass]"),
        ("value", "builtins.int", "builtins.int"),
        # A compound return type is never rewritten.
        ("Klass", "def () -> m.Klass | None", "def () -> m.Klass | None"),
        ("Klass", "def () -> builtins.list[m.Klass]", "def () -> builtins.list[m.Klass]"),
        # Not a plain dotted expression.
        ("kinds[0]", "def () -> m.Klass", "def () -> m.Klass"),
        # A thunk returning the class is a thunk returning its type (rich's markdown).
        ("lambda: Klass", "def () -> def (name: str) -> m.Klass", "def () -> type[m.Klass]"),
        ("lambda: make_thing", "def () -> def () -> m.Thing", "def () -> def () -> m.Thing"),
        ("lambda: Klass()", "def () -> m.Klass", "def () -> m.Klass"),
        # A named tuple's constructor, as mypy 1.14 writes it, makes the class.
        (
            "Span",
            "def (start: builtins.int) -> tuple[builtins.int, fallback=m.Span]",
            "type[m.Span]",
        ),
        # A class whose __init__ is overloaded: every signature makes it.
        (
            "Box",
            "Overload(def (x: builtins.int) -> m.Box, def (x: builtins.str) -> m.Box)",
            "type[m.Box]",
        ),
        (
            "Box",
            "Overload(def (x: builtins.int) -> m.Box, def (x: builtins.str) -> m.Other)",
            "Overload(def (x: builtins.int) -> m.Box, def (x: builtins.str) -> m.Other)",
        ),
    ],
)
def test_only_a_reference_to_the_class_itself_is_respelled(
    expression: str, revealed: str, expected: str
) -> None:
    assert class_object_revealed(expression, revealed) == expected


@requires_mypy
def test_a_class_argument_reaches_isinstance_with_its_type(tmp_path: Path) -> None:
    """The extracted guard takes the class as a parameter, which must stay a type."""
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
        class Alpha:
            def __init__(self, tag: str) -> None:
                self.tag = tag


        class Beta:
            def __init__(self, tag: str) -> None:
                self.tag = tag


        def describe_alpha(value: object) -> str:
            if not isinstance(value, Alpha):
                return "no"
            joined = "alpha" + str(value)
            return joined.strip().upper()


        def describe_beta(value: object) -> str:
            if not isinstance(value, Beta):
                return "no"
            joined = "beta" + str(value)
            return joined.strip().upper()
        """).lstrip())
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        proposals = engine.analyze_file(str(path))
        if not proposals:
            pytest.skip("no extraction offered for this shape")
        result = engine.apply_refactoring(str(path), proposals[0])
        helper = next(
            node
            for node in ast.walk(ast.parse(result))
            if isinstance(node, ast.FunctionDef) and "extracted" in node.name
        )
        # A string spells the same type; this project declares no Python, so
        # a union of classes is quoted for the oldest one.
        written = [
            (
                argument.annotation.value
                if isinstance(argument.annotation, ast.Constant)
                and isinstance(argument.annotation.value, str)
                else ast.unparse(argument.annotation)
            )
            for argument in helper.args.posonlyargs + helper.args.args
            if argument.annotation is not None
        ]
        assert any(
            spelling.startswith("type[") for spelling in written
        ), f"the class parameter kept a constructor type: {written}"
    finally:
        oracle.close()

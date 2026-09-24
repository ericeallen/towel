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

"""A checker's spelling of a type is read as the annotation it means, in both rungs.

The texts are what the checkers print, measured from mypy 2.3.1, mypy 1.14.1
(which rich's lock file pins) and pyright: a callable returning ``None``
without an arrow, one returning another, generic callables, named tuples and
their constructors, ``Union``/``Optional`` beside ``|``, and literals whose
text holds ``<``. Each case names the corpus decline it came from
(packaging, rich, mistune).
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import textwrap

import pytest

from towel.type_inference import CheckSuccess, MypyInferrer, RevealRequest
from towel.unification.annotations import (
    annotation_from_revealed,
    class_object_revealed,
    typing_imports_needed,
)
from towel.unification.type_bindings import TypeResolver, render_type

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

CLASSES = """\
from typing import NamedTuple


class Style:
    pass


class Span(NamedTuple):
    start: int
    style: str | Style


def site(span: Span) -> None:
    pass
"""


def written(revealed: str, host: str = "") -> str | None:
    annotation = annotation_from_revealed(revealed, ast.parse(host), True)
    return None if annotation is None else ast.unparse(annotation)


def unquoted(annotation: ast.expr | None) -> str:
    """The type an annotation spells, whether or not it is written as a string."""
    assert annotation is not None
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value
    return ast.unparse(annotation)


def resolved(revealed: str, source: str = CLASSES) -> str | None:
    """The generic rung's reading, in a module whose own classes are ``probe.*``."""
    resolver = TypeResolver(
        source,
        "/project/probe.py",
        len(source.splitlines()),
        source,
        "/project/probe.py",
        module_names={"/project/probe.py": "probe"}.get,
    )
    term = resolver.resolve_revealed(revealed)
    return None if term is None else ast.unparse(render_type(term))


@pytest.mark.parametrize(
    "revealed, expected",
    [
        # rich R5: mypy leaves out ``-> None``, in 2.x and in 1.14.
        ("def (x: int)", "Callable[[int], None]"),
        ("def (builtins.int)", "Callable[[int], None]"),
        ("def ()", "Callable[[], None]"),
        # packaging K and _parser: a function returning a function.
        ("def () -> def (key: str) -> int", "Callable[[], Callable[[str], int]]"),
        (
            "() -> ((key: str) -> ((int) -> str))",
            "Callable[[], Callable[[str], Callable[[int], str]]]",
        ),
        # packaging K: a generic callable keeps its binders' names, for the checker to
        # resolve where the helper is (written as a string: the host binds no T).
        ("def [T] (x: T) -> T", "'Callable[[T], T]'"),
        # rich R3, R4, R8: a named tuple is its class; its constructor makes the class.
        ("tuple[builtins.int, Union[builtins.str, probe.Style], fallback=probe.Span]", "Span"),
        ("def (tuple[int, str | probe.Style, fallback=probe.Span])", "Callable[[Span], None]"),
        ("TypedDict(probe.Span, {'name': str})", "Span"),
        ("TypedDict('probe.Span', {'name': builtins.str})", "Span"),
        # mypy 1.14 writes Union and Optional, and a callable member bare inside them.
        ("Union[builtins.str, None]", "Union[str, None]"),
        ("Union[def () -> builtins.int, None]", "Union[Callable[[], int], None]"),
        ("(def () -> int) | None", "Callable[[], int] | None"),
        # mistune M4: text inside a literal is not notation, and only an inferred literal widens.
        ("Literal['<a href=\"']?", "str"),
        ("Literal[True]?", "bool"),
        ("Literal['r'] | Literal['w']", "Literal['r'] | Literal['w']"),
        ("Literal[-1]", "Literal[-1]"),
        # Anything with no annotation.
        ("<nothing>", None),
        ("Never", None),
        ("list[Never]", None),
        ("Any", None),
        ("Overload(def (x: int) -> int, def (x: str) -> str)", None),
    ],
)
def test_the_ordinary_rung_writes_what_the_checker_spells(revealed: str, expected: str) -> None:
    host = "from .probe import Span, Style\n"
    assert written(revealed, host) == expected


@pytest.mark.parametrize(
    "revealed, expected",
    [
        ("def (a: int, *, b: str =) -> int", "Callable[..., int]"),
        ("def (*args: int, **kwargs: str)", "Callable[..., None]"),
        ('(a: int, *, b: str = "") -> int', "Callable[..., int]"),
        ("def [B <: int] (x: B) -> int", "Callable[..., int]"),
    ],
)
def test_the_ordinary_rung_keeps_the_result_of_a_callable_no_list_states(
    revealed: str, expected: str
) -> None:
    assert written(revealed) == expected


def test_union_optional_and_literal_are_imported_where_they_are_written() -> None:
    host = ast.parse("import os\n")
    for revealed, name in (
        ("Union[builtins.int, builtins.str]", "Union"),
        ("Literal['r']", "Literal"),
    ):
        annotation = annotation_from_revealed(revealed, host, True)
        assert annotation is not None
        helper = ast.parse("def helper(value): pass").body[0]
        assert isinstance(helper, ast.FunctionDef)
        helper.args.args[0].annotation = annotation
        assert typing_imports_needed(helper, host) == (("typing", name),)


@pytest.mark.parametrize(
    "revealed, expected",
    [
        # mypy 2.3 and 1.14 spell the same named tuple constructor.
        (
            "def (start: int, style: str | probe.Style) -> tuple[int, str | probe.Style,"
            " fallback=probe.Span]",
            "Callable[[int, str | Style], Span]",
        ),
        (
            "def (start: builtins.int, style: Union[builtins.str, probe.Style]) -> "
            "tuple[builtins.int, Union[builtins.str, probe.Style], fallback=probe.Span]",
            "Callable[[int, str | Style], Span]",
        ),
        ("def (tuple[int, str | probe.Style, fallback=probe.Span])", "Callable[[Span], None]"),
        ("def (builtins.int)", "Callable[[int], None]"),
        ("def () -> def (key: str) -> probe.Style", "Callable[[], Callable[[str], Style]]"),
        ("Union[builtins.str, probe.Style, None]", "str | Style | None"),
        ("Optional[probe.Style]", "Style | None"),
        ("typing.Iterator[tuple[int, str, fallback=probe.Span]]", "Iterator[Span]"),
        ("dict[str, Any] | None", "dict[str, Any] | None"),
        ("Literal['<a href=\"']?", "str"),
        ("Literal['r'] | Literal['w']", "Literal['r'] | Literal['w']"),
        ("Any", None),
    ],
)
def test_the_generic_rung_resolves_what_the_checker_spells(revealed: str, expected: str) -> None:
    assert resolved(revealed) == expected


def test_a_class_object_passed_as_an_argument_is_its_type_even_for_a_named_tuple() -> None:
    """rich R3/R4: ``Span`` passed as a value is ``type[Span]``, not its constructor."""
    constructor = (
        "def (start: builtins.int, style: Union[builtins.str, probe.Style]) -> "
        "tuple[builtins.int, Union[builtins.str, probe.Style], fallback=probe.Span]"
    )
    assert class_object_revealed("Span", constructor) == "type[probe.Span]"
    assert class_object_revealed("make_span", constructor) == constructor
    assert class_object_revealed("Style", "def () -> probe.Style") == "type[probe.Style]"


PARSER = """\
class Tokenizer:
    def consume(self, name: str) -> None:
        pass


def parse_extras(tokenizer: Tokenizer) -> list[str]:
    return []


def use(tokenizer: Tokenizer) -> None:
    pass
"""


@requires_mypy
def test_a_thunk_returning_a_function_is_spelled_as_mypy_reveals_it(tmp_path: Path) -> None:
    """packaging _parser: ``lambda: parse_extras`` takes nothing and returns a function.

    The spelling used to be ``Callable[[Tokenizer], list[str]]``, the inner
    function's type, which no lambda here is. The annotation must accept the
    lambda, and mypy must read the annotation back as the type it revealed.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    path = tmp_path / "program.py"
    path.write_text(PARSER)
    oracle = MypyInferrer()
    try:
        line = len(PARSER.splitlines())  # ``pass`` in ``use``
        revealed = oracle.reveal(
            [RevealRequest(str(path), PARSER, line, "    ", ("lambda: parse_extras",))]
        )[(str(path), line, 0)]
        spelled = unquoted(annotation_from_revealed(revealed, ast.parse(PARSER), True))
        assert spelled == "Callable[[], Callable[[Tokenizer], list[str]]]", revealed
        probe = "from collections.abc import Callable\n" + PARSER + textwrap.dedent(f"""

                def check(tokenizer: Tokenizer) -> list[str]:
                    thunk: {spelled} = lambda: parse_extras
                    return thunk()(tokenizer)
                """)
        assert oracle.check(str(path), probe) == CheckSuccess(), probe
        returning = probe.splitlines().index("    return thunk()(tokenizer)") + 1
        back = oracle.reveal([RevealRequest(str(path), probe, returning, "    ", ("thunk",))])[
            (str(path), returning, 0)
        ]
        assert unquoted(annotation_from_revealed(back, ast.parse(probe), True)) == spelled, back
    finally:
        oracle.close()

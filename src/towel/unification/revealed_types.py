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

"""Read a type checker's spelling of a type as the annotation expression it means.

``reveal_type`` answers in the checker's notation, which is not Python's.
mypy writes a callable ``def (x: int) -> str``, leaves out the result when it
is ``None`` (``def (builtins.int)``), quantifies a generic function
``def [T] (x: T) -> T``, spells a named tuple by its fields with the class as
a fallback (``tuple[int, str, fallback=pkg.Span]``), a typed dict as
``TypedDict(pkg.Movie, {...})``, and marks a literal it inferred, rather than
one the program declared, with ``?`` (``Literal['a']?``). Before 2.0 it
wrote ``builtins.`` before every builtin and ``Union[A, B]`` for a union,
with a callable member written bare inside it; since, a callable member is
parenthesized, ``(def () -> int) | None``. pyright writes a callable
``(x: int) -> str``, parenthesizes one returned by another,
``() -> ((key: str) -> Error)``, marks positional-only parameters with ``/``
and writes default values out. These are the spellings measured from mypy
1.14 and 2.3 and from pyright, and :func:`parse_revealed` reads all of them.

What it returns is the expression an annotation would be, so both annotation
rungs resolve it the way they resolve what a program declares. A callable
becomes ``Callable[[P1, P2], R]``; one no such list can state -- ``*args``,
keyword-only parameters, defaults, bounded or constrained type parameters,
an overload -- becomes ``Callable[..., R]`` or, where the caller asks, an
:class:`OpaqueType`, which only its spelling identifies and no annotation
can write. A generic callable's own type parameters are left as names:
the callable may be instantiated at any type, and in particular at a type
variable of the same name where the caller's scope has one, so ``def [T]
(x: T) -> T`` reads as ``Callable[[T], T]``. A named tuple and a typed dict
read as their class, and an inferred literal as the type its value belongs
to, ``str`` for ``Literal['a']?``; a declared literal stays a literal.
Anything else -- ``<nothing>``, a partial type, an unpacked tuple, an
old-style inferred marker ``int*`` -- makes the whole reading ``None``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import re
from typing import List, Literal, Optional, Sequence, Tuple

__all__ = ["OpaqueType", "UnwritableCallable", "parse_revealed"]


class OpaqueType(ast.expr):
    """A type the checker wrote that no annotation can state exactly, known only by that spelling.

    It is a node of the expression :func:`parse_revealed` returns, and no
    annotation: ``ast.unparse`` and ``compile`` reject it, so it cannot reach
    a helper's source by accident.
    """

    _fields = ("spelling",)
    spelling: str

    def __init__(self, spelling: str) -> None:
        super().__init__()
        self.spelling = spelling


UnwritableCallable = Literal["ellipsis", "opaque"]
"""How a callable no parameter list can state is read: ``Callable[..., R]``, or an :class:`OpaqueType`."""


_TOKEN = re.compile(
    r"""
    (?P<space>\s+)
    |(?P<string>[bB]?(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"))
    |(?P<number>\d+)
    |(?P<name>[A-Za-z_][A-Za-z0-9_]*)
    |(?P<op>->|\*\*|<:|\.\.\.|[][(),|?*=:./<>{}@-])
    """,
    re.VERBOSE,
)

_OPENING = {"(": ")", "[": "]", "{": "}"}


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    start: int
    end: int


class _Unreadable(Exception):
    """The spelling uses notation this reader does not know."""


def _tokens(text: str) -> Tuple[_Token, ...]:
    tokens: List[_Token] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None or match.lastgroup is None:
            raise _Unreadable(text[position:])
        if match.lastgroup != "space":
            tokens.append(_Token(match.lastgroup, match.group(), match.start(), match.end()))
        position = match.end()
    return tuple(tokens)


def _dotted(name: str) -> ast.expr:
    """``a.b.C`` as the expression that names it."""
    parts = name.split(".")
    node: ast.expr = ast.Name(id=parts[0], ctx=ast.Load())
    for part in parts[1:]:
        node = ast.Attribute(value=node, attr=part, ctx=ast.Load())
    return node


def _union(members: Sequence[ast.expr]) -> ast.expr:
    result = members[0]
    for member in members[1:]:
        result = ast.BinOp(left=result, op=ast.BitOr(), right=member)
    return result


def _subscript(head: ast.expr, arguments: Sequence[ast.expr]) -> ast.expr:
    argument = (
        arguments[0] if len(arguments) == 1 else ast.Tuple(elts=list(arguments), ctx=ast.Load())
    )
    return ast.Subscript(value=head, slice=argument, ctx=ast.Load())


def _widened(value: ast.expr) -> ast.expr:
    """The type an inferred literal's value belongs to: ``builtins.str`` for ``'a'``."""
    if isinstance(value, ast.Constant):
        if value.value is None:
            return value
        if type(value.value) in (bool, int, str, bytes):
            return _dotted(f"builtins.{type(value.value).__name__}")
    if isinstance(value, ast.UnaryOp):
        return _dotted("builtins.int")
    if isinstance(value, ast.Attribute):
        return value.value  # An enum member belongs to its enum.
    raise _Unreadable(ast.dump(value))


class _Reader:
    """Recursive descent over one spelling; each method consumes what it reads."""

    def __init__(self, text: str, callable_name: str, unwritable: UnwritableCallable) -> None:
        self.text = text
        self.tokens = _tokens(text)
        self.index = 0
        self.callable_name = callable_name
        self.unwritable: UnwritableCallable = unwritable

    # -- tokens ---------------------------------------------------------------

    def peek(self, offset: int = 0) -> Optional[str]:
        position = self.index + offset
        return self.tokens[position].text if position < len(self.tokens) else None

    def kind(self, offset: int = 0) -> Optional[str]:
        position = self.index + offset
        return self.tokens[position].kind if position < len(self.tokens) else None

    def take(self, expected: Optional[str] = None) -> _Token:
        if self.index >= len(self.tokens):
            raise _Unreadable("unexpected end")
        token = self.tokens[self.index]
        if expected is not None and token.text != expected:
            raise _Unreadable(f"expected {expected!r}, found {token.text!r}")
        self.index += 1
        return token

    def name(self) -> str:
        token = self.take()
        if token.kind != "name":
            raise _Unreadable(token.text)
        return token.text

    def closing(self, opening: int) -> int:
        """The index of the token that closes the bracket at ``opening``."""
        stack: List[str] = []
        for position in range(opening, len(self.tokens)):
            text = self.tokens[position].text
            if text in _OPENING:
                stack.append(_OPENING[text])
            elif text in _OPENING.values():
                if not stack or stack.pop() != text:
                    raise _Unreadable(text)
                if not stack:
                    return position
        raise _Unreadable("unbalanced")

    def spelling_since(self, start: int) -> str:
        """The source text of the tokens from ``start`` up to the current position."""
        first = self.tokens[start].start
        last = self.tokens[self.index - 1].end
        return " ".join(self.text[first:last].split())

    # -- grammar --------------------------------------------------------------

    def whole(self) -> ast.expr:
        node = self.union()
        if self.index != len(self.tokens):
            raise _Unreadable(self.text[self.tokens[self.index].start :])
        return node

    def union(self) -> ast.expr:
        members = [self.single()]
        while self.peek() == "|":
            self.take()
            members.append(self.single())
        return _union(members)

    def single(self) -> ast.expr:
        token = self.peek()
        if token == "def":
            return self.mypy_callable()
        if token == "(":
            return self.parenthesized()
        if token == "[":
            return self.bracketed()
        if token == "...":
            self.take()
            return ast.Constant(value=Ellipsis)
        if self.kind() != "name":
            raise _Unreadable(str(token))
        if token == "None":
            self.take()
            return ast.Constant(value=None)
        if token == "Overload" and self.peek(1) in ("(", "["):
            return self.overload()
        if token == "TypedDict" and self.peek(1) == "(":
            return self.typed_dict()
        return self.named()

    def named(self) -> ast.expr:
        parts = [self.name()]
        while self.peek() == "." and self.kind(1) == "name":
            self.take()
            parts.append(self.name())
        head = _dotted(".".join(parts))
        if self.peek() != "[":
            return head
        self.take("[")
        if parts[-1] == "Literal":
            values = [self.literal_value()]
            while self.peek() == ",":
                self.take()
                values.append(self.literal_value())
            self.take("]")
            if self.peek() == "?":
                self.take()
                widened: List[ast.expr] = []
                for value in values:
                    kind = _widened(value)
                    if ast.dump(kind) not in {ast.dump(seen) for seen in widened}:
                        widened.append(kind)
                return _union(widened)
            return _subscript(head, values)
        arguments: List[ast.expr] = []
        fallback: Optional[ast.expr] = None
        while True:
            if self.peek() == "fallback" and self.peek(1) == "=":
                self.take()
                self.take()
                fallback = self.union()
            elif self.peek() == "(" and self.peek(1) == ")":
                self.take()
                self.take()
                arguments.append(ast.Tuple(elts=[], ctx=ast.Load()))
            else:
                arguments.append(self.union())
            if self.peek() != ",":
                break
            self.take()
        self.take("]")
        if fallback is not None:
            # A named tuple, written as its fields: the class is the type.
            if parts[-1] not in ("tuple", "Tuple"):
                raise _Unreadable("fallback outside a tuple")
            return fallback
        return _subscript(head, arguments)

    def literal_value(self) -> ast.expr:
        token = self.take()
        if token.kind == "string":
            return ast.Constant(value=ast.literal_eval(token.text))
        if token.kind == "number":
            return ast.Constant(value=int(token.text))
        if token.text == "-" and self.kind() == "number":
            return ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=int(self.take().text)))
        if token.text in ("True", "False", "None"):
            return ast.Constant(value={"True": True, "False": False, "None": None}[token.text])
        if token.kind == "name":
            parts = [token.text]
            while self.peek() == "." and self.kind(1) == "name":
                self.take()
                parts.append(self.name())
            if len(parts) > 1:
                return _dotted(".".join(parts))  # An enum member.
        raise _Unreadable(token.text)

    def mypy_callable(self) -> ast.expr:
        start = self.index
        self.take("def")
        writable = True
        if self.peek() == "[":
            writable = self.binders()
        self.take("(")
        parameters, positional = self.parameters()
        self.take(")")
        result: ast.expr = ast.Constant(value=None)
        if self.peek() == "->":
            self.take()
            result = self.union()
        return self.callable_type(parameters if writable and positional else None, result, start)

    def binders(self) -> bool:
        """Read ``[T, U <: int, V in (int, str)]``; whether every binder is unrestricted."""
        self.take("[")
        unrestricted = True
        while True:
            if self.peek() in ("*", "**"):
                self.take()
                unrestricted = False
            self.name()
            if self.peek() == "<:":
                self.take()
                self.union()
                unrestricted = False
            elif self.peek() == "in":
                self.take()
                self.take("(")
                self.union()
                while self.peek() == ",":
                    self.take()
                    self.union()
                self.take(")")
                unrestricted = False
            if self.peek() == "=":
                self.take()
                self.union()
                unrestricted = False
            if self.peek() != ",":
                break
            self.take()
        self.take("]")
        return unrestricted

    def parameters(self) -> Tuple[List[ast.expr], bool]:
        """A callable's parameter types, and whether a positional list states them all.

        A bare entry is the type of an anonymous parameter, in both checkers'
        styles: pyright writes ``Callable[[int], str]`` as ``(int) -> str``.
        pyright writes a parameter with no declared type as its bare name too,
        so such a name reads as a type, and names nothing the rungs resolve.
        """
        types: List[ast.expr] = []
        positional = True
        if self.peek() == ")":
            return types, positional
        while True:
            if self.peek() in ("*", "/") and self.peek(1) in (",", ")"):
                # A marker: keyword-only parameters follow ``*``; ``/`` ends
                # the positional-only ones, which a positional list states.
                positional = positional and self.take().text == "/"
            else:
                if self.peek() in ("*", "**"):
                    self.take()
                    positional = False
                if self.kind() == "name" and self.peek(1) == ":":
                    self.take()
                    self.take(":")
                types.append(self.union())
                if self.peek() == "=":
                    self.take()
                    positional = False
                    self.skip_default()
            if self.peek() != ",":
                break
            self.take()
        return types, positional

    def skip_default(self) -> None:
        """Pass over a default value; mypy writes none (``x: int =``), pyright the value."""
        while self.peek() not in (",", ")", None):
            if self.peek() in _OPENING:
                self.index = self.closing(self.index) + 1
            else:
                self.take()

    def callable_type(
        self, parameters: Optional[List[ast.expr]], result: ast.expr, start: int
    ) -> ast.expr:
        head = _dotted(self.callable_name)
        if parameters is not None:
            return _subscript(head, [ast.List(elts=parameters, ctx=ast.Load()), result])
        if self.unwritable == "opaque":
            return OpaqueType(self.spelling_since(start))
        return _subscript(head, [ast.Constant(value=Ellipsis), result])

    def parenthesized(self) -> ast.expr:
        """pyright's ``(x: int) -> str``, or a type in grouping parentheses."""
        close = self.closing(self.index)
        start = self.index
        if close + 1 < len(self.tokens) and self.tokens[close + 1].text == "->":
            self.take("(")
            parameters, positional = self.parameters()
            self.take(")")
            self.take("->")
            result = self.union()
            return self.callable_type(parameters if positional else None, result, start)
        self.take("(")
        inner = self.union()
        self.take(")")
        return inner

    def bracketed(self) -> ast.expr:
        """A parameter list written out, ``[int, str]``."""
        self.take("[")
        items: List[ast.expr] = []
        while self.peek() != "]":
            items.append(self.union())
            if self.peek() != ",":
                break
            self.take()
        self.take("]")
        return ast.List(elts=items, ctx=ast.Load())

    def overload(self) -> ast.expr:
        start = self.index
        self.take("Overload")
        self.index = self.closing(self.index) + 1
        if self.unwritable != "opaque":
            raise _Unreadable("overload")
        return OpaqueType(self.spelling_since(start))

    def typed_dict(self) -> ast.expr:
        """``TypedDict(pkg.Movie, {...})``, quoted before mypy 2.0: the class is the type."""
        self.take("TypedDict")
        close = self.closing(self.index)
        self.take("(")
        if self.kind() == "string":
            name = ast.literal_eval(self.take().text)
            if not isinstance(name, str) or not all(
                part.isidentifier() for part in name.split(".")
            ):
                raise _Unreadable(repr(name))
            node = _dotted(name)
        elif self.kind() == "name":
            node = self.named()
        else:
            raise _Unreadable("anonymous TypedDict")
        if self.peek() != ",":
            raise _Unreadable("TypedDict without fields")
        self.index = close + 1
        return node


def parse_revealed(
    text: str,
    *,
    callable_name: str = "Callable",
    unwritable: UnwritableCallable = "ellipsis",
) -> Optional[ast.expr]:
    """The annotation expression a checker's ``text`` spells, or None when it spells none.

    ``callable_name`` is how the result names ``Callable``: the bare name an
    annotation imports, or ``collections.abc.Callable`` for a reader that
    resolves names by what they are rather than how a module spells them.
    """
    try:
        return _Reader(text.strip(), callable_name, unwritable).whole()
    except (_Unreadable, ValueError, SyntaxError):
        return None

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

"""A syntax tree's structure as one string, the same for every builder and every Python.

Towel compares trees it built with trees the parser built: a helper with its
own rendering parsed back, a reduced helper body with the block it replaces,
an annotation it wrote with one a site declares. ``ast.dump`` does not give
those comparisons one answer across the supported Pythons.

Before 3.13 a node constructor sets only the fields it is given. A
``FunctionDef`` built without ``type_params`` has no such attribute, and its
dump leaves the field out; the parser sets ``type_params=[]``, and 3.12's
dump writes it. A ``Name`` built without ``ctx`` dumps without one, a parsed
one with ``ctx=Load()``; a ``Call`` built without ``keywords`` likewise. From
3.13 a constructor fills in what it is not given (an empty list, ``None``,
``Load()`` for a context) and the dump leaves out every empty list and
``None``, so both trees dump alike. On 3.12 every helper Towel built was
therefore refused as not rendering to itself, and every other comparison of
a built tree with a parsed one could fail the same way.

``canonical_dump`` reads a missing field as a 3.13 constructor fills it in
and writes what 3.13's ``ast.dump`` writes, without positions: a field
holding ``None`` or an empty list is left out, except the ``value`` of a
``Constant`` or ``MatchSingleton``, where ``None`` is the value. On 3.13 it
equals ``ast.dump`` for every tree the parser or a constructor builds, and on
3.11 and 3.12 two trees spell alike exactly when 3.13 would dump them alike.
It therefore distinguishes everything that dump distinguishes; it only stops
"not given" differing from "given empty".

One difference from ``ast.dump``: an int too wide for ``repr``, which
refuses more decimal digits than ``sys.get_int_max_str_digits()`` allows, is
spelled in hexadecimal as ``int(0x...)``, where ``ast.dump`` raises
``ValueError``. ``repr`` never writes that for a constant, so the spelling
cannot equal another value's.

Every comparison, hash and cache key Towel builds from a tree's structure
goes through this function; ``tests/test_canonical_ast.py`` fails on any
``ast.dump`` call elsewhere in ``src/towel``.
"""

from __future__ import annotations

import ast
from typing import Final, List, Tuple, Type

_ABSENT: Final = object()
"""What ``getattr`` returns for a field the constructor was not given (before 3.13)."""

_CONTEXT_FIELD: Final = "ctx"
"""The one field name every supported grammar gives an ``expr_context`` field.

A 3.13 constructor sets a missing context to ``Load()``; it is the only
missing field that does not read as ``None`` or an empty list.
"""

_MISSING_CONTEXT: Final = "Load()"

_NONE_IS_A_VALUE: Final[Tuple[Type[ast.AST], ...]] = (ast.Constant, ast.MatchSingleton)
"""Nodes whose ``value`` field holding ``None`` is the constant ``None``, not an absence."""


def canonical_dump(node: ast.AST) -> str:
    """``node``'s structure as 3.13's ``ast.dump`` writes it, on every supported Python.

    Positions are never included. See the module docstring for how a
    field the constructor was not given is read.
    """
    return _spelled_node(node)


def _spelled(value: object) -> str:
    if isinstance(value, ast.AST):
        return _spelled_node(value)
    if isinstance(value, list):
        return "[" + ", ".join(_spelled(item) for item in value) + "]"
    return _spelled_constant(value)


def _spelled_node(node: ast.AST) -> str:
    none_is_a_value = isinstance(node, _NONE_IS_A_VALUE)
    fields: List[str] = []
    for name in node._fields:
        value = getattr(node, name, _ABSENT)
        if value is _ABSENT:
            if name == _CONTEXT_FIELD:
                fields.append(f"{name}={_MISSING_CONTEXT}")
            continue
        if value is None:
            if not (none_is_a_value and name == "value"):
                continue
        elif isinstance(value, list) and not value:
            continue
        fields.append(f"{name}={_spelled(value)}")
    return f"{type(node).__name__}({', '.join(fields)})"


def _spelled_constant(value: object) -> str:
    try:
        return repr(value)
    except ValueError:
        if isinstance(value, int):
            return f"int({value:#x})"
        raise

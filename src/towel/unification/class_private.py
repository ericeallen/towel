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

"""Class-private names: what a ``__name`` written in a class body is stored as.

A method helper is named ``__extracted_func_0`` so that no subclass, in the
project or outside it, can override it or collide with it (docs/DECISIONS.md,
"A method helper lives in the class that holds both duplicates"). Python
rewrites such an identifier wherever it is written inside a class body, so
``def __extracted_func_0`` in ``class A`` defines ``_A__extracted_func_0``
and ``self.__extracted_func_0()`` there calls it. Allocating a helper's name,
reading which names a project already claims, and renaming a helper all have
to agree with the compiler about that rewrite, so each applies it from here:

- only a name that starts with two underscores, does not end with two, and
  has no dot (an import's package path) is rewritten;
- the class's leading underscores are dropped, so ``__x`` in ``class _Foo``
  is ``_Foo__x``, and a class named only with underscores rewrites nothing;
- the innermost class whose body holds the identifier decides, at any depth
  of functions within that body: a method of a nested class mangles with
  the nested class, while a class statement's own name, decorators and
  bases belong to the scope around it;
- the rewrite reaches every identifier the compiler stores, loads or
  imports by name (``rewritten_identifiers``), and not the keywords of a
  call or of a class pattern, which the compiler passes on as written: in
  ``class A``, ``(lambda __p: 0)(__p=1)`` passes ``__p`` to a parameter
  stored as ``_A__p``, and raises.

A generic class (``class G[T](Base)``) compiles its type parameters and bases
in a scope of its own, where CPython 3.12 to 3.14 rewrite the parameters with
the class's name and the bases with none; that is modelled as observed, and
no name Towel writes or renames is spelled there in any case.
"""

from __future__ import annotations

import ast
from types import MappingProxyType
from typing import Dict, Iterator, List, Mapping, Optional, Tuple
from weakref import WeakKeyDictionary


def is_class_private(name: str) -> bool:
    """Whether ``name`` is rewritten when written inside a class body (``__x``, not ``__x__``)."""
    return name.startswith("__") and not name.endswith("__") and "." not in name


def mangling_prefix(class_name: str) -> Optional[str]:
    """What ``class_name`` puts before a private name: ``_Foo`` for ``Foo`` and ``_Foo``.

    None for a class named only with underscores, which rewrites nothing.
    """
    stripped = class_name.lstrip("_")
    return f"_{stripped}" if stripped else None


def mangled(name: str, class_name: Optional[str]) -> str:
    """The attribute ``name`` denotes when written in the body of ``class_name`` (None: no class)."""
    if class_name is None or not is_class_private(name):
        return name
    prefix = mangling_prefix(class_name)
    return name if prefix is None else prefix + name


_TYPE_PARAMETERS: Tuple[type, ...] = tuple(
    getattr(ast, kind) for kind in ("TypeVar", "ParamSpec", "TypeVarTuple") if hasattr(ast, kind)
)
"""PEP 695 type parameters (Python 3.12+), each of which binds its own name."""


def rewritten_identifiers(node: ast.AST) -> Iterator[str]:
    """The identifiers ``node`` itself spells that a class body's mangling would rewrite.

    Not those of its children: walk the tree to see them all. Every
    identifier the compiler turns into a symbol or a name it stores, loads
    or imports goes through ``_Py_Mangle`` (CPython's ``symtable.c``, and
    ``compiler_nameop`` and ``compiler_addop_name`` in ``compile.c``, 3.11
    to 3.14): a name read or bound; an attribute; a parameter of a function
    or lambda, positional, keyword-only, ``*`` or ``**``; the name a
    ``def`` or ``class`` statement binds; a ``global`` or ``nonlocal``
    declaration; an ``except ... as`` name; a ``match`` capture; a type
    parameter; an import's module, the member it takes and the name it
    binds. The keyword of a call and the keyword of a class pattern
    (``case Point(x=0)``) are passed on as written, and a dotted module
    name is left alone, so neither is yielded; whether a yielded name is
    private at all is ``is_class_private``'s to say.
    """
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Attribute):
        yield node.attr
    elif isinstance(node, ast.arg):
        yield node.arg
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        yield from node.names
    elif isinstance(node, ast.ExceptHandler):
        if node.name is not None:
            yield node.name
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
        if node.name is not None:
            yield node.name
    elif isinstance(node, ast.MatchMapping):
        if node.rest is not None:
            yield node.rest
    elif isinstance(node, _TYPE_PARAMETERS):
        # Read by attribute: Python 3.11's AST has no type-parameter classes.
        name: object = getattr(node, "name", None)
        if isinstance(name, str):
            yield name
    elif isinstance(node, ast.Import):
        for alias in node.names:
            # ``import a.b`` imports ``a.b`` and binds ``a``.
            yield alias.name
            yield alias.asname or alias.name.partition(".")[0]
    elif isinstance(node, ast.ImportFrom):
        if node.module is not None:
            yield node.module
        for alias in node.names:
            if alias.name != "*":
                yield alias.name
                yield alias.asname or alias.name


def mangling_classes(tree: ast.AST) -> Dict[ast.AST, Optional[ast.ClassDef]]:
    """Every node of ``tree`` with the class whose name rewrites the identifiers it spells.

    None for a node outside every class body.
    """
    owners: Dict[ast.AST, Optional[ast.ClassDef]] = {}
    pending: List[Tuple[ast.AST, Optional[ast.ClassDef]]] = [(tree, None)]
    while pending:
        node, owner = pending.pop()
        owners[node] = owner
        if isinstance(node, ast.ClassDef):
            type_params: List[ast.AST] = list(getattr(node, "type_params", None) or ())
            bases: List[ast.AST] = [*node.bases, *node.keywords]
            pending.extend((decorator, owner) for decorator in node.decorator_list)
            pending.extend((base, None if type_params else owner) for base in bases)
            pending.extend((parameter, node) for parameter in type_params)
            pending.extend((statement, node) for statement in node.body)
        else:
            pending.extend((child, owner) for child in ast.iter_child_nodes(node))
    return owners


_FUNCTION_CLASSES: "WeakKeyDictionary[ast.AST, Mapping[ast.AST, Optional[str]]]" = (
    WeakKeyDictionary()
)


def function_mangling_classes(tree: ast.AST) -> Mapping[ast.AST, Optional[str]]:
    """Each function and lambda of ``tree`` with the name of the class that mangles what its body spells.

    None for one outside every class body. Memoized per tree.
    """
    known = _FUNCTION_CLASSES.get(tree)
    if known is not None:
        return known
    classes: Dict[ast.AST, Optional[str]] = {
        node: None if owner is None else owner.name
        for node, owner in mangling_classes(tree).items()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    }
    frozen = MappingProxyType(classes)
    _FUNCTION_CLASSES[tree] = frozen
    return frozen


def class_qualnames(tree: ast.Module) -> Dict[ast.ClassDef, str]:
    """Every class reached through class bodies from the module's top level, by dotted qualname.

    A class-private helper is named by the class that stores it, and that
    class by its qualname in the module (``Outer.Inner``); a class defined in
    a function body has none that is stable, and is left out.
    """
    found: Dict[ast.ClassDef, str] = {}
    pending: List[Tuple[ast.stmt, str]] = [(statement, "") for statement in tree.body]
    while pending:
        node, prefix = pending.pop()
        if isinstance(node, ast.ClassDef):
            found[node] = prefix + node.name
            pending.extend((child, found[node] + ".") for child in node.body)
    return found

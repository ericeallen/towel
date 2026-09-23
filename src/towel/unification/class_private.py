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
  bases belong to the scope around it.

A generic class (``class G[T](Base)``) compiles its type parameters and bases
in a scope of its own, where CPython 3.12 to 3.14 rewrite the parameters with
the class's name and the bases with none; that is modelled as observed, and
no name Towel writes or renames is spelled there in any case.
"""

from __future__ import annotations

import ast
from typing import Dict, List, Optional, Tuple


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

# Copyright 2026 Eric Allen
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

"""The one lifetime relaxed mode still keeps: a local that holds a resource alive.

Relaxed mode assumes a program does not depend on when its objects are
finalized once it has stopped using them, so a helper's return releases the
locals of the block it took (docs/DECISIONS.md, "Relaxed mode is the default;
strict mode keeps exact lifetimes"). That assumption fails for an object
whose finalizer destroys something the program can still reach another way::

    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "report.txt")
    # ... end of the block ...
    os.listdir(tmp.name)

The program has not stopped using the directory; it has only stopped naming
the object that owns it. The language guarantees that an object lives while
it is reachable, and the original kept ``tmp`` reachable until its function
returned. So a block that binds a local directly to a call of one of the
constructors below returns that local, and its caller binds it under its own
name, exactly as long-lived as before.

A weak reference is the other way a program reaches an object it no longer
names::

    target = Target()
    watcher = weakref.ref(target)
    # ... end of the block ...
    return watcher() is not None

The original answers True, because ``target`` lives until the function
returns; a helper that returned only ``watcher`` would make it False. That
is a different value, not a finalizer running at another time, so a local
the block hands directly to one of the weak-reference constructors below is
returned and kept by the caller as well.

The guard reads syntax and nothing else. It sees a direct call whose callee
the module's imports resolve to a listed name; a resource reached through a
wrapper, a factory or an attribute, and an object registered in a
``WeakSet`` or a weak dictionary, are not seen, and need ``--strict`` or a
``with`` statement.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterator, Mapping, Optional, Sequence, Set
from weakref import WeakKeyDictionary

from .module_bindings import dotted_name, import_origin
from .statement_facts import imported_binding_name, memoized_per_node

# The criterion: a standard-library constructor whose result, when it is
# finalized, destroys or shuts down something the program can still reach by a
# route other than the object itself: a path on disk, a file descriptor, a
# pool's workers, a Tk image name, a mapped region. A lock, a socket or an
# ordinary open file does not qualify: closing one takes nothing from code
# that does not hold it. Each entry is the name the documentation gives the
# constructor, plus the defining module's name where the two differ. The list
# is closed on purpose; an addition is a decision, not a convenience.
RESOURCE_CONSTRUCTORS: FrozenSet[str] = frozenset(
    {
        # Deleted from disk on finalization; the path outlives the object.
        "tempfile.TemporaryDirectory",
        "tempfile.NamedTemporaryFile",
        "tempfile.TemporaryFile",
        "tempfile.SpooledTemporaryFile",
        # Workers are terminated on finalization; results and iterators the
        # pool handed out outlive the object.
        "multiprocessing.Pool",
        "multiprocessing.pool.Pool",
        "multiprocessing.pool.ThreadPool",
        # The Tk image is deleted on finalization; widgets refer to it by name.
        "tkinter.PhotoImage",
        "tkinter.BitmapImage",
        # Unmapped on finalization; exported buffers and the file are not.
        "mmap.mmap",
        # Named by the decision. The module's registry keeps a finalizer alive,
        # so returning it costs a name and protects nothing; it stays listed
        # until the decision's list is revised.
        "weakref.finalize",
    }
)

# Constructors that watch their first argument without keeping it alive. The
# object watched is the one whose early release the program could see: a
# dead reference, a proxy that raises, a callback that has already run.
WEAK_REFERENCE_CONSTRUCTORS: FrozenSet[str] = frozenset(
    {
        "weakref.ref",
        "weakref.proxy",
        "weakref.WeakMethod",
        "weakref.finalize",
    }
)

_MAX_ALIAS_DEPTH = 8


@dataclass(frozen=True)
class _ModuleSpellings:
    """Every dotted name a spelling could copy, in any scope of one module.

    Scopes and paths are not told apart: a name bound by an import or by a
    plain ``alias = a.b`` anywhere in the module may denote that origin
    wherever it is read. That over-approximates, and the guard's only cost
    for a false alarm is one more returned name.
    """

    origins: Mapping[str, FrozenSet[str]]
    star_modules: FrozenSet[str]


def _module_spellings(module: ast.AST) -> _ModuleSpellings:
    origins: Dict[str, Set[str]] = {}
    stars: Set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = imported_binding_name(alias)
                if name is None:
                    if isinstance(node, ast.ImportFrom) and not node.level and node.module:
                        stars.add(node.module)
                    continue
                origin = import_origin(node, alias)
                if origin is not None:
                    origins.setdefault(name, set()).add(origin)
        elif isinstance(node, ast.Assign):
            copied = dotted_name(node.value)
            if copied is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        origins.setdefault(target.id, set()).add(copied)
    return _ModuleSpellings(
        {name: frozenset(found) for name, found in origins.items()}, frozenset(stars)
    )


_SPELLINGS: "WeakKeyDictionary[ast.AST, _ModuleSpellings]" = WeakKeyDictionary()


def _possible_origins(spellings: _ModuleSpellings, dotted: str, depth: int = 0) -> Iterator[str]:
    """Every absolute name ``dotted`` could denote, itself included."""
    yield dotted
    head, _, rest = dotted.partition(".")
    if not rest:
        for module in spellings.star_modules:
            yield f"{module}.{head}"
    if depth >= _MAX_ALIAS_DEPTH:
        return
    for origin in spellings.origins.get(head, ()):
        target = f"{origin}.{rest}" if rest else origin
        if target != dotted:
            yield from _possible_origins(spellings, target, depth + 1)


def _calls_one_of(
    value: Optional[ast.AST], listed: FrozenSet[str], spellings: _ModuleSpellings
) -> bool:
    if not isinstance(value, ast.Call):
        return False
    callee = dotted_name(value.func)
    return callee is not None and any(
        origin in listed for origin in _possible_origins(spellings, callee)
    )


def _constructs_resource(value: Optional[ast.expr], spellings: _ModuleSpellings) -> bool:
    return _calls_one_of(value, RESOURCE_CONSTRUCTORS, spellings)


def _weakly_referenced(node: ast.AST, spellings: _ModuleSpellings) -> FrozenSet[str]:
    """The names read in the first argument of a weak-reference constructor's call."""
    if not _calls_one_of(node, WEAK_REFERENCE_CONSTRUCTORS, spellings):
        return frozenset()
    assert isinstance(node, ast.Call)
    if not node.args:
        return frozenset()
    return frozenset(
        name.id
        for name in ast.walk(node.args[0])
        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)
    )


def _own_scope(node: ast.AST) -> Iterator[ast.AST]:
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(current))


def resource_bound_names(block: Sequence[ast.stmt], module: Optional[ast.AST]) -> FrozenSet[str]:
    """The names of ``block``'s own scope whose objects must live as long as they did.

    A name bound directly to a listed constructor's result, and a name read
    in the first argument of a weak-reference constructor's call. The caller
    keeps those of them that the block newly binds. ``module`` is the parsed
    module holding the block, whose imports say what a callee's spelling
    denotes; without it nothing resolves but a full dotted name. Only a
    plain name target counts as bound to a resource: an attribute or an item
    is reachable through its container, and a ``with`` target is released by
    its statement in every mode.
    """
    spellings = (
        memoized_per_node(_SPELLINGS, module, _module_spellings)
        if module is not None
        else _ModuleSpellings({}, frozenset())
    )
    names: Set[str] = set()
    for statement in block:
        for node in _own_scope(statement):
            if isinstance(node, ast.Assign) and _constructs_resource(node.value, spellings):
                names.update(target.id for target in node.targets if isinstance(target, ast.Name))
            elif (
                isinstance(node, (ast.AnnAssign, ast.NamedExpr))
                and isinstance(node.target, ast.Name)
                and _constructs_resource(node.value, spellings)
            ):
                names.add(node.target.id)
            names.update(_weakly_referenced(node, spellings))
    return frozenset(names)

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

"""Conservative guards for extractions that change execution context.

Reads that depend on the frame (``locals``, ``eval``, ``warnings.warn`` stack
levels, and the module's imported or assigned aliases of them), external
names another function may rebind while the block runs, nested scopes and
loop control that cross the block boundary, names the block unbinds or
declares ``global``/``nonlocal``, arguments that cannot be evaluated eagerly,
and the names a block resolves at module scope, which a same-module helper
may read bare.
"""

from __future__ import annotations

import dataclasses

import ast
import builtins
from itertools import chain
from typing import (
    Callable,
    Dict,
    Optional,
    AbstractSet,
    TYPE_CHECKING,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Mapping,
    Sequence,
    Set,
    Tuple,
    Union,
)
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from .binding_detector import BindingDetector
from .builtins import BUILTIN_NAMES, PYTHON_BUILTINS
from .class_private import (
    function_mangling_classes,
    is_class_private,
    mangled,
    rewritten_identifiers,
)
from .definite_assignment import (
    definitely_bound_after,
    definitely_bound_before,
    locally_bound_names,
)
from .models import FunctionNode
from .bounded_cache import BoundedCache
from .scope_analyzer import ScopeAnalyzer, type_parameter_names
from .parameters import parameter_names
from .statement_facts import (
    bindings_of,
    import_binding_names,
    loaded_names,
    memoized_per_node,
    pattern_capture_names,
)
from .structural_memo import structural_id
from .visitors import FreeNameCollector, OwnScopeVisitor, type_parameter_expressions

if TYPE_CHECKING:
    from .substitution import Substitution


_PRIVATE_NAME_USE: BoundedCache[str, bool] = BoundedCache(65_536)
"""Whether nodes use a class-private name, by structural id.

Asked of every function that hosts a clustered site, once per proposal
that clusters into it; the answer depends only on the names the nodes
spell, which their structure holds. Per process; the workers fork after
parsing and each keeps its own copy.
"""


def uses_class_private_names(nodes: Iterable[ast.AST]) -> bool:
    """Whether moving these nodes to a different class changes name mangling."""
    block = tuple(nodes)
    key = structural_id(block)
    cached = _PRIVATE_NAME_USE.get(key)
    if cached is None:
        cached = _PRIVATE_NAME_USE.put(key, _uses_class_private_names(block))
    return cached


def _uses_class_private_names(nodes: Sequence[ast.AST]) -> bool:
    """Whether the nodes spell a class-private name anywhere the class's mangling rewrites one.

    Every such position counts (``rewritten_identifiers``): a parameter
    ``__p`` is stored as ``_A__p`` in ``class A`` while a keyword ``__p=``
    is passed as written, so ``(lambda __p=0: 7)(__p=y)`` raises in a method
    and returns 7 in a module-level helper.
    """
    return any(
        is_class_private(name)
        for statement in nodes
        for node in ast.walk(statement)
        for name in rewritten_identifiers(node)
    )


def nested_bindings_escape(function: FunctionNode, nodes: Iterable[ast.AST]) -> bool:
    """Reject nested extraction whose local writes are observable outside it.

    The engine's return-variable analysis covers top-level statement slices.
    A loop/branch body needs control-flow liveness, including reads on the next
    loop iteration. Until that analysis exists, require its bindings to remain
    entirely inside the extracted block. Attribute/subscript mutations do not
    rebind their base objects and are not counted as local writes.
    """
    block = tuple(nodes)
    if all(node in function.body for node in block):
        return False
    extracted = {child for statement in block for child in ast.walk(statement)}
    detector = BindingDetector()
    for statement in block:
        detector.visit(statement)
    bound = {binding.name for binding in detector.bindings}
    # Deletion changes the original local binding too, and is not a binding
    # construct reported by BindingDetector. Store contexts also conservatively
    # include declarations that do not initialize a value.
    bound.update(
        node.id
        for node in extracted
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    )
    if not bound:
        return False
    for node in ast.walk(function):
        if node in extracted:
            continue
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Load, ast.Del))
            and node.id in bound
        ):
            return True
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in bound
        ):
            return True
    return False


UNKNOWABLE_HAZARD = "*"
"""Stands for every external name when the module's scopes cannot be trusted."""


def rebound_external_names(
    analyzer: ScopeAnalyzer,
    function: FunctionNode,
    nodes: Iterable[ast.AST],
) -> FrozenSet[str]:
    """The external names the block reads that another function may rebind meanwhile.

    Passing such a name to the helper snapshots it before the block's later
    reads; a helper that reads it bare, where the block did, sees the
    rebinding as the block did. Explicit global/nonlocal declarations
    identify the bindings another function can update. Namespace reflection
    makes that identification unreliable, so its presence makes every external
    read a hazard, and a function the analyzer could not place is
    ``UNKNOWABLE_HAZARD``. Opaque mutation from outside the module is not
    modeled.
    """
    scope = analyzer.node_scopes.get(function)
    root = analyzer.root_scope
    if scope is None or root is None:
        return frozenset({UNKNOWABLE_HAZARD})
    hazards = analyzer.external_binding_hazards
    if hazards is None:
        return frozenset({UNKNOWABLE_HAZARD})
    rebound = hazards.rebound
    unreliable = hazards.unresolved_nonlocal or hazards.reflective
    found: Set[str] = set()
    for statement in nodes:
        for node in ast.walk(statement):
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            binding = scope.lookup(node.id)
            if binding is None:
                if unreliable or (root.scope_id, node.id) in rebound:
                    found.add(node.id)
                continue
            # A local captured by a nested function is a mutable cell too.
            # Passing it to the helper snapshots it before that function runs.
            if (binding.scope_id, node.id) in rebound:
                found.add(node.id)
            elif binding.scope_id != scope.scope_id and unreliable:
                found.add(node.id)
    return frozenset(found)


def snapshots_rebound_external_names(
    analyzer: ScopeAnalyzer,
    function: FunctionNode,
    nodes: Iterable[ast.AST],
    deferred: AbstractSet[str] = frozenset(),
) -> bool:
    """Whether passing the block's external names would snapshot a rebinding hazard.

    Names in ``deferred`` are read bare by the helper and are no hazard.
    """
    return bool(rebound_external_names(analyzer, function, nodes) - deferred)


def _has_comprehension_assignment(nodes: Iterable[ast.AST]) -> bool:
    """Whether a comprehension writes a binding in its containing function.

    This requires return/liveness analysis across the comprehension boundary;
    until that is modeled, such a comprehension must remain in its caller.
    """
    return any(
        isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
        and any(isinstance(child, ast.NamedExpr) for child in ast.walk(node))
        for statement in nodes
        for node in ast.walk(statement)
    )


# Attributes and callees whose behavior depends on the call stack, the active
# traceback, or a module's own source text. Moving code into a helper adds a
# frame and shifts line numbers, so a program that reads any of these can
# observe the refactoring even when its result is unchanged. These power the
# pre-run warning; they are names to look for, not a guarantee of breakage.
_FRAME_SENSITIVE_ATTRS = frozenset(
    {
        "f_back",
        "f_locals",
        "f_globals",
        "f_lineno",
        "f_code",
        "tb_frame",
        "tb_next",
        "tb_lineno",
        "__traceback__",
    }
)
_FRAME_SENSITIVE_CALLEES = frozenset(
    {
        "_getframe",
        "currentframe",
        "stack",
        "getouterframes",
        "getframeinfo",
        "extract_stack",
        "print_stack",
        "extract_tb",
        "walk_tb",
        "walk_stack",
        "format_exc",
        "format_stack",
        "print_exc",
    }
)
_SOURCE_OBSERVING_CALLEES = frozenset(
    {"getsource", "getsourcelines", "getsourcefile", "findsource", "getframeinfo"}
)


def frame_sensitivity_markers(source: str) -> FrozenSet[str]:
    """Frame-, traceback-, and source-observing constructs a module contains.

    A module in the returned-empty case is not proof of safety; a callee that
    inspects frames internally is invisible here. This exists to warn a person
    which files to review, not to decide any single extraction.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return frozenset()
    markers: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in _FRAME_SENSITIVE_ATTRS:
                markers.add("frame")
            if node.attr == "__traceback__":
                markers.add("traceback")
        elif isinstance(node, ast.Call):
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if name == "warn" and any(kw.arg == "stacklevel" for kw in node.keywords):
                markers.add("stacklevel-warning")
            if name in _FRAME_SENSITIVE_CALLEES:
                markers.add("frame")
            if name in _SOURCE_OBSERVING_CALLEES:
                markers.add("source")
    return frozenset(markers)


_NAMESPACE_CALLEES = frozenset({"locals", "globals", "eval", "exec"})
_NAMESPACE_CALLEES_NO_ARGS = frozenset({"vars", "dir"})


@dataclass(frozen=True)
class FrameAliases:
    """Local names through which a module reaches the frame-sensitive builtins.

    ``import builtins as bi`` makes ``bi.locals()`` a namespace read;
    ``from builtins import locals as l`` makes ``l()`` one; ``import warnings
    as w`` and ``from warnings import warn as w`` make ``w.warn(...)`` and
    ``w(...)`` warnings. Resolved from the module's import statements at any
    depth, so a shadowing local of the same name is over-approximated as the
    alias, which only declines.
    """

    builtins_modules: FrozenSet[str] = frozenset()
    namespace_functions: FrozenSet[str] = frozenset()
    warnings_modules: FrozenSet[str] = frozenset()
    warn_functions: FrozenSet[str] = frozenset()
    # ``gf = sys._getframe``: names bound to a frame- or stack-reading function.
    frame_functions: FrozenSet[str] = frozenset()
    # Functions and methods of the module whose own body reads a frame
    # relative to its caller (``sys._getframe(n)``, ``inspect.stack()``, a
    # ``stacklevel=``), directly or by calling another such function: a call
    # to one from inside a helper would see the helper instead.
    frame_readers: FrozenSet[str] = frozenset()
    # ``s = super`` and ``from builtins import super as s``: names bound to
    # ``super`` itself, whose call with no arguments reads the calling frame.
    super_functions: FrozenSet[str] = frozenset()


NO_ALIASES = FrameAliases()
_FRAME_ALIASES: "WeakKeyDictionary[ast.AST, FrameAliases]" = WeakKeyDictionary()


def frame_aliases(module: Optional[ast.AST]) -> FrameAliases:
    """The module's aliases of the frame-sensitive builtins, computed once per tree."""
    if module is None:
        return NO_ALIASES
    known = _FRAME_ALIASES.get(module)
    if known is not None:
        return known
    builtins_modules: Set[str] = set()
    namespace_functions: Set[str] = set()
    warnings_modules: Set[str] = set()
    warn_functions: Set[str] = set()
    frame_functions: Set[str] = set()
    super_functions: Set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "builtins":
                    builtins_modules.add(alias.asname or alias.name)
                elif alias.name == "warnings":
                    warnings_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                if node.module == "builtins" and (
                    alias.name in _NAMESPACE_CALLEES or alias.name in _NAMESPACE_CALLEES_NO_ARGS
                ):
                    namespace_functions.add(alias.asname or alias.name)
                elif node.module == "builtins" and alias.name == "super":
                    super_functions.add(alias.asname or alias.name)
                elif node.module == "warnings" and alias.name == "warn":
                    warn_functions.add(alias.asname or alias.name)
    # ``e = eval`` and ``warn = warnings.warn`` are aliases too, wherever they
    # are written; the pass is a fixed point, since an alias of an alias is
    # one. Rebinding elsewhere makes this conservative, never unsound.
    grew = True
    while grew:
        grew = False
        for node in ast.walk(module):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                continue
            aliased = targets[0].id
            value = node.value
            if isinstance(value, ast.Name):
                frame_builtin = (
                    value.id in _NAMESPACE_CALLEES
                    or value.id in _NAMESPACE_CALLEES_NO_ARGS
                    or value.id in namespace_functions
                )
                warning = value.id in warn_functions
                frame_reader = value.id in frame_functions
                super_alias = value.id == "super" or value.id in super_functions
            elif isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
                frame_builtin = value.value.id in builtins_modules and (
                    value.attr in _NAMESPACE_CALLEES or value.attr in _NAMESPACE_CALLEES_NO_ARGS
                )
                warning = value.value.id in warnings_modules and value.attr == "warn"
                # Any receiver: the attribute name alone identifies these.
                frame_reader = value.attr in _FRAME_RELATIVE_CALLEES
                super_alias = value.value.id in builtins_modules and value.attr == "super"
            else:
                continue
            if frame_builtin and aliased not in namespace_functions:
                namespace_functions.add(aliased)
                grew = True
            if warning and aliased not in warn_functions:
                warn_functions.add(aliased)
                grew = True
            if frame_reader and aliased not in frame_functions:
                frame_functions.add(aliased)
                grew = True
            if super_alias and aliased not in super_functions:
                super_functions.add(aliased)
                grew = True
    aliases = FrameAliases(
        frozenset(builtins_modules),
        frozenset(namespace_functions),
        frozenset(warnings_modules),
        frozenset(warn_functions),
        frozenset(frame_functions),
        super_functions=frozenset(super_functions),
    )
    result = dataclasses.replace(aliases, frame_readers=_frame_readers(module, aliases))
    _FRAME_ALIASES[module] = result
    return result


def _callee_name(call: ast.Call, aliases: FrameAliases) -> Optional[str]:
    """The builtin a call reaches, by name or through a builtins-module alias."""
    callee = call.func
    if isinstance(callee, ast.Name):
        if callee.id in aliases.namespace_functions:
            return callee.id
        return callee.id
    if (
        isinstance(callee, ast.Attribute)
        and isinstance(callee.value, ast.Name)
        and callee.value.id in aliases.builtins_modules
    ):
        return callee.attr
    return None


def is_namespace_access_call(node: ast.AST, aliases: FrameAliases = NO_ALIASES) -> bool:
    """Whether ``node`` is a call that reads or writes the caller's namespace.

    That is ``locals()``, ``globals()``, ``eval()``, ``exec()``, or the
    no-argument ``vars()`` and ``dir()`` (which read ``locals()``), spelled
    directly or through an alias of the ``builtins`` module. A local
    variable, parameter, or attribute that merely shares one of these names is
    not such a call, so callers can rely on this to avoid false positives on
    shadowing.
    """
    if not isinstance(node, ast.Call):
        return False
    name = _callee_name(node, aliases)
    if name is None:
        return False
    if isinstance(node.func, ast.Name) and node.func.id in aliases.namespace_functions:
        # ``from builtins import vars as v``: the alias stands for the builtin.
        return True
    if name in _NAMESPACE_CALLEES:
        return True
    return name in _NAMESPACE_CALLEES_NO_ARGS and not node.args and not node.keywords


def _is_warning_call(call: ast.Call, aliases: FrameAliases) -> bool:
    """``warnings.warn(...)`` however it is spelled.

    The warnings registry deduplicates by the calling site, so two sites that
    warn become one site that warns once; and a ``stacklevel`` attributes the
    warning to a caller a fixed number of frames up, which a helper shifts.
    A ``stacklevel`` keyword on any call is taken as forwarding to ``warn``.
    """
    if any(keyword.arg == "stacklevel" for keyword in call.keywords):
        return True
    callee = call.func
    if isinstance(callee, ast.Name):
        return callee.id == "warn" or callee.id in aliases.warn_functions
    if isinstance(callee, ast.Attribute) and callee.attr == "warn":
        return isinstance(callee.value, ast.Name) and (
            callee.value.id == "warnings" or callee.value.id in aliases.warnings_modules
        )
    return False


def requires_original_frame(nodes: Iterable[ast.AST], aliases: FrameAliases = NO_ALIASES) -> bool:
    """Reject suspension and operations that inspect the original call frame.

    Generator delegation needs a separate transformation preserving send/throw
    and return values. Moving frame inspection into a helper is not equivalent.
    Unknown shadowing of these call names is deliberately treated conservatively.
    Each statement's verdict is memoized: it is a property of that statement
    and its module alone, and a statement belongs to every block that spans it.
    """
    return any(
        memoized_per_node(
            _FRAME_SENSITIVE,
            statement,
            lambda item: _statement_requires_original_frame(item, aliases),
        )
        for statement in nodes
    )


def block_requires_original_frame(
    analyzer: "ScopeAnalyzer", function: FunctionNode, nodes: Sequence[ast.stmt]
) -> bool:
    """``requires_original_frame`` with the module's aliases resolved (the guard-memo form).

    A block that needs its class body (``needs_class_body``) counts too when
    its function's ``super()`` could mean something else in a helper of that
    class (``_super_differs_in_a_helper``): no home at all would keep it.
    """
    return requires_original_frame(nodes, frame_aliases(analyzer.analyzed_tree)) or (
        needs_class_body(nodes) and _super_differs_in_a_helper(function)
    )


def needs_class_body(nodes: Iterable[ast.AST]) -> bool:
    """Whether moved code reads its class's ``__class__`` cell through ``super``.

    Zero-argument ``super()`` is ``super(__class__, first)``. ``__class__`` is
    the cell the compiler gives every function of a class body that loads
    the name ``super`` or ``__class__``, and ``first`` is the current value of
    the calling frame's first argument. A helper compiled in the same class
    body gets the same cell, and one reached as ``self.__extracted_func_0()``
    has the method's receiver first; a module-level function has no cell,
    and its ``super()`` raises ``RuntimeError``. Such code may therefore move
    only into a method helper of the class that holds it, and placement
    declines every other home (docs/DECISIONS.md, "A method helper lives in
    the class that holds both duplicates").

    A call of ``super`` with a leading positional argument names its class
    and object, reads no cell, and moves anywhere. Every other load of the
    name counts: a call that may pass nothing (``super(*args)``,
    ``super(**kwargs)``), and the bare value, which whatever receives it may
    call with nothing (``s = super; s()`` works only in a function of the
    class body). A nested function, lambda or comprehension in the code
    counts as well; its cell is the same class's in a same-class helper, and
    its own first argument moves with it, so each behaves as it did.
    """
    return any(
        memoized_per_node(_CLASS_BODY_NEEDED, statement, _statement_needs_class_body)
        for statement in nodes
    )


_CLASS_BODY_NEEDED: "WeakKeyDictionary[ast.AST, bool]" = WeakKeyDictionary()


def _statement_needs_class_body(statement: ast.AST) -> bool:
    explicit = {
        id(node.func)
        for node in ast.walk(statement)
        if isinstance(node, ast.Call) and not _may_pass_no_arguments(node)
    }
    return any(
        isinstance(node, ast.Name)
        and node.id == "super"
        and isinstance(node.ctx, ast.Load)
        and id(node) not in explicit
        for node in ast.walk(statement)
    )


def _may_pass_no_arguments(call: ast.Call) -> bool:
    """Whether ``call`` may pass no positional argument: none written, or a leading ``*iterable``."""
    return not call.args or isinstance(call.args[0], ast.Starred)


def _super_differs_in_a_helper(function: FunctionNode) -> bool:
    """Whether zero-argument ``super()`` in ``function`` could mean something else in a method helper.

    The helper's ``super()`` reads the helper's own cell and first
    parameter, the method's reads the method's. They agree only while the
    method's first positional parameter is the receiver that
    ``self.__extracted_func_0()`` passes: never rebound or deleted in the
    method, not even through a nested function's ``nonlocal``. The method
    must also leave ``__class__`` alone: one that binds it, takes it as a
    parameter, or declares it ``global`` or ``nonlocal`` has no cell, and its
    ``super()`` raises where a helper's would not. A function with no
    positional parameter has no receiver for ``super()`` to read either.
    """
    positional = [*function.args.posonlyargs, *function.args.args]
    if not positional:
        return True
    rebound = set(
        chain.from_iterable(
            bindings_of(statement, into_nested_scopes=False) for statement in function.body
        )
    )
    rebound.update(
        name
        for node in ast.walk(function)
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    )
    return positional[0].arg in rebound or "__class__" in rebound | set(
        parameter_names(function.args)
    )


def frame_read_outside_block(
    analyzer: "ScopeAnalyzer", function: FunctionNode, nodes: Sequence[ast.stmt]
) -> bool:
    """Whether the function reads its own frame anywhere outside the block.

    ``locals()``, ``vars()``, ``dir()``, a direct ``eval``/``exec`` or a frame
    walk after the block sees the block's locals; moved into a helper, they
    are gone (``dir()`` after the block lists fewer names; ``eval("total")``
    raises). Only the function's own scope counts: a nested function's
    ``locals()`` is its own frame. An aliased ``super()`` outside the block
    (``_is_aliased_super_call``) counts when the block loads ``super`` or
    ``__class__``: that load may be what gives the function its class cell,
    and it moves into the helper.
    """
    aliases = frame_aliases(analyzer.analyzed_tree)
    inside = set(map(id, nodes))
    # Only a module that binds an alias of ``super`` or ``builtins`` can spell one.
    moves_cell_load = bool(
        aliases.super_functions or aliases.builtins_modules
    ) and _loads_class_cell_name(nodes)
    for statement in function.body:
        for node in _walk_own_scope_outside(statement, inside):
            if isinstance(node, ast.Call) and (
                is_namespace_access_call(node, aliases)
                or _reads_own_frame(node, aliases)
                or (moves_cell_load and _is_aliased_super_call(node, aliases))
            ):
                return True
    return False


def _walk_own_scope_outside(node: ast.AST, excluded: AbstractSet[int]) -> Iterator[ast.AST]:
    """``walk_own_scope(node)`` without the nodes whose ids are ``excluded``, or anything in them.

    The rest of a compound statement that holds the block is still walked: a
    frame read earlier in the same loop body sees the block's locals on the
    next iteration, and a frame handle taken before the block sees them
    afterwards.
    """
    pending = [node]
    while pending:
        current = pending.pop()
        if id(current) in excluded:
            continue
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(current))


def _frame_readers(module: ast.AST, aliases: FrameAliases) -> FrozenSet[str]:
    """Names of the module's functions and methods that read a caller-relative frame.

    A function qualifies when its own scope makes a frame-relative call, or
    calls, by bare name or as an attribute, a function that qualifies; the
    closure is taken to a fixed point. Resolution is by name, so a same-named
    function elsewhere is over-approximated as a reader, which only declines.
    """
    definitions = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    calls = {
        id(node): [
            item
            for statement in node.body
            for item in walk_own_scope(statement)
            if isinstance(item, ast.Call)
        ]
        for node in definitions
    }
    readers: Set[str] = {
        node.name
        for node in definitions
        if any(_is_frame_relative_call(call, aliases) for call in calls[id(node)])
    }
    grew = bool(readers)
    while grew:
        grew = False
        for node in definitions:
            if node.name in readers:
                continue
            if any(_called_name(call) in readers for call in calls[id(node)]):
                readers.add(node.name)
                grew = True
    return frozenset(readers)


def _called_name(call: ast.Call) -> Optional[str]:
    """The name a call reaches: ``f(...)`` gives ``f``, ``obj.f(...)`` gives ``f``."""
    callee = call.func
    if isinstance(callee, ast.Name):
        return callee.id
    if isinstance(callee, ast.Attribute):
        return callee.attr
    return None


def _is_aliased_super_call(call: ast.Call, aliases: FrameAliases) -> bool:
    """``super()`` reached through another name, with possibly no arguments: ``s()``, ``bi.super()``.

    Like ``super()`` it reads the calling frame's ``__class__`` cell and
    first argument, but the compiler gives a function that cell only for
    loads of the names ``super`` and ``__class__`` in it; ``s()`` works in a
    method only while something else in the method loads one of them. A
    helper holding the call may lack the cell the method had, so the call
    requires its original frame. The name ``super`` itself is the case
    ``needs_class_body`` handles.
    """
    if not _may_pass_no_arguments(call):
        return False
    callee = call.func
    if isinstance(callee, ast.Name):
        return callee.id != "super" and callee.id in aliases.super_functions
    return (
        isinstance(callee, ast.Attribute)
        and callee.attr == "super"
        and isinstance(callee.value, ast.Name)
        and callee.value.id in aliases.builtins_modules
    )


def _loads_class_cell_name(nodes: Iterable[ast.AST]) -> bool:
    """Whether the nodes load ``super`` or ``__class__``, either of which gives a function its class cell."""
    return any(
        isinstance(node, ast.Name)
        and node.id in {"super", "__class__"}
        and isinstance(node.ctx, ast.Load)
        for statement in nodes
        for node in ast.walk(statement)
    )


def _reads_own_frame(call: ast.Call, aliases: FrameAliases) -> bool:
    """``sys._getframe()`` or ``inspect.currentframe()``: a handle to this frame's locals.

    Stack listings and warnings attribute to frames above the call and are
    unchanged by a helper that has already returned; a frame object read
    later sees whatever locals are still there. A name bound to one of these
    (``gf = sys._getframe``) is over-approximated as the function itself.
    """
    callee = call.func
    if isinstance(callee, ast.Name) and callee.id in aliases.frame_functions:
        return True
    name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
    return name in {"_getframe", "currentframe"}


_FRAME_SENSITIVE: "WeakKeyDictionary[ast.AST, bool]" = WeakKeyDictionary()


def _statement_requires_original_frame(statement: ast.AST, aliases: FrameAliases) -> bool:
    block = (statement,)
    if has_external_loop_control(block) or _has_comprehension_assignment(block):
        return True
    for node in ast.walk(statement):
        if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            return True
        if isinstance(node, ast.comprehension) and node.is_async:
            # An async comprehension is only valid inside an async function.
            return True
        if isinstance(node, ast.Call):
            if is_namespace_access_call(node, aliases) or _is_aliased_super_call(node, aliases):
                return True
            if _is_frame_relative_call(node, aliases) or _is_warning_call(node, aliases):
                return True
            if _called_name(node) in aliases.frame_readers:
                return True
    return False


_FRAME_RELATIVE_CALLEES = frozenset(
    {"_getframe", "currentframe", "stack", "getouterframes", "extract_stack", "print_stack"}
)


def _is_frame_relative_call(call: ast.Call, aliases: FrameAliases = NO_ALIASES) -> bool:
    """Calls whose result depends on how many frames sit above them.

    ``warnings.warn(..., stacklevel=n)`` attributes the warning to the n-th
    caller; a helper adds one frame. Frame and stack inspection is likewise
    relative to the current frame. Only direct, recognizably named calls and
    the module's assigned aliases of them are detected; a callee that
    inspects frames internally is not.
    """
    callee = call.func
    if isinstance(callee, ast.Name) and callee.id in aliases.frame_functions:
        return True
    name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
    if any(keyword.arg == "stacklevel" for keyword in call.keywords):
        return True
    return name in _FRAME_RELATIVE_CALLEES


class _LoopControlVisitor(OwnScopeVisitor):
    def __init__(self) -> None:
        self.depth = 0
        self.external = False

    def visit_Break(self, node: ast.Break) -> None:
        if self.depth == 0:
            self.external = True

    def visit_Continue(self, node: ast.Continue) -> None:
        if self.depth == 0:
            self.external = True

    def _visit_loop(self, node: Union[ast.For, ast.AsyncFor, ast.While]) -> None:
        self.depth += 1
        for statement in node.body:
            self.visit(statement)
        self.depth -= 1
        # A loop's else-suite is outside that loop's break/continue scope.
        for statement in node.orelse:
            self.visit(statement)

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop

    def _nested_class(self, node: ast.ClassDef) -> None:
        """A loop inside a nested class body is not the block's loop."""


def has_external_loop_control(nodes: Iterable[ast.AST]) -> bool:
    """Whether break/continue targets a loop outside the extraction boundary."""

    visitor = _LoopControlVisitor()
    for statement in nodes:
        visitor.visit(statement)
    return visitor.external


def bound_names(nodes: Iterable[ast.AST]) -> Set[str]:
    """Every name a node binds or unbinds, including inside nested scopes.

    This over-approximates scope: a nested function's local counts too. The
    guards below use it to decide whether a nested scope and the extracted
    block could share a binding, where over-approximation only rejects.
    """
    names: Set[str] = set()
    for statement in nodes:
        bound: FrozenSet[str] = memoized_per_node(_BOUND_NAMES, statement, _all_bindings_of)
        names.update(bound)
    return names


_BOUND_NAMES: "WeakKeyDictionary[ast.AST, FrozenSet[str]]" = WeakKeyDictionary()


def _all_bindings_of(statement: ast.AST) -> FrozenSet[str]:
    return bindings_of(statement, into_nested_scopes=True)


def _deleted_names(nodes: Iterable[ast.AST]) -> Set[str]:
    """Names a block unbinds: explicit ``del`` and implicit except-clause cleanup."""
    names: Set[str] = set()
    for statement in nodes:
        deleted: FrozenSet[str] = memoized_per_node(
            _DELETED_NAMES, statement, _statement_deleted_names
        )
        names.update(deleted)
    return names


_DELETED_NAMES: "WeakKeyDictionary[ast.AST, FrozenSet[str]]" = WeakKeyDictionary()


def _statement_deleted_names(statement: ast.AST) -> FrozenSet[str]:
    names: Set[str] = set()
    for node in ast.walk(statement):
        if isinstance(node, ast.Delete):
            for target in node.targets:
                names.update(
                    child.id
                    for child in ast.walk(target)
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Del)
                )
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return frozenset(names)


def unbinds_external_name(
    function: FunctionNode,
    nodes: Iterable[ast.AST],
    bound_before: Set[str],
) -> bool:
    """Whether the block deletes a binding that outlives it.

    Deleting a helper parameter leaves the caller's variable bound; the
    original raised ``UnboundLocalError`` on the next read. ``except ... as e``
    deletes ``e`` when the handler exits, so it is a deletion too. Global and
    nonlocal names are rejected because the helper holds no such declaration.
    """
    deleted = _deleted_names(nodes)
    if not deleted:
        return False
    declared: Set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
    return bool(deleted & (bound_before | declared))


_NESTED_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.GeneratorExp)

_TYPE_ALIAS: Optional[type] = getattr(ast, "TypeAlias", None)
"""``type X = ...`` (Python 3.12+), whose value is evaluated lazily, like a lambda's body."""


def _lazily_read_names(node: ast.AST) -> Optional[FrozenSet[str]]:
    """The names ``node`` reads when something later asks, not where it stands, if it is such a scope.

    A function, lambda or generator reads its free names when it runs. A
    ``type`` statement's value and the bounds, constraints and defaults of
    PEP 695 type parameters are evaluated only when ``__value__``,
    ``__bound__`` or ``__default__`` is first read, and so is, from Python
    3.14, the annotation of a class attribute: each reads the variable's
    binding at that moment, a closure like any other. A class body and its
    bases run once, where the class stands, and are not.
    """
    if isinstance(node, _NESTED_SCOPE_TYPES):
        return frozenset(loaded_names(node))
    if _TYPE_ALIAS is not None and isinstance(node, _TYPE_ALIAS):
        # Its own type parameters are bound in its annotation scope.
        return frozenset(loaded_names(node) - type_parameter_names(node))
    if isinstance(node, ast.ClassDef):
        lazy: List[ast.AST] = [*type_parameter_expressions(node)]
        lazy.extend(
            statement.annotation for statement in node.body if isinstance(statement, ast.AnnAssign)
        )
        return frozenset(name for expression in lazy for name in loaded_names(expression))
    return None


class _ScopeFacts:
    """Nested scopes and top-level bindings of one function, computed once.

    The closure guard below runs once per candidate block; walking the whole
    function each time made it quadratic in the function's size per block.
    """

    def __init__(self, function: FunctionNode) -> None:
        self.nested: Tuple[Tuple[ast.AST, FrozenSet[str]], ...] = tuple(
            (node, lazy)
            for node in ast.walk(function)
            if node is not function and (lazy := _lazily_read_names(node)) is not None
        )
        self.top_level: Tuple[Tuple[ast.stmt, FrozenSet[str]], ...] = tuple(
            (statement, frozenset(bound_names([statement]))) for statement in function.body
        )


_SCOPE_FACTS: "WeakKeyDictionary[ast.AST, _ScopeFacts]" = WeakKeyDictionary()


def _scope_facts(function: FunctionNode) -> _ScopeFacts:
    facts = _SCOPE_FACTS.get(function)
    if facts is None:
        facts = _ScopeFacts(function)
        _SCOPE_FACTS[function] = facts
    return facts


def nested_scopes_cross_block_boundary(function: FunctionNode, nodes: Iterable[ast.AST]) -> bool:
    """Reject extraction when a closure and the block share a mutable binding.

    A nested function, lambda or generator reads its free names when it runs,
    not when it is defined, and a ``type`` statement or a type parameter's
    bound reads them when first asked (``_lazily_read_names``). If it is
    defined outside the block and the block rebinds one of those names, the
    helper rebinds its own local instead of the caller's cell. If it is defined inside the block and the caller rebinds one
    of its free names after the block, the closure keeps the helper's cell
    while the original saw the caller's rebinding; that holds whether the
    block bound the name itself (a loop target the caller assigns again
    later) or read it from the caller. Both cases are rejected; reads of
    names bound only before a top-level block remain eligible.
    """
    block = tuple(nodes)
    extracted = {child for statement in block for child in ast.walk(statement)}
    written_in_block = bound_names(block)
    facts = _scope_facts(function)
    if written_in_block and any(
        loaded & written_in_block for scope, loaded in facts.nested if scope not in extracted
    ):
        return True
    captured: Set[str] = set()
    inside = False
    for scope, loaded in facts.nested:
        if scope in extracted:
            inside = True
            captured.update(loaded)
    if not inside or not captured:
        return False
    top_level = all(node in function.body for node in block)
    block_end = max(getattr(node, "end_lineno", 0) or 0 for node in block)
    for statement, bound in facts.top_level:
        if statement in extracted:
            continue
        if top_level and (getattr(statement, "lineno", 0) or 0) <= block_end:
            continue
        if bound & captured:
            return True
    return False


_CONSUMING_BUILTINS = frozenset(
    {
        "all",
        "any",
        "dict",
        "frozenset",
        "list",
        "max",
        "min",
        "next",
        "set",
        "sorted",
        "sum",
        "tuple",
    }
)
"""Builtins that iterate their first argument where they are called and keep nothing of it."""

_WRAPPING_BUILTINS = frozenset({"enumerate", "filter", "map", "zip"})
"""Builtins whose iterator holds its arguments: consumed exactly when the iterator is."""

_KEY_CALLING_BUILTINS = frozenset({"max", "min", "sorted"})
"""Builtins that call a ``key=`` function on each element and keep nothing of it."""

_SHADOWED_BUILTINS: "WeakKeyDictionary[ast.AST, FrozenSet[str]]" = WeakKeyDictionary()


def _shadowable_names(module: Optional[ast.AST]) -> Optional[FrozenSet[str]]:
    """Every name the module binds anywhere, or None when a star import may bind any name.

    A builtin is trusted to call a key function and keep nothing only while
    no binding of the module can stand in for it; over-counting a local of
    another function as a shadow only declines.
    """
    if module is None:
        return None
    known = _SHADOWED_BUILTINS.get(module)
    if known is not None:
        return known
    if any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for node in ast.walk(module)
    ):
        return None
    names = bindings_of(module, into_nested_scopes=True)
    _SHADOWED_BUILTINS[module] = names
    return names


def created_object_escapes(
    analyzer: "ScopeAnalyzer", function: FunctionNode, nodes: Sequence[ast.stmt]
) -> bool:
    """Whether the block creates a function, class or generator that anything could observe.

    An object made by moved code is made by the helper: a function, lambda
    or generator expression carries ``__extracted_func_0.<locals>`` in its
    ``__qualname__`` where it carried the enclosing function's, and a
    lambda built from a unified template has the template's parameter
    names. Both reach output through ``repr``, logging, registries and
    ``inspect.signature``, and a caller passing ``other=`` by keyword to a
    lambda now spelled ``value`` gets TypeError. Such an object is safe only
    where nothing can look at it: a function or lambda that is only ever
    called inside the block, with arguments its parameters accept (a call
    that fails to bind raises TypeError naming the function), a ``key=``
    function of ``sorted``, ``min`` or ``max``, the function of a ``map``
    or ``filter`` consumed in the block, and a generator expression the
    block consumes on the spot. Anything else -- returned, yielded, stored
    anywhere, passed to another call, formatted, decorated, a coroutine
    function, or a class, whose every instance shows its qualified name --
    declines the block.
    """
    block = list(nodes)
    shadowable = _shadowable_names(analyzer.analyzed_tree)
    parents: Dict[ast.AST, ast.AST] = {
        child: parent
        for statement in block
        for parent in ast.walk(statement)
        for child in ast.iter_child_nodes(parent)
    }
    inside = {id(node) for statement in block for node in ast.walk(statement)}
    context = _CreatedObjectContext(
        function, parents, inside, shadowable, _parameter_storage(analyzer.analyzed_tree, function)
    )
    for statement in block:
        for node in ast.walk(statement):
            if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)):
                return True
            if isinstance(node, ast.FunctionDef):
                if (
                    node.decorator_list
                    or _makes_generator(node)
                    or not context.only_called(node.name, node.args)
                ):
                    return True
            elif isinstance(node, ast.Lambda):
                if _makes_generator(node) or not context.lambda_only_called(node):
                    return True
            elif isinstance(node, ast.GeneratorExp) and not context.consumed(node):
                return True
    return False


def _makes_generator(node: Union[ast.FunctionDef, ast.Lambda]) -> bool:
    """Whether calling ``node`` returns a generator, whose repr names the function."""
    return any(isinstance(child, (ast.Yield, ast.YieldFrom)) for child in _walk_own_body(node))


def _walk_own_body(node: Union[ast.FunctionDef, ast.Lambda]) -> Iterable[ast.AST]:
    """The nodes of a function's or lambda's body, not of the scopes nested in it."""
    body: List[ast.AST] = list(node.body) if isinstance(node, ast.FunctionDef) else [node.body]
    for statement in body:
        yield from walk_own_scope(statement)


@dataclass(frozen=True)
class _CreatedObjectContext:
    """Where the objects a block creates stand, for ``created_object_escapes``.

    An object is looked at only through the expression that makes it or a
    local name bound to it, so each is followed to every place it is read:
    a function or lambda must be called there, a generator or a builtin's
    iterator over one must be iterated there, all inside the block.
    """

    function: FunctionNode
    parents: Dict[ast.AST, ast.AST]
    inside: AbstractSet[int]
    shadowable: Optional[FrozenSet[str]]
    # The name the compiler stores a parameter of a function the block makes
    # under (see ``_parameter_storage``).
    stored_as: Callable[[str], Optional[str]]

    def _builtin(self, callee: ast.AST, names: FrozenSet[str]) -> Optional[str]:
        """The builtin of ``names`` a callee spells, when nothing in the module can shadow it."""
        if (
            isinstance(callee, ast.Name)
            and callee.id in names
            and self.shadowable is not None
            and callee.id not in self.shadowable
        ):
            return callee.id
        return None

    def _local_reads(self, name: str) -> Optional[List[ast.Name]]:
        """Every read of ``name`` in the function, or None when one is outside the block.

        A ``global`` or ``nonlocal`` declaration of the name makes storing
        to it a store outside the block, so it counts as None too.
        """
        reads: List[ast.Name] = []
        for node in ast.walk(self.function):
            if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
                return None
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
                if id(node) not in self.inside:
                    return None
                reads.append(node)
        return reads

    def _bound_name(self, node: ast.AST) -> Optional[str]:
        """The one local name an assignment whose value is ``node`` binds, if it is one."""
        parent = self.parents.get(node)
        if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is node:
            targets = _assignment_targets(parent)
            if len(targets) == 1 and isinstance(targets[0], ast.Name):
                return targets[0].id
        return None

    def only_called(self, name: str, arguments: ast.arguments) -> bool:
        """Whether every read of a function's name, all inside the block, calls it where allowed."""
        reads = self._local_reads(name)
        return reads is not None and all(self._called_here(read, arguments) for read in reads)

    def lambda_only_called(self, node: ast.Lambda) -> bool:
        """Whether a lambda is only called: where it stands, or through the local name it is bound to."""
        name = self._bound_name(node)
        if name is not None:
            return self.only_called(name, node.args)
        return self._called_here(node, node.args)

    def _called_here(self, node: ast.AST, arguments: ast.arguments) -> bool:
        """Whether the function ``node`` evaluates to is called where it stands, and nothing more.

        Called directly with arguments that bind; the ``key=`` of a trusted
        ``sorted``, ``min`` or ``max``, which calls it with one argument; or
        the function of a trusted ``map`` or ``filter`` whose iterator is
        consumed on the spot.
        """
        parent = self.parents.get(node)
        if isinstance(parent, ast.Call) and parent.func is node:
            return _call_binds(arguments, parent, self.stored_as)
        if isinstance(parent, ast.keyword) and parent.arg == "key":
            call = self.parents.get(parent)
            return (
                isinstance(call, ast.Call)
                and self._builtin(call.func, _KEY_CALLING_BUILTINS) is not None
                and _binds(arguments, 1, (), self.stored_as)
            )
        if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node:
            builtin = self._builtin(parent.func, frozenset({"map", "filter"}))
            iterables = parent.args[1:]
            if builtin is None or any(isinstance(item, ast.Starred) for item in iterables):
                return False
            arity = 1 if builtin == "filter" else len(iterables)
            return _binds(arguments, arity, (), self.stored_as) and self.consumed(parent)
        return False

    def consumed(self, node: ast.AST, seen: FrozenSet[str] = frozenset()) -> bool:
        """Whether an iterator the block makes is iterated where it stands, or through its local name.

        ``seen`` holds the names already being followed, so a name read in
        its own definition does not loop.
        """
        name = self._bound_name(node)
        if name is not None:
            reads = self._local_reads(name)
            return (
                name not in seen
                and reads is not None
                and all(self.consumed(read, seen | {name}) for read in reads)
            )
        parent = self.parents.get(node)
        if isinstance(parent, (ast.For, ast.comprehension)) and parent.iter is node:
            if isinstance(parent, ast.For):
                return True
            owner = self.parents.get(parent)
            return not isinstance(owner, ast.GeneratorExp) or self.consumed(owner, seen)
        if isinstance(parent, ast.Starred):
            return True
        if isinstance(parent, ast.Compare):
            return any(
                comparator is node and isinstance(op, (ast.In, ast.NotIn))
                for op, comparator in zip(parent.ops, parent.comparators)
            )
        if isinstance(parent, ast.Assign) and parent.value is node:
            return all(isinstance(target, (ast.Tuple, ast.List)) for target in parent.targets)
        if not isinstance(parent, ast.Call) or node not in parent.args:
            return False
        consumer = self._builtin(parent.func, _CONSUMING_BUILTINS)
        if consumer is not None:
            # ``min(a, gen)`` compares its arguments and may return one of them.
            return parent.args[0] is node and (
                consumer not in {"min", "max"} or len(parent.args) == 1
            )
        callee = parent.func
        if (
            isinstance(callee, ast.Attribute)
            and callee.attr == "join"
            and isinstance(callee.value, ast.Constant)
            and isinstance(callee.value.value, str)
        ):
            return parent.args == [node]
        if self._builtin(callee, _WRAPPING_BUILTINS) is not None:
            return self.consumed(parent, seen)
        return False


def _assignment_targets(statement: Union[ast.Assign, ast.AnnAssign]) -> List[ast.expr]:
    return list(statement.targets) if isinstance(statement, ast.Assign) else [statement.target]


def _as_written(name: str) -> Optional[str]:
    """A parameter's name outside every class body: stored as written."""
    return name


def _parameter_storage(
    tree: Optional[ast.AST], function: FunctionNode
) -> Callable[[str], Optional[str]]:
    """How the compiler stores the parameters of a function or lambda in ``function``'s body.

    A class-private name is mangled with the class whose body holds
    ``function`` (``function_mangling_classes``), found only when a
    parameter needs it; without the module's tree, or with a function the
    tree does not hold, its storage is unknown: None. Every other name is
    stored as written.
    """

    def stored_as(name: str) -> Optional[str]:
        if not is_class_private(name):
            return name
        classes = function_mangling_classes(tree) if tree is not None else {}
        if function not in classes:
            return None
        return mangled(name, classes[function])

    return stored_as


def _call_binds(
    arguments: ast.arguments,
    call: ast.Call,
    stored_as: Callable[[str], Optional[str]] = _as_written,
) -> bool:
    """Whether ``call`` binds its arguments to these parameters; unknowable with ``*``/``**``."""
    if any(isinstance(argument, ast.Starred) for argument in call.args):
        return False
    keywords = [keyword.arg for keyword in call.keywords]
    if any(keyword is None for keyword in keywords):
        return False
    return _binds(
        arguments, len(call.args), [keyword for keyword in keywords if keyword], stored_as
    )


def _binds(
    arguments: ast.arguments,
    positional: int,
    keywords: Sequence[str],
    stored_as: Callable[[str], Optional[str]] = _as_written,
) -> bool:
    """Whether ``positional`` arguments and these keywords bind without a TypeError.

    The TypeError a failed binding raises names the function by its
    qualified name, which a helper changes. A keyword binds the parameter
    whose name, as the compiler stores it (``stored_as``), is the keyword
    as written: in a class body a parameter ``__p`` is stored mangled and
    the keyword ``__p=`` is not (``class_private``), so it does not bind
    there. A parameter whose storage is unknown binds nothing.
    """
    ordered = [*arguments.posonlyargs, *arguments.args]
    if positional > len(ordered) and arguments.vararg is None:
        return False
    stored = {
        argument.arg: stored_as(argument.arg) for argument in [*ordered, *arguments.kwonlyargs]
    }
    if any(name is None for name in stored.values()):
        return False
    filled = {stored[argument.arg] for argument in ordered[:positional]}
    by_keyword = {stored[argument.arg] for argument in [*arguments.args, *arguments.kwonlyargs]}
    for keyword in keywords:
        if keyword in by_keyword:
            if keyword in filled:
                return False  # multiple values for one parameter
            filled.add(keyword)
        elif arguments.kwarg is None:
            return False  # an unexpected keyword, or a positional-only one
    required = ordered[: len(ordered) - len(arguments.defaults)]
    required_keywords = [
        argument
        for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults)
        if default is None
    ]
    return all(stored[argument.arg] in filled for argument in [*required, *required_keywords])


def is_eagerly_evaluable(expression: ast.AST, available: AbstractSet[str]) -> bool:
    """Whether hoisting this expression to the call site is unobservable.

    A parameter argument runs once, before the block, even when the block would
    have evaluated it later, repeatedly, conditionally, or not at all. Only
    expressions with no effects, no failure modes, and no fresh identity may
    move that way: literals, names the call site can resolve (``available``:
    a builtin, a module-level binding, a name bound on every path before the
    block, or a binding of an enclosing function), and tuples of those. A
    name the site cannot resolve raises NameError when hoisted out of a
    branch the block would not have taken. Attribute access can run a
    property, subscripts and operators can call arbitrary methods, calls are
    effects by definition, and mutable displays allocate.
    """
    if isinstance(expression, ast.Constant):
        return True
    if isinstance(expression, ast.Name):
        return expression.id in available
    # A tuple of such values is immutable, so one evaluation is as good as
    # many. List, set and dict displays create a fresh mutable object each
    # time they run; hoisting one out of a loop would alias every iteration.
    if isinstance(expression, ast.Tuple):
        return all(is_eagerly_evaluable(element, available) for element in expression.elts)
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.operand, ast.Constant):
        return isinstance(expression.op, (ast.USub, ast.UAdd, ast.Invert, ast.Not))
    return False


def available_argument_names(
    function: FunctionNode, block: Sequence[ast.stmt], analyzer: "ScopeAnalyzer"
) -> FrozenSet[str]:
    """Names a call standing where ``block`` stands can read without raising.

    A name is available only when every path that can reach the call has
    bound it and none can have unbound it since:

    - the function's own locals bound on every path from its entry to the
      block (``definitely_bound_before``);
    - module-level names bound on every path through the module body before
      the top-level statement that contains the function, since the function
      cannot be called before its definition has run, unless a module-level
      ``del`` or a ``global`` declaration that deletes them exists anywhere;
    - an enclosing function's locals bound on every path from its entry to
      the statement that defines the inner function, unless the enclosing
      function deletes them anywhere: the inner function may run at any point
      after its definition, and a name the enclosing function binds later, or
      only on some path, is an empty cell until then.

    Class attributes never count: a method reads them through the instance or
    class. PEP 695 type parameters do count, through their annotation scope.
    A binding in a nearer scope shadows every outer binding of the same name,
    even on paths where it is unbound. Only unshadowed builtins are available.
    """
    names: Set[str] = set()
    module = analyzer.analyzed_tree
    if not isinstance(module, ast.Module):
        return frozenset(definitely_bound_before(function, block[0]) if block else ())
    parents = _module_parents(module)
    shadowed: Set[str] = set()
    module_only: Set[str] = set()
    for scope in _lexical_name_scopes(function, analyzer):
        module_only.update(scope.global_names - shadowed)
        local_names = scope.local_names - module_only
        if isinstance(scope.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if scope.node is function:
                bound = definitely_bound_before(function, block[0]) if block else set()
            else:
                statement = _statement_within(parents, scope.node, function)
                bound = definitely_bound_before(scope.node, statement) - _deleted_names(
                    scope.node.body
                )
            names.update((bound & local_names) - shadowed)
        shadowed.update(local_names)
        parameters = scope.type_parameters - module_only
        names.update(parameters - shadowed)
        shadowed.update(parameters)
    if "__class__" not in shadowed | module_only and _within_class(parents, function):
        # The compiler fills the ``__class__`` cell before any method runs.
        names.add("__class__")
    module_names = _module_names_bound_before(module, _top_level_statement(parents, function))
    names.update((module_names | _AVAILABLE_BUILTINS) - shadowed)
    return frozenset(names)


_AVAILABLE_BUILTINS = frozenset(PYTHON_BUILTINS & vars(builtins).keys())


@dataclass(frozen=True)
class _LexicalNameScope:
    node: ast.AST
    local_names: FrozenSet[str]
    type_parameters: FrozenSet[str]
    global_names: FrozenSet[str]


def _lexical_name_scopes(
    function: FunctionNode, analyzer: "ScopeAnalyzer"
) -> Iterable[_LexicalNameScope]:
    """Function locals and annotation scopes, nearest first; class attributes are skipped."""
    module = analyzer.analyzed_tree
    parents = _module_parents(module) if isinstance(module, ast.Module) else {}
    node: Optional[ast.AST] = function
    while node is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local_names: Set[str] = set()
            global_names: Set[str] = set()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = analyzer.node_scopes.get(node)
                nonlocal_names: Set[str] = set()
                if scope is not None:
                    global_names = analyzer.global_vars.get(scope.scope_id, set())
                    nonlocal_names = analyzer.nonlocal_vars.get(scope.scope_id, set())
                local_names = locally_bound_names(node) - global_names - nonlocal_names
            yield _LexicalNameScope(
                node, frozenset(local_names), type_parameter_names(node), frozenset(global_names)
            )
        node = parents.get(node)


_MODULE_PARENTS: "WeakKeyDictionary[ast.AST, Dict[ast.AST, ast.AST]]" = WeakKeyDictionary()


def module_resolved_names(
    function: FunctionNode, analyzer: "ScopeAnalyzer", names: AbstractSet[str]
) -> FrozenSet[str]:
    """Of ``names``, those a read inside ``function`` resolves at module scope or nowhere.

    A name the function binds anywhere is its local everywhere in it, and a
    name an enclosing function binds is a cell; either read is not the
    module's. The annotation scopes of generic functions and classes bind
    their type parameters too. Class attributes do not count.
    Anything else reaches the module's namespace, then the builtins, and is
    the same lookup from any function of the module.
    """
    # ``__class__`` is the cell the compiler gives a method for zero-argument
    # ``super()``; it names the defining class, not a module binding.
    resolved = set(names) - {"__class__"}
    module_only: Set[str] = set()
    for scope in _lexical_name_scopes(function, analyzer):
        module_only.update(scope.global_names & resolved)
        resolved -= (scope.local_names | scope.type_parameters) - module_only
    return frozenset(resolved)


def _module_parents(module: ast.Module) -> Dict[ast.AST, ast.AST]:
    """Each node's parent in ``module``, computed once per tree.

    Top-level statements are absent: their parent is the module, and holding
    the module as a value would pin the weakly held key forever.
    """
    parents = _MODULE_PARENTS.get(module)
    if parents is None:
        parents = {
            child: parent
            for parent in ast.walk(module)
            if parent is not module
            for child in ast.iter_child_nodes(parent)
        }
        _MODULE_PARENTS[module] = parents
    return parents


_SCOPE_NODES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _within_class(parents: Dict[ast.AST, ast.AST], node: ast.AST) -> bool:
    """Whether a class body encloses ``node`` at any depth."""
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return True
        current = parents.get(current)
    return False


def _statement_within(
    parents: Dict[ast.AST, ast.AST], enclosing: ast.AST, node: ast.AST
) -> ast.stmt:
    """The statement of ``enclosing``'s own flow (at any depth) that contains ``node``.

    Climbs from ``node`` until the next parent is ``enclosing``; a class body on
    the way is a statement of the enclosing function like any other.
    """
    current = node
    while parents.get(current) is not enclosing:
        current = parents[current]
    if not isinstance(current, ast.stmt):
        raise ValueError("a definition is a statement of its enclosing function")
    return current


def _top_level_statement(parents: Dict[ast.AST, ast.AST], node: ast.AST) -> ast.stmt:
    current = node
    while current in parents:
        current = parents[current]
    if not isinstance(current, ast.stmt):
        raise ValueError("a definition is a statement of its module")
    return current


_MODULE_BOUND_BEFORE: "WeakKeyDictionary[ast.AST, Dict[int, FrozenSet[str]]]" = WeakKeyDictionary()


def _module_names_bound_before(module: ast.Module, statement: ast.stmt) -> FrozenSet[str]:
    """Module names bound on every path before ``statement`` and never deleted.

    A binding inside ``try``, a branch without an else (``if TYPE_CHECKING``),
    a loop, or a ``match`` case is not on every path; an ``except ... as``
    name is unbound when its handler exits. A name a module-level ``del``
    removes, or that a function declares ``global`` and deletes, may be
    unbound whenever the function runs.
    """
    by_index = _MODULE_BOUND_BEFORE.get(module)
    if by_index is None:
        by_index = {}
        _MODULE_BOUND_BEFORE[module] = by_index
    index = next(i for i, item in enumerate(module.body) if item is statement)
    known = by_index.get(index)
    if known is not None:
        return known
    bound = definitely_bound_after(module.body[:index]) or set()
    deleted = set(_deleted_names(module.body))
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            declared = {
                name
                for item in walk_own_scope(node)
                if isinstance(item, ast.Global)
                for name in item.names
            }
            if declared:
                deleted |= declared & set(_deleted_names(node.body))
    result = frozenset(bound - deleted)
    by_index[index] = result
    return result


def passes_lambdas_through(expressions: Sequence[Tuple[int, ast.AST]]) -> bool:
    """Whether every argument of a parameter is a lambda that takes nothing.

    Only the unifier makes such a parameter (``Unifier._unify_thunks``), for
    a lambda each block hands to a call, makes at most once per run, and
    holds once. Making it at the call site instead is unobservable: a lambda
    that takes nothing evaluates nothing when it is made and cannot fail, it
    closes over the same variables of the same function as the block's, and
    its ``__qualname__`` is the one the block's had. So it is passed as it
    is; a thunk around it would be the ``lambda: __param_0()`` the unifier
    avoided.
    """
    return all(
        isinstance(expression, ast.Lambda)
        and not (
            expression.args.posonlyargs
            or expression.args.args
            or expression.args.kwonlyargs
            or expression.args.vararg
            or expression.args.kwarg
        )
        for _block_idx, expression in expressions
    )


def has_impure_eager_parameters(
    substitution: "Substitution", available: Sequence[AbstractSet[str]]
) -> bool:
    """Whether any eagerly passed parameter argument may be observable when hoisted.

    ``available`` gives, per block, the names its call site can resolve (see
    ``available_argument_names``). Lambda-lifted parameters and forwarded
    callees are evaluated inside the helper at the original position, so any
    expression is acceptable there, and a lambda passed through is made the
    same way at the call site (``passes_lambdas_through``). Call this after
    extraction, which is when callee parameters are known.
    """
    deferred = (
        set(substitution.function_params)
        | set(substitution.params_used_as_callee)
        | set(substitution.inlined_parameters)
    )
    return any(
        not is_eagerly_evaluable(expression, available[block_idx])
        for name, expressions in substitution.param_expressions.items()
        if name not in deferred and not passes_lambdas_through(expressions)
        for block_idx, expression in expressions
    )


def defer_impure_parameters(
    substitution: "Substitution",
    template_block: Iterable[ast.AST],
    available: Sequence[AbstractSet[str]],
) -> None:
    """Turn parameters whose arguments cannot be hoisted into zero-argument thunks.

    The helper then evaluates ``__param_n()`` at the original position, as often
    and as conditionally as the block did. ``available`` gives, per block, the
    names its call site can resolve. Parameters already lambda-lifted keep
    their arguments; parameters used only as callees are forwarded lazily by
    the extractor and need no thunk, and neither does a lambda passed through
    (``passes_lambdas_through``).
    """
    callees: Set[str] = set()
    for statement in template_block:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call):
                parameter = substitution.get_param_for_expr(0, node.func)
                if parameter is not None:
                    callees.add(parameter)
    for name, expressions in substitution.param_expressions.items():
        if (
            name in substitution.function_params
            or name in callees
            or passes_lambdas_through(expressions)
        ):
            continue
        if any(
            not is_eagerly_evaluable(expression, available[block_idx])
            for block_idx, expression in expressions
        ):
            substitution.function_params[name] = []


def thunk_reads_possibly_unbound_local(
    call: ast.AST, function: FunctionNode, available: AbstractSet[str]
) -> bool:
    """Whether a thunk in ``call`` reads a local of ``function`` that may be unbound.

    A thunk keeps a read where the block had it, so the name is looked up only
    on the path that reads it; that is why a local bound on some paths only is
    passed as one. The lookup is not the same, though. The block read the name
    as a local of its function, and an unbound local raises
    ``UnboundLocalError``; the thunk reads it as a free variable of a lambda,
    and an unfilled closure cell raises ``NameError``, with a different
    message. ``UnboundLocalError`` is the subclass, so a handler for it, or an
    ``isinstance`` test, no longer matches. No thunk can raise the original
    error without restating the interpreter's message, so such a call site is
    declined. A module name or an enclosing function's cell reads the same way
    from both places and is not a concern here.
    """
    local = _own_scope_locals(function)
    for node in ast.walk(call):
        if isinstance(node, ast.Lambda):
            reader = FreeNameCollector()
            reader.visit(node)
            if (reader.used & local) - available:
                return True
    return False


def builtins_passed(
    call: ast.AST,
    helper_name: str,
    function: FunctionNode,
    analyzer: "ScopeAnalyzer",
    *,
    permitted: AbstractSet[int] = frozenset(),
) -> FrozenSet[str]:
    """The builtins that ``call``, standing in ``function``, hands the helper ``helper_name``.

    No helper takes a builtin as a parameter: ``helper(rows, len)`` would
    surprise every reader. A builtin is handed over when an argument, or the
    value a lambda argument returns, is a name that the site reads from the
    builtins, bare or inside a literal tuple, list, set or dict:
    ``lambda: __import__`` and ``(int, str)`` are, ``lambda: len(rows)``
    is not, since it hands over what ``len`` computed. A name the site's
    function or an enclosing one binds is its own local and is not a
    builtin; one the site's module binds is counted all the same, being
    spelled as a builtin. The positional arguments at ``permitted`` are not
    counted: ``parameterize_builtins`` lets a site pass a builtin there, as
    its binding of a free variable (``free_variable_positions``).
    """
    handed: Set[str] = set()
    for node in ast.walk(call):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == helper_name:
                arguments = [
                    argument for index, argument in enumerate(node.args) if index not in permitted
                ]
                for argument in [*arguments, *(keyword.value for keyword in node.keywords)]:
                    handed |= _builtin_values(argument, frozenset())
    return module_resolved_names(function, analyzer, handed) if handed else frozenset()


def free_variable_positions(
    param_order: Mapping[str, int], substitution: "Substitution"
) -> FrozenSet[int]:
    """Where a generated call passes a free variable of the blocks, as its site spells it.

    A parameter the substitution does not map is a free variable passed
    eagerly; one it maps to the same bare name at every site is a free
    variable passed as a thunk, because a site may not have bound it by the
    time of the call (``_thunk_uncertain_free_variables``). Every other
    parameter is a difference between the blocks, passed as a value or a
    thunk of one.
    """
    positions: Set[int] = set()
    for name, index in param_order.items():
        expressions = substitution.param_expressions.get(name)
        if expressions is None:
            positions.add(index)
            continue
        spellings = {
            expression.id if isinstance(expression, ast.Name) else None
            for _, expression in expressions
        }
        if len(spellings) == 1 and None not in spellings:
            positions.add(index)
    return frozenset(positions)


def _builtin_values(node: ast.expr, shadowed: FrozenSet[str]) -> Set[str]:
    """The builtin spellings that are ``node``'s value or its literal container's members."""
    if isinstance(node, ast.Lambda):
        return _builtin_values(node.body, shadowed | frozenset(parameter_names(node.args)))
    if isinstance(node, ast.Name):
        return {node.id} if node.id in BUILTIN_NAMES and node.id not in shadowed else set()
    if isinstance(node, ast.Starred):
        return _builtin_values(node.value, shadowed)
    members: List[ast.expr] = []
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        members = list(node.elts)
    elif isinstance(node, ast.Dict):
        members = [*(key for key in node.keys if key is not None), *node.values]
    return set().union(*(_builtin_values(member, shadowed) for member in members))


def _own_scope_locals(function: FunctionNode) -> Set[str]:
    """The names that are locals of ``function``'s own scope, exactly.

    ``locally_bound_names`` over-approximates on purpose and counts a
    comprehension's loop variable, which is a local of the comprehension: a
    read of that name in the function is a global lookup, and reads the same
    from a thunk. A walrus inside a comprehension does bind in the function,
    and a ``global`` or ``nonlocal`` declaration makes a name no local at all.
    """
    names: Set[str] = set(parameter_names(function.args))
    declared: Set[str] = set()
    pending: List[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            # What a definition evaluates where it stands runs in this scope:
            # decorators, defaults, annotations, bases and keywords.
            pending.extend(node.decorator_list)
            if isinstance(node, ast.ClassDef):
                pending.extend([*node.bases, *node.keywords])
            else:
                pending.extend([node.args, *([node.returns] if node.returns else [])])
            continue  # a nested scope binds its own names
        if isinstance(node, ast.Lambda):
            pending.append(node.args)
            continue
        if isinstance(node, ast.comprehension):
            pending.extend([node.iter, *node.ifs])  # the target is the comprehension's own
            continue
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(import_binding_names(node))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.match_case):
            names.update(pattern_capture_names(node.pattern))
        pending.extend(ast.iter_child_nodes(node))
    return names - declared


def moves_scope_declaration(function: FunctionNode, nodes: Iterable[ast.AST]) -> bool:
    """Whether the block carries a ``global``/``nonlocal`` declaration the caller still needs.

    A declaration inside the block moves into the helper with it. Any remaining
    use of that name in the caller then silently becomes a local access. Only
    declarations at the block's own scope count; a nested function's own
    declarations move with that function.
    """
    block = tuple(nodes)
    extracted = {child for statement in block for child in ast.walk(statement)}
    declared: Set[str] = set()
    for statement in block:
        for node in walk_own_scope(statement):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                declared.update(node.names)
    if not declared:
        return False
    for node in ast.walk(function):
        if node in extracted or node is function:
            continue
        if isinstance(node, ast.Name) and node.id in declared:
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and declared & set(node.names):
            return True
    return False


def walk_own_scope(node: ast.AST) -> Iterable[ast.AST]:
    """Yield nodes of ``node`` without entering nested function or class scopes."""
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(current))

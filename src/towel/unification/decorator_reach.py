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

"""Whether everything that can reach a function's body is known to leave the body alone.

Some decorators compile or instrument the body they decorate. typeguard's
``@typechecked`` recompiles the function from its source with a check after
every annotated assignment; numba's ``@njit`` compiles it in nopython mode. A
block moved out of such a function into a plain helper is no longer checked
or compiled: the check stops raising, or the kernel stops compiling. A helper
placed inside such a function, or in a class whose decorator instruments
every method, is instrumented where the code it replaced was not.

So a block is extracted, and a call site placed, only where every decorator
that can reach the code is known to leave the body alone, and a helper is
hosted only where the same holds for its host. The decorators that can reach
a function's code are its own, those of every function enclosing it, and
those of every class enclosing it. A decorator is known when the name it is
spelled with resolves, through the module's own bindings, to one of
``KNOWN_DECORATORS``, each entry read in the library's own source; or to a
function the project defines that :class:`_Resolver` can show is a plain
wrapper. Anything else declines, and :class:`DecoratorRefusal` names the
decorator.

Names are resolved by binding, never by spelling: ``from functools import
wraps as w`` makes ``@w(f)`` ``functools.wraps``, and a ``property`` the
module or class binds itself is not the builtin. A module-level name counts
only when every binding the module could give it is known, so which one is
in effect when the decorator runs never matters. A name bound in an
enclosing function is not followed. A decorator factory such as
``@pytest.mark.parametrize(...)`` resolves through the callee of its call.
A project module that takes the name of a third-party library in the list is
read as the project's code; one that takes a standard-library module's name
is taken to be the standard library, as everywhere else in Towel.

A decorator applied by hand reaches the body as surely as one written with
``@``: ``fast = numba.njit(kernel)``, ``f = typechecked(f)``, ``method =
wrap(method)`` in a class body, or ``njit(cache=True)(kernel)``. So every call
in the value of an assignment at module or class level, in any module of the
project, counts as applying its callee to each definition an argument of it
names, and is judged as that decorator would be. And a class's machinery
reaches every method: a metaclass or an ``__init_subclass__`` may wrap or
recompile them as the class is built. So every class enclosing the code must
pass the test a class that takes a method helper passes
(:meth:`ImportTimeCode.hosts_method_helpers`).
"""

from __future__ import annotations

import ast
import builtins
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Dict,
    FrozenSet,
    Iterator,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)
from weakref import WeakKeyDictionary

from ..consumers import MAXIMUM_FILES, SKIPPED_DIRECTORIES
from ..import_model import NameStatus
from ..source_text import read_source
from .bounded_cache import BoundedCache
from .exceptions import ProjectScanLimitError
from .import_graph import (
    _HOSTS_METHOD_HELPERS,
    ImportGraphCache,
    ImportTimeCode,
    imported_definition_sites,
)
from .known_decorators import (
    DecoratedKind,
    Form,
    KnownDecorator,
    known_decorator,
)
from .module_bindings import ModuleBindings, dotted_name, global_bindings
from .statement_facts import bindings_of

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]
Definition = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True, eq=False)
class ModuleSource:
    """A module as an analysis holds it: its path, its text, and the tree parsed from that text."""

    path: str
    source: str
    tree: ast.Module


RefusalKind = Literal["decorator", "machinery"]


@dataclass(frozen=True)
class DecoratorRefusal:
    """Something that can reach some code and is not known to leave its body alone.

    A decorator, written with ``@`` or applied by a call an assignment makes
    (``site`` then says where), or the machinery of an enclosing class.
    """

    decorator: str
    """The decorator's absolute name when it resolves to one, as the module spells it
    otherwise; for class machinery, what runs it: ``metaclass Meta``,
    ``__init_subclass__ of Base``."""
    holder: str
    """The definition it reaches: ``parse_record``, or ``class Model``."""
    kind: RefusalKind = "decorator"
    site: Optional[str] = None
    """``module.py:12``, the call applying a decorator by hand."""

    @property
    def detail(self) -> str:
        """``@typeguard.typechecked on parse_record``, and the like."""
        if self.kind == "machinery":
            return f"{self.decorator} builds {self.holder}"
        if self.site is not None:
            return f"{self.decorator}(...) at {self.site} applied to {self.holder}"
        return f"@{self.decorator} on {self.holder}"


# -- Where each definition sits -------------------------------------------------


@dataclass(frozen=True)
class _Slot:
    """Where a definition sits: the definition whose body holds it, and the statement there."""

    owner: Optional[Definition]
    """None for a definition in the module's own body."""
    index: int
    """The index, in the owner's (or the module's) body, of the statement holding it."""
    direct: bool
    """The definition is that statement itself, not within a compound statement."""


_LAYOUTS: "WeakKeyDictionary[ast.Module, Mapping[int, _Slot]]" = WeakKeyDictionary()


def _definitions_held(statement: ast.stmt) -> Iterator[Tuple[Definition, bool]]:
    """The definitions a statement holds in its own body: itself, or those its suites hold."""
    if isinstance(statement, _DEFINITIONS):
        yield statement, True
        return
    pending: List[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEFINITIONS):
                yield child, False
            elif isinstance(child, (ast.stmt, ast.excepthandler, ast.match_case)):
                pending.append(child)


def _layout(tree: ast.Module) -> Mapping[int, _Slot]:
    """Every definition of ``tree`` by ``id``, with where it sits; computed once per tree."""
    known = _LAYOUTS.get(tree)
    if known is not None:
        return known
    slots: Dict[int, _Slot] = {}
    pending: List[Tuple[Sequence[ast.stmt], Optional[Definition]]] = [(tree.body, None)]
    while pending:
        body, owner = pending.pop()
        for index, statement in enumerate(body):
            for definition, direct in _definitions_held(statement):
                slots[id(definition)] = _Slot(owner, index, direct)
                pending.append((definition.body, definition))
    _LAYOUTS[tree] = slots
    return slots


_SCOPE_NAMES: "WeakKeyDictionary[ast.AST, FrozenSet[str]]" = WeakKeyDictionary()


def _function_scope_names(function: FunctionNode) -> FrozenSet[str]:
    """Every name ``function``'s own scope may bind, or hands to another scope by declaration."""
    known = _SCOPE_NAMES.get(function)
    if known is not None:
        return known
    arguments = function.args
    names: Set[str] = {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *((arguments.vararg,) if arguments.vararg else ()),
            *((arguments.kwarg,) if arguments.kwarg else ()),
        )
    }
    for statement in function.body:
        names |= bindings_of(statement, into_nested_scopes=False)
        names |= {
            name
            for node in ast.walk(statement)
            if isinstance(node, (ast.Global, ast.Nonlocal))
            for name in node.names
        }
    _SCOPE_NAMES[function] = frozenset(names)
    return _SCOPE_NAMES[function]


# -- What a name can denote -----------------------------------------------------


@dataclass(frozen=True, eq=False)
class _Module:
    """A module this resolution reads: its path, text and tree, what its top level binds, its layout."""

    path: str
    source: str
    tree: ast.Module
    bindings: ModuleBindings
    layout: Mapping[int, _Slot]

    @property
    def key(self) -> str:
        """The module's file, however the path to it is spelled."""
        return os.path.realpath(self.path)


@dataclass(frozen=True)
class _Origin:
    """A name that denotes an absolute dotted name: an import, or a builtin."""

    dotted: str
    module: _Module
    spelled: str
    """How ``module`` spells it, which is what an import there resolves."""
    sole_binding: bool
    """The spelling's first name has this one binding in ``module``."""


@dataclass(frozen=True)
class _ProjectDef:
    """A name that denotes a function the project defines, at module level or in a class body."""

    module: _Module
    node: FunctionNode


@dataclass(frozen=True)
class _CallResult:
    """A name that holds what a call returned: the decorator that ``call`` of ``callee`` makes."""

    callee: Union[_Origin, _ProjectDef]
    call: ast.Call


_Denotation = Union[_Origin, _ProjectDef, _CallResult]


@dataclass(frozen=True, eq=False)
class _Application:
    """A callable reaching a definition: one of its decorators, or a call applying one by hand.

    ``callee`` names the callable, read in ``module`` where ``slot`` sits.
    ``expression`` is what an entry's reading must cover: the decorator
    itself, the factory call whose result is applied, or the call that
    applies the callable to the definition among other arguments.
    """

    callee: ast.expr
    expression: ast.expr
    form: Form
    module: _Module
    slot: _Slot
    site: Optional[str] = None


_Target = Tuple[str, str]
"""A definition by the file it is in and its qualified name there: ``(".../m.py", "C.m")``."""


@dataclass(frozen=True)
class _HandIndex:
    """Every call a module or class body of the project assigns, by the definitions it is given.

    ``by_name`` holds the calls given a name that could not be followed to
    its definition, under the name's last part, and is matched by name.
    """

    by_target: Mapping[_Target, Tuple[_Application, ...]]
    by_name: Mapping[str, Tuple[_Application, ...]]
    complete: bool


_BUILTIN_NAMES = frozenset(vars(builtins))
_PROPERTY_ACCESSORS = frozenset({"setter", "getter", "deleter"})
_STANDARD_MODULES = frozenset(sys.stdlib_module_names) | {"builtins"}
_MOST_HOPS = 6

# Attributes of a function a plain decorator may read: strings describing it.
_DESCRIPTIVE_ATTRIBUTES = frozenset({"__name__", "__qualname__", "__module__", "__doc__"})

# What each kind of registry container may be asked to do with the function.
_REGISTRY_METHODS: Mapping[str, Mapping[str, int]] = {
    "dict": {"setdefault": 2},
    "list": {"append": 1, "insert": 2},
    "set": {"add": 1},
}

_Stamp = Tuple[str, int, int]


@dataclass(frozen=True)
class _Memo:
    refusal: Optional[DecoratorRefusal]
    stamps: Tuple[_Stamp, ...]
    """Every other module the answer read, with its modification time and size then."""
    cache: int
    """The ``id`` of the import-graph cache the answer came from: one engine's."""


@dataclass(frozen=True)
class _PlainMemo:
    plain: bool
    stamps: Tuple[_Stamp, ...]


_REFUSALS: "WeakKeyDictionary[ast.AST, _Memo]" = WeakKeyDictionary()
_PLAIN: "WeakKeyDictionary[ast.AST, Dict[Form, _PlainMemo]]" = WeakKeyDictionary()
_LOADED: BoundedCache[_Stamp, Optional[_Module]] = BoundedCache(256)
_HAND_CALLS: "WeakKeyDictionary[ast.Module, Tuple[Tuple[_Application, str], ...]]" = (
    WeakKeyDictionary()
)
# The project's hand applications, per engine (its import-graph cache) and
# project root. Towel never writes a module-level or class-level assignment,
# so what the index holds stays true while the engine rewrites the project.
_HAND_INDEXES: "WeakKeyDictionary[ImportGraphCache, Dict[str, _HandIndex]]" = WeakKeyDictionary()
_CODES: "WeakKeyDictionary[ast.Module, Tuple[int, Optional[ImportTimeCode]]]" = WeakKeyDictionary()
_MACHINERY: "WeakKeyDictionary[ast.AST, Tuple[int, Optional[str]]]" = WeakKeyDictionary()


def _stamp(path: str) -> Optional[_Stamp]:
    try:
        status = os.stat(path)
    except OSError:
        return None
    return (path, status.st_mtime_ns, status.st_size)


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _holder(definition: Definition) -> str:
    return f"class {definition.name}" if isinstance(definition, ast.ClassDef) else definition.name


def _spelling(node: ast.expr) -> str:
    text = ast.unparse(node)
    return text if len(text) <= 60 else text[:57] + "..."


def decorator_refusal(
    definition: Definition, module: ModuleSource, cache: ImportGraphCache
) -> Optional[DecoratorRefusal]:
    """The first decorator reaching ``definition``'s body not known to leave it alone, if any.

    The decorators of ``definition`` itself come first, then the calls
    applying one to it by hand, then, for a class, its machinery; then the
    same for each definition enclosing it, innermost first. ``module`` holds the tree
    ``definition`` belongs to; ``cache`` answers where an import leads. The
    answer is remembered per definition, together with every other module
    it read, and computed again once one of those changes.
    """
    memo = _REFUSALS.get(definition)
    if (
        memo is not None
        and memo.cache == id(cache)
        and all(_stamp(stamp[0]) == stamp for stamp in memo.stamps)
    ):
        return memo.refusal
    resolver = _Resolver(cache)
    refusal = resolver.chain_refusal(definition, module)
    _REFUSALS[definition] = _Memo(refusal, tuple(sorted(resolver.stamps)), id(cache))
    return refusal


def class_named(tree: ast.Module, name: str) -> Optional[ast.ClassDef]:
    """The one class statement of ``tree``'s own body named ``name``, if there is exactly one."""
    found = [
        statement
        for statement in tree.body
        if isinstance(statement, ast.ClassDef) and statement.name == name
    ]
    return found[0] if len(found) == 1 else None


class _Resolver:
    """Resolves decorators for one question, recording every other module it reads."""

    def __init__(self, cache: ImportGraphCache) -> None:
        self._cache = cache
        self.stamps: Set[_Stamp] = set()

    # -- the chain ---------------------------------------------------------------

    def chain_refusal(
        self, definition: Definition, source: ModuleSource
    ) -> Optional[DecoratorRefusal]:
        bindings = global_bindings(source.source)
        if bindings is None:
            return DecoratorRefusal("(module does not parse)", _holder(definition))
        module = _Module(source.path, source.source, source.tree, bindings, _layout(source.tree))
        index = self._hand_index(module)
        if not index.complete:
            return DecoratorRefusal("(project too large to read whole)", _holder(definition))
        chain: List[Tuple[Definition, _Slot]] = []
        node: Optional[Definition] = definition
        while node is not None:
            slot = module.layout.get(id(node))
            if slot is None:
                return DecoratorRefusal("(definition outside its module)", _holder(node))
            chain.append((node, slot))
            node = slot.owner
        for node, slot in chain:
            for application in self._applications(node, slot, module, index):
                refused = self._refused(application, node)
                if refused is not None:
                    return DecoratorRefusal(refused, _holder(node), site=application.site)
        for node, _ in chain:
            if isinstance(node, ast.ClassDef):
                machinery = self._machinery_refused(node, module)
                if machinery is not None:
                    return DecoratorRefusal(machinery, _holder(node), kind="machinery")
        return None

    def _applications(
        self, node: Definition, slot: _Slot, module: _Module, index: _HandIndex
    ) -> List[_Application]:
        """Every callable applied to ``node``: its decorators, then the calls given it by hand."""
        found = [
            _Application(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                decorator,
                "called" if isinstance(decorator, ast.Call) else "bare",
                module,
                slot,
            )
            for decorator in node.decorator_list
        ]
        found.extend(index.by_name.get(node.name, ()))
        identity = _identity(node, module)
        if identity is not None:
            found.extend(index.by_target.get(identity, ()))
        return found

    def _refused(self, application: _Application, decorated: Definition) -> Optional[str]:
        """The name to report ``application`` by when it is not known; None when it is."""
        spelled = dotted_name(application.callee)
        if spelled is None:
            return _spelling(application.callee)
        denotations = self._denotations(spelled, application.slot, application.module)
        if denotations is None:
            return spelled
        for denotation in _in_order(denotations):
            refused = self._denotation_refused(denotation, application, decorated, spelled)
            if refused is not None:
                return refused
        return None

    def _denotation_refused(
        self,
        denotation: _Denotation,
        application: _Application,
        decorated: Definition,
        spelled: str,
        depth: int = 0,
    ) -> Optional[str]:
        form = application.form
        if isinstance(denotation, _CallResult):
            # Applied bare, the name is the call's decorator; called again, it is
            # whatever that decorator returns, which nothing here has read.
            if form != "bare" or depth > _MOST_HOPS:
                return spelled
            made = _Application(
                denotation.call.func,
                denotation.call,
                "called",
                application.module,
                application.slot,
            )
            return self._denotation_refused(denotation.callee, made, decorated, spelled, depth + 1)
        if isinstance(denotation, _ProjectDef):
            return None if form != "applied" and self._plain(denotation, form) else spelled
        kind: DecoratedKind = "class" if isinstance(decorated, ast.ClassDef) else "function"
        entry = known_decorator(denotation.dotted)
        top = denotation.dotted.partition(".")[0]
        if entry is not None and top in _STANDARD_MODULES:
            return None if _covers(entry, application, kind) else denotation.dotted
        if top in _STANDARD_MODULES or depth > _MOST_HOPS:
            return denotation.dotted
        external = self._is_external(denotation.module, top)
        if external is None:
            return denotation.dotted
        if external:
            # No module of the project takes the name: it is the installed
            # library an entry was read in, or a decorator nobody read.
            return (
                None
                if entry is not None and _covers(entry, application, kind)
                else denotation.dotted
            )
        # A module of the project: read as the project's code, entry or not.
        sites = self._project_sites(denotation.module, denotation.spelled)
        if not denotation.sole_binding or not sites:
            return denotation.dotted
        for site_module, qualname in sites:
            found = self._module_denotations(site_module, qualname, depth + 1)
            if found is None:
                return denotation.dotted
            for inner in _in_order(found):
                if self._denotation_refused(inner, application, decorated, spelled, depth + 1):
                    return denotation.dotted
        return None

    def _is_external(self, module: _Module, top: str) -> Optional[bool]:
        """Whether the program's imports find no module of the project named ``top``; None if unknown."""
        try:
            program = self._cache.program_for(Path(module.path))
        except ProjectScanLimitError:
            return None
        info = program.model.names.get(top)
        return None if info is None else info.status is NameStatus.EXTERNAL

    # -- names -------------------------------------------------------------------

    def _denotations(
        self, dotted: str, slot: _Slot, module: _Module
    ) -> Optional[FrozenSet[_Denotation]]:
        """Everything ``dotted`` may denote when read where ``slot`` sits.

        A decorator is read in the scope holding the definition, a call an
        assignment makes in the scope holding the assignment: a class body
        sees its own earlier bindings, a function its locals (which are not
        followed), and every scope past the first skips class bodies, as
        Python does.
        """
        head = dotted.partition(".")[0]
        owner, first = slot.owner, True
        while owner is not None:
            if isinstance(owner, ast.ClassDef):
                if first:
                    bound, local = self._class_body_denotations(dotted, owner, slot, module)
                    if bound:
                        return local
            elif head in _function_scope_names(owner):
                return None
            first = False
            owner = module.layout[id(owner)].owner
        return self._module_denotations(module, dotted, 0)

    def _class_body_denotations(
        self, dotted: str, klass: ast.ClassDef, slot: _Slot, module: _Module
    ) -> Tuple[bool, Optional[FrozenSet[_Denotation]]]:
        """Whether ``klass``'s body binds ``dotted``'s first name where ``slot`` sits, and to what.

        The class body runs in order, so the binding is the last statement
        of the body before the definition that binds the name. Only a
        function defined there, or the accessor of a ``property`` defined
        there, is known.
        """
        head, _, rest = dotted.partition(".")
        if not slot.direct and head in bindings_of(
            klass.body[slot.index], into_nested_scopes=False
        ):
            return True, None
        binders = [
            statement
            for statement in klass.body[: slot.index]
            if head in bindings_of(statement, into_nested_scopes=False)
        ]
        if not binders:
            return False, None
        last = binders[-1]
        if not isinstance(last, _FUNCTIONS) or last.name != head:
            return True, None
        if not rest:
            return True, frozenset({_ProjectDef(module, last)})
        if rest in _PROPERTY_ACCESSORS and self._is_property(last, module):
            return True, frozenset({_Origin(f"builtins.property.{rest}", module, dotted, True)})
        return True, None

    def _is_property(self, function: FunctionNode, module: _Module) -> bool:
        """Whether the outermost decorator of a class-body function makes its name a ``property``."""
        if not function.decorator_list:
            return False
        outer = function.decorator_list[0]
        spelled = dotted_name(outer)
        if spelled is None:
            return False
        found = self._denotations(spelled, module.layout[id(function)], module)
        if found is None or len(found) != 1:
            return False
        (only,) = found
        return isinstance(only, _Origin) and only.dotted in {
            "builtins.property",
            *(f"builtins.property.{accessor}" for accessor in _PROPERTY_ACCESSORS),
        }

    def _module_denotations(
        self, module: _Module, dotted: str, depth: int
    ) -> Optional[FrozenSet[_Denotation]]:
        """Everything ``dotted`` may denote in ``module``'s namespace, on any path through it.

        Every binding the module could give the first name counts, and a
        builtin of that name counts as well, since a path may leave the
        module's own binding unmade. None when some binding is not a known
        kind: an import, a plain ``alias = a.b``, a function the module
        defines, or a name assigned the result of a call
        (``needs_db = pytest.mark.skipif(...)``), which is that call's
        decorator.
        """
        if depth > _MOST_HOPS:
            return None
        bindings = module.bindings
        head, _, rest = dotted.partition(".")
        if (
            bindings.star_imports
            or "__builtins__" in bindings.bindings
            or "__builtins__" in bindings.rebound_by_global
            or head in bindings.rebound_by_global
        ):
            return None
        events = bindings.bindings.get(head, ())
        sole = len(events) == 1
        found: Set[_Denotation] = set()
        others: Dict[int, int] = {}
        if head in _BUILTIN_NAMES:
            found.add(_Origin(f"builtins.{dotted}", module, dotted, not events))
        for event in events:
            if event.is_import and event.origin is not None:
                target = f"{event.origin}.{rest}" if rest else event.origin
                found.add(_Origin(target, module, dotted, sole))
            elif event.is_import:
                if not sole:
                    return None
                sites = self._project_sites(module, dotted)
                if not sites:
                    return None
                for site_module, qualname in sites:
                    inner = self._module_denotations(site_module, qualname, depth + 1)
                    if inner is None:
                        return None
                    found |= inner
            elif event.origin is not None:
                target = f"{event.origin}.{rest}" if rest else event.origin
                inner = self._module_denotations(module, target, depth + 1)
                if inner is None:
                    return None
                found |= inner
            else:
                others[event.order] = others.get(event.order, 0) + 1
        for order, count in sorted(others.items()):
            held = self._statement_denotations(
                module, module.tree.body[order], dotted, count, depth
            )
            if held is None:
                return None
            found |= held
        return frozenset(found) if found else None

    def _statement_denotations(
        self, module: _Module, statement: ast.stmt, dotted: str, count: int, depth: int
    ) -> Optional[FrozenSet[_Denotation]]:
        """What the ``count`` bindings of ``dotted``'s name in one top-level statement denote.

        Each must be a ``def`` of the name, or an assignment of a call to it;
        a ``def`` in either branch of an ``if`` or a ``try`` is one of the
        possibilities, like an import there.
        """
        head, _, rest = dotted.partition(".")
        if rest:
            return None
        definitions = [
            node
            for node, _ in _definitions_held(statement)
            if isinstance(node, _FUNCTIONS) and node.name == head
        ]
        calls = [
            node.value
            for node in _statements_held(statement)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == head
            and isinstance(node.value, ast.Call)
        ]
        if len(definitions) + len(calls) != count:
            return None
        found: Set[_Denotation] = {_ProjectDef(module, node) for node in definitions}
        for call in calls:
            callee = dotted_name(call.func)
            inner = None if callee is None else self._module_denotations(module, callee, depth + 1)
            if inner is None:
                return None
            for denotation in inner:
                if isinstance(denotation, _CallResult):
                    return None  # The result of a call's result: nothing here read it.
                found.add(_CallResult(denotation, call))
        return frozenset(found)

    def _project_sites(
        self, module: _Module, spelled: str
    ) -> Optional[Tuple[Tuple[_Module, str], ...]]:
        """The project modules and names an import of ``spelled`` in ``module`` may reach.

        Empty when the import names no module of the project, None when
        where it leads is not known. ``from m import x`` may denote a
        submodule or an attribute; where both exist, a module that never
        binds the attribute is left out, since Python then imports the
        submodule.
        """
        sites = imported_definition_sites(module.path, spelled, self._cache)
        if sites is None:
            return None
        loaded: List[Tuple[_Module, str]] = []
        for path, qualname in sorted(sites):
            site = self._load(str(path))
            if site is None:
                return None
            loaded.append((site, qualname))
        if len(loaded) > 1:
            loaded = [
                (site, qualname)
                for site, qualname in loaded
                if site.bindings.may_bind(qualname.partition(".")[0])
            ]
            if not loaded:
                return None
        return tuple(loaded)

    def _load(self, path: str) -> Optional[_Module]:
        stamp = _stamp(path)
        if stamp is None:
            return None
        self.stamps.add(stamp)
        if stamp in _LOADED:
            return _LOADED[stamp]
        try:
            source = read_source(path)
            tree = ast.parse(source)
        except (OSError, UnicodeError, SyntaxError, ValueError):
            return _LOADED.put(stamp, None)
        bindings = global_bindings(source)
        return _LOADED.put(
            stamp,
            None if bindings is None else _Module(path, source, tree, bindings, _layout(tree)),
        )

    # -- decorators applied by hand --------------------------------------------

    def _hand_index(self, module: _Module) -> _HandIndex:
        """The hand applications of ``module``'s project, read once per engine and project."""
        # The run's own cache of project roots: every pair asks, once per call site.
        root = os.path.realpath(self._cache.project_root(Path(module.path).resolve()))
        indexes = _HAND_INDEXES.setdefault(self._cache, {})
        index = indexes.get(root)
        if index is None:
            # A resolver of its own: what it reads is the whole project, and no
            # answer that consults the index depends on any one module of it.
            index = indexes[root] = _Resolver(self._cache)._read_hand_index(Path(root))
        return index

    def _read_hand_index(self, root: Path) -> _HandIndex:
        """Every hand application of the project under ``root``, by the definitions it is given.

        The directories the consumer scan skips are skipped here too; past its
        limit the project cannot be read whole, and the index says so.
        """
        by_target: Dict[_Target, List[_Application]] = {}
        by_name: Dict[str, List[_Application]] = {}
        count = 0
        for parent, directories, files in os.walk(root, onerror=lambda _: None):
            directories[:] = sorted(name for name in directories if name not in SKIPPED_DIRECTORIES)
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                count += 1
                if count > MAXIMUM_FILES:
                    return _HandIndex({}, {}, complete=False)
                module = self._load(os.path.join(parent, name))
                if module is None:
                    continue  # A module that does not parse applies nothing.
                for application, spelled in _hand_calls(module):
                    targets = self._argument_targets(module, spelled, application.slot)
                    if targets is None:
                        by_name.setdefault(spelled.rpartition(".")[2], []).append(application)
                        continue
                    for target in targets:
                        by_target.setdefault(target, []).append(application)
        return _HandIndex(
            {target: tuple(found) for target, found in by_target.items()},
            {name: tuple(found) for name, found in by_name.items()},
            complete=True,
        )

    def _argument_targets(
        self, module: _Module, spelled: str, slot: _Slot
    ) -> Optional[FrozenSet[_Target]]:
        """The project's definitions an argument spelled ``spelled`` may be, where ``slot`` reads it.

        Empty when it can be none of them (a builtin, an import from outside
        the project); None when that cannot be told, and the argument is then
        taken to be every definition of its name.
        """
        head = spelled.partition(".")[0]
        owner, first = slot.owner, True
        while owner is not None:
            if isinstance(owner, ast.ClassDef):
                if first:
                    bound, local = self._class_body_targets(spelled, owner, slot, module)
                    if bound:
                        return local
            elif head in _function_scope_names(owner):
                return None
            first = False
            owner = module.layout[id(owner)].owner
        return self._module_targets(module, spelled, 0)

    def _class_body_targets(
        self, spelled: str, klass: ast.ClassDef, slot: _Slot, module: _Module
    ) -> Tuple[bool, Optional[FrozenSet[_Target]]]:
        """Whether ``klass``'s body binds the argument's first name before ``slot``, and to what."""
        head, _, rest = spelled.partition(".")
        if not slot.direct and head in bindings_of(
            klass.body[slot.index], into_nested_scopes=False
        ):
            return True, None
        binders = [
            statement
            for statement in klass.body[: slot.index]
            if head in bindings_of(statement, into_nested_scopes=False)
        ]
        if not binders:
            return False, None
        last = binders[-1]
        if not isinstance(last, _DEFINITIONS) or last.name != head:
            return True, None
        identity = _identity(last, module)
        if identity is None:
            return True, None
        path, qualname = identity
        return True, frozenset({(path, f"{qualname}.{rest}" if rest else qualname)})

    def _module_targets(
        self, module: _Module, dotted: str, depth: int
    ) -> Optional[FrozenSet[_Target]]:
        """The project's definitions ``dotted`` may name in ``module``'s namespace, on any path."""
        if depth > _MOST_HOPS:
            return None
        bindings = module.bindings
        head, _, rest = dotted.partition(".")
        if bindings.star_imports or head in bindings.rebound_by_global:
            return None
        events = bindings.bindings.get(head, ())
        found: Set[_Target] = set()
        others: Dict[int, int] = {}
        for event in events:
            if event.is_import:
                sites = self._project_sites(module, dotted) if len(events) == 1 else None
                if sites is None:
                    return None
                for site_module, qualname in sites:
                    inner = self._module_targets(site_module, qualname, depth + 1)
                    if inner is None:
                        return None
                    found |= inner
            elif event.origin is not None:
                target = f"{event.origin}.{rest}" if rest else event.origin
                inner = self._module_targets(module, target, depth + 1)
                if inner is None:
                    return None
                found |= inner
            else:
                others[event.order] = others.get(event.order, 0) + 1
        for order, count in others.items():
            definitions = [
                node for node, _ in _definitions_held(module.tree.body[order]) if node.name == head
            ]
            if len(definitions) != count:
                return None
            found.add((module.key, dotted))
        return frozenset(found)

    # -- class machinery ---------------------------------------------------------

    def _machinery_refused(self, klass: ast.ClassDef, module: _Module) -> Optional[str]:
        """What may wrap or recompile ``klass``'s methods as it is built, if anything may.

        The verdict is the method-host test's, :meth:`ImportTimeCode.hosts_method_helpers`.
        It judges a class of the module's own body where it stands. A class
        anywhere else is judged only when it has no bases and no keywords,
        since nothing of it then resolves through the scope holding it: the
        test is asked of the same class statement standing alone, binding
        the names its body binds. Any other class is refused. The answer
        names what fails the test.
        """
        known = _MACHINERY.get(klass)
        if known is not None and known[0] == id(self._cache):
            return known[1]
        slot = module.layout[id(klass)]
        if slot.owner is None and slot.direct:
            code = self._import_time_code(module)
            passes = code is not None and code.hosts_method_helpers(klass.name)
        else:
            passes = not klass.bases and not klass.keywords and _passes_standing_alone(klass)
        refused = None if passes else self._machinery_culprit(klass, module, 0)
        _MACHINERY[klass] = (id(self._cache), refused)
        return refused

    def _import_time_code(self, module: _Module) -> Optional[ImportTimeCode]:
        known = _CODES.get(module.tree)
        if known is not None and known[0] == id(self._cache):
            return known[1]
        try:
            code: Optional[ImportTimeCode] = ImportTimeCode(
                module.source, path=Path(module.path), cache=self._cache
            )
        except (SyntaxError, ValueError):
            code = None
        _CODES[module.tree] = (id(self._cache), code)
        return code

    def _machinery_culprit(self, klass: ast.ClassDef, module: _Module, depth: int) -> str:
        """What fails the method-host test for ``klass``: its metaclass, a member, or a base's.

        Only names the failure; the verdict is :meth:`_machinery_refused`'s. A
        base of the project is followed to the class that fails.
        """
        slot = module.layout[id(klass)]
        machinery = _HOSTS_METHOD_HELPERS
        if (slot.owner is not None or not slot.direct) and (klass.bases or klass.keywords):
            return f"class {klass.name} (not at module level, with bases)"
        for keyword in klass.keywords:
            if keyword.arg != "metaclass":
                return f"class keyword {keyword.arg}= of {klass.name}"
            found = self._denotations(dotted_name(keyword.value) or "?", slot, module)
            if not found or not all(
                isinstance(item, _Origin) and item.dotted in machinery.metaclasses for item in found
            ):
                return f"metaclass {_spelling(keyword.value)}"
        for member in sorted(machinery.forbidden_members):
            if any(
                member in bindings_of(statement, into_nested_scopes=False)
                for statement in klass.body
            ):
                return f"{member} of {klass.name}"
        for base in klass.bases:
            named = base.value if isinstance(base, ast.Subscript) else base
            spelled = dotted_name(named)
            if spelled is None:
                return f"base {_spelling(base)}"
            targets = self._argument_targets(module, spelled, slot)
            for path, qualname in sorted(targets or ()):
                other = self._load(path)
                inner = (
                    None if other is None or "." in qualname else class_named(other.tree, qualname)
                )
                if other is None or inner is None:
                    return f"base {spelled}"
                kept = machinery.ancestor_decorators
                for decorator in inner.decorator_list:
                    callee = decorator.func if isinstance(decorator, ast.Call) else decorator
                    found = self._denotations(
                        dotted_name(callee) or "?", other.layout[id(inner)], other
                    )
                    if not found or not all(
                        isinstance(item, _Origin) and item.dotted in kept for item in found
                    ):
                        return f"decorator {_spelling(callee)} of {inner.name}"
                if depth < _MOST_HOPS and self._machinery_refused(inner, other) is not None:
                    return self._machinery_culprit(inner, other, depth + 1)
            if targets:
                continue
            found = self._denotations(spelled, slot, module)
            origins = {item.dotted for item in found or () if isinstance(item, _Origin)}
            accepted = machinery.bases | machinery.subscripted_bases | machinery.builtin_bases
            if not found or not origins or not origins <= accepted:
                return f"base {min(origins) if origins else spelled}"
        return f"bases of {klass.name}"

    # -- decorators the project defines ------------------------------------------

    def _plain(self, denotation: _ProjectDef, form: Form) -> bool:
        """Whether the project's function is a plain decorator (bare) or a factory of one (called).

        Remembered per function, with the other modules the answer read.
        """
        verdicts = _PLAIN.setdefault(denotation.node, {})
        known = verdicts.get(form)
        if known is not None and all(_stamp(stamp[0]) == stamp for stamp in known.stamps):
            self.stamps.update(known.stamps)
            return known.plain
        reader = _Resolver(self._cache)
        node = denotation.node
        plain = (
            _PlainDecorator(reader, denotation.module, node, ()).holds()
            if form == "bare"
            else reader._plain_factory(denotation.module, node)
        )
        verdicts[form] = _PlainMemo(plain, tuple(sorted(reader.stamps)))
        self.stamps.update(reader.stamps)
        return plain

    def _plain_factory(self, module: _Module, factory: FunctionNode) -> bool:
        """Whether ``factory`` returns only nested functions each of which is a plain decorator.

        The nested function is named nowhere else in the factory, so nothing
        but the decorator's own caller ever receives it.
        """
        if isinstance(factory, ast.AsyncFunctionDef) or factory.decorator_list:
            return False
        if not factory.body or not isinstance(factory.body[-1], ast.Return):
            return False
        own = list(_own_scope(factory))
        if any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in own):
            return False
        nested = {
            node.name: node
            for node in own
            if isinstance(node, ast.FunctionDef) and _binds_once(factory, node.name)
        }
        returns = [node for node in own if isinstance(node, ast.Return)]
        if not all(isinstance(ret.value, ast.Name) and ret.value.id in nested for ret in returns):
            return False
        returned = {ret.value.id for ret in returns if isinstance(ret.value, ast.Name)}
        returned_values = {id(ret.value) for ret in returns}
        if any(
            isinstance(node, ast.Name) and node.id in returned and id(node) not in returned_values
            for node in ast.walk(factory)
        ):
            return False
        return all(
            _PlainDecorator(self, module, nested[name], (factory,)).holds() for name in returned
        )

    def resolves_to(
        self, module: _Module, dotted: str, scopes: Sequence[FunctionNode], target: str
    ) -> bool:
        """Whether ``dotted``, read inside ``scopes`` (innermost first), can only be ``target``."""
        head = dotted.partition(".")[0]
        if any(head in _function_scope_names(scope) for scope in scopes):
            return False
        found = self._module_denotations(module, dotted, 0)
        return bool(found) and all(
            isinstance(item, _Origin) and item.dotted == target for item in found or ()
        )


def _in_order(denotations: FrozenSet[_Denotation]) -> List[_Denotation]:
    """``denotations`` in an order that makes the first refusal reported the same every run."""

    def key(item: _Denotation) -> Tuple[int, str, int]:
        if isinstance(item, _CallResult):
            return (2, *key(item.callee)[1:])
        if isinstance(item, _Origin):
            return (0, item.dotted, 0)
        return (1, item.module.path, item.node.lineno)

    return sorted(denotations, key=key)


def _statements_held(statement: ast.stmt) -> Iterator[ast.stmt]:
    """``statement`` and every statement its suites hold, not those of nested definitions."""
    pending: List[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.stmt):
            yield node
            if isinstance(node, _DEFINITIONS):
                continue
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.stmt, ast.excepthandler, ast.match_case)):
                pending.append(child)


def _passes_standing_alone(klass: ast.ClassDef) -> bool:
    """The method-host test of a class with no bases and no keywords, asked of it standing alone.

    Such a class is built by ``type`` and has only ``object`` after it on
    its order, wherever it is written, so only the names its body binds
    decide, and a module holding just the class statement, binding them,
    asks the test exactly that.
    """
    bound = sorted(
        {
            name
            for statement in klass.body
            for name in bindings_of(statement, into_nested_scopes=False)
        }
    )
    lines = [f"    {name} = None" for name in bound] or ["    pass"]
    return ImportTimeCode("\n".join(["class _Standing:", *lines, ""])).hosts_method_helpers(
        "_Standing"
    )


def _qualified_names(definition: Definition, module: _Module) -> List[str]:
    """The names of ``definition`` and every definition enclosing it, outermost first."""
    names = [definition.name]
    owner = module.layout[id(definition)].owner
    while owner is not None:
        names.append(owner.name)
        owner = module.layout[id(owner)].owner
    return names[::-1]


def _identity(definition: Definition, module: _Module) -> Optional[_Target]:
    """``definition`` by its file and qualified name, when only classes enclose it.

    A definition inside a function is visible to no module-level or
    class-level assignment of another scope, and has no such name.
    """
    owner = module.layout[id(definition)].owner
    while owner is not None:
        if not isinstance(owner, ast.ClassDef):
            return None
        owner = module.layout[id(owner)].owner
    return module.key, ".".join(_qualified_names(definition, module))


def _hand_calls(module: _Module) -> Tuple[Tuple[_Application, str], ...]:
    """Every call in the value of an assignment at module or class level, with each name given it.

    Each argument, positional or keyword, spelled as a name or an attribute
    chain, is paired with the application the call makes of its callee:
    ``f(x)`` applies ``f`` bare, ``f(a)(x)`` applies the factory call
    ``f(a)``, and ``f(x, y)`` applies ``f`` to ``x`` among other arguments.
    Remembered per module tree.
    """
    known = _HAND_CALLS.get(module.tree)
    if known is not None:
        return known
    found: List[Tuple[_Application, str]] = []
    scopes: List[Tuple[Sequence[ast.stmt], Optional[ast.ClassDef]]] = [(module.tree.body, None)]
    scopes += [
        (node.body, node) for node in ast.walk(module.tree) if isinstance(node, ast.ClassDef)
    ]
    name = os.path.basename(module.path)
    for body, owner in scopes:
        for index, statement in enumerate(body):
            for held in _statements_held(statement):
                if not isinstance(held, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    continue
                if held.value is None:
                    continue
                slot = _Slot(owner, index, held is statement)
                for call in ast.walk(held.value):
                    if not isinstance(call, ast.Call):
                        continue
                    arguments = (*call.args, *(keyword.value for keyword in call.keywords))
                    for argument in arguments:
                        given = argument.value if isinstance(argument, ast.Starred) else argument
                        spelled = dotted_name(given)
                        if spelled is not None:
                            site = f"{name}:{call.lineno}"
                            found.append(
                                (_hand_application(call, argument, module, slot, site), spelled)
                            )
    _HAND_CALLS[module.tree] = tuple(found)
    return _HAND_CALLS[module.tree]


def _hand_application(
    call: ast.Call, argument: ast.expr, module: _Module, slot: _Slot, site: str
) -> _Application:
    """What ``call`` applies to its ``argument``: its callee bare, a factory's result, or among others."""
    if isinstance(call.func, ast.Call):
        return _Application(call.func.func, call.func, "called", module, slot, site)
    if len(call.args) == 1 and not call.keywords and call.args[0] is argument:
        return _Application(call.func, call.func, "bare", module, slot, site)
    return _Application(call.func, call, "applied", module, slot, site)


def _covers(entry: KnownDecorator, application: _Application, kind: DecoratedKind) -> bool:
    """Whether the reading behind ``entry`` covers ``application`` to a ``kind``."""
    if kind not in entry.decorates or application.form not in entry.forms:
        return False
    decorator = application.expression
    if not isinstance(decorator, ast.Call):
        return True
    if entry.refused_keywords and any(
        keyword.arg is None or keyword.arg in entry.refused_keywords
        for keyword in decorator.keywords
    ):
        return False
    if entry.most_positional is not None and (
        len(decorator.args) > entry.most_positional
        or any(isinstance(argument, ast.Starred) for argument in decorator.args)
    ):
        return False
    return True


# -- The plain-wrapper analysis -------------------------------------------------

_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _own_scope(function: FunctionNode) -> Iterator[ast.AST]:
    """Every node of ``function``'s body in its own scope; a nested definition's header included."""
    pending: List[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            header: List[ast.AST] = list(node.decorator_list)
            if isinstance(node, ast.ClassDef):
                header += [*node.bases, *(keyword.value for keyword in node.keywords)]
            else:
                header += [*node.args.defaults, *(d for d in node.args.kw_defaults if d)]
            pending.extend(header)
            continue
        if isinstance(
            node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            continue
        pending.extend(ast.iter_child_nodes(node))


def _binds_once(function: FunctionNode, name: str) -> bool:
    """Whether ``function``'s own scope binds ``name`` in exactly one statement, a ``def``."""
    count = 0
    for node in _own_scope(function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            count += node.name == name
        elif isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load):
            return False
        elif isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            if name in bindings_of(node, into_nested_scopes=False) or (
                isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names
            ):
                return False
        elif isinstance(node, ast.ExceptHandler) and node.name == name:
            return False
        elif isinstance(node, ast.match_case) and name in bindings_of(
            node, into_nested_scopes=False
        ):
            return False
    return count == 1


class _PlainDecorator:
    """Whether a function the project defines is a plain decorator of its one argument ``p``.

    It is when it returns ``p`` unchanged, possibly after keeping it in a
    registry, or returns a wrapper that only calls ``p`` with the wrapper's
    own arguments, with or without ``functools.wraps``. Everything it does
    with ``p`` must be one of these:

    - return it;
    - store it in a module-level ``dict``, ``list`` or ``set`` display the
      module binds once, by item assignment or ``setdefault``, ``append``,
      ``insert`` or ``add``;
    - read its ``__name__``, ``__qualname__``, ``__module__`` or ``__doc__``;
    - set an attribute of it that is not a dunder;
    - hand it to ``functools.wraps`` decorating a wrapper, or to
      ``functools.update_wrapper`` with the wrapper;
    - call it, inside a wrapper, with the wrapper's own parameters as they
      arrived.

    The wrapper is a function the decorator defines once and names only to
    return it, to hand it to ``update_wrapper``, or to set or read a
    non-dunder attribute of it. Anything else, a lambda or a comprehension
    mentioning ``p`` included, and any other decorator on the wrapper, is
    something this cannot show harmless.
    """

    def __init__(
        self,
        resolver: _Resolver,
        module: _Module,
        function: FunctionNode,
        enclosing: Tuple[FunctionNode, ...],
    ) -> None:
        self._resolver = resolver
        self._module = module
        self._function = function
        self._enclosing = enclosing
        self._parents: Dict[int, Tuple[ast.AST, str]] = {}
        for parent in ast.walk(function):
            for field, value in ast.iter_fields(parent):
                for child in value if isinstance(value, list) else [value]:
                    if isinstance(child, ast.AST):
                        self._parents[id(child)] = (parent, field)
        arguments = function.args
        positional = [*arguments.posonlyargs, *arguments.args]
        self._p = positional[0].arg if len(positional) == 1 else ""
        self._own = tuple(_own_scope(function))
        self._wrappers: Mapping[str, FunctionNode] = {
            node.name: node
            for node in self._own
            if isinstance(node, _FUNCTIONS) and _binds_once(function, node.name)
        }

    def holds(self) -> bool:
        """Whether the decorator does nothing with ``p`` but what the class docstring lists."""
        function = self._function
        arguments = function.args
        if (
            isinstance(function, ast.AsyncFunctionDef)
            or function.decorator_list
            or not self._p
            or arguments.vararg
            or arguments.kwarg
            or arguments.kwonlyargs
            or arguments.defaults
            or not function.body
            or not isinstance(function.body[-1], ast.Return)
        ):
            return False
        if any(isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)) for node in self._own):
            return False
        if _rebinds(function, self._p):
            return False
        if not all(self._wrapper_is_plain(wrapper) for wrapper in self._wrappers.values()):
            return False
        for node in ast.walk(function):
            if not isinstance(node, ast.Name):
                continue
            if node.id == self._p and not self._use_of_p_is_plain(node):
                return False
            if node.id in self._wrappers and not self._use_of_wrapper_is_plain(node):
                return False
        return all(
            self._returns_plainly(node.value) for node in self._own if isinstance(node, ast.Return)
        )

    # -- the wrapper ---------------------------------------------------------------

    def _wrapper_is_plain(self, wrapper: FunctionNode) -> bool:
        """The wrapper takes ``p`` from the decorator, keeps its own arguments, and has only ``wraps(p)``."""
        if self._p in _function_scope_names(wrapper):
            return False
        arguments = wrapper.args
        parameters = {
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                *((arguments.vararg,) if arguments.vararg else ()),
                *((arguments.kwarg,) if arguments.kwarg else ()),
            )
        }
        if any(
            isinstance(node, ast.Name)
            and node.id in parameters
            and not isinstance(node.ctx, ast.Load)
            for node in _own_scope(wrapper)
        ):
            return False
        return all(self._is_wraps_of_p(decorator) for decorator in wrapper.decorator_list)

    def _is_wraps_of_p(self, node: ast.AST) -> bool:
        """``functools.wraps(p)``, however the module spells ``functools.wraps``."""
        return (
            isinstance(node, ast.Call)
            and len(node.args) == 1
            and not node.keywords
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == self._p
            and self._names(node.func, "functools.wraps")
        )

    def _names(self, node: ast.expr, target: str) -> bool:
        spelled = dotted_name(node)
        return spelled is not None and self._resolver.resolves_to(
            self._module, spelled, (self._function, *self._enclosing), target
        )

    # -- where p and the wrappers may appear ---------------------------------------

    def _scope_of(self, node: ast.AST) -> Optional[ast.AST]:
        """The innermost scope ``node`` is evaluated in, within the decorator; None past it."""
        current = node
        while current is not self._function:
            parent_field = self._parents.get(id(current))
            if parent_field is None:
                return None
            parent, field = parent_field
            if isinstance(parent, _SCOPES):
                in_own_body = field == "body" or (
                    isinstance(parent, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
                )
                if in_own_body:
                    return parent
            current = parent
        return self._function

    def _parent(self, node: ast.AST) -> Optional[ast.AST]:
        found = self._parents.get(id(node))
        return None if found is None else found[0]

    def _use_of_p_is_plain(self, node: ast.Name) -> bool:
        if not isinstance(node.ctx, ast.Load):
            return False
        scope = self._scope_of(node)
        parent = self._parent(node)
        wrapper = next((w for w in self._wrappers.values() if w is scope), None)
        if parent is None or (scope is not self._function and wrapper is None):
            return False
        if isinstance(parent, ast.Attribute) and parent.value is node:
            if isinstance(parent.ctx, ast.Load):
                return parent.attr in _DESCRIPTIVE_ATTRIBUTES
            return not _is_dunder(parent.attr) and scope is self._function
        if wrapper is not None:
            return (
                isinstance(parent, ast.Call)
                and parent.func is node
                and _passes_own_arguments(parent, wrapper)
            )
        if isinstance(parent, ast.Return):
            return True
        if isinstance(parent, ast.Call) and node in parent.args:
            return self._plain_call_with_p(parent, node)
        if isinstance(parent, ast.Assign) and parent.value is node:
            return (
                len(parent.targets) == 1
                and isinstance(parent.targets[0], ast.Subscript)
                and isinstance(parent.targets[0].value, ast.Name)
                and self._registry_kind(parent.targets[0].value.id) == "dict"
            )
        return False

    def _plain_call_with_p(self, call: ast.Call, node: ast.Name) -> bool:
        """A call receiving ``p``: ``wraps(p)``, ``update_wrapper(w, p)``, or a registry method."""
        if self._is_wraps_of_p(call):
            holder = self._parent(call)
            if isinstance(holder, _FUNCTIONS):
                return self._wrappers.get(holder.name) is holder and call in holder.decorator_list
            return (
                isinstance(holder, ast.Call)
                and holder.func is call
                and self._wraps_application(holder)
                and isinstance(self._parent(holder), ast.Return)
            )
        if self._is_update_wrapper(call):
            return isinstance(self._parent(call), (ast.Return, ast.Expr))
        if call.keywords or any(isinstance(argument, ast.Starred) for argument in call.args):
            return False
        target = call.func
        if not (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and call.args
            and call.args[-1] is node
            and isinstance(self._parent(call), ast.Expr)
        ):
            return False
        kind = self._registry_kind(target.value.id)
        arity = None if kind is None else _REGISTRY_METHODS[kind].get(target.attr)
        return arity is not None and len(call.args) == arity

    def _is_update_wrapper(self, call: ast.Call) -> bool:
        """``functools.update_wrapper(w, p)`` for a wrapper ``w``."""
        return (
            len(call.args) == 2
            and not call.keywords
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id in self._wrappers
            and isinstance(call.args[1], ast.Name)
            and call.args[1].id == self._p
            and self._names(call.func, "functools.update_wrapper")
        )

    def _wraps_application(self, call: ast.Call) -> bool:
        """``functools.wraps(p)(w)`` for a wrapper ``w``."""
        return (
            isinstance(call.func, ast.Call)
            and self._is_wraps_of_p(call.func)
            and len(call.args) == 1
            and not call.keywords
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id in self._wrappers
        )

    def _use_of_wrapper_is_plain(self, node: ast.Name) -> bool:
        if not isinstance(node.ctx, ast.Load):
            return False
        parent = self._parent(node)
        scope = self._scope_of(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            return not _is_dunder(parent.attr) and (
                scope is self._function or scope is self._wrappers.get(node.id)
            )
        if scope is self._wrappers.get(node.id):
            return isinstance(parent, ast.Call) and parent.func is node
        if scope is not self._function:
            return False
        if isinstance(parent, ast.Return):
            return True
        if isinstance(parent, ast.Call):
            return (self._is_update_wrapper(parent) and parent.args[0] is node) or (
                self._wraps_application(parent) and isinstance(self._parent(parent), ast.Return)
            )
        return False

    def _returns_plainly(self, value: Optional[ast.expr]) -> bool:
        """A return of ``p``, of a wrapper, or of the wrapper as ``update_wrapper`` or ``wraps`` leave it."""
        if isinstance(value, ast.Name):
            return value.id == self._p or value.id in self._wrappers
        if isinstance(value, ast.Call):
            return self._is_update_wrapper(value) or self._wraps_application(value)
        return False

    def _registry_kind(self, name: str) -> Optional[str]:
        """``dict``, ``list`` or ``set`` when ``name`` is a module-level registry of that kind.

        The module binds the name once, unconditionally, to a display or an
        empty call of the builtin constructor, and no function rebinds it.
        """
        if any(
            name in _function_scope_names(scope) for scope in (self._function, *self._enclosing)
        ):
            return None
        bindings = self._module.bindings
        events = bindings.bindings.get(name, ())
        if (
            len(events) != 1
            or not events[0].certain
            or events[0].is_import
            or events[0].origin is not None
            or name in bindings.rebound_by_global
            or bindings.star_imports
        ):
            return None
        statement = self._module.tree.body[events[0].order]
        value: Optional[ast.expr] = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            value = statement.value if isinstance(target, ast.Name) and target.id == name else None
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            value = statement.value if statement.target.id == name else None
        if isinstance(value, ast.Dict):
            return "dict"
        if isinstance(value, ast.List):
            return "list"
        if isinstance(value, ast.Set):
            return "set"
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in _REGISTRY_METHODS
            and not value.args
            and not value.keywords
            and not bindings.may_bind(value.func.id)
        ):
            return value.func.id
        return None


def _rebinds(function: FunctionNode, name: str) -> bool:
    """Whether anything in ``function`` but its own parameter, at any depth, binds or declares ``name``."""
    own_parameters = {
        id(argument) for argument in (*function.args.posonlyargs, *function.args.args)
    }
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load):
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return True
        if isinstance(node, _DEFINITIONS) and node is not function and node.name == name:
            return True
        if isinstance(node, ast.arg) and node.arg == name and id(node) not in own_parameters:
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)) and name in bindings_of(
            node, into_nested_scopes=False
        ):
            return True
        if isinstance(node, ast.ExceptHandler) and node.name == name:
            return True
    return False


def _passes_own_arguments(call: ast.Call, wrapper: FunctionNode) -> bool:
    """Whether every argument of ``call`` is one of ``wrapper``'s parameters, passed as it came."""
    arguments = wrapper.args
    named = {
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    }
    vararg = arguments.vararg.arg if arguments.vararg else None
    kwarg = arguments.kwarg.arg if arguments.kwarg else None
    for argument in call.args:
        if isinstance(argument, ast.Starred):
            if not (isinstance(argument.value, ast.Name) and argument.value.id == vararg):
                return False
        elif not (isinstance(argument, ast.Name) and argument.id in named):
            return False
    for keyword in call.keywords:
        if keyword.arg is None:
            if not (isinstance(keyword.value, ast.Name) and keyword.value.id == kwarg):
                return False
        elif not (isinstance(keyword.value, ast.Name) and keyword.value.id in named):
            return False
    return True

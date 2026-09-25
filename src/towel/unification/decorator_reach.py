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

"""Whether every decorator that can reach a function's body is known to leave the body alone.

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

from ..import_model import NameStatus
from ..source_text import read_source
from .bounded_cache import BoundedCache
from .exceptions import ProjectScanLimitError
from .import_graph import ImportGraphCache, imported_definition_sites
from .module_bindings import ModuleBindings, dotted_name, global_bindings
from .statement_facts import bindings_of

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]
Definition = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
DecoratedKind = Literal["function", "class"]
Form = Literal["bare", "called"]

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)

FUNCTION: FrozenSet[DecoratedKind] = frozenset({"function"})
CLASS: FrozenSet[DecoratedKind] = frozenset({"class"})
EITHER: FrozenSet[DecoratedKind] = FUNCTION | CLASS
BARE: FrozenSet[Form] = frozenset({"bare"})
CALLED: FrozenSet[Form] = frozenset({"called"})
BARE_OR_CALLED: FrozenSet[Form] = BARE | CALLED


@dataclass(frozen=True)
class KnownDecorator:
    """A decorator read in its library's source and found to leave the body it decorates alone.

    ``origin`` is the absolute dotted name the decorator resolves to; one
    ending in ``.*`` stands for any single public attribute of the name
    before it. ``decorates`` says whether the reading covered functions,
    classes, or both, and ``forms`` whether it covered the decorator used
    bare (``@lru_cache``), called (``@lru_cache(64)``), or both. A called
    form passing a keyword in ``refused_keywords``, or ``**`` arguments that
    could carry one, or more than ``most_positional`` positional arguments,
    is outside the reading. ``note`` is the verification: the version read,
    and why the body stays untouched.
    """

    origin: str
    decorates: FrozenSet[DecoratedKind]
    forms: FrozenSet[Form]
    note: str
    refused_keywords: FrozenSet[str] = frozenset()
    most_positional: Optional[int] = None


_CPYTHON = "CPython 3.11.15, 3.12.13, 3.13.7"
_TYPING_EXTENSIONS = "typing_extensions 4.16.0 on " + _CPYTHON
_PYTEST = "pytest 9.1.1"
_CLICK = "click 8.5.0"


def _known(
    origins: Sequence[str],
    decorates: FrozenSet[DecoratedKind],
    forms: FrozenSet[Form],
    note: str,
    *,
    refused_keywords: FrozenSet[str] = frozenset(),
    most_positional: Optional[int] = None,
) -> Tuple[KnownDecorator, ...]:
    return tuple(
        KnownDecorator(origin, decorates, forms, note, refused_keywords, most_positional)
        for origin in origins
    )


KNOWN_DECORATORS: Tuple[KnownDecorator, ...] = (
    *_known(
        ["builtins.property"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, Objects/descrobject.c: property_init stores the function as fget and"
        " reads only its __doc__; property_descr_get calls fget(obj).",
    ),
    *_known(
        ["builtins.property.setter", "builtins.property.getter", "builtins.property.deleter"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, Objects/descrobject.c: property_copy builds a new property holding"
        " the function as fset, fget or fdel, and calls it only through the descriptor.",
    ),
    *_known(
        ["builtins.staticmethod"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, Objects/funcobject.c: sm_init stores the callable and copies"
        " __module__, __name__, __qualname__, __doc__, __annotations__; sm_descr_get"
        " returns it and sm_call calls it.",
    ),
    *_known(
        ["builtins.classmethod"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, Objects/funcobject.c: cm_init stores the callable and copies the"
        " same attributes; cm_descr_get binds it to the class (3.11 and 3.12 through the"
        " function's own __get__).",
    ),
    *_known(
        ["functools.wraps"],
        EITHER,
        CALLED,
        f"{_CPYTHON}, functools.py: partial(update_wrapper, wrapped=...) copies"
        " WRAPPER_ASSIGNMENTS onto the decorated function, updates its __dict__, sets"
        " __wrapped__, and returns that same function.",
    ),
    *_known(
        ["functools.cache"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, functools.py: lru_cache(maxsize=None)(user_function).",
    ),
    *_known(
        ["functools.lru_cache"],
        FUNCTION,
        BARE_OR_CALLED,
        f"{_CPYTHON}, functools.py: _lru_cache_wrapper (the Python fallback of the C"
        " version) calls user_function(*args, **kwds) on a miss; update_wrapper copies"
        " attributes.",
    ),
    *_known(
        ["functools.cached_property"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, functools.py: stores func and its __doc__ and __module__; __get__"
        " calls func(instance) once and keeps the value in instance.__dict__.",
    ),
    *_known(
        ["functools.singledispatch"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, functools.py: registers func for object; the wrapper calls"
        " dispatch(args[0].__class__)(*args, **kw); register() reads only annotations.",
    ),
    *_known(
        ["functools.singledispatchmethod"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, functools.py: holds singledispatch(func); __get__ dispatches on the"
        " first argument's class and calls the implementation.",
    ),
    *_known(
        ["functools.partialmethod"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, functools.py: stores func; the unbound method calls"
        " self.func(cls_or_self, *self.args, *args, **keywords).",
    ),
    *_known(
        ["functools.total_ordering"],
        CLASS,
        BARE,
        f"{_CPYTHON}, functools.py: sets only the comparison methods the class lacks,"
        " module functions calling the one it defines; returns the class.",
    ),
    *_known(
        ["contextlib.contextmanager", "contextlib.asynccontextmanager"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, contextlib.py: helper(*args, **kwds) returns"
        " _GeneratorContextManager (_AsyncGeneratorContextManager) built from"
        " func(*args, **kwds); wraps copies attributes.",
    ),
    *_known(
        ["abc.abstractmethod"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, abc.py: sets __isabstractmethod__ = True and returns the function.",
    ),
    *_known(
        ["typing.overload", "typing_extensions.overload"],
        FUNCTION,
        BARE,
        f"{_CPYTHON}, typing.py (typing_extensions 4.16.0 re-exports it): records func"
        " under its __code__.co_firstlineno for get_overloads() and returns"
        " _overload_dummy; the decorated body never runs.",
    ),
    *_known(
        ["typing.override", "typing_extensions.override"],
        FUNCTION,
        BARE,
        "CPython 3.12.13 and 3.13.7 typing.py (3.11 has none), and typing_extensions 4.16.0"
        f" on {_CPYTHON}: sets __override__ = True and returns the argument.",
    ),
    *_known(
        ["typing.final", "typing_extensions.final"],
        EITHER,
        BARE,
        f"{_CPYTHON}, typing.py (re-exported by typing_extensions 4.16.0): sets"
        " __final__ = True and returns the argument.",
    ),
    *_known(
        ["typing.no_type_check", "typing_extensions.no_type_check"],
        EITHER,
        BARE,
        f"{_CPYTHON}, typing.py (re-exported by typing_extensions 4.16.0): sets"
        " __no_type_check__ on the function, or on each function the class defines;"
        " returns the argument.",
    ),
    *_known(
        ["typing.runtime_checkable", "typing_extensions.runtime_checkable"],
        CLASS,
        BARE,
        f"{_CPYTHON} typing.py, and {_TYPING_EXTENSIONS}: sets _is_runtime_protocol and"
        " records the non-callable members; returns the class.",
    ),
    *_known(
        ["typing.dataclass_transform", "typing_extensions.dataclass_transform"],
        EITHER,
        CALLED,
        f"{_CPYTHON} typing.py, and {_TYPING_EXTENSIONS}: the decorator sets"
        " __dataclass_transform__ and returns its argument.",
    ),
    *_known(
        ["warnings.deprecated"],
        EITHER,
        CALLED,
        "CPython 3.13.7, warnings.py (3.11 and 3.12 have none): the wrapper warns, then calls"
        " arg(*args, **kwargs); on a class only __new__ and __init_subclass__ are wrapped.",
    ),
    *_known(
        ["typing_extensions.deprecated"],
        EITHER,
        CALLED,
        f"{_TYPING_EXTENSIONS} (its own class there; warnings.deprecated from 3.13.8): the"
        " wrapper warns, then calls arg(*args, **kwargs); on a class only __new__ and"
        " __init_subclass__ are wrapped.",
    ),
    *_known(
        ["dataclasses.dataclass"],
        CLASS,
        BARE_OR_CALLED,
        f"{_CPYTHON}, dataclasses.py: _process_class adds generated methods only where"
        " the class lacks them (_set_new_attribute; __hash__ per its table), exec()s only"
        " their new source, and with slots=True rebuilds the class from a copy of its"
        " __dict__; no function the class defines is read or rewritten.",
    ),
    *_known(
        ["enum.unique"],
        CLASS,
        BARE,
        f"{_CPYTHON}, enum.py: checks __members__ for aliases and returns the class.",
    ),
    *_known(
        [
            "unittest.mock.patch",
            "unittest.mock.patch.object",
            "unittest.mock.patch.dict",
            "unittest.mock.patch.multiple",
        ],
        EITHER,
        CALLED,
        f"{_CPYTHON}, unittest/mock.py: decorate_callable wraps the function in"
        " patched(*args, **keywargs), which enters the patches and calls"
        " func(*newargs, **newkeywargs); decorate_class does so for each test* method.",
    ),
    *_known(
        ["unittest.skip"],
        EITHER,
        BARE_OR_CALLED,
        f"{_CPYTHON}, unittest/case.py: replaces a function by skip_wrapper, which raises"
        " SkipTest without calling it, and marks a class; neither body is read.",
    ),
    *_known(
        ["unittest.skipIf", "unittest.skipUnless"],
        EITHER,
        CALLED,
        f"{_CPYTHON}, unittest/case.py: returns skip(reason) or _id, the identity.",
    ),
    *_known(
        ["unittest.expectedFailure"],
        EITHER,
        BARE,
        f"{_CPYTHON}, unittest/case.py: sets __unittest_expecting_failure__ and returns"
        " the argument.",
    ),
    *_known(
        ["pytest.fixture"],
        FUNCTION,
        BARE_OR_CALLED,
        f"{_PYTEST}, _pytest/fixtures.py: FixtureFunctionMarker.__call__ wraps the function"
        " in FixtureFunctionDefinition, which stores it, copies its attributes, and is"
        " called by pytest; its source is read only to print a failed fixture lookup.",
    ),
    *_known(
        ["pytest.mark.*"],
        EITHER,
        BARE_OR_CALLED,
        f"{_PYTEST}, _pytest/mark/structures.py: MarkGenerator.__getattr__ returns a"
        " MarkDecorator; called with the function, it appends a Mark to its pytestmark"
        " and returns it; called otherwise, with_args returns another MarkDecorator.",
    ),
    *_known(
        ["click.command", "click.group"],
        FUNCTION,
        BARE_OR_CALLED,
        f"{_CLICK}, click/decorators.py and core.py: builds Command(name=..., callback=f,"
        " params=...), and Command.invoke calls ctx.invoke(self.callback, **ctx.params);"
        " a cls argument can be any class, so that form is not covered.",
        refused_keywords=frozenset({"cls"}),
        most_positional=1,
    ),
    *_known(
        [
            "click.option",
            "click.argument",
            "click.confirmation_option",
            "click.password_option",
            "click.version_option",
            "click.help_option",
        ],
        FUNCTION,
        CALLED,
        f"{_CLICK}, click/decorators.py: _param_memo appends a new Parameter to"
        " f.__click_params__ (or to the Command's params) and returns f; the"
        " Parameter never receives f.",
    ),
    *_known(
        ["click.pass_context", "click.pass_obj"],
        FUNCTION,
        BARE,
        f"{_CLICK}, click/decorators.py: new_func(*args, **kwargs) calls"
        " f(get_current_context(), *args, **kwargs) (or .obj); update_wrapper copies"
        " attributes.",
    ),
)

_BY_ORIGIN: Mapping[str, KnownDecorator] = {entry.origin: entry for entry in KNOWN_DECORATORS}


def known_decorator(origin: str) -> Optional[KnownDecorator]:
    """The entry ``origin`` resolves to: its own, or a ``.*`` entry of the name above it."""
    entry = _BY_ORIGIN.get(origin)
    if entry is not None:
        return entry
    parent, _, attribute = origin.rpartition(".")
    if not parent or not attribute or attribute.startswith("_") or attribute == "with_args":
        return None
    return _BY_ORIGIN.get(f"{parent}.*")


@dataclass(frozen=True, eq=False)
class ModuleSource:
    """A module as an analysis holds it: its path, its text, and the tree parsed from that text."""

    path: str
    source: str
    tree: ast.Module


@dataclass(frozen=True)
class DecoratorRefusal:
    """A decorator that can reach some code and is not known to leave its body alone."""

    decorator: str
    """The decorator's absolute name when it resolves to one, as the module spells it otherwise."""
    holder: str
    """The definition it decorates: ``parse_record``, or ``class Model``."""

    @property
    def detail(self) -> str:
        """``@typeguard.typechecked on parse_record``."""
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
    """A module this resolution reads: its path, its tree, what its top level binds, its layout."""

    path: str
    tree: ast.Module
    bindings: ModuleBindings
    layout: Mapping[int, _Slot]


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


@dataclass(frozen=True)
class _PlainMemo:
    plain: bool
    stamps: Tuple[_Stamp, ...]


_REFUSALS: "WeakKeyDictionary[ast.AST, _Memo]" = WeakKeyDictionary()
_PLAIN: "WeakKeyDictionary[ast.AST, Dict[Form, _PlainMemo]]" = WeakKeyDictionary()
_LOADED: BoundedCache[_Stamp, Optional[_Module]] = BoundedCache(256)


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

    The decorators of ``definition`` itself come first, then those of each
    definition enclosing it, innermost first. ``module`` holds the tree
    ``definition`` belongs to; ``cache`` answers where an import leads. The
    answer is remembered per definition, together with every other module
    it read, and computed again once one of those changes.
    """
    memo = _REFUSALS.get(definition)
    if memo is not None and all(_stamp(stamp[0]) == stamp for stamp in memo.stamps):
        return memo.refusal
    resolver = _Resolver(cache)
    refusal = resolver.chain_refusal(definition, module)
    _REFUSALS[definition] = _Memo(refusal, tuple(sorted(resolver.stamps)))
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
        module = _Module(source.path, source.tree, bindings, _layout(source.tree))
        node: Optional[Definition] = definition
        while node is not None:
            slot = module.layout.get(id(node))
            if slot is None:
                return DecoratorRefusal("(definition outside its module)", _holder(node))
            for decorator in node.decorator_list:
                refused = self._refused(decorator, node, module)
                if refused is not None:
                    return DecoratorRefusal(refused, _holder(node))
            node = slot.owner
        return None

    def _refused(
        self, decorator: ast.expr, decorated: Definition, module: _Module
    ) -> Optional[str]:
        """The name to report ``decorator`` by when it is not known; None when it is."""
        callee = decorator.func if isinstance(decorator, ast.Call) else decorator
        spelled = dotted_name(callee)
        if spelled is None:
            return _spelling(decorator)
        denotations = self._denotations(spelled, decorated, module)
        if denotations is None:
            return spelled
        for denotation in _in_order(denotations):
            refused = self._denotation_refused(denotation, decorator, decorated, spelled)
            if refused is not None:
                return refused
        return None

    def _denotation_refused(
        self,
        denotation: _Denotation,
        decorator: ast.expr,
        decorated: Definition,
        spelled: str,
        depth: int = 0,
    ) -> Optional[str]:
        form: Form = "called" if isinstance(decorator, ast.Call) else "bare"
        if isinstance(denotation, _CallResult):
            # Applied bare, the name is the call's decorator; called again, it is
            # whatever that decorator returns, which nothing here has read.
            if form == "called" or depth > _MOST_HOPS:
                return spelled
            return self._denotation_refused(
                denotation.callee, denotation.call, decorated, spelled, depth + 1
            )
        if isinstance(denotation, _ProjectDef):
            return None if self._plain(denotation, form) else spelled
        kind: DecoratedKind = "class" if isinstance(decorated, ast.ClassDef) else "function"
        entry = known_decorator(denotation.dotted)
        top = denotation.dotted.partition(".")[0]
        if entry is not None and top in _STANDARD_MODULES:
            return None if _covers(entry, decorator, kind) else denotation.dotted
        if top in _STANDARD_MODULES or depth > _MOST_HOPS:
            return denotation.dotted
        external = self._is_external(denotation.module, top)
        if external is None:
            return denotation.dotted
        if external:
            # No module of the project takes the name: it is the installed
            # library an entry was read in, or a decorator nobody read.
            return (
                None if entry is not None and _covers(entry, decorator, kind) else denotation.dotted
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
                if self._denotation_refused(inner, decorator, decorated, spelled, depth + 1):
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
        self, dotted: str, decorated: Definition, module: _Module
    ) -> Optional[FrozenSet[_Denotation]]:
        """Everything ``dotted`` may denote where ``decorated``'s decorators run.

        They run in the scope holding the definition: a class body sees its
        own earlier bindings, a function its locals (which are not followed),
        and every scope past the first skips class bodies, as Python does.
        """
        head = dotted.partition(".")[0]
        slot = module.layout.get(id(decorated))
        if slot is None:
            return None
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
        found = self._denotations(spelled, function, module)
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
            stamp, None if bindings is None else _Module(path, tree, bindings, _layout(tree))
        )

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


def _covers(entry: KnownDecorator, decorator: ast.expr, kind: DecoratedKind) -> bool:
    """Whether the reading behind ``entry`` covers ``decorator`` applied to a ``kind``."""
    form: Form = "called" if isinstance(decorator, ast.Call) else "bare"
    if kind not in entry.decorates or form not in entry.forms:
        return False
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

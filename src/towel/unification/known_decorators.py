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

"""The decorators read in their libraries' source and found to leave the body they decorate alone.

Each entry records the absolute name it resolves to, what it was read to
cover (functions or classes; used bare, called as a factory, or called with
the function among other arguments), and the verification: the version whose
source was read and why the body stays untouched. ``decorator_reach`` decides
which decorators reach a body; this is the list it consults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Literal, Mapping, Optional, Sequence, Tuple

DecoratedKind = Literal["function", "class"]
Form = Literal["bare", "called", "applied"]
"""How a callable reaches a definition: ``@d``; ``@d(...)``, whose result is
applied; or ``d(f, ...)`` in an assignment, with the function among several
arguments."""

FUNCTION: FrozenSet[DecoratedKind] = frozenset({"function"})
CLASS: FrozenSet[DecoratedKind] = frozenset({"class"})
EITHER: FrozenSet[DecoratedKind] = FUNCTION | CLASS
BARE: FrozenSet[Form] = frozenset({"bare"})
CALLED: FrozenSet[Form] = frozenset({"called"})
BARE_OR_CALLED: FrozenSet[Form] = BARE | CALLED
APPLIED: FrozenSet[Form] = frozenset({"applied"})


@dataclass(frozen=True)
class KnownDecorator:
    """A decorator read in its library's source and found to leave the body it decorates alone.

    ``origin`` is the absolute dotted name the decorator resolves to; one
    ending in ``.*`` stands for any single public attribute of the name
    before it. ``decorates`` says whether the reading covered functions,
    classes, or both, and ``forms`` whether it covered the decorator used
    bare (``@lru_cache``), called (``@lru_cache(64)``), applied to the
    function among other arguments (``x = property(get, set)``), or several.
    A called
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
_RICH = "rich 15.0.0"


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
        BARE | APPLIED,
        f"{_CPYTHON}, Objects/descrobject.c: property_init stores fget, fset and fdel and"
        " reads only fget's __doc__; the descriptor calls them with the instance.",
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
        ["typing.TypeVar", "typing_extensions.TypeVar"],
        EITHER,
        APPLIED,
        "CPython 3.11.15 typing.py, 3.12.13 and 3.13.7 Objects/typevarobject.c, and"
        " typing_extensions 4.16.0 (which calls typing.TypeVar): a bound or constraint is"
        " passed through typing._type_check, which wraps a string and refuses special"
        " forms, and stored; nothing of it is called or read.",
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
    *_known(
        ["rich.repr.auto", "rich.repr.rich_repr"],
        CLASS,
        BARE_OR_CALLED,
        f"{_RICH}, rich/repr.py: do_replace sets __repr__ to a new auto_repr and, where the"
        " class lacks one, __rich_repr__ to a new auto_rich_repr; these call the class's"
        " __rich_repr__, and read __init__ only through inspect.signature; no method body"
        " is read or rewritten (rich_repr is auto, or auto(angular=...)).",
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

"""A method helper only where every call of it reaches the class as written.

A helper made a method is reached as ``self.__extracted_func_0(...)``, which
holds only for receivers the class machinery built with the helper in place:

- ``def m(self: HasV, n)`` declares that anything with a ``v`` may be the
  receiver, and ``Box.m(SimpleNamespace(v=10), 1)`` is a well-typed call
  that the method-form helper would break with ``AttributeError``;
- a metaclass, or an ``__init_subclass__`` on the class's method resolution
  order, sees the class's namespace as it is built and may wrap, register or
  drop the helper there;
- a ``__getattribute__`` on that order intercepts the lookup of the helper
  itself, and a proxy answers it from another object.

A block under such an annotation gets the module-level helper that takes the
receiver as an argument. A block in a class whose machinery fails the test
is not moved at all: the metaclass or ``__init_subclass__`` that could wrap
a helper could wrap or recompile the methods' own code as well
(``decorator_reach``), so the class keeps its code. ``type``, ``abc.ABCMeta``, the enum metaclass, ``typing.Generic``
and ``object`` are known to leave plain functions alone, every builtin class
but ``type`` and ``super`` looks attributes up as ``object`` does, and these
keep the method form; the last tests check that of the running interpreter.
Each other case runs the program before and after the refactoring.
"""

from __future__ import annotations

import builtins
import contextlib
import enum
import importlib
import io
from pathlib import Path
import subprocess
import sys
import textwrap
import types
import typing
from typing import Dict, Optional, Tuple

import pytest

from tests.test_helpers import refactor_to_fixed_point_silently
from towel.unification.import_graph import _HOSTS_METHOD_HELPERS, _OWN_ATTRIBUTE_LOOKUP
from towel.unification.known_bases import KNOWN_BASES
from towel.unification.refactor_engine import UnificationRefactorEngine

METHODS = """
    def first(self, n):
        print("first", n)
        total = self.v + n
        total = total * 2
        print("done", total)
        return total

    def second(self, n):
        print("second", n)
        total = self.v + n
        total = total * 2
        print("done", total)
        return total
"""

DRIVER = "from box import Box\nprint(Box().first(1), Box().second(2))\n"


def _module(prelude: str, header: str, methods: str = METHODS) -> str:
    """``prelude``, then the class ``header`` opens with ``methods`` in its body."""
    body = textwrap.indent(textwrap.dedent(methods).strip("\n"), "    ")
    return textwrap.dedent(prelude).strip("\n") + "\n\n\n" + header + "\n" + body + "\n"


def _run(directory: Path, driver: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(driver)],
        cwd=directory,
        capture_output=True,
        text=True,
        env={"PATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    return completed.stdout + "|" + "".join(completed.stderr.strip().splitlines()[-1:])


def _refactored(
    tmp_path: Path, source: str, driver: str = DRIVER, *, moves: bool = True
) -> Tuple[str, str, str]:
    """The driver's output before and after refactoring ``box.py`` to a fixed point, and the result.

    ``moves`` says whether the run is to move code at all.
    """
    path = tmp_path / "box.py"
    path.write_text(source)
    before = _run(tmp_path, driver)
    final, applied = refactor_to_fixed_point_silently(str(path), min_lines=3)
    assert (applied > 0) == moves
    assert moves or final == source
    path.write_text(final)
    return before, _run(tmp_path, driver), final


def _refactored_project(
    tmp_path: Path, files: Dict[str, str], driver: str, *, moves: bool = True
) -> Tuple[str, str, str]:
    """As :func:`_refactored`, for a directory of modules; the result is ``box.py``'s."""
    for name, text in files.items():
        (tmp_path / name).write_text(textwrap.dedent(text).lstrip("\n"))
    before = _run(tmp_path, driver)
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), progress="none"
        )
    assert (sum(applied for applied, _ in results.values()) > 0) == moves
    return before, _run(tmp_path, driver), (tmp_path / "box.py").read_text()


def test_a_receiver_annotated_as_a_protocol_takes_a_module_helper(tmp_path: Path) -> None:
    source = _module(
        """
        from typing import Protocol


        class HasV(Protocol):
            v: int
        """,
        "class Box:\n    v = 1\n",
        METHODS.replace("(self, n)", "(self: HasV, n)"),
    )
    driver = """
        from types import SimpleNamespace
        from box import Box
        print(Box().first(1), Box.second(SimpleNamespace(v=10), 1))
    """
    before, after, final = _refactored(tmp_path, source, driver)
    assert after == before
    assert "self.__extracted_func" not in final


def test_a_class_method_whose_receiver_may_be_another_class_takes_a_module_helper(
    tmp_path: Path,
) -> None:
    methods = METHODS.replace("    def ", "    @classmethod\n    def ").replace("self", "cls")
    source = _module("", "class Box:\n    v = 1\n", methods.replace("(cls, n)", "(cls: type, n)"))
    driver = """
        from box import Box


        class Other:
            v = 10


        print(Box.first(1), vars(Box)["second"].__func__(Other, 1))
    """
    before, after, final = _refactored(tmp_path, source, driver)
    assert after == before
    assert "self.__extracted_func" not in final


@pytest.mark.parametrize(
    "annotation, prelude",
    [
        ('"Box"', ""),
        ("Box", "from __future__ import annotations"),
        ("Self", "from typing import Self"),
        ("typing.Self", "import typing"),
        ("T", 'from typing import TypeVar\n\nT = TypeVar("T", bound="Box")'),
    ],
    ids=["own-class", "own-class-postponed", "self-type", "self-type-attribute", "bound-variable"],
)
def test_a_receiver_annotated_as_its_own_class_keeps_the_method_helper(
    tmp_path: Path, annotation: str, prelude: str
) -> None:
    source = _module(
        prelude, "class Box:\n    v = 1\n", METHODS.replace("(self, n)", f"(self: {annotation}, n)")
    )
    before, after, final = _refactored(tmp_path, source)
    assert after == before
    assert "self.__extracted_func" in final


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 input requires Python 3.12")
def test_a_receiver_of_a_type_parameter_bound_to_its_class_keeps_the_method_helper(
    tmp_path: Path,
) -> None:
    source = _module(
        "", "class Box:\n    v = 1\n", METHODS.replace("(self, n)", "[T: Box](self: T, n)")
    )
    before, after, final = _refactored(tmp_path, source)
    assert after == before
    assert "self.__extracted_func" in final


def test_a_receiver_of_a_variable_bound_elsewhere_takes_a_module_helper(tmp_path: Path) -> None:
    source = _module(
        """
        from typing import TypeVar


        class Base:
            v = 10


        T = TypeVar("T", bound=Base)
        """,
        "class Box(Base):\n    v = 1\n",
        METHODS.replace("(self, n)", "(self: T, n)"),
    )
    driver = "from box import Base, Box\nprint(Box().first(1), Box.second(Base(), 1))\n"
    before, after, final = _refactored(tmp_path, source, driver)
    assert after == before
    assert "self.__extracted_func" not in final


WRAPPING_METACLASS = """
import functools


class Logged(type):
    def __new__(mcs, name, bases, namespace):
        for key, value in list(namespace.items()):
            if callable(value) and not key.startswith("__"):
                namespace[key] = mcs.logged(key, value)
        return super().__new__(mcs, name, bases, namespace)

    @staticmethod
    def logged(key, function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            print("call", key)
            return function(*args, **kwargs)

        return wrapper
"""

REGISTERING_BASE = """
REGISTRY = []


class Registered:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        REGISTRY.extend(sorted(key for key, value in vars(cls).items() if callable(value)))
"""


@pytest.mark.parametrize(
    "prelude, header, driver",
    [
        (WRAPPING_METACLASS, "class Box(metaclass=Logged):\n    v = 1\n", DRIVER),
        (
            REGISTERING_BASE,
            "class Box(Registered):\n    v = 1\n",
            "import box\nprint(box.Box().first(1), box.REGISTRY)\n",
        ),
        (
            "SEEN = []",
            "class Box:\n    v = 1\n\n    def __init_subclass__(cls, **kwargs):\n"
            "        super().__init_subclass__(**kwargs)\n"
            '        SEEN.append([name for name in dir(cls) if not name.startswith("__")])\n',
            "import box\n\n\nclass Sub(box.Box):\n    pass\n\n\nprint(Sub().first(1), box.SEEN)\n",
        ),
    ],
    ids=["wrapping-metaclass", "registering-base", "own-init-subclass"],
)
def test_class_machinery_that_sees_the_namespace_keeps_the_methods_code(
    tmp_path: Path, prelude: str, header: str, driver: str
) -> None:
    before, after, final = _refactored(tmp_path, _module(prelude, header), driver, moves=False)
    assert after == before
    assert "self.__extracted_func" not in final


FORWARDING_PROXY = """
class Target:
    v = 10


class Proxy:
    \"\"\"Answers every attribute but its methods and its target from the target.\"\"\"

    def __init__(self, target):
        object.__setattr__(self, "_target", target)

    def __getattribute__(self, name):
        if name in ("first", "second", "_target", "__class__", "__dict__"):
            return object.__getattribute__(self, name)
        target = object.__getattribute__(self, "_target")
        return getattr(target, name)
"""


@pytest.mark.parametrize(
    "prelude, header",
    [
        (FORWARDING_PROXY, None),
        (
            FORWARDING_PROXY.replace("class Proxy:", "class ProxyBase:"),
            "class Box(ProxyBase):\n",
        ),
    ],
    ids=["own-getattribute", "inherited-getattribute"],
)
def test_a_class_that_intercepts_attribute_lookup_keeps_the_methods_code(
    tmp_path: Path, prelude: str, header: Optional[str]
) -> None:
    """``self.__extracted_func_0`` on a forwarding proxy asks the target, which has no such member.

    The methods read ``self.v``, which the proxy answers from its target, and
    work; a method helper would be looked up the same way and raise
    ``AttributeError``. Such a class fails the method-host test, which every
    class code moves out of must pass too, so its code stays. ``__getattr__``
    is no reason to refuse, since it runs only when normal lookup fails and
    nothing spells the helper's stored name.
    """
    if header is None:
        source = (
            textwrap.dedent(prelude).strip("\n")
            + "\n"
            + textwrap.indent(textwrap.dedent(METHODS).strip("\n"), "    ")
            + "\n"
        )
        driver = "from box import Proxy, Target\nbox = Proxy(Target())\nprint(box.first(1), box.second(2))\n"
    else:
        source = _module(prelude, header.rstrip("\n") + "\n    pass\n")
        driver = (
            "from box import Box, Target\nbox = Box(Target())\n"
            "print(box.first(1), box.second(2))\n"
        )
    before, after, final = _refactored(tmp_path, source, driver, moves=False)
    assert "AttributeError" not in before
    assert after == before
    assert "self.__extracted_func" not in final


def test_a_class_whose_getattr_serves_missing_names_keeps_the_method_helper(tmp_path: Path) -> None:
    source = _module(
        "",
        "class Box:\n    v = 1\n\n    def __getattr__(self, name):\n        return 'field ' + name\n",
    )
    driver = (
        "from box import Box\nbox = Box()\nprint(box.first(1), box.second(2),"
        " box._extracted_func_0, box.__extracted_func_0)\n"
    )
    before, after, final = _refactored(tmp_path, source, driver)
    assert after == before
    assert "self.__extracted_func" in final


def test_a_metaclass_keeps_its_methods_code(tmp_path: Path) -> None:
    """A metaclass's instances are classes, and ``type`` looks their attributes up its own way."""
    source = _module("", "class Meta(type):\n    v = 1\n")
    driver = (
        "from box import Meta\nKind = Meta('Kind', (), {})\nprint(Kind.first(1), Kind.second(2))\n"
    )
    before, after, final = _refactored(tmp_path, source, driver, moves=False)
    assert after == before
    assert "self.__extracted_func" not in final


def test_a_base_imported_from_the_project_is_followed_to_its_metaclass(tmp_path: Path) -> None:
    before, after, final = _refactored_project(
        tmp_path,
        {
            "meta.py": WRAPPING_METACLASS + "\n\nclass Base(metaclass=Logged):\n    pass\n",
            "box.py": _module("from meta import Base", "class Box(Base):\n    v = 1\n"),
        },
        DRIVER,
        moves=False,
    )
    assert after == before
    assert "self.__extracted_func" not in final


def test_a_base_imported_from_the_project_is_judged_where_it_is_defined(tmp_path: Path) -> None:
    before, after, final = _refactored_project(
        tmp_path,
        {
            "base.py": "import abc\n\n\nclass Base(abc.ABC):\n    pass\n",
            "box.py": _module("from base import Base", "class Box(Base):\n    v = 1\n"),
        },
        DRIVER,
    )
    assert after == before
    assert "self.__extracted_func" in final


ENUM_BOX = "class Box(enum.Enum):\n    ONE = 1\n\n    @property\n    def v(self):\n        return self.value\n"


@pytest.mark.parametrize(
    "prelude, header",
    [
        ("import abc", "class Box(abc.ABC):\n    v = 1\n"),
        ("from abc import ABCMeta as Meta", "class Box(metaclass=Meta):\n    v = 1\n"),
        (
            'from typing import Generic, TypeVar\n\nT = TypeVar("T")',
            "class Box(Generic[T]):\n    v = 1\n",
        ),
        ("class Base:\n    pass", "class Box(Base):\n    v = 1\n"),
        ("", "class Box(dict):\n    v = 1\n"),
        ("import enum", ENUM_BOX),
        ("import enum", ENUM_BOX.replace("enum.Enum", "enum.IntEnum")),
    ],
    ids=["abc", "abcmeta", "generic", "plain-base", "builtin-base", "enum", "int-enum"],
)
def test_class_machinery_known_to_leave_functions_alone_keeps_the_method_helper(
    tmp_path: Path, prelude: str, header: str
) -> None:
    driver = (
        "from box import Box\nprint(Box.ONE.first(1), Box.ONE.second(2), list(Box))\n"
        if "enum" in prelude
        else DRIVER
    )
    before, after, final = _refactored(tmp_path, _module(prelude, header), driver)
    assert after == before
    assert "self.__extracted_func" in final


def _resolve(dotted: str) -> object:
    module, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(module), name)


# The ``__init_subclass__`` of a library base read in its source (``known_bases``).
_READ_INIT_SUBCLASS = frozenset(base.init_subclass for base in KNOWN_BASES if base.init_subclass)


def _only_quiet_init_subclass(cls: type) -> bool:
    """Whether every ``__init_subclass__`` a subclass of ``cls`` runs is ``Generic``'s, ``object``'s, or one read."""
    return all(
        "__init_subclass__" not in vars(klass)
        or klass in {object, typing.Generic}
        or f"{klass.__module__}.{klass.__qualname__}" in _READ_INIT_SUBCLASS
        for klass in cls.__mro__
    )


def _keeps_a_plain_function(bases: Tuple[object, ...], metaclass: Optional[type] = None) -> bool:
    """Whether a class built on ``bases`` keeps a plain function of its body as given."""

    def helper(self: object) -> None:
        del self

    def body(namespace: Dict[str, object]) -> None:
        namespace["_extracted_func_0"] = helper

    keywords = {} if metaclass is None else {"metaclass": metaclass}
    built = types.new_class("Box", bases, keywords, body)
    return vars(built)["_extracted_func_0"] is helper and _only_quiet_init_subclass(built)


@pytest.mark.parametrize("dotted", sorted(_HOSTS_METHOD_HELPERS.bases))
def test_each_function_keeping_base_keeps_a_plain_function(dotted: str) -> None:
    base = _resolve(dotted)
    # A ReprEnum is only ever built mixed with the data type it represents.
    assert _keeps_a_plain_function((int, base) if base is enum.ReprEnum else (base,))


@pytest.mark.parametrize("dotted", sorted(_HOSTS_METHOD_HELPERS.subscripted_bases))
def test_each_function_keeping_generic_base_keeps_a_plain_function(dotted: str) -> None:
    generic = typing.cast(typing.Any, _resolve(dotted))
    assert _keeps_a_plain_function((generic[typing.TypeVar("T")],))


@pytest.mark.parametrize("dotted", sorted(_HOSTS_METHOD_HELPERS.metaclasses))
def test_each_function_keeping_metaclass_keeps_a_plain_function(dotted: str) -> None:
    metaclass = _resolve(dotted)
    assert isinstance(metaclass, type)
    # The enum metaclass builds only enums; the others take any class.
    bases = (enum.Enum,) if issubclass(metaclass, enum.EnumMeta) else ()
    assert _keeps_a_plain_function(bases, metaclass)


def test_every_builtin_class_keeps_a_plain_function() -> None:
    """``ImportTimeCode`` takes any builtin class as a base that leaves the helper alone."""
    classes = [value for value in vars(builtins).values() if isinstance(value, type)]
    assert classes
    for cls in classes:
        assert type(cls) is type, cls
        assert _only_quiet_init_subclass(cls), cls


def test_the_builtin_classes_a_method_helper_may_inherit_look_attributes_up_as_object_does() -> (
    None
):
    """``_OWN_ATTRIBUTE_LOOKUP`` names every builtin class whose lookup is not ``object``'s.

    Before CPython 3.14 many builtin classes carry a ``__getattribute__`` slot
    of their own; each that can be subclassed and built without arguments is
    checked to answer as the generic lookup does, the instance's dictionary
    before the class's non-data members.
    """
    classes = {name: value for name, value in vars(builtins).items() if isinstance(value, type)}
    assert _OWN_ATTRIBUTE_LOOKUP <= {f"builtins.{name}" for name in classes}
    for name, cls in classes.items():
        if f"builtins.{name}" in _OWN_ATTRIBUTE_LOOKUP:
            assert "__getattribute__" in vars(cls), cls
            continue
        if "__getattribute__" not in vars(cls):
            continue
        try:
            probe = type("Probe", (cls,), {"_Probe__helper": lambda self: "helper"})()
        except TypeError:
            continue
        lookup = vars(cls)["__getattribute__"]
        assert lookup(probe, "_Probe__helper")() == "helper", cls
        vars(probe)["_Probe__helper"] = "shadow"
        assert lookup(probe, "_Probe__helper") == "shadow", cls


@pytest.mark.parametrize("dotted", sorted(_HOSTS_METHOD_HELPERS.metaclasses))
def test_each_method_helper_metaclass_looks_class_attributes_up_as_type_does(dotted: str) -> None:
    """A class-method helper is reached as ``cls.__extracted_func_0``, through the metaclass."""
    metaclass = _resolve(dotted)
    assert isinstance(metaclass, type)
    assert not [
        klass for klass in metaclass.__mro__[:-2] if "__getattribute__" in vars(klass)
    ], metaclass.__mro__
    assert metaclass.__mro__[-2:] == (type, object)

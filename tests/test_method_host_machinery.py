"""A method helper only where every call of it reaches the class as written.

A helper made a method is reached as ``self.__extracted_func_0(...)``, which
holds only for receivers the class machinery built with the helper in place:

- ``def m(self: HasV, n)`` declares that anything with a ``v`` may be the
  receiver, and ``Box.m(SimpleNamespace(v=10), 1)`` is a well-typed call
  that the method-form helper would break with ``AttributeError``;
- a metaclass, or an ``__init_subclass__`` on the class's method resolution
  order, sees the class's namespace as it is built and may wrap, register or
  drop the helper there.

Such blocks get the module-level helper that takes the receiver as an
argument. ``type``, ``abc.ABCMeta``, the enum metaclass, ``typing.Generic``
and ``object`` are known to leave plain functions alone, and keep the
method form; the last tests check that of the running interpreter. Each other
case runs the program before and after the refactoring.
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
from towel.unification.import_graph import _KEEPS_FUNCTIONS
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


def _refactored(tmp_path: Path, source: str, driver: str = DRIVER) -> Tuple[str, str, str]:
    """The driver's output before and after refactoring ``box.py`` to a fixed point, and the result."""
    path = tmp_path / "box.py"
    path.write_text(source)
    before = _run(tmp_path, driver)
    final, applied = refactor_to_fixed_point_silently(str(path), min_lines=3)
    assert applied > 0
    path.write_text(final)
    return before, _run(tmp_path, driver), final


def _refactored_project(tmp_path: Path, files: Dict[str, str], driver: str) -> Tuple[str, str, str]:
    """As :func:`_refactored`, for a directory of modules; the result is ``box.py``'s."""
    for name, text in files.items():
        (tmp_path / name).write_text(textwrap.dedent(text).lstrip("\n"))
    before = _run(tmp_path, driver)
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), progress="none"
        )
    assert sum(applied for applied, _ in results.values()) > 0
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
def test_class_machinery_that_sees_the_namespace_gets_a_module_helper(
    tmp_path: Path, prelude: str, header: str, driver: str
) -> None:
    before, after, final = _refactored(tmp_path, _module(prelude, header), driver)
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


def _only_quiet_init_subclass(cls: type) -> bool:
    """Whether every ``__init_subclass__`` a subclass of ``cls`` runs is ``Generic``'s or ``object``'s."""
    return all(
        "__init_subclass__" not in vars(klass) or klass in {object, typing.Generic}
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


@pytest.mark.parametrize("dotted", sorted(_KEEPS_FUNCTIONS.bases))
def test_each_function_keeping_base_keeps_a_plain_function(dotted: str) -> None:
    base = _resolve(dotted)
    # A ReprEnum is only ever built mixed with the data type it represents.
    assert _keeps_a_plain_function((int, base) if base is enum.ReprEnum else (base,))


@pytest.mark.parametrize("dotted", sorted(_KEEPS_FUNCTIONS.subscripted_bases))
def test_each_function_keeping_generic_base_keeps_a_plain_function(dotted: str) -> None:
    generic = typing.cast(typing.Any, _resolve(dotted))
    assert _keeps_a_plain_function((generic[typing.TypeVar("T")],))


@pytest.mark.parametrize("dotted", sorted(_KEEPS_FUNCTIONS.metaclasses))
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

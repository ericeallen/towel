"""What counts as running code while a module is imported.

``ImportTimeCode`` is the one judgment behind two decisions: whether a
borrower may import a helper's host, which would run the host's top level
wherever the borrower is imported, and whether a helper may be placed after
a statement, which could call a function whose block went into the helper
before the helper exists. A decorator, a default, an evaluated annotation, a
base class's metaclass or ``__init_subclass__``, and any call are code unless
what runs is one of a few callables resolved through the module's own
imports. Each allowlisted callable is checked here against what it does at
runtime, and each deliberately left out is shown doing what left it out.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from typing import Tuple

import pytest

from towel.unification.import_graph import (
    ImportTimeCode,
    _QUIET_BASES,
    _QUIET_CLASS_DECORATORS,
    _QUIET_FUNCTION_DECORATORS,
)
from towel.unification.insertion import InsertionPoints


def _runs(source: str) -> Tuple[bool, ...]:
    return ImportTimeCode(textwrap.dedent(source)).statements()


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            """
            def register(function):
                return function

            @register
            def plugin():
                return 1
            """,
            (False, True),
        ),
        (
            """
            class Box:
                @property
                def value(self):
                    return 1

                @value.setter
                def value(self, new):
                    pass

                @staticmethod
                def make():
                    return Box()

                @classmethod
                def build(cls):
                    return cls()
            """,
            (False,),
        ),
        (
            """
            class Box:
                def other(self):
                    return 1

                @other.setter
                def value(self, new):
                    pass
            """,
            (True,),
        ),
        ("class Failure(Exception):\n    pass\n", (False,)),
        (
            """
            class Base:
                def __init_subclass__(cls):
                    pass

            class Child(Base):
                pass
            """,
            (False, True),
        ),
        (
            """
            class Base:
                pass

            class Child(Base):
                __slots__ = ()
            """,
            (False, False),
        ),
        (
            """
            class Meta(type):
                pass

            class Child(metaclass=Meta):
                pass
            """,
            (False, True),
        ),
        (
            """
            from abc import ABCMeta

            class Child(metaclass=ABCMeta):
                pass
            """,
            (False, False),
        ),
        (
            """
            import sys
            from typing import TYPE_CHECKING

            if TYPE_CHECKING:
                if sys.version_info >= (3, 11):
                    from typing import Self
                print("never")
            """,
            (False, False, False),
        ),
        (
            """
            from typing import TYPE_CHECKING

            if TYPE_CHECKING:
                pass
            else:
                print("always")
            """,
            (False, True),
        ),
        (
            """
            TYPE_CHECKING = False

            if TYPE_CHECKING:
                print("never")
            """,
            (False, False),
        ),
        (
            """
            import sys

            if sys.version_info >= (3, 8) and sys.platform != "win32":
                LIMIT = 1
            """,
            (False, False),
        ),
        ("from typing import TypeVar\nT = TypeVar('T', bound=int)\n", (False, False)),
        ("T = TypeVar('T')\n", (True,)),
        ("import re\nPATTERN = re.compile(r'\\d+', re.I | re.X)\n", (False, False)),
        (
            "import re\nBODY = r'[a-z]+'\nPATTERN = re.compile(r'^' + BODY + r'$', re.VERBOSE)\n",
            (False, False, False),
        ),
        ("import re\nPATTERN = re.compile('[')\n", (False, True)),
        ("import re\nPATTERN = re.compile('[[a]')\n", (False, True)),
        ("import logging\nlog = logging.getLogger(__name__)\n", (False, True)),
        ("_MISSING = object()\n", (False,)),
        ("object = type\n_MISSING = object()\n", (False, True)),
        ("def f(x=g()):\n    pass\n", (True,)),
        ("def f(x=1, *, y=(1, 2)):\n    pass\n", (False,)),
        (
            "from typing import List, Optional\ndef f(x: List[int]) -> Optional[str]:\n    pass\n",
            (False, False),
        ),
        (
            "class Foo:\n    pass\ndef f(x: Foo[int]) -> None:\n    pass\n",
            (False, True),
        ),
        (
            "from __future__ import annotations\ndef f(x: Foo[int]) -> None:\n    pass\n",
            (False, False),
        ),
        ("from typing import overload\n@overload\ndef f(x: int) -> int: ...\n", (False, True)),
        ("from os import *\n@property\ndef f(self):\n    pass\n", (False, True)),
        ("X = a.b\n", (True,)),
        ("X = [1, 'a', (2, 3)]\nY = {'a': X}\n", (False, False)),
        ("X = {name}\n", (True,)),
    ],
    ids=[
        "registration-decorator",
        "property-staticmethod-classmethod",
        "setter-of-a-non-property",
        "builtin-base",
        "base-with-init-subclass",
        "plain-project-base",
        "project-metaclass",
        "abcmeta",
        "type-checking-with-branches",
        "type-checking-else",
        "module-type-checking-constant",
        "platform-test",
        "typevar-imported",
        "typevar-unresolved",
        "re-compile",
        "re-compile-of-module-constants",
        "re-compile-error",
        "re-compile-warning",
        "get-logger",
        "sentinel",
        "shadowed-object",
        "call-in-default",
        "constant-defaults",
        "typing-annotations",
        "project-generic-annotation",
        "postponed-annotations",
        "overload",
        "star-import-hides-builtins",
        "attribute-access",
        "literal-containers",
        "set-of-names-hashes",
    ],
)
def test_what_runs_code_at_import(source: str, expected: Tuple[bool, ...]) -> None:
    assert _runs(source) == expected


def _observable_registries(setup: str, snippet: str) -> str:
    """Whether running ``snippet`` after ``setup`` changes a registry a program could observe.

    Checked in a fresh interpreter: loggers the logging manager holds,
    overloads typing records, non-standard modules imported, exit handlers,
    pickling reducers, and any warning issued. Modules of the standard
    library a callable imports lazily are no effect of the program's.
    """
    probe = (
        textwrap.dedent("""
        import atexit, copyreg, logging, sys, typing, warnings
        namespace = {"__name__": "probed"}
        exec(compile(SETUP, "<setup>", "exec"), namespace)
        def state():
            return (
                sorted(logging.Logger.manager.loggerDict),
                sum(len(v) for v in typing._overload_registry.values()),
                sorted(
                    name for name in sys.modules
                    if name.split(".")[0] not in sys.stdlib_module_names
                ),
                atexit._ncallbacks(),
                sorted(map(repr, copyreg.dispatch_table)),
            )
        before = state()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            exec(compile(SNIPPET, "<snippet>", "exec"), namespace)
        after = state()
        print("changed" if before != after or caught else "unchanged")
        """)
        .replace("SETUP", repr(textwrap.dedent(setup)))
        .replace("SNIPPET", repr(textwrap.dedent(snippet)))
    )
    return subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()


def _spelled(dotted: str) -> Tuple[str, str]:
    """The import that makes ``dotted`` reachable, and how code then spells it."""
    module, _, name = dotted.rpartition(".")
    return ("", name) if module == "builtins" else (f"import {module}\n", dotted)


@pytest.mark.parametrize(
    "dotted",
    sorted(name for name in _QUIET_FUNCTION_DECORATORS if not name.startswith("typing_extensions")),
)
def test_each_quiet_function_decorator_registers_nothing(dotted: str) -> None:
    setup, spelled = _spelled(dotted)
    snippet = f"class Box:\n    @{spelled}\n    def f(self):\n        return 1\n"
    if "contextmanager" in dotted:
        snippet = snippet.replace("return 1", "yield 1")
    assert _observable_registries(setup, snippet) == "unchanged"


@pytest.mark.parametrize(
    "dotted",
    sorted(name for name in _QUIET_CLASS_DECORATORS if not name.startswith("typing_extensions")),
)
def test_each_quiet_class_decorator_registers_nothing(dotted: str) -> None:
    setup, spelled = _spelled(dotted)
    # Each decorator is applied to a class it accepts.
    bases = {"typing.runtime_checkable": "(typing.Protocol)", "enum.unique": "(enum.Enum)"}
    body = "    def __lt__(self, other):\n        return False\n"
    snippet = f"@{spelled}\nclass Box{bases.get(dotted, '')}:\n    x: int = 1\n{body}"
    assert _observable_registries("import enum, typing\n" + setup, snippet) == "unchanged"


@pytest.mark.parametrize(
    "call",
    [
        "typing.TypeVar('T', bound=int)",
        "typing.ParamSpec('P')",
        "typing.TypeVarTuple('Ts')",
        "typing.NewType('UserId', int)",
        "dataclasses.field(default=0)",
        "property(lambda self: 1)",
        "staticmethod(len)",
        "object()",
        "re.compile(r'\\d+', re.I)",
    ],
)
def test_each_quiet_constructor_registers_nothing(call: str) -> None:
    assert _observable_registries("import dataclasses, re, typing\n", f"VALUE = {call}\n") == (
        "unchanged"
    )


@pytest.mark.parametrize(
    "base", sorted(_QUIET_BASES - {"typing_extensions.Protocol"}) + ["typing.Generic[T]"]
)
def test_each_quiet_base_registers_nothing_on_subclassing(base: str) -> None:
    body = "    x: int\n" if base.endswith("NamedTuple") else "    pass\n"
    setup = f"import typing, {base.split('[')[0].rpartition('.')[0]}\nT = typing.TypeVar('T')\n"
    snippet = f"class Box({base}):\n{body}"
    assert _observable_registries(setup, snippet) == "unchanged"


def test_overload_is_code_because_its_registry_sees_an_earlier_import() -> None:
    assert (
        _observable_registries("import typing\n", "@typing.overload\ndef f(x: int) -> int: ...\n")
        == "changed"
    )


def test_get_logger_is_code_because_a_later_dict_config_disables_it() -> None:
    # In a fresh interpreter: ``dictConfig`` disables every logger that
    # exists when it runs, including this suite's own.
    probe = textwrap.dedent("""
        import logging, logging.config
        early = logging.getLogger("towel.test.early")
        logging.config.dictConfig({"version": 1})
        late = logging.getLogger("towel.test.late")
        print(early.disabled, late.disabled)
        """)
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert completed.stdout.split() == ["True", "False"]


def test_init_subclass_of_a_base_runs_project_code() -> None:
    seen: list[str] = []

    class Base:
        def __init_subclass__(cls) -> None:
            seen.append(cls.__name__)

    class Child(Base):
        pass

    assert seen == ["Child"]
    assert _runs("""
        class Base:
            def __init_subclass__(cls):
                pass

        class Child(Base):
            pass
        """) == (False, True)


def test_a_helper_is_not_placed_after_a_class_whose_base_runs_code() -> None:
    source = textwrap.dedent("""
        class Registered:
            def __init_subclass__(cls):
                cls.install()

        class Plugin(Registered):
            @classmethod
            def install(cls):
                pass

        class Late:
            pass
        """)
    assert InsertionPoints.placeable_after(source) == {"Registered"}

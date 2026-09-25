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

"""Which decorators can reach a block, and which of them Towel knows to leave the body alone.

A decorator that compiles or instruments the body it decorates (typeguard's
``@typechecked``, numba's ``@njit``) loses a block moved into a helper, so a
block is extracted, and a call site or helper placed, only where every
decorator reaching the code is known (``towel.unification.decorator_reach``).
The table below pins the verdict for each way of spelling a decorator:
aliases, shadowing, factories, class decorators, nested functions, and
decorators the project defines. The hostile battery runs the instrumenting
shapes (``r7d_*`` in ``tests/hostile_cases``); the last test here holds every
entry of the allowlist to a verification note and a program that still
refactors.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from towel.unification import known_decorators
from towel.unification.decorator_reach import Definition, ModuleSource, decorator_refusal
from towel.unification.known_decorators import KNOWN_DECORATORS, KnownDecorator
from towel.unification.import_graph import ImportGraphCache
from towel.unification.models import RejectReason
from towel.unification.refactor_engine import UnificationRefactorEngine


@dataclass(frozen=True)
class Spelling:
    """A module (and any modules beside it), the definition asked about, and the verdict."""

    name: str
    source: str
    target: str
    """The definition's path through enclosing definitions: ``f``, ``C.m``, ``outer.inner``."""
    refused: Optional[str]
    """The decorator the refusal names, or None when every decorator reaching it is known."""
    others: Dict[str, str] = field(default_factory=dict)
    """Other files of the project, by path relative to its root."""
    module: str = "m.py"


def _find(tree: ast.Module, target: str) -> Definition:
    """The definition ``target`` names, the last of its name at each level (a property's setter)."""
    body: List[ast.stmt] = tree.body
    found: Optional[Definition] = None
    for part in target.split("."):
        candidates = [node for node in _held(body) if node.name == part]
        assert candidates, f"no {part} in {target}"
        found = candidates[-1]
        body = found.body
    assert found is not None
    return found


def _held(body: List[ast.stmt]) -> List[Definition]:
    """The definitions ``body`` holds in its own scope, those in its compound statements included."""
    found: List[Definition] = []
    for statement in body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append(statement)
            continue
        for suite in ("body", "orelse", "finalbody"):
            found.extend(_held(getattr(statement, suite, [])))
    return found


def _verdict(root: Path, case: Spelling) -> Optional[str]:
    (root / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n')
    for relative, text in {case.module: case.source, **case.others}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    path = root / case.module
    source = path.read_text()
    tree = ast.parse(source)
    refusal = decorator_refusal(
        _find(tree, case.target), ModuleSource(str(path), source, tree), ImportGraphCache()
    )
    return None if refusal is None else refusal.decorator


_WRAPPER = """
import functools

def logged(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        print("call", fn.__name__)
        return fn(*args, **kwargs)
    return wrapper
"""

_TRACED = """
import inspect

def traced(fn):
    namespace = {}
    exec(compile(inspect.getsource(fn), "<traced>", "exec"), fn.__globals__, namespace)
    return namespace[fn.__name__]
"""

SPELLINGS: Tuple[Spelling, ...] = (
    # -- instrumenting decorators, as reported -------------------------------
    Spelling(
        "typechecked",
        "from typeguard import typechecked\n@typechecked\ndef f(x):\n    return x\n",
        "f",
        "typeguard.typechecked",
    ),
    Spelling("njit", "import numba\n@numba.njit\ndef f(x):\n    return x\n", "f", "numba.njit"),
    # -- aliases --------------------------------------------------------------
    Spelling(
        "wraps imported under another name",
        """
        from functools import wraps as w
        def deco(fn):
            @w(fn)
            def inner(*args, **kwargs):
                return fn(*args, **kwargs)
            return inner
        """,
        "deco.inner",
        None,
    ),
    Spelling(
        "module imported under another name",
        "import functools as ft\n@ft.lru_cache(maxsize=None)\ndef f(x):\n    return x\n",
        "f",
        None,
    ),
    Spelling(
        "decorator imported under another name",
        "from functools import lru_cache as memo\n@memo\ndef f(x):\n    return x\n",
        "f",
        None,
    ),
    Spelling(
        "alias assignment",
        "import functools\ncached = functools.cache\n@cached\ndef f(x):\n    return x\n",
        "f",
        None,
    ),
    Spelling(
        "typing as t",
        "import typing as t\nclass C:\n    @t.overload\n    def m(self, x: int) -> int: ...\n",
        "C.m",
        None,
    ),
    Spelling(
        "a mark imported from pytest",
        "from pytest import mark\n@mark.parametrize('x', [1])\ndef test_f(x):\n    return x\n",
        "test_f",
        None,
    ),
    Spelling(
        "unittest.mock through its package",
        "import unittest.mock\n@unittest.mock.patch('os.getcwd')\ndef f(getcwd):\n    return 1\n",
        "f",
        None,
    ),
    Spelling(
        "patch.object through mock",
        "import os\nfrom unittest import mock\n"
        "@mock.patch.object(os, 'getcwd')\ndef f(getcwd):\n    return 1\n",
        "f",
        None,
    ),
    Spelling(
        "compatibility import in try",
        """
        try:
            from typing import override
        except ImportError:
            from typing_extensions import override
        class C:
            @override
            def m(self):
                return 1
        """,
        "C.m",
        None,
    ),
    # -- shadowing ------------------------------------------------------------
    Spelling(
        "module-level property that instruments",
        _TRACED
        + "property = traced\nclass C:\n    @property\n    def m(self):\n        return 1\n",
        "C.m",
        "property",
    ),
    Spelling(
        "class-body staticmethod",
        "class C:\n    staticmethod = print\n    @staticmethod\n    def m():\n        return 1\n",
        "C.m",
        "staticmethod",
    ),
    Spelling(
        "decorator bound in the enclosing function",
        """
        def outer(make):
            lru_cache = make()
            @lru_cache
            def inner():
                return 1
            return inner
        """,
        "outer.inner",
        "lru_cache",
    ),
    Spelling(
        "known name rebound later in the module",
        _TRACED + "from functools import lru_cache\n@lru_cache\ndef f():\n    return 1\n"
        "lru_cache = traced\n",
        "f",
        "lru_cache",
    ),
    Spelling(
        "known name a function rebinds with global",
        "from functools import cache\n@cache\ndef f():\n    return 1\n"
        "def reset():\n    global cache\n    cache = None\n",
        "f",
        "cache",
    ),
    Spelling(
        "star import may bind anything",
        "from os.path import *\nclass C:\n    @property\n    def m(self):\n        return 1\n",
        "C.m",
        "property",
    ),
    Spelling(
        "the builtin, unshadowed",
        "class C:\n    @property\n    def m(self):\n        return 1\n",
        "C.m",
        None,
    ),
    # -- factories ------------------------------------------------------------
    Spelling(
        "pytest.mark.parametrize",
        "import pytest\n@pytest.mark.parametrize('x', [1, 2])\ndef test_f(x):\n    return x\n",
        "test_f",
        None,
    ),
    Spelling(
        "click options on a command",
        "import click\n@click.command()\n@click.option('--x')\ndef main(x):\n    return x\n",
        "main",
        None,
    ),
    Spelling(
        "click command with its own class",
        "import click\nclass Cmd(click.Command): pass\n"
        "@click.command(cls=Cmd)\ndef main():\n    return 1\n",
        "main",
        "click.command",
    ),
    Spelling(
        "a method of an application object",
        "from flask import Flask\napp = Flask(__name__)\n"
        "@app.route('/x')\ndef view():\n    return 'x'\n",
        "view",
        "app.route",
    ),
    Spelling(
        "wraps used bare",
        "import functools\n@functools.wraps\ndef f(x):\n    return x\n",
        "f",
        "functools.wraps",
    ),
    Spelling(
        "plain factory the project defines",
        """
        import functools
        def retry(times):
            def deco(fn):
                @functools.wraps(fn)
                def wrapper(*args, **kwargs):
                    for _ in range(times):
                        try:
                            return fn(*args, **kwargs)
                        except OSError:
                            pass
                return wrapper
            return deco
        @retry(3)
        def f():
            return 1
        """,
        "f",
        None,
    ),
    Spelling(
        "factory whose decorator reads the code",
        """
        def retry(times):
            def deco(fn):
                deco.code = fn.__code__
                return fn
            return deco
        @retry(3)
        def f():
            return 1
        """,
        "f",
        "retry",
    ),
    Spelling(
        "a mark kept in a module name",
        "import pytest\nneeds_db = pytest.mark.skipif(True, reason='no db')\n"
        "@needs_db\ndef test_f():\n    return 1\n",
        "test_f",
        None,
    ),
    Spelling(
        "a mark kept in a module name, called again",
        "import pytest\nneeds_db = pytest.mark.skipif(True, reason='no db')\n"
        "@needs_db()\ndef test_f():\n    return 1\n",
        "test_f",
        "needs_db",
    ),
    Spelling(
        "the call of an unknown factory kept in a module name",
        "import numba\nfast = numba.jit(nopython=True)\n@fast\ndef f():\n    return 1\n",
        "f",
        "numba.jit",
    ),
    Spelling(
        "one decorator per branch, every one known",
        """
        import sys
        if sys.version_info >= (3, 13):
            from warnings import deprecated
        else:
            import functools
            import warnings

            def deprecated(message):
                def decorator(func):
                    @functools.wraps(func)
                    def wrapper(*args, **kwargs):
                        warnings.warn(message, DeprecationWarning, stacklevel=2)
                        return func(*args, **kwargs)
                    return wrapper
                return decorator
        class Version:
            @deprecated("private")
            def m(self):
                return 1
        """,
        "Version.m",
        None,
    ),
    Spelling(
        "one decorator per branch, one of them unknown",
        """
        try:
            from typeguard import typechecked as checked
        except ImportError:
            def checked(fn):
                return fn
        @checked
        def f():
            return 1
        """,
        "f",
        "typeguard.typechecked",
    ),
    # -- class decorators -----------------------------------------------------
    Spelling(
        "instrumenting class decorator",
        "from typeguard import typechecked\n@typechecked\nclass C:\n    def m(self):\n        return 1\n",
        "C.m",
        "typeguard.typechecked",
    ),
    Spelling(
        "dataclass",
        "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass C:\n    x: int\n"
        "    def m(self):\n        return self.x\n",
        "C.m",
        None,
    ),
    Spelling(
        "total_ordering",
        "import functools\n@functools.total_ordering\nclass C:\n"
        "    def __lt__(self, other):\n        return True\n",
        "C.__lt__",
        None,
    ),
    Spelling(
        "a function decorator on a class",
        "import functools\n@functools.lru_cache\nclass C:\n    def m(self):\n        return 1\n",
        "C.m",
        "functools.lru_cache",
    ),
    Spelling(
        "class decorator the project defines that wraps every method",
        """
        def logged(cls):
            for name, fn in list(vars(cls).items()):
                setattr(cls, name, fn)
            return cls
        @logged
        class C:
            def m(self):
                return 1
        """,
        "C.m",
        "logged",
    ),
    Spelling(
        "class registry the project defines",
        "CLASSES = []\ndef register(cls):\n    CLASSES.append(cls)\n    return cls\n"
        "@register\nclass C:\n    def m(self):\n        return 1\n",
        "C.m",
        None,
    ),
    Spelling(
        "pytest mark on a test class",
        "import pytest\n@pytest.mark.usefixtures('db')\nclass TestC:\n"
        "    def test_m(self):\n        return 1\n",
        "TestC.test_m",
        None,
    ),
    Spelling(
        "a helper's host class, instrumented",
        "from typeguard import typechecked\n@typechecked\nclass C:\n    pass\n",
        "C",
        "typeguard.typechecked",
    ),
    Spelling(
        "a helper's host class, a dataclass",
        "import dataclasses\n@dataclasses.dataclass\nclass C:\n    x: int = 0\n",
        "C",
        None,
    ),
    Spelling(
        "decorator on a class enclosing the class",
        "from typeguard import typechecked\n@typechecked\nclass Outer:\n    class Inner:\n"
        "        def m(self):\n            return 1\n",
        "Outer.Inner.m",
        "typeguard.typechecked",
    ),
    # -- nested functions -----------------------------------------------------
    Spelling(
        "decorator on the enclosing function",
        "from numba import njit\n@njit\ndef outer():\n    def inner():\n        return 1\n"
        "    return inner()\n",
        "outer.inner",
        "numba.njit",
    ),
    Spelling(
        "a method of a class in a decorated function",
        "from numba import njit\n@njit\ndef outer():\n    class C:\n        def m(self):\n"
        "            return 1\n    return C\n",
        "outer.C.m",
        "numba.njit",
    ),
    Spelling("the wrapper of a plain decorator", _WRAPPER, "logged.wrapper", None),
    Spelling(
        "an unknown decorator on a nested function",
        "def outer(deco):\n    @deco\n    def inner():\n        return 1\n    return inner\n",
        "outer.inner",
        "deco",
    ),
    # -- property accessors ---------------------------------------------------
    Spelling(
        "property setter",
        "class C:\n    @property\n    def x(self):\n        return 1\n"
        "    @x.setter\n    def x(self, value):\n        self._x = value\n",
        "C.x",
        None,
    ),
    Spelling(
        "setter of something not a property",
        "class C:\n    x = object()\n    @x.setter\n    def x(self, value):\n        pass\n",
        "C.x",
        "x.setter",
    ),
    Spelling(
        "setter of a cached_property",
        "import functools\nclass C:\n    @functools.cached_property\n    def x(self):\n"
        "        return 1\n    @x.setter\n    def x(self, value):\n        pass\n",
        "C.x",
        "x.setter",
    ),
    # -- decorators of other project modules ----------------------------------
    Spelling(
        "relative import of a plain wrapper",
        "from .decorators import logged\n@logged\ndef f():\n    return 1\n",
        "f",
        None,
        others={"pkg/__init__.py": "", "pkg/decorators.py": _WRAPPER},
        module="pkg/m.py",
    ),
    Spelling(
        "absolute import of an instrumenting decorator",
        "from pkg.decorators import traced\n@traced\ndef f():\n    return 1\n",
        "f",
        "pkg.decorators.traced",
        others={"pkg/__init__.py": "", "pkg/decorators.py": _TRACED},
        module="pkg/m.py",
    ),
    Spelling(
        "re-exported plain wrapper",
        "from pkg import logged\n@logged\ndef f():\n    return 1\n",
        "f",
        None,
        others={
            "pkg/__init__.py": "from .decorators import logged\n",
            "pkg/decorators.py": _WRAPPER,
        },
        module="pkg/m.py",
    ),
    Spelling(
        "submodule imported from its package",
        "from . import decorators\n@decorators.logged\ndef f():\n    return 1\n",
        "f",
        None,
        others={"pkg/__init__.py": "", "pkg/decorators.py": _WRAPPER},
        module="pkg/m.py",
    ),
    Spelling(
        "project module named like a listed library",
        "import click\n@click.command()\ndef main():\n    return 1\n",
        "main",
        "click.command",
        others={"click/__init__.py": _TRACED + "def command():\n    return traced\n"},
    ),
    # -- decorators applied by hand -------------------------------------------
    Spelling(
        "compiled by hand",
        "import numba\ndef kernel(x):\n    return x\nfast = numba.njit(kernel)\n",
        "kernel",
        "numba.njit",
    ),
    Spelling(
        "rebound by hand",
        "from typeguard import typechecked\ndef f(x):\n    return x\nf = typechecked(f)\n",
        "f",
        "typeguard.typechecked",
    ),
    Spelling(
        "method wrapped in its class body",
        "from tracer import trace\nclass C:\n    def m(self):\n        return 1\n    m = trace(m)\n",
        "C.m",
        "tracer.trace",
    ),
    Spelling(
        "factory applied by hand",
        "from numba import njit\ndef kernel(x):\n    return x\nfast = njit(cache=True)(kernel)\n",
        "kernel",
        "numba.njit",
    ),
    Spelling(
        "known factory applied by hand",
        "import functools\ndef f(x):\n    return x\nf = functools.lru_cache(maxsize=None)(f)\n",
        "f",
        None,
    ),
    Spelling(
        "known decorator applied by hand",
        "import functools\ndef f(x):\n    return x\ncached = functools.cache(f)\n",
        "f",
        None,
    ),
    Spelling(
        "property built by hand",
        "class C:\n    def getx(self):\n        return 1\n    def setx(self, value):\n"
        "        pass\n    x = property(getx, setx)\n",
        "C.setx",
        None,
    ),
    Spelling(
        "a sort key assigned at module level",
        "def key(v):\n    return -v\nORDER = sorted([3, 1], key=key)\n",
        "key",
        "builtins.sorted",
    ),
    Spelling(
        "a class decorated by hand",
        "from typeguard import typechecked\nclass C:\n    def m(self):\n        return 1\n"
        "C = typechecked(C)\n",
        "C.m",
        "typeguard.typechecked",
    ),
    Spelling(
        "an alias compiled by hand",
        "import numba\ndef kernel(x):\n    return x\nk = kernel\nfast = numba.njit(k)\n",
        "kernel",
        "numba.njit",
    ),
    Spelling(
        "compiled by hand in another module",
        "def slow(x):\n    return x\n",
        "slow",
        "numba.njit",
        others={
            "pkg/__init__.py": "",
            "pkg/fast.py": "from numba import njit\nfrom .kernels import slow\nfast = njit(slow)\n",
        },
        module="pkg/kernels.py",
    ),
    Spelling(
        "compiled by hand through its module",
        "def slow(x):\n    return x\n",
        "slow",
        "numba.njit",
        others={
            "pkg/__init__.py": "",
            "pkg/fast.py": "import numba\nfrom . import kernels\nfast = numba.njit(kernels.slow)\n",
        },
        module="pkg/kernels.py",
    ),
    Spelling(
        "a name that cannot be followed is taken at its word",
        "from os.path import *\ndef kernel(x):\n    return x\n",
        "kernel",
        "numba.njit",
        others={"other.py": "import numba\nfrom os.path import *\nfast = numba.njit(kernel)\n"},
    ),
    Spelling(
        "a plain decorator of the project applied by hand",
        _WRAPPER + "def f():\n    return 1\nf = logged(f)\n",
        "f",
        None,
    ),
    Spelling(
        "a project function given the function among other arguments",
        "def register(name, fn):\n    return fn\ndef f():\n    return 1\nf = register('f', f)\n",
        "f",
        "register",
    ),
    Spelling(
        "handed to a call inside a function body (not seen)",
        "import threading\ndef work():\n    return 1\ndef start():\n"
        "    thread = threading.Thread(target=work)\n    return thread\n",
        "work",
        None,
    ),
    Spelling(
        "handed to a call in an expression statement (not seen)",
        "import atexit\ndef cleanup():\n    return 1\natexit.register(cleanup)\n",
        "cleanup",
        None,
    ),
    # -- class machinery ------------------------------------------------------
    Spelling(
        "a metaclass of the project",
        "class Traced(type):\n    pass\nclass C(metaclass=Traced):\n    def m(self):\n"
        "        return 1\n",
        "C.m",
        "metaclass Traced",
    ),
    Spelling(
        "an __init_subclass__ on a base",
        "class Base:\n    def __init_subclass__(cls, **kwargs):\n"
        "        super().__init_subclass__(**kwargs)\nclass C(Base):\n    def m(self):\n"
        "        return 1\n",
        "C.m",
        "__init_subclass__ of Base",
    ),
    Spelling(
        "a metaclass of a base in another module",
        "from .base import Base\nclass C(Base):\n    def m(self):\n        return 1\n",
        "C.m",
        "metaclass Meta",
        others={
            "pkg/__init__.py": "",
            "pkg/base.py": "class Meta(type):\n    pass\nclass Base(metaclass=Meta):\n    pass\n",
        },
        module="pkg/m.py",
    ),
    Spelling(
        "a library base read in its source",
        "import unittest\nclass T(unittest.TestCase):\n    def test_m(self):\n        return 1\n",
        "T.test_m",
        None,
    ),
    Spelling(
        "a library base subscripted",
        "from collections.abc import Mapping\nclass M(Mapping[str, int]):\n"
        "    def __len__(self):\n        return 0\n",
        "M.__len__",
        None,
    ),
    Spelling(
        "a base outside the project nobody read",
        "import pydantic\nclass Model(pydantic.BaseModel):\n    def m(self):\n        return 1\n",
        "Model.m",
        "base pydantic.BaseModel",
    ),
    Spelling(
        "a builtin imported for compatibility",
        "try:\n    from builtins import object\nexcept ImportError:\n    pass\n"
        "class State(object):\n    def m(self):\n        return 1\n",
        "State.m",
        None,
    ),
    Spelling(
        "an ancestor skipped by unittest",
        "import unittest\n@unittest.skipIf(True, 'no graphviz')\nclass Base(unittest.TestCase):\n"
        "    pass\nclass T(Base):\n    def test_m(self):\n        return 1\n",
        "T.test_m",
        None,
    ),
    Spelling(
        "an ancestor whose decorator wraps its tests",
        "import unittest\nfrom unittest import mock\n@mock.patch('os.getcwd')\n"
        "class Base(unittest.TestCase):\n    pass\nclass T(Base):\n    def test_m(self):\n"
        "        return 1\n",
        "T.test_m",
        "decorator mock.patch of Base",
    ),
    Spelling(
        "a nested class with no bases",
        "class Outer:\n    class Inner:\n        def m(self):\n            return 1\n",
        "Outer.Inner.m",
        None,
    ),
    Spelling(
        "a nested class with a base",
        "class Base:\n    pass\nclass Outer:\n    class Inner(Base):\n        def m(self):\n"
        "            return 1\n",
        "Outer.Inner.m",
        "class Inner (not at module level, with bases)",
    ),
    Spelling(
        "a local class binding __init_subclass__",
        "def make():\n    class Local:\n        def __init_subclass__(cls):\n            pass\n"
        "        def m(self):\n            return 1\n    return Local\n",
        "make.Local.m",
        "__init_subclass__ of Local",
    ),
    Spelling(
        "ABC, ABCMeta, an enum and Generic",
        "import abc\nimport enum\nimport typing\nT = typing.TypeVar('T')\n"
        "class A(abc.ABC):\n    pass\nclass B(metaclass=abc.ABCMeta):\n    pass\n"
        "class Colour(enum.Enum):\n    RED = 1\nclass C(A, B, typing.Generic[T]):\n"
        "    def m(self):\n        return 1\n",
        "C.m",
        None,
    ),
    # -- what a plain decorator may do with the function ----------------------
    Spelling(
        "wrapper passing other arguments",
        "def deco(fn):\n    def wrapper(*args, **kwargs):\n"
        "        return fn(*args, extra=1, **kwargs)\n    return wrapper\n@deco\ndef f():\n    return 1\n",
        "f",
        "deco",
    ),
    Spelling(
        "wrapper calling in a lambda",
        "def deco(fn):\n    def wrapper(*args):\n        return run(lambda: fn(*args))\n"
        "    return wrapper\n@deco\ndef f():\n    return 1\n",
        "f",
        "deco",
    ),
    Spelling(
        "decorator passing the function to inspect",
        "import inspect\ndef deco(fn):\n    inspect.signature(fn)\n    return fn\n"
        "@deco\ndef f():\n    return 1\n",
        "f",
        "deco",
    ),
    Spelling(
        "decorator replacing the code",
        "def deco(fn):\n    fn.__code__ = (lambda: 2).__code__\n    return fn\n"
        "@deco\ndef f():\n    return 1\n",
        "f",
        "deco",
    ),
    Spelling(
        "registry that is not a display",
        "REGISTRY = make()\ndef deco(fn):\n    REGISTRY.append(fn)\n    return fn\n"
        "@deco\ndef f():\n    return 1\n",
        "f",
        "deco",
    ),
    Spelling(
        "decorator returning another object",
        "class Command:\n    def __init__(self, fn):\n        self.fn = fn\n"
        "def deco(fn):\n    return Command(fn)\n@deco\ndef f():\n    return 1\n",
        "f",
        "deco",
    ),
    Spelling(
        "decorator using setattr",
        "def deco(fn):\n    setattr(fn, 'hook', True)\n    return fn\n@deco\ndef f():\n    return 1\n",
        "f",
        "deco",
    ),
    Spelling(
        "registry keyed by name, and a mark",
        "HANDLERS = {}\nHOOKS = []\ndef deco(fn):\n    HANDLERS[fn.__name__] = fn\n"
        "    HOOKS.append(fn)\n    fn.is_hook = True\n    return fn\n@deco\ndef f():\n    return 1\n",
        "f",
        None,
    ),
    Spelling(
        "async wrapper with update_wrapper",
        "import functools\ndef deco(fn):\n    async def wrapper(*args, **kwargs):\n"
        "        return await fn(*args, **kwargs)\n    return functools.update_wrapper(wrapper, fn)\n"
        "@deco\nasync def f():\n    return 1\n",
        "f",
        None,
    ),
)


@pytest.mark.parametrize("case", SPELLINGS, ids=[case.name for case in SPELLINGS])
def test_decorator_spelling_verdict(tmp_path: Path, case: Spelling) -> None:
    assert _verdict(tmp_path, case) == case.refused


# -- What the rest of the program may bind a decorator's name to ------------------
#
# Round-4 audit P1-07: ``app.checks.checked`` was a no-op, and a test setup
# module set ``app.checks.checked = typeguard.typechecked`` before ``app.core``
# was imported, so the function it decorated was instrumented after all. A
# binding is known only if every write the program may make over it is known.

_CHECKED = "def checked(fn):\n    return fn\n"


def _rebound(
    name: str,
    rebinding: str,
    refused: Optional[str],
    *,
    where: str = "enable.py",
    checks: str = _CHECKED,
) -> Spelling:
    """``@checked``, the plain wrapper of ``pkg/checks.py``, with a module ``where`` beside it."""
    return Spelling(
        name,
        "from pkg.checks import checked\n@checked\ndef f():\n    return 1\n",
        "f",
        refused,
        others={
            "pkg/__init__.py": "",
            "pkg/checks.py": checks,
            **({where: rebinding} if rebinding else {}),
        },
        module="pkg/m.py",
    )


_TYPECHECKED = "import typeguard\nimport pkg.checks\npkg.checks.checked = typeguard.typechecked\n"

REBINDINGS: Tuple[Spelling, ...] = (
    _rebound("no module rebinds it", "", None),
    _rebound("an attribute store in another module", _TYPECHECKED, "pkg.checks.checked"),
    _rebound(
        "an attribute store through a relative import",
        "import typeguard\nfrom . import checks\nchecks.checked = typeguard.typechecked\n",
        "pkg.checks.checked",
        where="pkg/enable.py",
    ),
    _rebound(
        "an attribute store to a known decorator",
        "import functools\nimport pkg.checks\npkg.checks.checked = functools.cache\n",
        None,
    ),
    _rebound(
        "an attribute store to a known factory's decorator",
        "import functools\nimport pkg.checks\n"
        "pkg.checks.checked = functools.lru_cache(maxsize=None)\n",
        None,
    ),
    _rebound(
        "an attribute store in a function, whose value is not read",
        "import functools\nimport pkg.checks\ndef enable():\n"
        "    pkg.checks.checked = functools.cache\n",
        "pkg.checks.checked",
    ),
    _rebound(
        "an attribute store of another name",
        "import pkg.checks\npkg.checks.VERBOSE = True\n",
        None,
    ),
    _rebound(
        "setattr with its name",
        "import functools\nimport pkg.checks\nsetattr(pkg.checks, 'checked', functools.cache)\n",
        "pkg.checks.checked",
    ),
    _rebound(
        "setattr with another name",
        "import pkg.checks\nsetattr(pkg.checks, 'VERBOSE', True)\n",
        None,
    ),
    _rebound(
        "setattr with a name computed",
        "import pkg.checks\ndef enable(name, value):\n    setattr(pkg.checks, name, value)\n",
        "pkg.checks.checked",
    ),
    _rebound(
        "a store into the module's __dict__",
        "import typeguard\nimport pkg.checks\n"
        "pkg.checks.__dict__['checked'] = typeguard.typechecked\n",
        "pkg.checks.checked",
    ),
    _rebound(
        "an update of the module's __dict__",
        "import pkg.checks\npkg.checks.__dict__.update(checked=print)\n",
        "pkg.checks.checked",
    ),
    _rebound(
        "globals() of its own module, after the definition",
        "",
        "pkg.checks.checked",
        checks=_CHECKED + "globals()['checked'] = print\n",
    ),
    # Counted before the definition too: an import cycle may run the
    # decorator between the store and the def.
    _rebound(
        "globals() of its own module, before the definition",
        "",
        "pkg.checks.checked",
        checks="globals()['checked'] = print\n" + _CHECKED,
    ),
    _rebound(
        "globals() of its own module with a name computed",
        "",
        "pkg.checks.checked",
        checks=_CHECKED + "def define(name, value):\n    globals()[name] = value\n",
    ),
    _rebound(
        "globals() of another module",
        "globals()['checked'] = print\n",
        None,
    ),
    _rebound(
        "importlib.reload of its module",
        "import importlib\nimport pkg.checks\nimportlib.reload(pkg.checks)\n",
        "pkg.checks.checked",
    ),
    # The module may be any, the one the decorator is read in first.
    _rebound(
        "reload of a module not known",
        "from importlib import reload\ndef again(module):\n    return reload(module)\n",
        "checked",
    ),
    _rebound(
        "mock.patch of it in a test",
        "from unittest import mock\n@mock.patch('pkg.checks.checked')\n"
        "def test_it(checked):\n    pass\n",
        "pkg.checks.checked",
    ),
    _rebound(
        "monkeypatch.setattr of it in a test",
        "import pkg.checks\ndef test_it(monkeypatch):\n"
        "    monkeypatch.setattr(pkg.checks, 'checked', print)\n",
        "pkg.checks.checked",
    ),
    _rebound(
        "the importer's own binding of it",
        "import typeguard\nimport pkg.m\npkg.m.checked = typeguard.typechecked\n",
        "typeguard.typechecked",
    ),
    Spelling(
        "a library decorator rebound in the project",
        "import functools\n@functools.cache\ndef f():\n    return 1\n",
        "f",
        "functools.cache",
        others={"enable.py": "import functools\nimport numba\nfunctools.cache = numba.njit\n"},
    ),
    Spelling(
        "a library decorator rebound to a known one",
        "import functools\n@functools.cache\ndef f():\n    return 1\n",
        "f",
        None,
        others={
            "enable.py": "import functools\nfunctools.cache = functools.lru_cache(maxsize=None)\n"
        },
    ),
    Spelling(
        "a builtin decorator rebound in builtins",
        "class C:\n    @property\n    def m(self):\n        return 1\n",
        "C.m",
        "builtins.property",
        others={"enable.py": "import builtins\nbuiltins.property = print\n"},
    ),
    Spelling(
        "a builtin decorator's name written into the module",
        "class C:\n    @property\n    def m(self):\n        return 1\n",
        "C.m",
        "builtins.print",
        others={"enable.py": "import m\nm.property = print\n"},
    ),
    Spelling(
        "functools.wraps of a plain wrapper rebound",
        "from .decorators import logged\n@logged\ndef f():\n    return 1\n",
        "f",
        "logged",
        others={
            "pkg/__init__.py": "",
            "pkg/decorators.py": _WRAPPER,
            "enable.py": "import functools\nfunctools.wraps = print\n",
        },
        module="pkg/m.py",
    ),
    Spelling(
        "the registry of a plain registering decorator rebound",
        "from .decorators import register\n@register\ndef f():\n    return 1\n",
        "f",
        "register",
        others={
            "pkg/__init__.py": "",
            "pkg/decorators.py": "HANDLERS = {}\ndef register(fn):\n"
            "    HANDLERS[fn.__name__] = fn\n    return fn\n",
            "pkg/enable.py": "from . import decorators\ndecorators.HANDLERS = None\n",
        },
        module="pkg/m.py",
    ),
)


# Round-4 audit P2-02: any star import made every decorator of the module
# unknown, though the provider's ``__all__ = ["scale"]`` cannot bind
# ``staticmethod``. A star import makes unknown only a name it may bind.

_SHAPES = "{star}\nclass Shapes:\n    @staticmethod\n    def square(k):\n        return scale(k)\n"
_SCALE = "def scale(v):\n    return v * 3\n"


def _starred(
    name: str,
    common: str,
    refused: Optional[str],
    *,
    star: str = "from .common import *",
    others: Optional[Dict[str, str]] = None,
) -> Spelling:
    """``@staticmethod`` in ``pkg/shapes.py``, which star-imports ``pkg/common.py``."""
    return Spelling(
        name,
        _SHAPES.format(star=star),
        "Shapes.square",
        refused,
        others={"pkg/__init__.py": "", "pkg/common.py": common, **(others or {})},
        module="pkg/shapes.py",
    )


_STATICMETHOD = "def staticmethod(fn):\n    return fn\n"

STAR_IMPORTS: Tuple[Spelling, ...] = (
    _starred("a literal __all__", "__all__ = ['scale']\n" + _SCALE, None),
    _starred("a literal __all__ as a tuple", "__all__ = ('scale',)\n" + _SCALE, None),
    _starred(
        "a literal __all__ naming it",
        "__all__ = ['scale', 'staticmethod']\n" + _SCALE + _STATICMETHOD,
        "staticmethod",
    ),
    # A module imported part way through an import cycle exports what it has
    # bound so far, whatever its __all__ will say.
    _starred(
        "a literal __all__ leaving out a public name",
        "__all__ = ['scale']\n" + _SCALE + _STATICMETHOD,
        "staticmethod",
    ),
    _starred("no __all__", _SCALE + "_cache = {}\n", None),
    _starred("no __all__, a public name", _SCALE + _STATICMETHOD, "staticmethod"),
    _starred(
        "no __all__, a name a function declares global",
        _SCALE + "def setup():\n    global staticmethod\n    staticmethod = print\n",
        "staticmethod",
    ),
    _starred(
        "no __all__, a name the project writes into it",
        _SCALE,
        "staticmethod",
        others={"enable.py": "import pkg.common\npkg.common.staticmethod = print\n"},
    ),
    _starred(
        "a dynamic __all__",
        "__all__ = ['scale']\n__all__ += ['extra']\nextra = 1\n" + _SCALE,
        "staticmethod",
    ),
    _starred(
        "an __all__ built by a call",
        "__all__ = list(['scale'])\n" + _SCALE,
        "staticmethod",
    ),
    _starred(
        "a nested star import",
        "from .base import *\n",
        None,
        others={"pkg/base.py": "__all__ = ['scale']\n" + _SCALE},
    ),
    _starred(
        "a nested star import binding it",
        "from .base import *\n",
        "staticmethod",
        others={"pkg/base.py": _SCALE + _STATICMETHOD},
    ),
    _starred("a cycle of star imports", "from .shapes import *\n" + _SCALE, "staticmethod"),
    _starred(
        "an absolute star import of the project",
        "__all__ = ['scale']\n" + _SCALE,
        None,
        star="from pkg.common import *",
    ),
    _starred(
        "a star import from outside the project",
        _SCALE,
        "staticmethod",
        star="from requests import *",
    ),
    _starred(
        "a star import of the standard library",
        _SCALE,
        "staticmethod",
        star="from os.path import *",
    ),
    _starred(
        "a star import of a module not found",
        _SCALE,
        "staticmethod",
        star="from .missing import *",
    ),
    Spelling(
        "a registry beside a star import that cannot bind it",
        "from .common import *\nHANDLERS = {}\ndef register(fn):\n"
        "    HANDLERS[fn.__name__] = fn\n    return fn\n@register\ndef f():\n    return scale(1)\n",
        "f",
        None,
        others={"pkg/__init__.py": "", "pkg/common.py": "__all__ = ['scale']\n" + _SCALE},
        module="pkg/m.py",
    ),
    Spelling(
        "a registry a star import may bind",
        "from .common import *\nHANDLERS = {}\ndef register(fn):\n"
        "    HANDLERS[fn.__name__] = fn\n    return fn\n@register\ndef f():\n    return scale(1)\n",
        "f",
        "register",
        others={"pkg/__init__.py": "", "pkg/common.py": _SCALE + "HANDLERS = None\n"},
        module="pkg/m.py",
    ),
)


@pytest.mark.parametrize(
    "case", REBINDINGS + STAR_IMPORTS, ids=[case.name for case in REBINDINGS + STAR_IMPORTS]
)
def test_r9dc_rebinding_and_star_import_verdict(tmp_path: Path, case: Spelling) -> None:
    assert _verdict(tmp_path, case) == case.refused


# -- The engine declines, and names the decorator --------------------------------

_DUPLICATED = """
{prelude}

{decorator}
def first(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 3


{decorator}
def second(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 5
"""


_DECLINED = str(RejectReason.DECORATOR_MAY_TRANSFORM_BODY)


def _analyze(path: Path) -> Tuple[int, Dict[str, int]]:
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        proposals = engine.analyze_file(str(path))
    return len(proposals), dict(engine.declined_pairs)


def test_declined_pairs_are_counted_under_the_decorator_they_name(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(_DUPLICATED.format(prelude="import numba", decorator="@numba.njit"))
    proposals, declined = _analyze(path)
    assert proposals == 0
    assert set(declined) == {f"{_DECLINED}[numba.njit]"}


def test_a_clustered_site_under_an_unknown_decorator_keeps_its_code(tmp_path: Path) -> None:
    third = _DUPLICATED.split("{decorator}\ndef second")[1].replace("second", "third")
    path = tmp_path / "m.py"
    path.write_text(
        _DUPLICATED.format(prelude="import numba", decorator="")
        + "\n\n@numba.njit\ndef third"
        + third.replace("{decorator}", "")
    )
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        proposals = engine.analyze_file(str(path))
    lines = path.read_text().splitlines()
    third_line = next(i for i, line in enumerate(lines, 1) if line.startswith("def third"))
    assert proposals
    for proposal in proposals:
        assert all(r.line_range[0] < third_line for r in proposal.replacements)


def test_a_helper_is_not_hosted_where_a_decorator_may_instrument_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Hosts are chosen among what encloses the blocks, which the blocks' own
    # check covers; the host is checked all the same, so a placement that one
    # day chooses otherwise is declined too. Here the placement is forced.
    from towel.unification.models import HelperHome
    from towel.unification.pair_evaluation import PairEvaluation

    path = tmp_path / "m.py"
    path.write_text(
        _DUPLICATED.format(prelude=_TRACED + "\n@traced\nclass Host:\n    pass\n", decorator="")
    )

    def forced_home(self: PairEvaluation, pair: object, *args: object) -> HelperHome:
        return HelperHome(str(path), "Host", None, "staticmethod", None)

    monkeypatch.setattr(PairEvaluation, "_helper_home", forced_home)
    proposals, declined = _analyze(path)
    assert proposals == 0
    assert f"{_DECLINED}[traced]" in declined


# -- Every entry of the allowlist -------------------------------------------------


@dataclass(frozen=True)
class Usage:
    """A program using an allowlist entry on two functions (or classes) that share a block."""

    prelude: str
    decorator: str
    shape: str = "function"
    """``function``: two decorated functions; ``method``: two decorated methods of ``Holder``;
    ``static``: the same without a receiver; ``class``: two methods of a decorated class;
    ``accessor``: two properties' decorated accessors; ``bound``: two methods of a class a
    call names as the ``bound=`` of a type variable it assigns."""


_BLOCK = """total = 0
for item in {items}:
    total = total + item * 2
result = total + 7
"""


def _program(usage: Usage) -> str:
    """The module for ``usage``: its prelude, then the two definitions sharing one block."""

    def body(indent: str, items: str, ret: str) -> str:
        return textwrap.indent(_BLOCK.format(items=items) + ret, indent)

    decorator = usage.decorator
    if usage.shape == "function":
        return f"{usage.prelude}\n" + "".join(
            f"\n@{decorator}\ndef {name}(items):\n{body('    ', 'items', f'return result * {k}')}\n"
            for name, k in (("first", 3), ("second", 5))
        )
    if usage.shape in {"method", "static"}:
        parameters, items = ("self", "self.items") if usage.shape == "method" else ("", "ITEMS")
        members = "".join(
            f"\n    @{decorator}\n    def {name}({parameters}):\n"
            f"{body('        ', items, f'return result * {k}')}\n"
            for name, k in (("first", 3), ("second", 5))
        )
        return f"{usage.prelude}\nITEMS = (1, 5, 9)\n\nclass Holder:\n    items = ITEMS\n{members}"
    if usage.shape == "class":
        members = "".join(
            f"\n    def {name}(self):\n{body('        ', 'self.items', f'return result * {k}')}\n"
            for name, k in (("first", 3), ("second", 5))
        )
        return f"{usage.prelude}\n\n@{decorator}\nclass Holder:\n    items = (1, 5, 9)\n{members}"
    if usage.shape == "bound":
        members = "".join(
            f"\n    def {name}(self):\n{body('        ', 'self.items', f'return result * {k}')}\n"
            for name, k in (("first", 3), ("second", 5))
        )
        return (
            f"{usage.prelude}\n\nclass Holder:\n    items = (1, 5, 9)\n{members}\n"
            f"T = {decorator}('T', bound=Holder)\n"
        )
    assert usage.shape == "accessor"
    accessor = decorator.rpartition(".")[2]
    members = "".join(
        f"\n    @property\n    def {name}(self):\n        return 0\n"
        f"\n    @{name}.{accessor}\n    def {name}(self{', value' if accessor == 'setter' else ''}):\n"
        f"{body('        ', 'self.items', f'self.stored = result * {k}')}\n"
        for name, k in (("first", 3), ("second", 5))
    )
    return f"{usage.prelude}\n\nclass Holder:\n    items = (1, 5, 9)\n{members}"


USAGES: Dict[str, Usage] = {
    "builtins.property": Usage("", "property", "method"),
    "builtins.property.setter": Usage("", "x.setter", "accessor"),
    "builtins.property.getter": Usage("", "x.getter", "accessor"),
    "builtins.property.deleter": Usage("", "x.deleter", "accessor"),
    "builtins.staticmethod": Usage("", "staticmethod", "static"),
    "builtins.classmethod": Usage("", "classmethod", "method"),
    "functools.wraps": Usage(
        "import functools\n\ndef original():\n    pass", "functools.wraps(original)"
    ),
    "functools.cache": Usage("import functools", "functools.cache"),
    "functools.lru_cache": Usage("from functools import lru_cache", "lru_cache(maxsize=64)"),
    "functools.cached_property": Usage("import functools", "functools.cached_property", "method"),
    "functools.singledispatch": Usage("import functools", "functools.singledispatch"),
    "functools.singledispatchmethod": Usage(
        "import functools", "functools.singledispatchmethod", "method"
    ),
    "functools.partialmethod": Usage("import functools", "functools.partialmethod", "method"),
    "functools.total_ordering": Usage("import functools", "functools.total_ordering", "class"),
    "contextlib.contextmanager": Usage("import contextlib", "contextlib.contextmanager"),
    "contextlib.asynccontextmanager": Usage("import contextlib", "contextlib.asynccontextmanager"),
    "abc.abstractmethod": Usage("import abc", "abc.abstractmethod", "method"),
    "typing.overload": Usage("import typing", "typing.overload", "method"),
    "typing_extensions.overload": Usage("import typing_extensions", "typing_extensions.overload"),
    "typing.override": Usage("from typing import override", "override", "method"),
    "typing_extensions.override": Usage(
        "import typing_extensions", "typing_extensions.override", "method"
    ),
    "typing.final": Usage("from typing import final", "final", "class"),
    "typing_extensions.final": Usage("import typing_extensions", "typing_extensions.final"),
    "typing.no_type_check": Usage("import typing", "typing.no_type_check"),
    "typing_extensions.no_type_check": Usage(
        "import typing_extensions", "typing_extensions.no_type_check", "class"
    ),
    "typing.runtime_checkable": Usage("import typing", "typing.runtime_checkable", "class"),
    "typing_extensions.runtime_checkable": Usage(
        "import typing_extensions", "typing_extensions.runtime_checkable", "class"
    ),
    "typing.dataclass_transform": Usage("import typing", "typing.dataclass_transform()"),
    "typing_extensions.dataclass_transform": Usage(
        "import typing_extensions", "typing_extensions.dataclass_transform()", "class"
    ),
    "warnings.deprecated": Usage("import warnings", "warnings.deprecated('old')"),
    "typing_extensions.deprecated": Usage(
        "import typing_extensions", "typing_extensions.deprecated('old')", "class"
    ),
    "typing.TypeVar": Usage("import typing", "typing.TypeVar", "bound"),
    "typing_extensions.TypeVar": Usage(
        "import typing_extensions", "typing_extensions.TypeVar", "bound"
    ),
    "dataclasses.dataclass": Usage("import dataclasses", "dataclasses.dataclass", "class"),
    "enum.unique": Usage("import enum", "enum.unique", "class"),
    "unittest.mock.patch": Usage("from unittest import mock", "mock.patch('os.getcwd')"),
    "unittest.mock.patch.object": Usage(
        "import os\nfrom unittest import mock", "mock.patch.object(os, 'getcwd')"
    ),
    "unittest.mock.patch.dict": Usage(
        "import os\nfrom unittest import mock", "mock.patch.dict(os.environ, {})", "class"
    ),
    "unittest.mock.patch.multiple": Usage(
        "import os\nfrom unittest import mock", "mock.patch.multiple(os, getcwd=None)"
    ),
    "unittest.skip": Usage("import unittest", "unittest.skip('slow')"),
    "unittest.skipIf": Usage("import unittest", "unittest.skipIf(False, 'never')", "class"),
    "unittest.skipUnless": Usage("import unittest", "unittest.skipUnless(True, 'always')"),
    "unittest.expectedFailure": Usage("import unittest", "unittest.expectedFailure"),
    "pytest.fixture": Usage("import pytest", "pytest.fixture(scope='module')"),
    "pytest.mark.*": Usage("import pytest", "pytest.mark.slow"),
    "click.command": Usage("import click", "click.command()"),
    "click.group": Usage("import click", "click.group"),
    "click.option": Usage("import click", "click.option('--x')"),
    "click.argument": Usage("import click", "click.argument('x')"),
    "click.confirmation_option": Usage("import click", "click.confirmation_option()"),
    "click.password_option": Usage("import click", "click.password_option()"),
    "click.version_option": Usage("import click", "click.version_option('1.0')"),
    "click.help_option": Usage("import click", "click.help_option()"),
    "click.pass_context": Usage("import click", "click.pass_context"),
    "click.pass_obj": Usage("import click", "click.pass_obj"),
    "rich.repr.auto": Usage("import rich.repr", "rich.repr.auto", "class"),
    "rich.repr.rich_repr": Usage(
        "from rich.repr import rich_repr", "rich_repr(angular=True)", "class"
    ),
}

_VERSION = re.compile(r"\b\d+\.\d+(\.\d+)?\b")


@pytest.mark.parametrize("entry", KNOWN_DECORATORS, ids=[e.origin for e in KNOWN_DECORATORS])
def test_every_known_decorator_is_verified_and_still_refactors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: KnownDecorator
) -> None:
    # The note records the version read and why the body is untouched.
    assert _VERSION.search(entry.note), entry.note
    assert len(entry.note) > 60, entry.note
    usage = USAGES.get(entry.origin)
    assert usage is not None, f"no program uses {entry.origin}"
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n')
    path = tmp_path / "m.py"
    path.write_text(_program(usage))
    proposals, declined = _analyze(path)
    assert proposals, (path.read_text(), declined)
    assert not any(key.startswith(_DECLINED) for key in declined), declined
    # The program refactors because of this entry: without it, the pairs are
    # declined under the name the entry reads.
    monkeypatch.setattr(
        known_decorators,
        "_BY_ORIGIN",
        {
            origin: known
            for origin, known in known_decorators._BY_ORIGIN.items()
            if known is not entry
        },
    )
    _, declined = _analyze(path)
    named = {key[len(_DECLINED) + 1 : -1] for key in declined if key.startswith(_DECLINED)}
    assert named and all(
        name == entry.origin or entry.origin.endswith(".*") and name.startswith(entry.origin[:-1])
        for name in named
    ), declined


def test_every_program_above_belongs_to_an_entry() -> None:
    assert set(USAGES) == {entry.origin for entry in KNOWN_DECORATORS}

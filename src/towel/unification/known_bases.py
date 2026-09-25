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

"""Library classes read in their source and found to build a subclass with Python's own machinery.

The method-host test (``ImportTimeCode.hosts_method_helpers``) accepts a base
only when it can read what subclassing it runs: a builtin, ``abc.ABC``,
``typing.Generic[...]``, an enum, or a class of the project that qualifies in
turn. A class outside the project it cannot read, so ``class T(TestCase)``
failed it, though ``TestCase`` is built by ``type`` and its one
``__init_subclass__`` touches no method. Each entry here was read in
CPython 3.11.15, 3.12.13 and 3.13.7: every class on its method resolution
order but ``object`` is written in Python, declares no metaclass but
``ABCMeta`` (through ``abc.ABC``, where it does), and defines no
``__init_subclass__`` and no ``__getattribute__``, except where the entry
names the ``__init_subclass__`` it read. tests/test_decorator_reach.py checks
the same of the running interpreter, so a Python that changes one fails
the suite. Third-party classes are not listed: none has been read.

``ANCESTOR_CLASS_DECORATORS`` are the class decorators that, read in the
same sources (see ``known_decorators``), return the class they are given
with its namespace, adding no metaclass, ``__init_subclass__`` or
``__getattribute__``: a class of the project carrying one is still a quiet
ancestor for the method-host test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional, Sequence, Tuple

_CPYTHON = "CPython 3.11.15, 3.12.13, 3.13.7"


@dataclass(frozen=True)
class KnownBase:
    """A library class whose subclasses Python's own machinery builds, and the reading behind it."""

    origins: Tuple[str, ...]
    """Every absolute name the class is imported by: ``unittest.TestCase``, ``unittest.case.TestCase``."""
    note: str
    init_subclass: Optional[str] = None
    """The one ``__init_subclass__`` on its order, as ``module.Class``, when it has one."""
    subscripted: bool = False
    """Also accepted subscripted (``Mapping[str, int]``), which ``__class_getitem__`` allows."""


def _read(module: str, classes: str) -> str:
    return (
        f"{_CPYTHON}, {module}: {classes} define no __init_subclass__, no __getattribute__"
        " and no metaclass, so a subclass is built by type."
    )


def _read_abc(module: str, classes: str) -> str:
    return (
        f"{_CPYTHON}, {module}: {classes} define no __init_subclass__ and no __getattribute__;"
        " their metaclass is ABCMeta, whose __new__ only records the abstract methods."
    )


def _names(module: str, public: str, names: Sequence[str]) -> Tuple[Tuple[str, str], ...]:
    """Each class under its public module and the module that defines it: ``(public, defining)``."""
    return tuple((f"{public}.{name}", f"{module}.{name}") for name in names)


_COLLECTIONS_ABC = (
    "Mapping",
    "MutableMapping",
    "Sequence",
    "MutableSequence",
    "Set",
    "MutableSet",
    "Sized",
    "Iterable",
    "Iterator",
    "Container",
    "Hashable",
    "Awaitable",
    "AsyncIterator",
    "AsyncIterable",
    "Generator",
    "Collection",
)
_ASYNCIO_PROTOCOLS = (
    "BaseProtocol",
    "Protocol",
    "BufferedProtocol",
    "DatagramProtocol",
    "SubprocessProtocol",
)

KNOWN_BASES: Tuple[KnownBase, ...] = (
    KnownBase(
        ("unittest.TestCase", "unittest.case.TestCase"),
        f"{_CPYTHON}, unittest/case.py: TestCase declares no metaclass and no"
        " __getattribute__; its __init_subclass__ sets _classSetupFailed and _class_cleanups"
        " on the subclass and calls object's; no method is read or replaced.",
        init_subclass="unittest.case.TestCase",
    ),
    KnownBase(
        ("unittest.IsolatedAsyncioTestCase", "unittest.async_case.IsolatedAsyncioTestCase"),
        f"{_CPYTHON}, unittest/async_case.py: IsolatedAsyncioTestCase adds no metaclass,"
        " __init_subclass__ or __getattribute__ to TestCase's (unittest/case.py, read).",
        init_subclass="unittest.case.TestCase",
    ),
    KnownBase(
        ("unittest.TestResult", "unittest.result.TestResult"),
        _read("unittest/result.py", "TestResult"),
    ),
    KnownBase(
        ("unittest.TextTestResult", "unittest.runner.TextTestResult"),
        _read("unittest/runner.py and result.py", "TextTestResult and TestResult"),
    ),
    *(
        KnownBase(
            pair,
            _read("asyncio/protocols.py", "BaseProtocol and its subclasses"),
        )
        for pair in _names("asyncio.protocols", "asyncio", _ASYNCIO_PROTOCOLS)
    ),
    KnownBase(("ast.NodeVisitor",), _read("ast.py", "NodeVisitor")),
    KnownBase(("ast.NodeTransformer",), _read("ast.py", "NodeTransformer and NodeVisitor")),
    KnownBase(("logging.Filterer",), _read("logging/__init__.py", "Filterer")),
    KnownBase(("logging.Filter",), _read("logging/__init__.py", "Filter")),
    KnownBase(("logging.Formatter",), _read("logging/__init__.py", "Formatter")),
    KnownBase(("logging.Handler",), _read("logging/__init__.py", "Handler and Filterer")),
    KnownBase(
        ("logging.StreamHandler",),
        _read("logging/__init__.py", "StreamHandler, Handler and Filterer"),
    ),
    KnownBase(("threading.Thread",), _read("threading.py", "Thread")),
    KnownBase(
        ("html.parser.HTMLParser",),
        _read("html/parser.py and _markupbase.py", "HTMLParser and ParserBase"),
    ),
    KnownBase(
        ("json.JSONEncoder", "json.encoder.JSONEncoder"), _read("json/encoder.py", "JSONEncoder")
    ),
    KnownBase(
        ("json.JSONDecoder", "json.decoder.JSONDecoder"), _read("json/decoder.py", "JSONDecoder")
    ),
    KnownBase(("argparse.Action",), _read("argparse.py", "Action and _AttributeHolder")),
    KnownBase(("argparse.HelpFormatter",), _read("argparse.py", "HelpFormatter")),
    KnownBase(
        ("argparse.ArgumentParser",),
        _read("argparse.py", "ArgumentParser, _AttributeHolder and _ActionsContainer"),
    ),
    KnownBase(("argparse.Namespace",), _read("argparse.py", "Namespace and _AttributeHolder")),
    KnownBase(
        ("http.server.BaseHTTPRequestHandler",),
        _read(
            "http/server.py and socketserver.py",
            "BaseHTTPRequestHandler, StreamRequestHandler and BaseRequestHandler",
        ),
    ),
    KnownBase(("socketserver.ThreadingMixIn",), _read("socketserver.py", "ThreadingMixIn")),
    KnownBase(("string.Formatter",), _read("string.py", "Formatter")),
    KnownBase(("textwrap.TextWrapper",), _read("textwrap.py", "TextWrapper")),
    KnownBase(("contextlib.ContextDecorator",), _read("contextlib.py", "ContextDecorator")),
    KnownBase(
        ("contextlib.AbstractContextManager",),
        _read_abc("contextlib.py", "AbstractContextManager and abc.ABC"),
        subscripted=True,
    ),
    KnownBase(
        ("contextlib.AbstractAsyncContextManager",),
        _read_abc("contextlib.py", "AbstractAsyncContextManager and abc.ABC"),
        subscripted=True,
    ),
    KnownBase(
        ("collections.UserDict",),
        _read_abc("collections/__init__.py and _collections_abc.py", "UserDict and its bases"),
    ),
    *(
        KnownBase(
            (public,),
            _read_abc("_collections_abc.py", f"{public.rpartition('.')[2]} and its bases"),
            # Sized and Hashable define no __class_getitem__.
            subscripted=public not in {"collections.abc.Sized", "collections.abc.Hashable"},
        )
        for public, _ in _names("_collections_abc", "collections.abc", _COLLECTIONS_ABC)
    ),
    KnownBase(("importlib.abc.MetaPathFinder",), _read_abc("importlib/abc.py", "MetaPathFinder")),
    KnownBase(("importlib.abc.Loader",), _read_abc("importlib/_abc.py", "Loader")),
)

KNOWN_BASE_ORIGINS: FrozenSet[str] = frozenset(
    origin for base in KNOWN_BASES for origin in base.origins
)
KNOWN_SUBSCRIPTED_BASE_ORIGINS: FrozenSet[str] = frozenset(
    origin for base in KNOWN_BASES if base.subscripted for origin in base.origins
)

# Read in known_decorators: each returns the class it is given, its namespace
# as it was but for attributes it sets, and adds no metaclass,
# __init_subclass__ or __getattribute__.
ANCESTOR_CLASS_DECORATORS: FrozenSet[str] = frozenset(
    {
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "unittest.expectedFailure",
        "typing.no_type_check",
        "typing_extensions.no_type_check",
        "typing.dataclass_transform",
        "typing_extensions.dataclass_transform",
    }
)

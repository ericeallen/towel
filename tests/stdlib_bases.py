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

"""Standard-library base fixtures with the source notes that originally identified them.

These notes record CPython 3.11.15, 3.12.13 and 3.13.7; they are historical
provenance for a diverse extraction battery, not an allowlist for placement.
The production engine does not consult this module or require a library
base's hooks to match these observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

_CPYTHON = "CPython 3.11.15, 3.12.13, 3.13.7"


@dataclass(frozen=True)
class StandardLibraryBase:
    """A library base fixture and historical source observations about its class hooks."""

    origins: Tuple[str, ...]
    """Every absolute name the class is imported by: ``unittest.TestCase``, ``unittest.case.TestCase``."""
    note: str
    init_subclass: Optional[str] = None
    """The one ``__init_subclass__`` on its order, as ``module.Class``, when it has one."""
    subscripted: bool = False
    """Also tested subscripted (``Mapping[str, int]``), which ``__class_getitem__`` allows."""


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

STDLIB_BASES: Tuple[StandardLibraryBase, ...] = (
    StandardLibraryBase(
        ("unittest.TestCase", "unittest.case.TestCase"),
        f"{_CPYTHON}, unittest/case.py: TestCase declares no metaclass and no"
        " __getattribute__; its __init_subclass__ sets _classSetupFailed and _class_cleanups"
        " on the subclass and calls object's; no method is read or replaced.",
        init_subclass="unittest.case.TestCase",
    ),
    StandardLibraryBase(
        ("unittest.IsolatedAsyncioTestCase", "unittest.async_case.IsolatedAsyncioTestCase"),
        f"{_CPYTHON}, unittest/async_case.py: IsolatedAsyncioTestCase adds no metaclass,"
        " __init_subclass__ or __getattribute__ to TestCase's (unittest/case.py, read).",
        init_subclass="unittest.case.TestCase",
    ),
    StandardLibraryBase(
        ("unittest.TestResult", "unittest.result.TestResult"),
        _read("unittest/result.py", "TestResult"),
    ),
    StandardLibraryBase(
        ("unittest.TextTestResult", "unittest.runner.TextTestResult"),
        _read("unittest/runner.py and result.py", "TextTestResult and TestResult"),
    ),
    *(
        StandardLibraryBase(
            pair,
            _read("asyncio/protocols.py", "BaseProtocol and its subclasses"),
        )
        for pair in _names("asyncio.protocols", "asyncio", _ASYNCIO_PROTOCOLS)
    ),
    StandardLibraryBase(("ast.NodeVisitor",), _read("ast.py", "NodeVisitor")),
    StandardLibraryBase(
        ("ast.NodeTransformer",), _read("ast.py", "NodeTransformer and NodeVisitor")
    ),
    StandardLibraryBase(("logging.Filterer",), _read("logging/__init__.py", "Filterer")),
    StandardLibraryBase(("logging.Filter",), _read("logging/__init__.py", "Filter")),
    StandardLibraryBase(("logging.Formatter",), _read("logging/__init__.py", "Formatter")),
    StandardLibraryBase(("logging.Handler",), _read("logging/__init__.py", "Handler and Filterer")),
    StandardLibraryBase(
        ("logging.StreamHandler",),
        _read("logging/__init__.py", "StreamHandler, Handler and Filterer"),
    ),
    StandardLibraryBase(("threading.Thread",), _read("threading.py", "Thread")),
    StandardLibraryBase(
        ("html.parser.HTMLParser",),
        _read("html/parser.py and _markupbase.py", "HTMLParser and ParserBase"),
    ),
    StandardLibraryBase(
        ("json.JSONEncoder", "json.encoder.JSONEncoder"), _read("json/encoder.py", "JSONEncoder")
    ),
    StandardLibraryBase(
        ("json.JSONDecoder", "json.decoder.JSONDecoder"), _read("json/decoder.py", "JSONDecoder")
    ),
    StandardLibraryBase(("argparse.Action",), _read("argparse.py", "Action and _AttributeHolder")),
    StandardLibraryBase(("argparse.HelpFormatter",), _read("argparse.py", "HelpFormatter")),
    StandardLibraryBase(
        ("argparse.ArgumentParser",),
        _read("argparse.py", "ArgumentParser, _AttributeHolder and _ActionsContainer"),
    ),
    StandardLibraryBase(
        ("argparse.Namespace",), _read("argparse.py", "Namespace and _AttributeHolder")
    ),
    StandardLibraryBase(
        ("http.server.BaseHTTPRequestHandler",),
        _read(
            "http/server.py and socketserver.py",
            "BaseHTTPRequestHandler, StreamRequestHandler and BaseRequestHandler",
        ),
    ),
    StandardLibraryBase(
        ("socketserver.ThreadingMixIn",), _read("socketserver.py", "ThreadingMixIn")
    ),
    StandardLibraryBase(("string.Formatter",), _read("string.py", "Formatter")),
    StandardLibraryBase(("textwrap.TextWrapper",), _read("textwrap.py", "TextWrapper")),
    StandardLibraryBase(
        ("contextlib.ContextDecorator",), _read("contextlib.py", "ContextDecorator")
    ),
    StandardLibraryBase(
        ("contextlib.AbstractContextManager",),
        _read_abc("contextlib.py", "AbstractContextManager and abc.ABC"),
        subscripted=True,
    ),
    StandardLibraryBase(
        ("contextlib.AbstractAsyncContextManager",),
        _read_abc("contextlib.py", "AbstractAsyncContextManager and abc.ABC"),
        subscripted=True,
    ),
    StandardLibraryBase(
        ("collections.UserDict",),
        _read_abc("collections/__init__.py and _collections_abc.py", "UserDict and its bases"),
    ),
    *(
        StandardLibraryBase(
            (public,),
            _read_abc("_collections_abc.py", f"{public.rpartition('.')[2]} and its bases"),
            # Sized and Hashable define no __class_getitem__.
            subscripted=public not in {"collections.abc.Sized", "collections.abc.Hashable"},
        )
        for public, _ in _names("_collections_abc", "collections.abc", _COLLECTIONS_ABC)
    ),
    StandardLibraryBase(
        ("importlib.abc.MetaPathFinder",), _read_abc("importlib/abc.py", "MetaPathFinder")
    ),
    StandardLibraryBase(("importlib.abc.Loader",), _read_abc("importlib/_abc.py", "Loader")),
)

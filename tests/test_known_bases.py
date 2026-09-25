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

"""The library bases and ancestor decorators the method-host test accepts, held to the running Python.

Each entry of ``known_bases`` was read in the library's source; these tests
ask the interpreter the suite runs on the same questions, so a Python that
gives a base a metaclass, an ``__init_subclass__`` or a ``__getattribute__``
fails here before Towel moves code out of, or into, its subclasses. The
predicate itself is asked of each entry too.
"""

from __future__ import annotations

import abc
import importlib
import re
import types
import unittest

import pytest

from towel.unification.import_graph import ImportTimeCode
from towel.unification.known_bases import ANCESTOR_CLASS_DECORATORS, KNOWN_BASES, KnownBase

_VERSION = re.compile(r"\b3\.1[1-3]\.\d+\b")


def _resolve(dotted: str) -> object:
    module, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(module), name)


def _named(klass: type) -> str:
    return f"{klass.__module__}.{klass.__qualname__}"


@pytest.mark.parametrize("base", KNOWN_BASES, ids=[base.origins[0] for base in KNOWN_BASES])
def test_each_known_base_is_built_by_pythons_own_machinery(base: KnownBase) -> None:
    classes = {_resolve(origin) for origin in base.origins}
    assert len(classes) == 1, base.origins  # every spelling names one class
    (cls,) = classes
    assert isinstance(cls, type)
    assert type(cls) in (type, abc.ABCMeta), type(cls)
    init_subclass = {
        _named(k) for k in cls.__mro__ if k is not object and "__init_subclass__" in vars(k)
    }
    assert init_subclass == ({base.init_subclass} if base.init_subclass else set())
    assert not [k for k in cls.__mro__ if k is not object and "__getattribute__" in vars(k)]
    assert _VERSION.search(base.note) and len(base.note) > 60, base.note
    # A subclass keeps a function of its body as given.

    def method(self: object) -> int:
        return 1

    built = types.new_class("Box", (cls,), {}, lambda namespace: namespace.update(m=method))
    assert vars(built)["m"] is method


@pytest.mark.parametrize("origin", sorted(o for b in KNOWN_BASES for o in b.origins))
def test_the_method_host_test_accepts_each_known_base(origin: str) -> None:
    module, _, name = origin.rpartition(".")
    source = f"from {module} import {name}\n\n\nclass Box({name}):\n    v = 1\n"
    assert ImportTimeCode(source).hosts_method_helpers("Box")


def test_a_base_nobody_read_is_still_refused() -> None:
    source = "from pydantic import BaseModel\n\n\nclass Box(BaseModel):\n    v = 1\n"
    assert not ImportTimeCode(source).hosts_method_helpers("Box")


def _decorated(dotted: str) -> type:
    """A class with one method, through ``dotted`` applied as the decorator is written."""

    def method(self: object) -> int:
        return 1

    cls: type = types.new_class("Box", (), {}, lambda namespace: namespace.update(m=method))
    decorator = _resolve(dotted)
    if dotted.endswith(("skipIf", "skipUnless")):
        decorator = decorator(dotted.endswith("skipIf"), "skipped")  # type: ignore[operator]
    elif dotted.endswith("skip"):
        decorator = decorator("skipped")  # type: ignore[operator]
    elif dotted.endswith("dataclass_transform"):
        decorator = decorator()  # type: ignore[operator]
    returned = decorator(cls)  # type: ignore[operator]
    kept = returned is cls
    assert kept and vars(cls)["m"] is method
    return cls


@pytest.mark.parametrize("dotted", sorted(ANCESTOR_CLASS_DECORATORS))
def test_each_ancestor_decorator_keeps_the_class_and_adds_no_machinery(dotted: str) -> None:
    cls = _decorated(dotted)
    assert type(cls) is type
    assert "__init_subclass__" not in vars(cls) and "__getattribute__" not in vars(cls)


def test_a_class_private_helper_is_collected_by_neither_runner() -> None:
    """Hosting in a ``TestCase`` subclass adds ``_Case__extracted_func_0``, which no loader collects."""

    class Case(unittest.TestCase):
        def test_first(self) -> None:
            self.__extracted_func_0()

        def __extracted_func_0(self) -> None:
            pass

    names = unittest.TestLoader().getTestCaseNames(Case)
    assert names == ["test_first"]
    assert not any(name.startswith("test") for name in vars(Case) if "extracted" in name)

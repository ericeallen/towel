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

"""Supported instrumentation is protected across decorator, call, and hook syntax."""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest

from towel.unification.decorator_reach import ModuleSource, decorator_refusal
from towel.unification.import_graph import ImportGraphCache


def refusal(root: Path, source: str) -> str | None:
    path = root / "subject.py"
    source = textwrap.dedent(source)
    path.write_text(source)
    (root / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    tree = ast.parse(source)
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "C")
    method = next(
        node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == "f"
    )
    result = decorator_refusal(method, ModuleSource(str(path), source, tree), ImportGraphCache())
    return None if result is None else result.decorator


@pytest.mark.parametrize(
    "application",
    [
        "@checked\nclass C:\n    def f(self): return 1\n",
        "class C:\n    @checked\n    def f(self): return 1\n",
        "class C:\n    def f(self): return 1\nC = checked(C)\n",
        "class C:\n    def f(self): return 1\nchecked(C)\n",
        "class C:\n    def f(self): return 1\ndef setup():\n    checked(C)\nsetup()\n",
        "class Meta(type):\n    def __new__(meta, name, bases, namespace):\n"
        "        for key, method in list(namespace.items()):\n"
        "            namespace[key] = checked(method)\n"
        "        return super().__new__(meta, name, bases, namespace)\n"
        "class C(metaclass=Meta):\n    def f(self): return 1\n",
        "class Meta(type):\n    def __new__(meta, name, bases, namespace):\n"
        "        return checked(super().__new__(meta, name, bases, namespace))\n"
        "class C(metaclass=Meta):\n    def f(self): return 1\n",
        "class Meta(type):\n    def __init__(cls, name, bases, namespace):\n"
        "        checked(cls)\n"
        "class C(metaclass=Meta):\n    def f(self): return 1\n",
        "class Base:\n    def __init_subclass__(cls):\n        checked(cls)\n"
        "class C(Base):\n    def f(self): return 1\n",
        "class Base:\n    def __init_subclass__(cls):\n"
        "        for method in vars(cls).values():\n            checked(method)\n"
        "class C(Base):\n    def f(self): return 1\n",
        "class Base:\n    def __init_subclass__(cls):\n"
        "        for name, method in cls.__dict__.items():\n            checked(method)\n"
        "class Middle(Base): pass\nclass C(Middle):\n    def f(self): return 1\n",
    ],
)
def test_typeguard_keeps_the_same_boundary(tmp_path: Path, application: str) -> None:
    assert (
        refusal(tmp_path, "from typeguard import typechecked as checked\n" + application)
        == "typeguard.typechecked"
    )


@pytest.mark.parametrize(
    "body",
    [
        "print(tuple(namespace))",
        "for name, method in namespace.items():\n            print(method.__name__)",
        "def later():\n            checked(namespace['f'])",
        "checked(123)",
    ],
)
def test_observing_names_or_an_unrelated_argument_is_not_instrumentation(
    tmp_path: Path, body: str
) -> None:
    source = (
        "from typeguard import typechecked as checked\n"
        "class Meta(type):\n    def __new__(meta, name, bases, namespace):\n"
        f"        {body}\n        return super().__new__(meta, name, bases, namespace)\n"
        "class C(metaclass=Meta):\n    def f(self): return 1\n"
    )
    assert refusal(tmp_path, source) is None


def test_a_shadowed_instrumenter_is_resolved_by_binding(tmp_path: Path) -> None:
    assert (
        refusal(
            tmp_path,
            "def checked(value): return value\n"
            "class Base:\n    def __init_subclass__(cls):\n        checked(cls)\n"
            "class C(Base):\n    def f(self): return 1\n",
        )
        is None
    )


def test_instrumentation_in_an_imported_metaclass_is_followed(tmp_path: Path) -> None:
    (tmp_path / "support.py").write_text(
        "from typeguard import typechecked as checked\n"
        "class Meta(type):\n    def __new__(meta, name, bases, namespace):\n"
        "        namespace['f'] = checked(namespace['f'])\n"
        "        return super().__new__(meta, name, bases, namespace)\n"
        "class Base(metaclass=Meta): pass\n"
    )
    assert (
        refusal(
            tmp_path,
            "from support import Base as Parent\nclass C(Parent):\n    def f(self): return 1\n",
        )
        == "typeguard.typechecked"
    )


def test_ordinary_namespace_iteration_reaches_class_methods(tmp_path: Path) -> None:
    """The same method enumeration is protected outside a class hook as inside it."""
    assert (
        refusal(
            tmp_path,
            """
        from typeguard import typechecked as checked
        class C:
            def f(self): return 1
        for name, method in vars(C).items():
            checked(method)
    """,
        )
        == "typeguard.typechecked"
    )


def test_imported_argument_keeps_its_identity_in_an_ordinary_call(tmp_path: Path) -> None:
    """A project import in another module must still point to C, without assignment syntax."""
    (tmp_path / "install.py").write_text(
        "from subject import C\nfrom typeguard import typechecked\ntypechecked(C)\n"
    )
    assert refusal(tmp_path, "class C:\n    def f(self): return 1\n") == "typeguard.typechecked"


def test_bare_class_body_application_targets_the_defined_method(tmp_path: Path) -> None:
    """Local class-body names denote their methods before the class itself is bound."""
    assert (
        refusal(
            tmp_path,
            """
        from typeguard import typechecked as checked
        class C:
            def f(self): return 1
            checked(f)
    """,
        )
        == "typeguard.typechecked"
    )

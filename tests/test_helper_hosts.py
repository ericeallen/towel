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

"""Which classes can take a helper into their body.

A helper placed in a class is one more member of it, and that is invisible
only when the class statement's namespace is what the class ends up with
and nothing treats its members as a contract. The hostile battery runs each
refused shape and compares the program's output (``p10`` to ``p18`` in
``tests/hostile_cases``); these tests pin down the rule that decides, and
that a class the rule accepts still gets its helper as a method.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import shutil
import textwrap
from typing import Optional

import pytest

from towel.unification.module_bindings import global_bindings
from towel.unification.refactor_engine import UnificationRefactorEngine

CASES = Path(__file__).parent / "hostile_cases"


def _refusal(source: str, qualname: str) -> Optional[str]:
    table = global_bindings(textwrap.dedent(source))
    assert table is not None
    return table.refuses_helper(qualname)


@pytest.mark.parametrize(
    "source",
    [
        "class Base: pass\n",
        'class Base: "doc"; k = 0\n',
        "class Base(\n    object,\n): k = 0\n",
        "class Ünïcode: k = 0\n",
    ],
)
def test_a_body_on_the_header_line_takes_no_helper(source: str) -> None:
    qualname = "Ünïcode" if "Ünïcode" in source else "Base"
    assert _refusal(source, qualname) == "body on the header line"


def test_a_body_below_the_header_takes_one() -> None:
    assert _refusal("class Base:\n    k = 0\n", "Base") is None
    assert _refusal("class Base(\n    object,\n):\n    k = 0\n", "Base") is None


@pytest.mark.parametrize(
    "header",
    [
        "from typing import Protocol\nclass P(Protocol):",
        "import typing\nclass P(typing.Protocol):",
        "import typing as t\nclass P(t.Protocol):",
        "from typing import Protocol, TypeVar\nT = TypeVar('T')\nclass P(Protocol[T]):",
        "from typing_extensions import Protocol as Proto\nclass P(Proto):",
        # Whichever import ran, the name is a protocol.
        "try:\n    from typing import Protocol as Proto\n"
        "except ImportError:\n    from typing_extensions import Protocol as Proto\n"
        "class P(Proto):",
        "import typing\nAlias = typing.Protocol\nclass P(Alias):",
        # Spelled like one and bound out of sight: a star import.
        "from typing import *\nclass P(Protocol):",
    ],
)
def test_a_protocol_takes_no_helper(header: str) -> None:
    assert _refusal(header + "\n    k: int\n", "P") == "protocol"


def test_an_implementation_of_a_protocol_takes_one() -> None:
    # A subclass that does not list Protocol again is an ordinary class.
    source = "from typing import Protocol\nclass P(Protocol):\n    k: int\nclass C(P):\n    k = 0\n"
    assert _refusal(source, "C") is None


@pytest.mark.parametrize(
    "decorator",
    [
        "from dataclasses import dataclass\n@dataclass",
        "from dataclasses import dataclass\n@dataclass(frozen=True, slots=True)",
        "import dataclasses\n@dataclasses.dataclass(order=True)",
        "import functools\n@functools.total_ordering",
        "from typing import final\n@final",
        "import enum\n@enum.unique",
    ],
)
def test_decorators_that_keep_the_namespace_allow_a_helper(decorator: str) -> None:
    assert _refusal(decorator + "\nclass C:\n    k = 0\n", "C") is None


@pytest.mark.parametrize(
    "decorator",
    [
        "def register(cls):\n    return cls\n@register",
        # Named like a known decorator, but not the one the allowlist means.
        "from mylib import dataclass\n@dataclass",
        "def dataclass(cls):\n    return cls\n@dataclass",
        "try:\n    from dataclasses import dataclass\nexcept ImportError:\n"
        "    from mylib import dataclass\n@dataclass",
        "import dataclasses\n@dataclasses.dataclass\n@(lambda cls: cls)",
    ],
)
def test_any_other_decorator_refuses_a_helper(decorator: str) -> None:
    assert _refusal(decorator + "\nclass C:\n    k = 0\n", "C") == "decorator"


def test_classes_the_rule_accepts_keep_their_helpers_as_methods(tmp_path: Path) -> None:
    """The positive control: a rule that refused every class would pass the battery."""
    module = tmp_path / "m.py"
    shutil.copy(CASES / "p18_known_class_decorators_keep_the_helper.py", module)
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        _, applied, _ = engine.refactor_to_fixed_point(str(module))
    assert applied == 3
    tree = ast.parse(module.read_text())
    hosts = {
        node.name: [
            item.name
            for item in node.body
            if isinstance(item, ast.FunctionDef) and "extracted_func" in item.name
        ]
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert all(len(hosts[name]) == 1 for name in ("Point", "Rank", "Colour")), hosts
    assert not [
        node for node in tree.body if isinstance(node, ast.FunctionDef)
    ], "every helper is a method"

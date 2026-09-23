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

A helper placed in a class is one more member of it: a body written on the
header's line cannot take the statement, and a ``Protocol`` treats every
member as part of its contract. The hostile battery runs each refused shape
and compares the program's output (``p10`` to ``p14`` in
``tests/hostile_cases``); these tests pin down the rule that decides.
"""

from __future__ import annotations

import textwrap
from typing import Optional

import pytest

from towel.unification.placement import _global_bindings


def _refusal(source: str, qualname: str) -> Optional[str]:
    table = _global_bindings(textwrap.dedent(source))
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

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

"""A method helper keeps the declarations of the attributes it assigns, and ``Any`` does not win a join.

packaging's ``Tag.__init__``/``__setstate__`` needed both. ``__init__`` passes
``interpreter: str``; ``__setstate__`` passes the ``Any`` it read from a pickled
dict; the join was ``Any``. And mypy takes an attribute's type from the class's
first assignment to it, in the order the methods are written: with the helper
at the end of the class, the first was ``__setstate__``'s unpacking of an
``Any`` tuple, so every property returning an attribute returned ``Any``. The
study tried each fix alone and both were refused.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.typed_fixtures import apply_one, requires_mypy

TAG = """
class Tag:
    __slots__ = ["_abi", "_hash", "_interpreter", "_platform"]

    def __init__(self, interpreter: str, abi: str, platform: str) -> None:
        self._interpreter = interpreter.lower()
        self._abi = abi.lower()
        self._platform = platform.lower()
        self._hash = hash((self._interpreter, self._abi, self._platform))

    @property
    def interpreter(self) -> str:
        return self._interpreter

    @property
    def abi(self) -> str:
        return self._abi

    def __hash__(self) -> int:
        return self._hash

    def __str__(self) -> str:
        return f"{self._interpreter}-{self._abi}-{self._platform}"

    def __setstate__(self, state: object) -> None:
        if isinstance(state, tuple):
            if len(state) == 3:
                self._interpreter, self._abi, self._platform = state
                self._hash = hash((self._interpreter, self._abi, self._platform))
                return
            if len(state) == 2 and isinstance(state[1], dict):
                _, slots = state
                interpreter = slots["_interpreter"]
                abi = slots["_abi"]
                platform = slots["_platform"]
                self._interpreter = interpreter.lower()
                self._abi = abi.lower()
                self._platform = platform.lower()
                self._hash = hash((self._interpreter, self._abi, self._platform))
                return
        raise TypeError(f"Cannot restore Tag from {state!r}")
"""


@requires_mypy
def test_the_helper_takes_the_known_type_and_the_place_of_the_first_assignment(
    tmp_path: Path,
) -> None:
    outcome = apply_one(tmp_path, TAG, pick="__init__ and __setstate__")
    assert outcome.error is None, outcome.error
    assert outcome.signature() == "(self, abi: str, interpreter: str, platform: str) -> None"
    assert outcome.module is not None
    owner = ast.parse(outcome.module).body[0]
    assert isinstance(owner, ast.ClassDef)
    members = [node.name for node in owner.body if isinstance(node, ast.FunctionDef)]
    helper = outcome.helper().name
    assert members.index("__init__") + 1 == members.index(helper), members
    assert members.index(helper) < members.index("__setstate__"), members
    assert outcome.prospective_checks == 1, outcome.checked_helpers

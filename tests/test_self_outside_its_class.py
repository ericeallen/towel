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

"""A ``Self`` the sites revealed is spelled, outside a class, as a type variable bound to their classes.

packaging's ``Version.from_parts``/``__replace__`` build ``cls.__new__(cls)``
and return it as ``Self``; the helper they share is a module function, where
``Self`` means nothing and both checkers refuse it. ``_TowelSelf`` bound to
``Version`` gives each caller back its own ``Self``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.typed_fixtures import apply_one, requires_mypy


@requires_mypy
def test_self_becomes_a_type_variable_bound_to_the_sites_classes(tmp_path: Path) -> None:
    outcome = apply_one(
        tmp_path,
        """
        from typing import Self


        class Version:
            def __init__(self, local: tuple[int | str, ...] | None) -> None:
                self._local = local

            @classmethod
            def from_parts(cls, local: tuple[int | str, ...] | None) -> Self:
                new_version = cls.__new__(cls)
                new_version._local = local
                return new_version

            def __replace__(self, local: tuple[int | str, ...] | None) -> Self:
                new_version = self.__class__.__new__(self.__class__)
                new_version._local = local
                return new_version
        """,
        pick="from_parts and __replace__",
    )
    assert outcome.error is None, outcome.error
    assert outcome.module is not None
    tree = ast.parse(outcome.module)
    bounds = [
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "bound" and isinstance(keyword.value, ast.Constant)
    ]
    assert bounds == ["Version"], outcome.module
    signature = outcome.signature()
    assert "Self" not in signature.replace("_TowelSelf", ""), signature
    assert signature.endswith("-> _TowelSelf"), signature
    assert outcome.prospective_checks == 1, outcome.checked_helpers

# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Local syntax proofs that a module-level conditional body cannot execute."""

from __future__ import annotations

import ast
from typing import FrozenSet


def module_false_guards(tree: ast.Module) -> FrozenSet[ast.expr]:
    """Module-level literal False tests and the immutable comparison ``0 > 1``.

    Both integer operands are exact literals, so no name lookup, user
    operator or concurrent rebinding can change the result. Imported flags
    and locally assigned False names are mutable and prove nothing here.
    """
    return frozenset(
        statement.test
        for statement in tree.body
        if isinstance(statement, ast.If) and _false_test(statement.test)
    )


def _false_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Constant):
        return test.value is False
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Constant)
        and type(test.left.value) is int
        and test.left.value == 0
        and len(test.ops) == len(test.comparators) == 1
        and isinstance(test.ops[0], ast.Gt)
        and isinstance(test.comparators[0], ast.Constant)
        and type(test.comparators[0].value) is int
        and test.comparators[0].value == 1
    )

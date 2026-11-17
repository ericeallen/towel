# Copyright 2026 Eric Allen
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

"""Audit R2 / shape 7: one independent case is the useful extraction unit."""

import ast

import pytest

from towel.unification.models import Replacement
from towel.unification.profitability import worthwhile_helper


def _worthwhile(parameters: str, statements: str) -> bool:
    helper = ast.parse(f"def helper({parameters}):\n{statements}").body[0]
    assert isinstance(helper, ast.FunctionDef)
    before = ast.dump(helper)
    sites = [
        Replacement((index, index), ast.parse(f"helper({parameters})").body[0])
        for index in range(20)
    ]
    result = worthwhile_helper(helper, sites)
    assert ast.dump(helper) == before
    return result


@pytest.mark.parametrize("count", [2, 3, 5])
@pytest.mark.parametrize("receiver", ["self", "case"])
def test_independent_case_groups_are_not_joined(count: int, receiver: str) -> None:
    """R2: twenty call sites still do not justify arbitrary groups of assertions."""
    parameters = ", ".join([receiver, *(f"n{i}, expected{i}" for i in range(count))])
    statements = "".join(
        f"    {receiver}.assertEqual(num2words(n{i}, lang='tet'), expected{i})\n"
        for i in range(count)
    )
    assert not _worthwhile(parameters, statements)


def test_single_case_remains_worthwhile() -> None:
    """R2: the existing cost rule can still accept one assertion per helper call."""
    assert _worthwhile(
        "self, number, expected",
        "    self.assertEqual(num2words(number, lang='tet'), expected)\n",
    )


@pytest.mark.parametrize(
    "parameters, statements, worthwhile",
    [
        (
            "self, a, b, expected",
            "    self.assertEqual(num2words(a, lang='tet'), expected)\n"
            "    self.assertEqual(num2words(b, lang='tet'), expected)\n",
            False,
        ),
        (
            "self, a, b, c",
            "    self.assertEqual(num2words(a, lang='tet'), b)\n"
            "    self.assertEqual(num2words(b, lang='tet'), c)\n",
            True,
        ),
        (
            "self, a, b, c, d",
            "    self.assertEqual(num2words(a, lang='tet'), b)\n"
            "    self.assertEqual(num2words(c, lang='en'), d)\n",
            False,
        ),
        (
            "self, a, b",
            "    self.assertEqual(num2words(a, lang='tet'), b)\n"
            "    self.assertEqual(num2words(a, lang='tet'), b)\n",
            False,
        ),
    ],
)
def test_shared_context_preserves_independent_groups_but_overlapping_data_does_not(
    parameters: str, statements: str, worthwhile: bool
) -> None:
    """Re-audit 2: literal changes or identical cases do not make a useful group."""
    # Shared expected values still permit independent groups (the same receiver
    # is shared too); only partial overlap or different structure links cases.
    assert _worthwhile(parameters, statements) is worthwhile

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

"""Audit D3: nested local stores establish a consistent bijection."""

import ast

import pytest

from towel.unification.unifier import Unifier


@pytest.mark.parametrize(
    "body",
    [
        "for item in rows:\n    {name} = item.strip()\n    output.append({name})",
        "if rows:\n    {name} = rows[0].strip()\n    output.append({name})",
        "while rows:\n    {name} = rows.pop().strip()\n    output.append({name})",
        "if rows:\n    {name}, count = rows[0]\n    output.append(({name}, count))",
    ],
)
def test_nested_local_correspondence(body: str) -> None:
    """Audit D3: a renamed local stays bound at its original nested store."""
    blocks = [ast.parse(body.format(name=name)).body for name in ("normalized", "cleaned")]
    renames: list[dict[str, str]] = [{}, {}]
    result = Unifier().unify_blocks(blocks, renames)
    assert result is not None
    assert not result.param_expressions
    assert renames[0]["normalized"] == renames[1]["cleaned"]


@pytest.mark.parametrize(
    "left,right",
    [
        ("a=1; a=2; use(a)", "b=1; c=2; use(c)"),
        ("a=1; c=2; use(c)", "b=1; b=2; use(b)"),
        ("a=1; c=2; use(a)", "b=1; d=2; use(d)"),
        ("a=1; a=2; use(a)", "b=1; a=2; use(a)"),
        ("a=1; c=2; use(a,c)", "a=1; a=2; use(a,a)"),
    ],
)
def test_nested_binding_correspondence_is_bijective(left: str, right: str) -> None:
    """Audit D3: neither rebinding nor later loads may switch correspondence."""
    blocks = [ast.parse("if ready:\n    " + body).body for body in (left, right)]
    assert Unifier().unify_blocks(blocks, [{}, {}]) is None

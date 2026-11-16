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
from pathlib import Path
import subprocess
import sys

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


@pytest.mark.parametrize(
    "prefix,free_first", [("", False), ("for x in xs:\n    ", False), ("for x in xs:\n    ", True)]
)
def test_review_d2_free_name_cannot_alias_a_nested_store(prefix: str, free_first: bool) -> None:
    """Review defect 2: unchanged names participate in the rename bijection, in either order."""
    sep = "\n    " if prefix else "\n"
    bodies = ["a = x * 2" + sep + "out.append(a + a)", "b = x * 2" + sep + "out.append(b + a)"]
    if free_first:
        bodies = ["out.append(a)" + sep + body for body in bodies]
    assert (
        Unifier().unify_blocks([ast.parse(prefix + body).body for body in bodies], [{}, {}]) is None
    )


@pytest.mark.parametrize("case", ["free_name", "nested_scope_leak"])
def test_review_d2_unifier_declines_before_instantiation(case: str) -> None:
    """Review defect 2: both review reproductions fail in the unifier itself."""
    fixture = Path(__file__).parent / "fixtures" / "unify_review" / "d2_nonbijective_nested_store"
    tree = ast.parse((fixture / (case + ".py.txt")).read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert Unifier().unify_blocks([node.body for node in functions], [{}, {}]) is None


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("case", ["free_name", "nested_scope_leak"])
def test_review_d2_pipeline_preserves_distinct_bindings(
    tmp_path: Path, strict: bool, case: str
) -> None:
    """Review defect 2: each original reproduction preserves actual outputs in both modes."""
    from towel.unification.refactor_engine import UnificationRefactorEngine

    fixture = Path(__file__).parent / "fixtures" / "unify_review" / "d2_nonbijective_nested_store"
    source = (fixture / (case + ".py.txt")).read_text()
    path = tmp_path / "case.py"
    path.write_text(source)
    transformed, _, _ = UnificationRefactorEngine(
        min_lines=1, strict=strict
    ).refactor_to_fixed_point(str(path))
    arguments = "[1, 2, 3], 100" if case == "free_name" else "[1, 2, 3], 100, 200"
    driver = f"\nprint(first({arguments})); print(second({arguments}))\n"
    observations = [
        subprocess.run(
            [sys.executable, "-c", program + driver], capture_output=True, text=True, check=True
        ).stdout
        for program in (source, transformed)
    ]
    assert observations[0] == observations[1]


@pytest.mark.parametrize("kind", ["def inner():", "async def inner():", "class Inner:"])
def test_definition_binders_do_not_rename_the_outer_scope(kind: str) -> None:
    """Review defect 2: definitions keep local spellings and never export their maps."""
    blocks = [ast.parse(f"{kind}\n    same = 1\nread(same)").body for _ in range(2)]
    renames: list[dict[str, str]] = [{}, {}]
    assert Unifier().unify_blocks(blocks, renames) is not None
    assert all("same" not in names for names in renames)


@pytest.mark.parametrize(
    "definition",
    [
        "def inner():\n    nonlocal a\n    return a",
        "class Inner:\n    a = 1\n    def get(self):\n        return a",
        "class Inner:\n    global a\n    def get(self):\n        return a",
    ],
    ids=[
        "nonlocal_uses_enclosing_binding",
        "method_skips_class_binding",
        "method_skips_class_global_declaration",
    ],
)
def test_nested_scopes_cannot_hide_distinct_enclosing_bindings(definition: str) -> None:
    """Review defect 2: lexical scope must not identify a renamed local with a free name."""
    blocks = [
        ast.parse(f"for x in xs:\n    {local} = x * 2\n{definition}\nout.append({local})").body
        for local in ("a", "b")
    ]
    assert Unifier().unify_blocks(blocks, [{}, {}]) is None


def test_top_level_canonical_name_cannot_alias_a_free_name() -> None:
    """Review defect 2: generated canonical names must avoid original source names too."""
    blocks = [
        ast.parse(source).body
        for source in ("a = x * 2\nout.append(a + a)", "b = x * 2\nout.append(b + __temp_0)")
    ]
    assert Unifier().unify_blocks(blocks, [{}, {}]) is None


@pytest.mark.parametrize("declaration", ["a = 1", "global a"])
def test_definition_local_and_global_names_mask_outer_renames(declaration: str) -> None:
    """Review defect 2: a true local or global remains distinct from the renamed outer local."""
    blocks = [
        ast.parse(
            f"for x in xs:\n    {local} = x * 2\n"
            f"def inner():\n    {declaration}\n    return a\nout.append({local})"
        ).body
        for local in ("a", "b")
    ]
    renames: list[dict[str, str]] = [{}, {}]
    assert Unifier().unify_blocks(blocks, renames) is not None
    assert renames[0]["a"] == renames[1]["b"]
    assert "a" not in renames[1]

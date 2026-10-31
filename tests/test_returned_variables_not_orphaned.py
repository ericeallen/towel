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

"""A variable the helper returns is not an orphan at the call site.

The orphan guard rejects a block that binds a name read later, since the
binding would move into the helper. When the name is one the helper returns,
the generated ``name = helper(...)`` rebinds it on every path out of the block,
so the later read is fine. This is the README's own example; the guard used
to reject it, which made every assignment-form extraction impossible.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import textwrap
from types import SimpleNamespace

from towel.unification.refactor_engine import UnificationRefactorEngine

OWNED_PARTIAL = textwrap.dedent("""
    def order_summary(order):
        items = [i for i in order.items if i.in_stock]
        subtotal = sum(i.price for i in items)
        total = round(subtotal * 1.08, 2)
        return f"Order {order.id}: ${total}"

    def quote_summary(quote):
        items = [i for i in quote.items if i.in_stock]
        subtotal = sum(i.price for i in items)
        total = round(subtotal * 1.08, 2)
        return f"Quote {quote.id}: ${total}"
    """)

README_EXAMPLE = 'def order_summary(order, label="Order"):\n    items = [i for i in order.items if i.in_stock]\n    subtotal = sum(i.price for i in items)\n    total = round(subtotal * 1.08, 2)\n    return f"{label} {order.id}: ${total}"\n\ndef quote_summary(quote, label="Quote"):\n    items = [i for i in quote.items if i.in_stock]\n    subtotal = sum(i.price for i in items)\n    total = round(subtotal * 1.08, 2)\n    return f"{label} {quote.id}: ${total}"\n'


def _run(source: str) -> list[str]:
    namespace: dict[str, object] = {}
    exec(compile(source, "<readme>", "exec"), namespace)
    record = SimpleNamespace(
        id=7,
        items=[
            SimpleNamespace(price=10.0, in_stock=True),
            SimpleNamespace(price=5.0, in_stock=False),
        ],
    )
    return [namespace[name](record) for name in ("order_summary", "quote_summary")]  # type: ignore[operator]


def test_readme_example_extracts_with_the_returned_variable(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(README_EXAMPLE)
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()):
        final, applied, _ = engine.refactor_to_fixed_point(str(path))
    assert applied == 1
    assert final.count("_towel_arguments.pop()") == 2
    assert "del order" in final and "del quote" in final
    assert final.count("del label") == 2
    helper = next(
        node
        for node in ast.parse(final).body
        if isinstance(node, ast.FunctionDef) and node.name == "__extracted_func_0"
    )
    assert isinstance(helper.body[-1], ast.Return)
    assert _run(final) == _run(README_EXAMPLE) == ["Order 7: $10.8", "Quote 7: $10.8"]


def test_a_name_bound_before_and_rebound_in_the_block_is_still_an_orphan(tmp_path: Path) -> None:
    # ``count`` enters bound, is rebound inside the block, and is read after:
    # the helper cannot return it (it is not newly bound) and the guard holds.
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            def first(items):
                count = 0
                for item in items:
                    count = count + item
                    print(item)
                return count

            def second(items):
                count = 0
                for item in items:
                    count = count + item
                    print(item)
                return count * 2
            """))
    engine = UnificationRefactorEngine(min_lines=2, reuse_existing_functions=False)
    proposals = engine.analyze_file(str(path))
    assert all(
        "count" not in ast.unparse(proposal.extracted_function).split("\n", 1)[0]
        or proposal.return_variables
        for proposal in proposals
    )
    for proposal in proposals:
        rewritten = engine.apply_refactoring(str(path), proposal)
        namespace: dict[str, object] = {}
        exec(compile(rewritten, "<orphan>", "exec"), namespace)
        assert namespace["first"]([1, 2, 3]) == 6  # type: ignore[operator]
        assert namespace["second"]([1, 2, 3]) == 12  # type: ignore[operator]


def test_original_partial_example_is_refused_without_changing_behavior(tmp_path: Path) -> None:
    path = tmp_path / "partial.py"
    path.write_text(OWNED_PARTIAL)
    engine = UnificationRefactorEngine(min_lines=3)
    assert engine.analyze_file(str(path)) == []
    assert "owned_binding_frame_boundary" in engine.declined_pairs
    assert _run(OWNED_PARTIAL) == ["Order 7: $10.8", "Quote 7: $10.8"]

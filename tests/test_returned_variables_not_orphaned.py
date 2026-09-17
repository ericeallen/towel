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

README_EXAMPLE = textwrap.dedent("""
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
    assert "total = __extracted_func_0(order)" in final
    assert "total = __extracted_func_0(quote)" in final
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

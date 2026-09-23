"""A duplicate that is the whole body of a function is extracted like any other.

Two identical functions once kept the first as it was and rewrote the second
to call it. A call by name looks the function up in its module every time,
so patching or rebinding the first then changed the second as well (audit
case r06; ``tests/test_functions_keep_their_own_bodies.py``). Every function
whose body is the duplicate now calls one new helper instead, and depends on
nothing another function could be replaced by.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
from typing import Unpack

from tests.test_helpers import (
    EngineOptions,
    module_functions,
    refactor_to_fixed_point_silently,
    unparsed_body,
    write_module,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

IDENTICAL_PAIR = """
def alpha(value):
    tmp = value + 1
    total = tmp * 2
    return total

def beta(value):
    tmp = value + 1
    total = tmp * 2
    return total
"""


def _fixed_point(path: str, **engine_options: Unpack[EngineOptions]) -> str:
    return refactor_to_fixed_point_silently(path, **engine_options)[0]


def _fixed_point_directory(package: Path, **engine_options: Unpack[EngineOptions]) -> list[str]:
    engine = UnificationRefactorEngine(min_lines=1, **engine_options)
    with contextlib.redirect_stdout(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(package), str(package), max_iterations=0, progress="none"
        )
    return sorted({desc for _count, descs in results.values() for desc in descs})


def _extracted_helper(
    final: str, originals: tuple[str, ...], sites: tuple[str, ...] | None = None
) -> str:
    """The one function added to ``originals``, which every duplicate site calls.

    The helper's name is not part of the contract; its shape is: exactly one
    definition was added, and each site (all originals unless given) calls it.
    """
    sites = originals if sites is None else sites
    definitions = [
        node
        for node in ast.parse(final).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    added = [node.name for node in definitions if node.name not in originals]
    assert len(added) == 1, f"expected exactly one new helper, found {added}:\n{final}"
    helper = added[0]
    for node in definitions:
        if node.name in sites:
            calls = {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
            assert helper in calls, f"{node.name} does not call {helper}:\n{final}"
    return helper


def test_identical_functions_both_call_one_new_helper(tmp_path: Path) -> None:
    final = _fixed_point(write_module(tmp_path, IDENTICAL_PAIR))
    helper = _extracted_helper(final, ("alpha", "beta"))
    functions = module_functions(final)
    assert unparsed_body(functions["alpha"]) == f"return {helper}(value)"
    assert unparsed_body(functions["beta"]) == f"return {helper}(value)"


def test_the_proposal_extracts_a_helper_and_reuses_no_function(tmp_path: Path) -> None:
    path = write_module(tmp_path, IDENTICAL_PAIR)
    (proposal,) = UnificationRefactorEngine(min_lines=1).analyze_file(path)
    assert proposal.reused_function is None
    assert proposal.description == "Extract common code from alpha and beta"
    assert sorted(replacement.line_range for replacement in proposal.replacements) == [
        (3, 5),
        (8, 10),
    ]


def test_a_block_inside_a_larger_function_shares_the_helper_with_a_whole_body(
    tmp_path: Path,
) -> None:
    final = _fixed_point(
        write_module(
            tmp_path,
            """
            def norm(items):
                total = sum(items)
                scaled = [i / total for i in items]
                return scaled

            def report(data, label):
                print(label)
                total = sum(data)
                scaled = [i / total for i in data]
                return scaled
            """,
        )
    )
    helper = _extracted_helper(final, ("norm", "report"))
    functions = module_functions(final)
    assert unparsed_body(functions["report"]) == f"print(label)\nreturn {helper}(data)"
    assert "norm(" not in unparsed_body(functions["report"])


def test_three_copies_all_call_one_helper(tmp_path: Path) -> None:
    final = _fixed_point(
        write_module(
            tmp_path,
            """
            def c(value):
                tmp = value + 1
                total = tmp * 2
                return total

            def a(value):
                tmp = value + 1
                total = tmp * 2
                return total

            def b(value):
                tmp = value + 1
                total = tmp * 2
                return total
            """,
        )
    )
    _extracted_helper(final, ("a", "b", "c"))


def test_a_method_and_a_function_share_a_helper(tmp_path: Path) -> None:
    final = _fixed_point(
        write_module(
            tmp_path,
            """
            def alpha(value):
                tmp = value + 1
                total = tmp * 2
                return total

            class K:
                def m(self, value):
                    tmp = value + 1
                    total = tmp * 2
                    return total
            """,
        )
    )
    assert "return alpha(value)" not in final
    assert "_extracted_func_0(value)" in final


def test_non_returning_bodies_call_the_helper_as_a_statement(tmp_path: Path) -> None:
    final = _fixed_point(
        write_module(
            tmp_path,
            """
            def alpha(items, log):
                for i in items:
                    log.append(i * 2)
                log.append(len(items))

            def beta(items, log):
                for i in items:
                    log.append(i * 2)
                log.append(len(items))
            """,
        )
    )
    helper = _extracted_helper(final, ("alpha", "beta"))
    assert unparsed_body(module_functions(final)["beta"]) == f"{helper}(items, log)"


def test_async_duplicates_share_a_synchronous_helper(tmp_path: Path) -> None:
    final = _fixed_point(
        write_module(
            tmp_path,
            """
            async def alpha(value):
                tmp = value + 1
                total = tmp * 2
                return total

            async def gamma(value):
                tmp = value + 1
                total = tmp * 2
                return total
            """,
        )
    )
    _extracted_helper(final, ("alpha", "gamma"))


def test_globally_rebound_functions_share_a_helper(tmp_path: Path) -> None:
    final = _fixed_point(
        write_module(
            tmp_path,
            """
            def alpha(value):
                tmp = value + 1
                total = tmp * 2
                return total

            def beta(value):
                tmp = value + 1
                total = tmp * 2
                return total

            def swap():
                global alpha, beta
                alpha, beta = beta, alpha
            """,
        )
    )
    _extracted_helper(final, ("alpha", "beta", "swap"), sites=("alpha", "beta"))
    assert "return alpha(value)" not in final
    assert "return beta(value)" not in final


def test_the_reuse_setting_redirects_nothing_either_way(tmp_path: Path) -> None:
    for reuse in (True, False):
        final = _fixed_point(
            write_module(tmp_path, IDENTICAL_PAIR, f"reuse_{reuse}.py"),
            reuse_existing_functions=reuse,
        )
        _extracted_helper(final, ("alpha", "beta"))


def test_trivial_forwarding_bodies_are_still_skipped(tmp_path: Path) -> None:
    path = write_module(
        tmp_path,
        """
        def first(value):
            return forward(value, 1, 2, 3)

        def second(value):
            return forward(value, 1, 2, 3)
        """,
    )
    assert UnificationRefactorEngine(min_lines=1).analyze_file(path) == []


def test_a_cross_file_site_imports_the_new_helper(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name, function in (("a.py", "alpha"), ("b.py", "beta")):
        write_module(
            package,
            f"""
            def {function}(value):
                tmp = value + 1
                total = tmp * 2
                return total
            """,
            name,
        )
    assert _fixed_point_directory(package) == [
        "Extract common code from alpha (a.py) and beta (b.py)"
    ]
    a_source, b_source = (package / "a.py").read_text(), (package / "b.py").read_text()
    helper = _extracted_helper(a_source, ("alpha",))
    assert f"from .a import {helper}" in b_source
    assert unparsed_body(module_functions(b_source)["beta"]) == f"return {helper}(value)"


def test_a_cross_file_helper_is_hosted_where_no_import_cycle_closes(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    write_module(
        package,
        """
        from .b import beta

        def alpha(value):
            tmp = value + 1
            total = tmp * 2
            return total
        """,
        "a.py",
    )
    write_module(
        package,
        """
        def beta(value):
            tmp = value + 1
            total = tmp * 2
            return total
        """,
        "b.py",
    )
    # ``a`` already imports ``b``, so ``b`` importing from ``a`` would close
    # a cycle: the helper lives in ``b``, which ``a`` imports already.
    assert _fixed_point_directory(package)
    assert "from .a import" not in (package / "b.py").read_text()
    assert "def __extracted_func" in (package / "b.py").read_text()

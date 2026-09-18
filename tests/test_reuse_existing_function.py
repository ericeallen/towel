"""A duplicate that is the whole body of a plain function calls that function.

Extracting a helper from two identical functions restates one of them and
turns both into forwarders. When one duplicate site is the entire body of a
module-level function whose parameters the generated call passes exactly, the
helper applied to any arguments is that function applied to them, so the
engine keeps the function as it is and rewrites the other sites to call it.
Anything a call by name could not reproduce falls back to ordinary extraction.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import textwrap

from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.overlap import get_affected_lines

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


def _write(tmp_path: Path, code: str, name: str = "m.py") -> str:
    path = tmp_path / name
    path.write_text(textwrap.dedent(code))
    return str(path)


def _fixed_point(path: str, **engine_options: object) -> str:
    engine = UnificationRefactorEngine(min_lines=1, **engine_options)  # type: ignore[arg-type]
    with contextlib.redirect_stdout(io.StringIO()):
        final, _applied, _descriptions = engine.refactor_to_fixed_point(path, max_iterations=0)
    return final


def _fixed_point_directory(package: Path, **engine_options: object) -> list[str]:
    engine = UnificationRefactorEngine(min_lines=1, **engine_options)  # type: ignore[arg-type]
    with contextlib.redirect_stdout(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(package), str(package), max_iterations=0, progress="none"
        )
    return sorted({desc for _count, descs in results.values() for desc in descs})


def _functions(source: str) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}


def _body(function: ast.FunctionDef) -> str:
    return "\n".join(ast.unparse(statement) for statement in function.body)


def test_identical_functions_keep_the_first_and_forward_the_second(tmp_path: Path) -> None:
    final = _fixed_point(_write(tmp_path, IDENTICAL_PAIR))
    functions = _functions(final)
    assert set(functions) == {"alpha", "beta"}, "no helper is emitted"
    assert _body(functions["alpha"]) == "tmp = value + 1\ntotal = tmp * 2\nreturn total"
    assert _body(functions["beta"]) == "return alpha(value)"


def test_proposal_records_the_reused_function_and_covers_its_lines(tmp_path: Path) -> None:
    path = _write(tmp_path, IDENTICAL_PAIR)
    (proposal,) = UnificationRefactorEngine(min_lines=1).analyze_file(path)
    assert proposal.reused_function is not None
    assert proposal.reused_function.name == "alpha"
    assert proposal.reused_function.file_path == path
    assert "Reuse alpha" in proposal.description
    assert [replacement.line_range for replacement in proposal.replacements] == [(8, 10)]
    # Overlap filtering treats the reused definition as covered so no other
    # proposal rewrites it underneath the new calls.
    assert {line for _path, line in get_affected_lines(proposal)} == {2, 3, 4, 5, 8, 9, 10}


def test_block_inside_a_larger_function_calls_the_existing_function(tmp_path: Path) -> None:
    final = _fixed_point(
        _write(
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
    assert _body(_functions(final)["report"]) == "print(label)\nreturn norm(data)"


def test_arguments_follow_the_existing_functions_parameter_order(tmp_path: Path) -> None:
    final = _fixed_point(
        _write(
            tmp_path,
            """
            def combine(b, a):
                tmp = a + 1
                total = tmp * b
                return total

            def other(x, y):
                tmp = x + 1
                total = tmp * y
                return total
            """,
        )
    )
    assert _body(_functions(final)["other"]) == "return combine(y, x)"


def test_same_module_definitions_the_body_reads_are_not_passed(tmp_path: Path) -> None:
    final = _fixed_point(
        _write(
            tmp_path,
            """
            def helper_fn(v):
                return v * 3

            def alpha(value):
                tmp = helper_fn(value) + 1
                total = tmp * 2
                return total

            def beta(value):
                tmp = helper_fn(value) + 1
                total = tmp * 2
                return total
            """,
        )
    )
    assert _body(_functions(final)["beta"]) == "return alpha(value)"


def test_three_copies_all_call_the_first(tmp_path: Path) -> None:
    final = _fixed_point(
        _write(
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
    functions = _functions(final)
    assert set(functions) == {"a", "b", "c"}
    assert _body(functions["a"]) == "return c(value)"
    assert _body(functions["b"]) == "return c(value)"


def test_method_site_calls_the_module_level_function(tmp_path: Path) -> None:
    final = _fixed_point(
        _write(
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
    assert "return alpha(value)" in final
    assert "__extracted_func" not in final


def test_non_returning_bodies_call_as_a_statement(tmp_path: Path) -> None:
    final = _fixed_point(
        _write(
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
    assert _body(_functions(final)["beta"]) == "alpha(items, log)"


def test_shadowed_name_at_the_site_falls_back_to_extraction(tmp_path: Path) -> None:
    final = _fixed_point(
        _write(
            tmp_path,
            """
            def alpha(value):
                tmp = value + 1
                total = tmp * 2
                return total

            def beta(value):
                alpha = None
                tmp = value + 1
                total = tmp * 2
                return total
            """,
        )
    )
    assert "__extracted_func_0" in final
    assert "return alpha(value)" not in final


def test_decorated_and_variadic_functions_are_not_reused(tmp_path: Path) -> None:
    # Neither can be the target: a decorator may change what a call does, and
    # a variadic signature is not what the helper's positional call binds.
    # The undecorated plain function still is, so the others forward to it.
    final = _fixed_point(
        _write(
            tmp_path,
            """
            import functools

            @functools.lru_cache
            def cached(value):
                tmp = value + 1
                total = tmp * 2
                return total

            def variadic(value, *rest):
                tmp = value + 1
                total = tmp * 2
                return total

            def plain(value):
                tmp = value + 1
                total = tmp * 2
                return total
            """,
        )
    )
    functions = _functions(final)
    assert _body(functions["cached"]) == "return plain(value)"
    assert _body(functions["variadic"]) == "return plain(value)"
    assert "__extracted_func" not in final


def test_async_function_is_not_reused(tmp_path: Path) -> None:
    # The generated helper is synchronous; calling a coroutine function from
    # a synchronous site would return a coroutine instead of the value. An
    # async body may still forward to a plain function that is a target.
    final = _fixed_point(
        _write(
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

            def beta(value):
                tmp = value + 1
                total = tmp * 2
                return total
            """,
        )
    )
    assert "return alpha(value)" not in final
    assert "return gamma(value)" not in final
    assert "return beta(value)" in final
    assert "__extracted_func" not in final


def test_only_async_duplicates_fall_back_to_extraction(tmp_path: Path) -> None:
    final = _fixed_point(
        _write(
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
    assert "__extracted_func_0" in final


def test_globally_rebound_function_is_not_reused(tmp_path: Path) -> None:
    final = _fixed_point(
        _write(
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
    assert "__extracted_func_0" in final
    assert "return alpha(value)" not in final
    assert "return beta(value)" not in final


def test_reuse_can_be_switched_off(tmp_path: Path) -> None:
    final = _fixed_point(_write(tmp_path, IDENTICAL_PAIR), reuse_existing_functions=False)
    assert "__extracted_func_0" in final
    assert "return alpha(value)" not in final


def test_trivial_forwarding_bodies_are_still_skipped(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        def first(value):
            return forward(value, 1, 2, 3)

        def second(value):
            return forward(value, 1, 2, 3)
        """,
    )
    assert UnificationRefactorEngine(min_lines=1).analyze_file(path) == []


def test_cross_file_site_imports_the_existing_function(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    _write(
        package,
        """
        def alpha(value):
            tmp = value + 1
            total = tmp * 2
            return total
        """,
        "a.py",
    )
    _write(
        package,
        """
        def beta(value):
            tmp = value + 1
            total = tmp * 2
            return total
        """,
        "b.py",
    )
    assert _fixed_point_directory(package) == ["Reuse alpha (a.py) for duplicated code in beta"]
    b_source = (package / "b.py").read_text()
    assert "from .a import alpha" in b_source
    assert _body(_functions(b_source)["beta"]) == "return alpha(value)"
    assert "__extracted_func" not in (package / "a.py").read_text()


def test_cross_file_reuse_that_would_close_an_import_cycle_falls_back(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    _write(
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
    _write(
        package,
        """
        def beta(value):
            tmp = value + 1
            total = tmp * 2
            return total
        """,
        "b.py",
    )
    # ``a`` already imports ``b``, so ``b`` importing ``alpha`` would cycle;
    # ``beta`` is defined first in ``b`` but ``alpha`` sorts first and is
    # tried first. Neither direction is safe: b -> a cycles, and a -> b for
    # beta is fine only if alpha's module can import b, which it already does.
    descriptions = _fixed_point_directory(package)
    a_source = (package / "a.py").read_text()
    assert "from .a import" not in (package / "b.py").read_text()
    assert descriptions
    assert "cycle" not in a_source


def test_identical_absolute_imports_are_ambient_across_files(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name, function in (("a.py", "alpha"), ("b.py", "beta")):
        _write(
            package,
            f"""
            import math

            def {function}(value):
                tmp = math.floor(value) + 1
                total = tmp * 2
                return total
            """,
            name,
        )
    assert _fixed_point_directory(package) == ["Reuse alpha (a.py) for duplicated code in beta"]
    assert _body(_functions((package / "b.py").read_text())["beta"]) == "return alpha(value)"


def test_same_named_definitions_in_different_modules_are_not_ambient(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name, function, factor in (("a.py", "alpha", 3), ("b.py", "beta", 5)):
        _write(
            package,
            f"""
            def helper_fn(v):
                return v * {factor}

            def {function}(value):
                tmp = helper_fn(value) + 1
                total = tmp * 2
                return total
            """,
            name,
        )
    descriptions = _fixed_point_directory(package)
    assert not any(description.startswith("Reuse") for description in descriptions)


def test_overloaded_function_is_reused_through_its_implementation(tmp_path: Path) -> None:
    # ``@overload`` stubs precede the implementation; the last definition is
    # the runtime binding, so calling it by name is what the redirect needs.
    final = _fixed_point(
        _write(
            tmp_path,
            """
            from typing import overload

            @overload
            def normalize(value: str) -> str: ...
            @overload
            def normalize(value: None) -> None: ...
            def normalize(value):
                if value is None:
                    return None
                text = value.strip()
                return text.lower()

            def other(value):
                if value is None:
                    return None
                text = value.strip()
                return text.lower()
            """,
        )
    )
    assert _body(_functions(final)["other"]) == "return normalize(value)"
    assert "__extracted_func" not in final

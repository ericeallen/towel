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
import subprocess
import sys
import textwrap
from typing import Unpack

from tests.test_functions_keep_their_own_bodies import _assert_owned_helper_call
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
    engine = UnificationRefactorEngine(min_lines=1, cross_module_helpers=True, **engine_options)
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


def _observed(source: str, driver: str) -> str:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source) + "\n" + driver],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout


def test_identical_functions_both_call_one_new_helper(tmp_path: Path) -> None:
    final = _fixed_point(write_module(tmp_path, IDENTICAL_PAIR))
    helper = _extracted_helper(final, ("alpha", "beta"))
    functions = module_functions(final)
    _assert_owned_helper_call(functions["alpha"], functions[helper], ("value",))
    _assert_owned_helper_call(functions["beta"], functions[helper], ("value",))
    assert _observed(final, "print(alpha(3), beta(5))") == "8 12\n"


def test_the_proposal_extracts_a_helper_and_reuses_no_function(tmp_path: Path) -> None:
    path = write_module(tmp_path, IDENTICAL_PAIR)
    (proposal,) = UnificationRefactorEngine(min_lines=1).analyze_file(path)
    assert proposal.reused_function is None
    assert proposal.description == "Extract common code from alpha and beta"
    assert sorted(replacement.line_range for replacement in proposal.replacements) == [
        (3, 5),
        (8, 10),
    ]


def test_a_partial_owned_body_declines_but_complete_bodies_share(tmp_path: Path) -> None:
    original = """
    def norm(items):
        total = sum(items)
        scaled = [i / total for i in items]
        return scaled

    def report(data, label):
        print(label)
        total = sum(data)
        scaled = [i / total for i in data]
        return scaled
    """
    driver = "print(norm([1, 3]), report([2, 2], 'report'))"
    unchanged = _fixed_point(write_module(tmp_path, original))
    assert ast.dump(ast.parse(unchanged)) == ast.dump(ast.parse(textwrap.dedent(original)))
    assert _observed(unchanged, driver) == _observed(original, driver)
    # A complete matching body can transfer both arguments without changing
    # norm's quiet behavior or report's label effect.
    whole = """
    def norm(items, label=None):
        if label is not None:
            print(label)
        total = sum(items)
        scaled = [i / total for i in items]
        return scaled

    def report(data, label):
        if label is not None:
            print(label)
        total = sum(data)
        scaled = [i / total for i in data]
        return scaled
    """
    final = _fixed_point(write_module(tmp_path, whole, "whole.py"))
    helper = _extracted_helper(final, ("norm", "report"))
    functions = module_functions(final)
    _assert_owned_helper_call(functions["norm"], functions[helper], ("items", "label"))
    _assert_owned_helper_call(functions["report"], functions[helper], ("data", "label"))
    assert _observed(final, driver) == _observed(original, driver)


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
    functions = module_functions(final)
    helpers = [node for name, node in functions.items() if name != "alpha"]
    (helper,) = helpers
    _assert_owned_helper_call(functions["alpha"], helper, ("value",))
    klass = next(node for node in ast.parse(final).body if isinstance(node, ast.ClassDef))
    method = next(node for node in klass.body if isinstance(node, ast.FunctionDef))
    storage, delete_self, delete_value, invocation = method.body
    assert isinstance(storage, ast.Assign) and isinstance(storage.targets[0], ast.Name)
    box = storage.targets[0].id
    assert ast.dump(storage.value) == ast.dump(ast.parse("[(value, self)]", mode="eval").body)
    assert ast.dump(delete_self) == ast.dump(ast.parse("del self").body[0])
    assert ast.dump(delete_value) == ast.dump(ast.parse("del value").body[0])
    expected = ast.parse(f"return {helper.name}({box}[0][0], {box}.pop())").body[0]
    assert ast.dump(invocation) == ast.dump(expected)
    assert len(helper.args.args + helper.args.posonlyargs) == 2
    assert _observed(final, "print(alpha(3), K().m(5))") == "8 12\n"


def test_non_returning_owned_loop_declines_and_binding_free_statements_share(
    tmp_path: Path,
) -> None:
    body = """
    def alpha(items, log):
        for i in items:
            log.append(i * 2)
        log.append(len(items))

    def beta(items, log):
        for i in items:
            log.append(i * 2)
        log.append(len(items))
    """
    final = _fixed_point(write_module(tmp_path, body))
    assert ast.dump(ast.parse(final)) == ast.dump(ast.parse(textwrap.dedent(body)))
    safe = textwrap.dedent(body).replace(
        "    for i in items:\n        log.append(i * 2)",
        "    log.append(items[0] * 2)\n    log.append(items[1] * 2)",
    )
    final = _fixed_point(write_module(tmp_path, safe, "statements.py"))
    helper = _extracted_helper(final, ("alpha", "beta"))
    functions = module_functions(final)
    assert unparsed_body(functions["beta"]) == f"{helper}(items, log)"
    driver = "log=[]; print(alpha([1, 3], log), beta([2, 4], log), log)"
    assert _observed(final, driver) == _observed(body, driver) == "None None [2, 6, 2, 4, 8, 2]\n"


def test_async_owned_bodies_decline_but_binding_free_work_shares(tmp_path: Path) -> None:
    original = """
    async def alpha(value):
        tmp = value + 1
        total = tmp * 2
        return total

    async def gamma(value):
        tmp = value + 1
        total = tmp * 2
        return total
    """
    final = _fixed_point(write_module(tmp_path, original))
    assert ast.dump(ast.parse(final)) == ast.dump(ast.parse(textwrap.dedent(original)))
    safe = textwrap.dedent(original).replace(
        "    tmp = value + 1\n    total = tmp * 2\n    return total",
        "    value + 1\n    value * 2\n    return (value + 1) * 2",
    )
    final = _fixed_point(write_module(tmp_path, safe, "async_safe.py"))
    helper = _extracted_helper(final, ("alpha", "gamma"))
    assert isinstance(module_functions(final)[helper], ast.FunctionDef)
    driver = "import asyncio; print(asyncio.run(alpha(3)), asyncio.run(gamma(5)))"
    assert _observed(final, driver) == _observed(original, driver) == "8 12\n"


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
        imports = "from .a import alpha" if name == "b.py" else ""
        write_module(
            package,
            f"""
            {imports}

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
    _assert_owned_helper_call(
        module_functions(b_source)["beta"], module_functions(a_source)[helper], ("value",)
    )


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

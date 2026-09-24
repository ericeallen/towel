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

"""A same-module helper reads module-level names bare instead of taking them.

A shared free name that both sites resolve at module scope (or nowhere) is the
same lookup from a helper in that module, made where the block made it. Across
files the other module's same-named binding may differ, so it stays a
parameter there; a clustered occurrence whose same-spelled name is a local
does not join.
"""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap

from towel.diagnostics import Settings
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import module_resolved_names

SERIAL = Settings.from_environ({"TOWEL_WORKERS": "1"})

TWO_SITES = textwrap.dedent("""
    import json

    def first(text, flag):
        text = text.strip()
        data = json.loads(text)
        names = [item["name"] for item in data]
        if flag:
            names.append(helper(text))
        return names

    def second(text, flag):
        data = json.loads(text)
        names = [item["name"] for item in data]
        if flag:
            names.append(helper(text))
        return names + ["done"]

    def helper(text):
        return text.upper()
    """)


def _helper_signature(source: str) -> str:
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func")
    )
    return ", ".join(arg.arg for arg in helper.args.args)


def test_module_names_are_read_bare_inside_a_same_file_helper(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(TWO_SITES)
    engine = UnificationRefactorEngine(min_lines=3, settings=SERIAL)
    final, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert applied == 1
    assert _helper_signature(final) == "flag, text"
    assert "json.loads(text)" in final and "helper(text)" in final
    assert "lambda" not in final


def test_a_cross_file_helper_still_takes_module_names(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "a.py").write_text(textwrap.dedent("""
        def scale(value):
            return value * 2

        def first(items):
            total = sum(items)
            total = scale(total)
            total += 1
            print("first", total)
            return total
        """))
    (tmp_path / "pkg" / "b.py").write_text(textwrap.dedent("""
        def scale(value):
            return value * 3

        def second(items):
            total = sum(items)
            total = scale(total)
            total += 1
            print("second", total)
            return total - 1
        """))
    engine = UnificationRefactorEngine(min_lines=3, settings=SERIAL, cross_module_helpers=True)
    results, _ = engine.refactor_directory_to_fixed_point(
        str(tmp_path / "pkg"), str(tmp_path / "out"), progress="none"
    )
    assert sum(count for count, _ in results.values()) > 0
    rewritten = (tmp_path / "out" / "a.py").read_text() + (tmp_path / "out" / "b.py").read_text()
    assert "scale" in _helper_signature(rewritten)


def test_module_resolution_stops_at_the_function_and_its_enclosing_functions() -> None:
    tree = ast.parse(textwrap.dedent("""
            LIMIT = 1
            def outer(cell):
                def inner(local, flag):
                    if flag:
                        local = 0
                    return LIMIT + cell + local + unbound
                return inner
            """))
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    outer = tree.body[1]
    assert isinstance(outer, ast.FunctionDef)
    inner = outer.body[0]
    assert isinstance(inner, ast.FunctionDef)
    names = {"LIMIT", "cell", "local", "flag", "unbound", "outer"}
    assert module_resolved_names(inner, analyzer, names) == {"LIMIT", "unbound", "outer"}
    assert module_resolved_names(outer, analyzer, {"cell", "LIMIT"}) == {"LIMIT"}

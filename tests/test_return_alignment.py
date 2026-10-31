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

"""One helper return serves call sites whose live variables are spelled differently."""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path

from towel.unification.refactor_engine import UnificationRefactorEngine

SOURCE = """\
def f(items):
    result = []
    for item in items:
        result.append(item * 2)
    print(len(result))
    return result + [0]


def g(values):
    out = []
    for value in values:
        out.append(value * 2)
    print(len(out))
    return sorted(out)
"""


def test_each_site_receives_the_helper_result_under_its_own_name(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.py"
    unsafe.write_text(SOURCE)
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    assert not engine.analyze_file(str(unsafe))
    safe = (
        "def f():\n    result = [item * 2 for item in (1, 2, 3)]\n"
        "    print(len(result))\n    print(sum(result))\n    return result + [0]\n\n"
        "def g():\n    out = [value * 2 for value in (1, 2, 3)]\n"
        "    print(len(out))\n    print(sum(out))\n    return sorted(out)\n"
    )
    source = tmp_path / "src"
    source.mkdir()
    (source / "m.py").write_text(safe)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, reason = engine.refactor_directory_to_fixed_point(
            str(source), str(tmp_path / "out"), max_iterations=0, progress="none"
        )
    assert reason == "fixed_point"
    assert sum(count for count, _ in results.values()) == 1
    rewritten = (tmp_path / "out" / "m.py").read_text()
    tree = ast.parse(rewritten)
    for name, local in (("f", "result"), ("g", "out")):
        function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
        )
        assert isinstance(function.body[0], ast.Assign)
        assert local in {
            node.id for node in ast.walk(function.body[0].targets[0]) if isinstance(node, ast.Name)
        }
        assert isinstance(function.body[0].value, ast.Call)
        assert "__extracted_func" in ast.unparse(function.body[0].value)
    observations = []
    for text in (safe, rewritten):
        namespace: dict[str, object] = {}
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            exec(compile(text, "m.py", "exec"), namespace)
            observations.append(([namespace[name]() for name in ("f", "g")], stream.getvalue()))  # type: ignore[operator]
    assert observations[0] == observations[1]

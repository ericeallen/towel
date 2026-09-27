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

"""A file too deeply nested to analyze is skipped with a warning; the run goes on.

The analysis walks each tree recursively, and one expression of a few hundred
chained terms is deeper than the interpreter's recursion limit. That used to
raise RecursionError out of directory mode and end the run for every file.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
import contextlib
import io
from pathlib import Path

import pytest

from towel.unification.exceptions import ProjectScanLimitError
from towel.unification.instrumentation_flow import UNKNOWN, Flow, Symbol, Value, ValueKind
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_instrumentation_forms import refusal


def _function(name: str, threshold: int, tail: str = "") -> str:
    return (
        f"def {name}(items):\n    total = 0\n    for item in items:\n"
        f"        if item > {threshold}:\n            total += item * 2\n"
        f"    return total + 1{tail}\n"
    )


def test_a_file_too_deep_to_analyze_is_skipped_and_the_others_are_refactored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    chain = " + (" + "+".join(str(term) for term in range(1500)) + ")"
    deep = _function("a", 4, chain) + "\n\n" + _function("b", 5, chain)
    (project / "deep.py").write_text(deep)
    (project / "ok.py").write_text(_function("c", 4) + "\n\n" + _function("d", 5))
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()):
        results, termination = engine.refactor_directory_to_fixed_point(
            str(project), str(project), progress="none"
        )
    assert termination == "fixed_point"
    assert set(results) == {str(project / "ok.py")}
    assert f"Skipping {project / 'deep.py'}: it nests too deeply" in caplog.text
    assert (project / "deep.py").read_text() == deep


@pytest.mark.parametrize("position", ["before", "after", "alias"])
def test_deep_expression_flow_visits_instrumenters_in_order(position: str) -> None:
    """Deep discarded values must not hide calls or reorder a callee's walrus binding."""
    chain = "+".join(str(term) for term in range(1500))
    application = "(0 if checked(C) else 0)"
    expressions = {
        "before": f"{application} + {chain}",
        "after": f"{chain} + {application}",
        "alias": f"(0 if (apply := checked) else 0) + {chain} + (0 if apply(C) else 0)",
    }
    checked = Value(symbols=frozenset({Symbol("typeguard.typechecked", imported=True)}))
    klass = Value.kind(ValueKind.CLASS)
    calls: list[tuple[Value, tuple[Value, ...]]] = []

    def call(
        node: ast.Call, callee: Value, args: tuple[Value, ...], keywords: Mapping[str, Value]
    ) -> Value:
        calls.append((callee, args))
        return UNKNOWN

    flow = Flow(
        call,
        {"checked": checked, "C": klass},
        lambda node, bases: UNKNOWN,
        lambda value: value,
    )
    tree = ast.parse(expressions[position], mode="eval")
    assert flow.value(tree.body) == UNKNOWN
    assert calls == [(checked, (klass,))]


@pytest.mark.parametrize("position", ["before", "after"])
def test_deep_module_cannot_hide_another_modules_instrumenter(
    tmp_path: Path, position: str
) -> None:
    """Remaining recursive scans must fail closed, rather than drop an unread module."""
    chain = "+".join(str(term) for term in range(1500))
    application = "(0 if checked(C) else 0)"
    expression = f"{application} + {chain}" if position == "before" else f"{chain} + {application}"
    deep = (
        "from subject import C\nfrom typeguard import typechecked as checked\n"
        f"def setup():\n    return {expression}\nsetup()\n"
    )
    (tmp_path / "deep.py").write_text(deep)
    with pytest.raises(ProjectScanLimitError, match="instrumentation analysis exceeds"):
        refusal(tmp_path, "class C:\n    def f(self): return 1\n")
    assert (tmp_path / "deep.py").read_text() == deep


def test_unresolved_recursive_instrumentation_analysis_fails_explicitly(tmp_path: Path) -> None:
    """Unscanned value semantics cannot be mistaken for absence of instrumentation."""
    source = (
        "class C:\n    def f(self): return 1\n" f"def setup():\n    return C{'.attribute' * 1500}\n"
    )
    with pytest.raises(ProjectScanLimitError, match="instrumentation analysis exceeds"):
        refusal(tmp_path, source)

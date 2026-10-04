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

"""Where a file's project lies is looked up once per file and run, not once per call site.

Every call site of every pair asks which project holds its file: for the
coverage configuration that decides excluded lines, and, across modules, for
the project's own writes into module namespaces and its declared
dependencies. The answer depends only on the path and the run's stage, so it
is remembered for the run. Remembered by the answer instead (a table keyed by
project root, reached by recomputing the root), it cost 12,205 calls of
``find_project_root`` and of ``_origin_of`` for one input on fifty
near-identical functions, and made the run twice as slow as 1.732's. These
tests count those calls, so a lookup that repeats per call site fails here
without a timing.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
import sys

import pytest

from towel import project_layout
from towel.diagnostics import Settings
from towel.unification.annotation_wiring import HelperAnnotationWiring
from towel.unification.refactor_engine import UnificationRefactorEngine

_FUNCTION = """\
def {name}{index}(order):
    items = [i for i in order.items if i.in_stock]
    subtotal = sum(i.price for i in items)
    total = round(subtotal * 1.08, 2)
    return "{name}{index} " + str(total)
"""

SERIAL = Settings(
    workers=1,
    check_ast_immutable=False,
    debug_rejections=False,
    debug_validation=False,
    debug_overlap=False,
    debug_types=False,
)
"""Every pair judged in this process, so every lookup is counted here."""


def _project(root: Path, modules: dict[str, int]) -> Path:
    """A project whose ``pkg`` holds, per module, that many functions sharing one block."""
    package = root / "pkg"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n')
    (package / "__init__.py").write_text("")
    for module, count in modules.items():
        (package / f"{module}.py").write_text(
            "\n\n".join(_FUNCTION.format(name=module, index=index) for index in range(count))
        )
    return package


def _count_lookups(monkeypatch: pytest.MonkeyPatch) -> Counter[tuple[str, str]]:
    """Count every call of ``find_project_root`` and ``_origin_of``, by function and input."""
    calls: Counter[tuple[str, str]] = Counter()
    real_root = project_layout.find_project_root
    real_origin = HelperAnnotationWiring._origin_of

    def root(path: Path) -> Path:
        calls["find_project_root", str(path)] += 1
        return real_root(path)

    def origin(self: HelperAnnotationWiring, path: str) -> str:
        calls["_origin_of", str(path)] += 1
        return real_origin(self, path)

    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("towel") and (
            getattr(module, "find_project_root", None) is real_root
        ):
            monkeypatch.setattr(module, "find_project_root", root)
    monkeypatch.setattr(HelperAnnotationWiring, "_origin_of", origin)
    return calls


@pytest.mark.parametrize(
    "cross_module, modules",
    [(False, {"mod": 18}), (True, {"left": 8, "right": 8})],
    ids=["one-module", "cross-module"],
)
def test_an_analysis_looks_up_each_files_project_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cross_module: bool,
    modules: dict[str, int],
) -> None:
    package = _project(tmp_path, modules)
    engine = UnificationRefactorEngine(
        min_lines=3, settings=SERIAL, cross_module_helpers=cross_module
    )
    calls = _count_lookups(monkeypatch)
    proposals = engine.analyze_directory(str(package), progress="none")
    assert proposals, "the fixture must give pairs, and so call sites, to judge"
    repeated = {key: count for key, count in calls.items() if count > 1}
    assert not repeated, repeated
    assert {function for function, _ in calls} == {"find_project_root", "_origin_of"}


def test_a_fixed_point_run_looks_up_each_input_a_fixed_number_of_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent of how many call sites there are: once to pair, once to write the helper.

    Writing a helper looks its host's project up again (``materialize``), once
    per helper; three times as many near-identical functions give the same
    one helper, and so the same counts.
    """
    counted = []
    for count in (6, 18):
        package = _project(tmp_path / str(count), {"mod": count})
        engine = UnificationRefactorEngine(min_lines=3, settings=SERIAL)
        with monkeypatch.context() as scoped:
            calls = _count_lookups(scoped)
            results, _ = engine.refactor_directory_to_fixed_point(
                str(package), str(package), progress="none"
            )
        assert sum(applied for applied, _ in results.values()) == 1
        counted.append(sorted(count for count in calls.values()))
    assert counted[0] == counted[1], counted
    assert max(counted[1]) <= 2, counted


def test_python_floor_reuses_layout_but_rechecks_host_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _project(tmp_path, {"mod": 1})
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0"\nrequires-python = ">=3.7"\n'
    )
    path = str(package / "mod.py")
    engine = UnificationRefactorEngine(settings=SERIAL)
    engine.begin_refactoring_run([path])
    calls = _count_lookups(monkeypatch)
    old_host = ast.parse("value = 1")
    younger_host = ast.parse("value: list[int] = []")
    assert [engine._oldest_python_for(path, old_host) for _ in range(5)] == [(3, 7)] * 5
    assert engine._oldest_python_for(path, younger_host) == (3, 9)
    assert engine._oldest_python_for(path, old_host) == (3, 7)
    assert calls["_origin_of", path] == 1
    assert calls["find_project_root", str(Path(path).resolve())] == 1


def test_python_floor_refreshes_declaration_and_stage_origin_between_runs(tmp_path: Path) -> None:
    package = _project(tmp_path / "original", {"mod": 1})
    original = package / "mod.py"
    config = package.parent / "pyproject.toml"
    config.write_text('[project]\nname = "p"\nversion = "0"\nrequires-python = ">=3.7"\n')
    stage = _project(tmp_path / "stage", {"mod": 1})
    (stage.parent / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0"\nrequires-python = ">=3.12"\n'
    )
    copied = str(stage / "mod.py")
    engine = UnificationRefactorEngine(settings=SERIAL)
    host = ast.parse("value = 1")
    engine.begin_refactoring_run([str(original)])
    engine._output_origin = (package.parent, stage.parent)
    assert engine._oldest_python_for(copied, host) == (3, 7)
    config.write_text('[project]\nname = "p"\nversion = "0"\nrequires-python = ">=3.9"\n')
    # Layout and declared metadata are stable within a run; a new explicit run
    # rereads them. The host's evaluated syntax is never memoized with the layout.
    assert engine._oldest_python_for(copied, host) == (3, 7)
    engine.begin_refactoring_run([str(original)])
    engine._output_origin = (package.parent, stage.parent)
    assert engine._oldest_python_for(copied, host) == (3, 9)
    engine.begin_refactoring_run([copied])
    assert engine._oldest_python_for(copied, host) == (3, 12)


def test_a_functions_own_locals_are_found_once_per_block_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every call site's thunk check asks; the answer is kept per block site, not recomputed.

    Unkept, fifty near-identical functions computed it 12,202 times. Kept,
    each function computes it once for each of its blocks that reaches the
    check, however many other functions it is paired with.
    """
    from towel.unification import block_analysis, semantic_safety

    real = semantic_safety.own_scope_locals
    most = []
    for count in (6, 18):
        calls: Counter[tuple[str, int]] = Counter()

        def counted(function: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
            calls[function.name, function.lineno] += 1
            return real(function)

        monkeypatch.setattr(block_analysis, "own_scope_locals", counted)
        package = _project(tmp_path / str(count), {"mod": count})
        engine = UnificationRefactorEngine(min_lines=3, settings=SERIAL)
        assert engine.analyze_directory(str(package), progress="none")
        assert len(calls) == count, calls
        most.append(max(calls.values()))
    template = ast.parse(_FUNCTION.format(name="f", index=0)).body[0]
    assert isinstance(template, ast.FunctionDef)
    statements = len(template.body)
    assert most[0] == most[1] <= statements, most


def test_the_run_lookups_keep_nothing_when_memoization_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``memoization_disabled`` must reach these memos too, so the suite can see a verdict they change."""
    from towel.unification.bounded_cache import memoization_disabled

    package = _project(tmp_path, {"mod": 6})
    engine = UnificationRefactorEngine(min_lines=3, settings=SERIAL)
    calls = _count_lookups(monkeypatch)
    with memoization_disabled():
        assert engine.analyze_directory(str(package), progress="none")
    assert max(calls.values()) > 1, calls

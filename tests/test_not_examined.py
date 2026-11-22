# Copyright 2026 Eric Allen
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

"""The census counts the code no candidate is drawn from, per file.

Audit finding 2 (engine map E, scenarios 20, 31 and 32): module-level
statements and early-return guards are never candidates, and a run that found
nothing said only "No duplicates found!". The census now carries, per file,
how many of each the file holds, so the report can say what was not examined.
"""

from __future__ import annotations

import ast
import contextlib
import io
import textwrap
from pathlib import Path

import pytest

from towel.unification.models import NotExamined
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.unexamined import (
    early_return_guards,
    module_level_statements,
    not_examined,
)

SCRIPT = '''
"""Nightly export."""

import csv
import sys

ROWS = []


def load(path):
    with open(path) as handle:
        return list(csv.reader(handle))


for path in sys.argv[1:]:
    ROWS.extend(load(path))
print(len(ROWS))

if __name__ == "__main__":
    print("done")
    sys.exit(0)
'''

GUARDS = """
def find(items, name):
    if not items:
        return None
    for item in items:
        if item.name == name:
            return item
    return None


def both(flag):
    if flag:
        return 1
    else:
        return 2
"""


def test_module_level_statements_are_counted() -> None:
    """``ROWS = []``, the ``for`` loop, ``print`` and the two statements under ``__main__``."""
    assert module_level_statements(ast.parse(textwrap.dedent(SCRIPT))) == 5


def test_early_return_guards_are_counted_and_complete_returns_are_not() -> None:
    """``if not items: return None`` and the ``if`` inside the loop; ``both`` returns on every path."""
    assert early_return_guards(ast.parse(textwrap.dedent(GUARDS))) == 2


def test_a_file_with_nothing_unexamined_is_not_listed() -> None:
    source = "import os\n\n\ndef f(x):\n    return x + 1\n"
    assert not_examined("clean.py", ast.parse(source)) is None


def test_a_run_s_census_and_report_carry_the_counts(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "script.py").write_text(textwrap.dedent(SCRIPT))
    (source / "guards.py").write_text(textwrap.dedent(GUARDS))
    (source / "clean.py").write_text("def f(x):\n    return x + 1\n")
    engine = UnificationRefactorEngine()
    engine.analyze_directory(str(source), progress="none")
    by_name = {Path(found.path).name: found for found in engine.last_census.not_examined}
    assert set(by_name) == {"script.py", "guards.py"}
    assert by_name["script.py"].module_statements == 5
    assert by_name["guards.py"] == NotExamined(by_name["guards.py"].path, 0, 2)

    out = tmp_path / "out"
    with contextlib.redirect_stdout(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(source), str(out), progress="none")
    report = {Path(found.path).name: found for found in engine.run_report.not_examined}
    assert report["script.py"].module_statements == 5
    assert report["guards.py"].early_return_guards == 2


def test_census_locates_the_windows_left_out_of_one_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final-speed lane: the census names each limited function and the size of the search."""
    from towel.cli_reporting import analysed_record, not_examined_lines
    from towel.unification import refactor_engine

    path = tmp_path / "repeated.py"
    path.write_text(
        "flag = True\n\nclass Graph:\n    def build(self):\n"
        + "".join(f"        value = {i} + 1\n" for i in range(8))
    )
    monkeypatch.setattr(refactor_engine, "MAX_SAME_FUNCTION_WINDOWS", 5, raising=False)
    engine = UnificationRefactorEngine(min_lines=1, expression_candidates=False)
    engine.refactor_to_fixed_point(str(path), progress="none", output_path=str(tmp_path / "out.py"))
    report = engine.run_report

    def relative(p: str) -> str:
        return Path(p).name

    (entry,) = analysed_record(report, relative)["not_examined"]
    assert entry["path"] == "out.py" and entry["module_statements"] == 1
    assert entry.get("limited_functions") == [
        {"function": "build", "line": 4, "windows": 23, "limit": 5}
    ]
    assert any(
        "build" in line and "23" in line and "retained 5" in line
        for line in not_examined_lines(report, relative, nothing_found=False, explain=True)
    )
    assert any(
        "1 function" in line and "--explain" in line
        for line in not_examined_lines(report, relative, nothing_found=False, explain=False)
    )


def test_pipeline_census_includes_the_pairing_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from towel.unification import refactor_engine
    from towel.unification.models import AnalysisCensus
    from towel.unification.pipeline import run_pipeline

    path = tmp_path / "many.py"
    path.write_text("def run():\n" + "".join(f"    value = {i} + 1\n" for i in range(8)))
    monkeypatch.setattr(refactor_engine, "MAX_SAME_FUNCTION_WINDOWS", 5)
    censuses: list[AnalysisCensus] = []
    engine = UnificationRefactorEngine(min_lines=1, expression_candidates=False)
    run_pipeline([str(path)], engine=engine, census=censuses.append, progress="none")
    assert len(censuses) == 1
    assert len(censuses[-1].not_examined) == 1
    assert censuses[-1].not_examined[0].limited_functions[0].windows == 23


def test_later_pass_can_first_limit_a_function(tmp_path: Path) -> None:
    """Cap census 1: extracting the if blocks joins two runs of expression statements."""
    from towel.cli_reporting import analysed_record, not_examined_lines

    def steps(start: int, stop: int) -> str:
        return "".join(
            f"    g.step({', '.join(str(i * 100 + j) for j in range(7))})\n"
            for i in range(start, stop)
        )

    def prepare(value: int) -> str:
        return (
            f"    if g.ready({value}):\n"
            f"        g.prepare_stage_one({value}, 20, 30)\n"
            f"        g.prepare_stage_two({value}, 40, 50)\n"
            f"        g.prepare_stage_three({value}, 60, 70)\n"
        )

    def marked(value: int) -> str:
        return (
            "    g.mark_alpha(999, 998, 997, 996)\n"
            + prepare(value)
            + ("    g.finish_omega(999, 998, 997, 996)\n")
        )

    path = tmp_path / "mod.py"
    path.write_text(
        "def build(g):\n"
        + steps(0, 36)
        + marked(10)
        + steps(36, 42)
        + marked(20)
        + steps(42, 56)
        + "".join(f"def seed_{i}(g):\n" + prepare(i) for i in (30, 40, 50))
    )
    engine = UnificationRefactorEngine(strict=True)
    engine.analyze_files([str(path)], progress="none")
    assert engine.last_census.not_examined == ()
    _, applied, _ = engine.refactor_to_fixed_point(
        str(path), output_path=str(tmp_path / "out.py"), progress="none"
    )
    report = engine.run_report
    assert applied >= 1
    assert report.functions_analysed == 4

    def relative(path: str) -> str:
        return Path(path).name

    (entry,) = analysed_record(report, relative)["not_examined"]
    assert entry["limited_functions"] == [
        {"function": "build", "line": 1, "windows": 1169, "limit": 1024}
    ]
    for explain in (False, True):
        assert any(
            "limited its search for repeated blocks inside 1 function" in line
            for line in not_examined_lines(report, relative, nothing_found=False, explain=explain)
        )


def _three_edge_builders(tmp_path: Path) -> Path:
    path = tmp_path / "edges.py"
    body = "".join(f"    g.edge({i}, {i + 1})\n" for i in range(60))
    path.write_text("".join(f"def build{i}(g):\n{body}" for i in range(3)))
    return path


def test_pair_budget_excludes_all_capped_windows(tmp_path: Path) -> None:
    """Cap census 2: a budget that leaves no pairs gives no same-function cap notice."""
    from towel.cli_reporting import run_outcome

    path = _three_edge_builders(tmp_path)
    engine = UnificationRefactorEngine(strict=True, max_candidate_pairs=1)
    engine.refactor_to_fixed_point(str(path), output_path=str(tmp_path / "out.py"), progress="none")
    report = engine.run_report
    assert report.candidate_pairs == 0
    assert not report.not_examined
    assert run_outcome(report, 0) == "no_repeated_code"


def test_partial_pass_does_not_report_an_unchanged_function_limit(tmp_path: Path) -> None:
    """Cap census 2: unchanged functions do not pair with themselves on a partial pass."""
    from towel.unification.models import AnalysisCensus
    from towel.unification.pipeline import run_pipeline

    path = _three_edge_builders(tmp_path)
    small = tmp_path / "small.py"
    small.write_text("def tiny(x):\n    return x + 1\n")
    censuses: list[AnalysisCensus] = []
    run_pipeline(
        [str(small), str(path)],
        engine=UnificationRefactorEngine(strict=True),
        changed_files=frozenset({str(small)}),
        census=censuses.append,
        progress="none",
    )
    assert len(censuses) == 1
    assert not censuses[0].not_examined


def test_cap_omits_an_entire_bucket_that_fits_the_pair_budget(tmp_path: Path) -> None:
    """Cap census re-review 1: the only affordable pair can be outside the retained windows."""
    from towel.cli_reporting import run_outcome

    path = tmp_path / "single.py"
    assignments = "".join(f"    a = g.edge({i}, {i + 1})\n" for i in range(65))
    other = "".join(f"    b = h.node(x + y + z + w + {i})\n" for i in range(65))
    repeated = (
        "    if g.ready(10):\n        g.alpha(10, 20, 30)\n"
        "        g.beta(40, 50, 60)\n        g.gamma(70, 80, 90)\n"
    )
    path.write_text(
        f"def build0(g):\n{assignments}{repeated}{repeated}def build1(h,x,y,z,w):\n{other}"
    )
    engine = UnificationRefactorEngine(strict=True, max_candidate_pairs=1)
    engine.refactor_to_fixed_point(str(path), output_path=str(tmp_path / "out.py"), progress="none")
    report = engine.run_report
    assert report.candidate_pairs == 0
    assert run_outcome(report, 0) == "not_fully_examined"
    (entry,) = report.not_examined
    (limited,) = entry.limited_functions
    assert limited.function == "build0" and limited.limit == 0 and limited.windows >= 2


def test_a_new_helper_cannot_be_reported_as_an_input_definition(tmp_path: Path) -> None:
    """Cap census re-review 3: extracting a long body must not invent a third input function."""
    body = (
        "    t = 0\n"
        + "".join(
            f"    g.step_{i}(x)\n    v = g.load_{i}(x)\n    t += v\n    assert g.ok(t)\n    pass\n"
            for i in range(13)
        )
        + "    return t\n"
    )
    path = tmp_path / "two_funcs.py"
    path.write_text(f"def f1(g, x):\n{body}def f2(g, x):\n{body}")
    engine = UnificationRefactorEngine(strict=True)
    _, applied, _ = engine.refactor_to_fixed_point(
        str(path), output_path=str(tmp_path / "out.py"), progress="none"
    )
    assert applied > 0
    report = engine.run_report
    assert report.functions_analysed == 2
    (entry,) = report.not_examined
    assert [(f.function, f.line) for f in entry.limited_functions] == [("f1", 1), ("f2", 69)]

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

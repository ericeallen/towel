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

"""Two things a typed run does with the files it reads, with checkers whose answers are fixed.

A file it cannot read is left out and left alone, as without types: one
undecodable module of test data used to refuse the whole typed run (D7). And a
class the checker reveals by its whole path, which the helper's module does
not import, is imported under ``TYPE_CHECKING`` and named directly, as the
documentation says: it used to become ``Any`` unless the module bound the
path's head (D13).
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from towel.type_inference import (
    CheckResult,
    CheckSuccess,
    MypyInferrer,
    RevealKey,
    RevealRequest,
    Subtyping,
)
from towel.unification.annotations import annotation_from_revealed, unwritten_as_any
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.probe_answers import answer_probes

TWINS = textwrap.dedent("""\
    def first(value: int) -> int:
        total = value + 1
        doubled = total * 2
        answer = doubled - 3
        return answer


    def second(value: int) -> int:
        total = value + 1
        doubled = total * 2
        answer = doubled - 3
        return answer
    """)

LATIN = b'NAME = "caf\xe9"\n'
"""Latin-1 bytes with no coding cookie: not a module Python, or Towel, can read."""


class _Fixed(MypyInferrer):
    """A checker with fixed answers that says it kept warm state, so the run confirms it cold.

    ``types`` answers a reveal of an expression; every check is clean. No mypy
    runs: every question is answered here.
    """

    def __init__(self, types: Mapping[str, str] = {}) -> None:
        super().__init__()
        self.types = dict(types)
        self.checked: List[Dict[str, str]] = []
        self.cold = False

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checked.append(dict(sources))
        self.answered_from_warm_state = True
        return CheckSuccess(())

    def forget_warm_state(self) -> None:
        self.cold = True

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        answers: Dict[RevealKey, str] = dict(answer_probes(requests))
        for request in requests:
            for index, expression in enumerate(request.expressions):
                key = (request.file_path, request.line, index)
                if expression.startswith("_towel_imported_"):
                    answers[key] = "types.ModuleType"
                elif expression in self.types:
                    answers[key] = self.types[expression]
        return answers

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        return [Subtyping.UNKNOWN for _ in pairs]


def test_a_file_that_cannot_be_read_is_left_alone_and_the_typed_run_goes_on(
    tmp_path: Path,
) -> None:
    """D7: ``Cannot read original source`` refused the run; ``--no-types`` completed it."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text(TWINS)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "latin.py").write_bytes(LATIN)
    oracle = _Fixed()
    engine = UnificationRefactorEngine(type_oracle=oracle)
    results, _ = engine.refactor_directory_to_fixed_point(
        str(tmp_path), str(tmp_path), progress="none"
    )
    assert {Path(path).name for path in results} == {"m.py"}
    assert (tmp_path / "data" / "latin.py").read_bytes() == LATIN
    assert "__extracted_func_0" in (tmp_path / "pkg" / "m.py").read_text()
    assert oracle.cold, "the finished project was still confirmed from nothing"
    assert all(
        not path.endswith("latin.py") for sources in oracle.checked for path in sources
    ), "the checker reads the file itself, as the project's own check would"


ROW = textwrap.dedent("""\
    from pkg.models import make_row


    def first(k: int) -> int:
        row = make_row(k)
        print("first row", row.n)
        total = row.n * 2
        return total


    def second(k: int) -> int:
        row = make_row(k + 1)
        print("second row", row.n)
        total = row.n * 2
        return total
    """)

MODELS = textwrap.dedent("""\
    class Row:
        def __init__(self, n: int) -> None:
            self.n = n


    def make_row(n: int) -> Row:
        return Row(n)
    """)


def test_a_class_revealed_by_its_whole_path_is_imported_for_the_checker(tmp_path: Path) -> None:
    """D13: ``pkg.models.Row``, which report.py does not import, is named ``"Row"``."""
    package = tmp_path / "pkg"
    package.mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "0"\n')
    (package / "__init__.py").write_text("")
    (package / "models.py").write_text(MODELS)
    (package / "report.py").write_text(ROW)
    oracle = _Fixed({"row": "pkg.models.Row", '"first row"': "builtins.str"})
    engine = UnificationRefactorEngine(type_oracle=oracle)
    engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    written = (package / "report.py").read_text()
    tree = ast.parse(written)
    guard = next(node for node in tree.body if isinstance(node, ast.If))
    assert ast.unparse(guard.body[0]) == "from pkg.models import Row", written
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    assert "row: 'Row'" in ast.unparse(helper.args), written


def test_a_whole_path_the_host_cannot_import_is_written_any() -> None:
    """Where no module of the project owns the class, the annotation is Any, as before."""
    host = ast.parse("import os\n")
    kept = annotation_from_revealed("_io.TextIOWrapper", host, True)
    assert isinstance(kept, ast.Constant) and kept.value == "_io.TextIOWrapper"
    helper = ast.parse(
        "def helper(stream: '_io.TextIOWrapper', path: 'os.PathLike[str]') -> None: ..."
    ).body[0]
    assert isinstance(helper, ast.FunctionDef)
    assert unwritten_as_any(helper, host) == (("typing", "Any"),)
    assert ast.unparse(helper.args) == "stream: Any, path: 'os.PathLike[str]'"

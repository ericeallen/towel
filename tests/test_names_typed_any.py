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

"""A file whose imports the checker cannot see into is found by asking, not by its errors.

A typed run leaves alone a file where the check types a name as ``Any``: a
change there is checked against ``Any``, which accepts it. It found such files
only by the errors that report an unresolved import, and a configuration can
silence exactly those: mypy's ``ignore_missing_imports``, pyright's
``reportMissingImports = "none"``. With the module missing where Towel runs
and present in the project's environment, Towel accepted ``int`` for a
parameter one site passes a ``str`` (D11), and a change that left a
``type: ignore`` unused on uvicorn (P1-1). Now each checker is asked what every
import binds. And a subtype question about a type the checker sees as
``Any``, whose yes comes from ``Any`` accepting everything, answers UNKNOWN.
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import textwrap
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

import pytest

from towel.type_baseline import import_probes, imports_typed_as_any
from towel.type_inference import (
    CheckResult,
    CheckSuccess,
    MypyInferrer,
    RevealKey,
    RevealRequest,
    Subtyping,
)
from towel.unification.annotations import normalize_union, oracle_subtypes
from towel.unification.exceptions import UnverifiableChangeError
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.probe_answers import answer_probes

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
requires_pyright = pytest.mark.skipif(
    shutil.which("pyright") is None and importlib.util.find_spec("pyright") is None,
    reason="pyright absent",
)

PROTO = textwrap.dedent("""\
    import os
    import towel_absent_module
    from typing import TYPE_CHECKING

    from towel_absent_module import Frame

    if TYPE_CHECKING:
        from os import PathLike


    def handle(event: Frame) -> object:
        return towel_absent_module.parse(event), os.sep
    """)
"""A module importing, whole and by name, a module the checker cannot resolve."""

RECORDED = {
    # mypy 2.3.1 and pyright 1.1.414 on PROTO's probes, with the missing-import
    # report silenced: [tool.mypy] ignore_missing_imports = true, and
    # [tool.pyright] reportMissingImports = "none". By (probe line, index).
    "mypy": {
        (2, 0): "types.ModuleType",
        (2, 1): "str",
        (4, 0): "Any",
        (4, 1): "Any",
        (7, 0): "types.ModuleType",
        (7, 1): "bool",
        (11, 0): "Any",
        (11, 1): "Any",
        (16, 0): "types.ModuleType",
        (16, 1): "def [AnyStr_co in (str, bytes)] () -> os.PathLike[AnyStr_co]",
    },
    "pyright": {
        (2, 0): 'Module("os")',
        (2, 1): "str",
        (4, 0): 'Module("towel_absent_module")',
        (4, 1): "Unknown",
        (7, 0): 'Module("typing")',
        (7, 1): "bool",
        (11, 0): 'Module("towel_absent_module")',
        (11, 1): "Unknown",
        (16, 0): 'Module("os")',
        (16, 1): "type[PathLike[Unknown]]",
    },
}


def test_each_import_is_probed_where_it_stands_under_names_of_its_own() -> None:
    probes = import_probes("/project/pkg/proto.py", PROTO)
    assert probes is not None
    source = probes.requests[0].source
    assert source.splitlines()[:4] == [
        "import os as _towel_imported_0",
        "import os",
        "import towel_absent_module as _towel_imported_1",
        "import towel_absent_module",
    ]
    assert "    from os import PathLike as _towel_imported_7\n    from os import PathLike" in (
        source
    ), "inside TYPE_CHECKING, as the import is"
    lines = source.splitlines()
    for request in probes.requests:
        assert all(request.source == source for request in probes.requests)
        assert lines[request.line - 1].lstrip().startswith(("import", "from")), request
    asked = {(q.key[1], q.key[2]): (q.kind, q.subject) for q in probes.questions}
    assert asked[(4, 1)] == ("attribute", '"parse" of module "towel_absent_module"')
    assert asked[(11, 1)] == ("name", '"Frame" imported from "towel_absent_module"')
    compile(source, "<probes>", "exec")


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
def test_recorded_answers_name_the_imports_the_checker_cannot_see(checker: str) -> None:
    path = "/project/pkg/proto.py"
    probes = import_probes(path, PROTO)
    assert probes is not None
    answers = {(path, line, index): text for (line, index), text in RECORDED[checker].items()}
    found = imports_typed_as_any(probes, [answers], path)
    assert {error.line for error in found} == {2, 5}, found
    assert all(error.path == path for error in found)
    if checker == "mypy":
        assert 'module "towel_absent_module" as Any' in found[0].message
    else:
        assert '"parse" of module "towel_absent_module" as Unknown' in found[0].message


def test_an_import_the_checker_does_not_look_at_is_not_named() -> None:
    """No answer is the checker not looking there; the unreachable rule deals with that."""
    path = "/project/m.py"
    probes = import_probes(path, "import sys\nif sys.platform == 'win32':\n    import winreg\n")
    assert probes is not None
    answered = {q.key: "types.ModuleType" for q in probes.questions if q.line == 1}
    assert imports_typed_as_any(probes, [answered], path) == ()


def test_a_declared_any_or_a_future_import_is_not_a_blind_spot() -> None:
    path = "/project/m.py"
    source = "from __future__ import annotations\nfrom .typed import VALUE\n"
    probes = import_probes(path, source)
    assert probes is not None
    for request in probes.requests:
        first, *rest = request.source.split("\n")
        assert first == "from __future__ import annotations", "nothing may stand before it"
        assert "__future__" not in "\n".join(rest)
    declared = {
        q.key: ("types.ModuleType" if q.kind == "module" else "Any") for q in probes.questions
    }
    assert imports_typed_as_any(probes, [declared], path) == ()


TWINS = textwrap.dedent("""
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


class _Silenced:
    """A checker configured not to report the import it cannot resolve: no error, only ``Any``."""

    def __init__(self) -> None:
        self.inferences = 0

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        return CheckSuccess(())

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        answers: Dict[RevealKey, str] = dict(answer_probes(requests))
        for request in requests:
            if not request.expressions or not request.expressions[0].startswith("_towel_"):
                self.inferences += 1
                continue
            lines = request.source.split("\n")
            for index, expression in enumerate(request.expressions):
                bound = [line for line in lines if line.endswith(f" as {expression}")]
                blind = bool(bound) and "gone" in bound[0]
                answers[(request.file_path, request.line, index)] = (
                    "Any" if blind else "types.ModuleType"
                )
        return answers

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        return [Subtyping.UNKNOWN for _ in pairs]

    def close(self) -> None:
        pass


def test_a_file_whose_import_the_checker_types_as_any_is_left_alone_without_any_error(
    tmp_path: Path,
) -> None:
    """D11: no error names the import, so only asking the checker finds it."""
    blind, seen = tmp_path / "blind.py", tmp_path / "seen.py"
    blind.write_text("import gone\n\n" + TWINS)
    seen.write_text(TWINS)
    engine = UnificationRefactorEngine(type_oracle=_Silenced())
    results, _ = engine.refactor_directory_to_fixed_point(
        str(tmp_path), str(tmp_path), progress="none"
    )
    assert {Path(path).name for path in results} == {"seen.py"}
    assert blind.read_text() == "import gone\n\n" + TWINS
    assert engine.run_report.declined_proposals == {
        "not verifiable: its file holds a name the type checker cannot type": 1
    }
    proposal = engine.analyze_file(str(blind))[0]
    with pytest.raises(UnverifiableChangeError, match='module "gone" as Any'):
        engine.apply_refactoring(str(blind), proposal)


# --- A subtype question about Any ---------------------------------------------------


class _Assignable:
    """Answers as mypy does: ``Any``, and a class with an ``Any`` base, are assignable to anything."""

    def __init__(self, untyped: Sequence[str], *, looks: bool = True) -> None:
        self.untyped = set(untyped)
        self.looks = looks
        self.asked: List[Tuple[str, str]] = []

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        assert "class _TowelUnrelatedType" in source
        self.asked.extend(pairs)
        if not self.looks:
            return [Subtyping.YES for _ in pairs]  # the probe was never checked
        order = {"bool": 0, "int": 1, "float": 2}
        verdicts = []
        for narrow, wide in pairs:
            if narrow in self.untyped or wide in self.untyped or narrow == wide:
                verdicts.append(Subtyping.YES)
            elif wide == "_TowelUnrelatedType":
                verdicts.append(Subtyping.NO)
            elif narrow in order and wide in order:
                verdicts.append(Subtyping.YES if order[narrow] <= order[wide] else Subtyping.NO)
            else:
                verdicts.append(Subtyping.NO)
        return verdicts


def _members(*texts: str) -> List[ast.expr]:
    return [ast.parse(text, mode="eval").body for text in texts]


def _normalized(oracle: _Assignable, *texts: str) -> List[str]:
    relation = oracle_subtypes(oracle, "/project/m.py", "")  # type: ignore[arg-type]
    return [ast.unparse(member) for member in normalize_union(_members(*texts), relation)]


def test_a_type_spelled_with_any_is_asked_nothing_and_absorbs_nothing() -> None:
    oracle = _Assignable(untyped=[])
    assert _normalized(oracle, "list[Any]", "list[int]") == ["list[Any]", "list[int]"]
    assert not any("Any" in narrow or "Any" in wide for narrow, wide in oracle.asked)


def test_a_class_the_checker_sees_as_any_is_unknown_never_a_subtype() -> None:
    """D11's collapse: ``Base`` with an ``Any`` base is assignable to ``int``, and dropped."""
    oracle = _Assignable(untyped=["Base"])
    assert _normalized(oracle, "Base", "int") == ["Base", "int"]
    assert ("Base", "_TowelUnrelatedType") in oracle.asked
    assert _normalized(_Assignable(untyped=[]), "bool", "int") == [
        "int"
    ], "the relation still holds"


def test_a_checker_that_did_not_look_answers_nothing() -> None:
    """``int`` assignable to the probe's own class means the probe was never checked."""
    assert _normalized(_Assignable(untyped=[], looks=False), "bool", "int") == ["bool", "int"]


# --- With the checkers themselves: the missing-import report silenced ---------------

SILENCED = {
    "mypy": "[tool.mypy]\nignore_missing_imports = true\nwarn_unused_ignores = true\n",
    "pyright": '[tool.pyright]\ninclude = ["pkg"]\nreportMissingImports = "none"\n',
}

BLIND = textwrap.dedent("""\
    from towel_absent_module import make


    def first(count: int) -> str:
        value = make()
        label = "first"
        print(label, value)
        return label


    def second(count: int) -> str:
        value = count
        label = "second"
        print(label, value)
        return label
    """)


def _run(tmp_path: Path, checker: str) -> str:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0"\n\n' + SILENCED[checker], encoding="utf-8"
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "m.py").write_text(BLIND, encoding="utf-8")
    if checker == "mypy":
        oracle = MypyInferrer()
    else:
        from towel.type_inference import PyrightOracle

        oracle = PyrightOracle(language_server=False)  # type: ignore[assignment]
    try:
        engine = UnificationRefactorEngine(min_lines=2, type_oracle=oracle)
        engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    finally:
        oracle.close()
    return (package / "m.py").read_text(encoding="utf-8")


@requires_mypy
def test_mypy_with_ignore_missing_imports_the_file_is_left_alone(tmp_path: Path) -> None:
    """D11 and P1-1: mypy reports nothing, and a change there would be checked against Any."""
    assert _run(tmp_path, "mypy") == BLIND


@requires_pyright
def test_pyright_with_missing_imports_unreported_the_file_is_left_alone(tmp_path: Path) -> None:
    assert _run(tmp_path, "pyright") == BLIND

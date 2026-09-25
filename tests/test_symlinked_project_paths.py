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

"""A project reached through a symbolic link is one project, whichever way its files are spelled.

mypy is given each file by its resolved path and prints it that way; Towel
matched what it printed against the path it was asked about, unresolved. On
macOS, where ``/var`` is ``/private/var`` and so is every temporary
directory, every probe then went unanswered: every proposal was declined as
code the checker does not look at, every subtype question was answered yes,
and every import looked resolved. The same case applied nothing under
``/var/folders/...`` and one change under ``/private/var/folders/...``. Now
every place the checker's answers are matched to files compares one resolved
identity.
"""

from __future__ import annotations

import importlib.util
import os
import textwrap
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

import pytest

from towel.pyright_session import pyright_scope
from towel.type_baseline import KnownErrors
from towel.type_inference import (
    CheckFailure,
    MypyInferrer,
    RevealKey,
    RevealRequest,
    Revealed,
    Subtyping,
    TypeDiagnostic,
    _BuildMessages,
    _BuildSource,
    _RelocatedOracle,
    _same_file,
    _subtype_probes,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


@pytest.fixture
def linked(tmp_path: Path) -> Tuple[Path, Path]:
    """A directory, and a symbolic link to it: ``(real, link)``."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    return real, link


def _module(root: Path, text: str = "x = 1\n") -> Path:
    package = root / "pkg"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    module = package / "m.py"
    module.write_text(text, encoding="utf-8")
    return module


def test_a_file_the_checker_prints_resolved_is_the_file_asked_about(
    linked: Tuple[Path, Path],
) -> None:
    real, link = linked
    _module(real)
    assert _same_file(str(real / "pkg" / "m.py"), str(link / "pkg" / "m.py"))
    assert not _same_file(str(real / "pkg" / "m.py"), str(link / "pkg" / "__init__.py"))


class _Printing(MypyInferrer):
    """mypy's worker as it answers: every file printed by its resolved path. No mypy runs."""

    def __init__(self, messages: Sequence[str]) -> None:
        super().__init__()
        self.messages = tuple(messages)

    def _build_errors(
        self,
        sources: Sequence[_BuildSource],
        *,
        complete: bool = False,
        excluded_paths: Sequence[str] = (),
        consumers: Sequence[str] = (),
        root: Optional[Path] = None,
        foreign: Optional[Mapping[str, str]] = None,
        nested: Sequence[Path] = (),
    ) -> _BuildMessages | CheckFailure:
        return _BuildMessages(self.messages)


def test_a_reveal_asked_through_a_link_is_answered(linked: Tuple[Path, Path]) -> None:
    real, link = linked
    _module(real)
    asked = str(link / "pkg" / "m.py")
    oracle = _Printing([f'{real / "pkg" / "m.py"}:1: note: Revealed type is "builtins.int"'])
    try:
        answered = oracle.reveal([RevealRequest(asked, "x = 1\n", 1, "", ("x",))])
    finally:
        oracle.close()
    assert dict(answered) == {(asked, 1, 0): "builtins.int"}


def test_a_subtype_asked_through_a_link_is_not_a_yes_by_silence(
    linked: Tuple[Path, Path],
) -> None:
    """``int`` is no ``str``: mypy's error on the probe's return line, printed resolved, says so."""
    real, link = linked
    _module(real)
    _, _, returns = _subtype_probes("x = 1\n", [("int", "str")])
    line = next(iter(returns))
    oracle = _Printing([f"{real / 'pkg' / 'm.py'}:{line}: error: Incompatible return value type"])
    try:
        verdicts = oracle.is_subtype(str(link / "pkg" / "m.py"), "x = 1\n", [("int", "str")])
    finally:
        oracle.close()
    assert list(verdicts) == [Subtyping.NO]


class _Echo:
    """A checker that answers every probe under the path it was asked about."""

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        return Revealed({(r.file_path, r.line, 0): "int" for r in requests})


def test_a_relocated_answer_comes_back_under_the_spelling_it_was_asked_by(
    linked: Tuple[Path, Path],
) -> None:
    real, link = linked
    (real / "input").mkdir()
    (real / "output").mkdir()
    oracle = _RelocatedOracle(
        _Echo(), (real / "input").resolve(), (real / "output").resolve()  # type: ignore[arg-type]
    )
    asked = str(link / "output" / "m.py")
    answered = oracle.reveal([RevealRequest(asked, "", 3, "", ("(0)",))])
    assert dict(answered) == {(asked, 3, 0): "int"}


def test_the_baseline_compares_a_file_by_one_identity(linked: Tuple[Path, Path]) -> None:
    real, link = linked
    module = _module(real)
    message = "Incompatible types in assignment  [assignment]"
    known = KnownErrors.of([TypeDiagnostic(str(module), message, 1)])
    through_the_link = TypeDiagnostic(str(link / "pkg" / "m.py"), message, 1)
    assert known.introduced([through_the_link]) == ()
    assert known.introduced([through_the_link, through_the_link]) == (through_the_link,)


def test_pyrights_scope_is_the_same_through_a_link(linked: Tuple[Path, Path]) -> None:
    real, link = linked
    (real / "pyrightconfig.json").write_text('{"exclude": ["pkg/legacy"]}', encoding="utf-8")
    for root in (real, link):
        scope = pyright_scope(root)
        for spelled in (real, link):
            assert scope.reports_on(spelled / "pkg" / "core.py")
            assert not scope.reports_on(spelled / "pkg" / "legacy" / "m.py")


@requires_mypy
def test_the_worker_names_a_file_once_whichever_way_it_is_spelled(
    linked: Tuple[Path, Path],
) -> None:
    from mypy.build import BuildSource

    from towel._mypy_worker import _one_source_per_module

    real, link = linked
    _module(real)
    spellings = [str(real / "pkg" / "m.py"), str(link / "pkg" / "m.py")]
    sources = [BuildSource(path, "pkg.m", None) for path in spellings]
    assert len(_one_source_per_module(sources)) == 1


TWINS = textwrap.dedent("""\
    def first(value: int, name: str) -> int:
        total = value + len(name)
        doubled = total * 2
        answer = doubled - 3
        return answer


    def second(value: int, name: str) -> int:
        total = value + len(name)
        doubled = total * 2
        answer = doubled - 3
        return answer
    """)


def _applied(root: Path) -> Tuple[int, Mapping[str, int]]:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0"\n\n[tool.mypy]\n', encoding="utf-8"
    )
    module = _module(root, TWINS)
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
        results, _ = engine.refactor_directory_to_fixed_point(
            str(module.parent), str(module.parent), progress="none"
        )
    finally:
        oracle.close()
    declined = engine.run_report.declined_proposals
    return len(results), {str(reason): count for reason, count in declined.items()}


@requires_mypy
def test_mypy_the_same_project_through_a_link_and_resolved_applies_the_same(
    linked: Tuple[Path, Path],
) -> None:
    real, link = linked
    (real / "a").mkdir()
    (real / "b").mkdir()
    through_the_link = _applied(link / "a")
    resolved = _applied(real / "b")
    assert os.path.realpath(link / "a") == str((real / "a").resolve())
    assert through_the_link == resolved == (1, {})

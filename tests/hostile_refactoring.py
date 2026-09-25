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

"""How the hostile batteries refactor a fixture, and which fixtures are expected to fail.

Towel runs through its library entry, ``UnificationRefactorEngine(min_lines=3)``,
in place on a copy: :func:`refactor_script` for a single-file fixture and
:func:`refactor_package` for a package. The differential harness
(:mod:`tests.differential`) checks each fixture it writes with the same two,
so a fixture it hands over fails its battery the way it failed the harness.

A fixture exposing a defect not yet fixed is a strict expected failure
(:func:`with_known_defects`), named by the id the audit that found it gave
it. When the fix lands the fixture passes, pytest reports the XPASS as a
failure, and the entry is removed.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple, TypeVar

from _pytest.mark.structures import ParameterSet
import pytest

from towel.formatting import import_sorter_for_project
from towel.type_inference import CheckerNotInstalled, TypeOracle, type_oracle_for_project
from towel.unification.refactor_engine import UnificationRefactorEngine

Fixture = TypeVar("Fixture")


def refactor_script(script: Path) -> int:
    """Refactor the single file ``script`` in place, as the file battery does; the count applied."""
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        _, applied, _ = engine.refactor_to_fixed_point(str(script))
    return applied


def refactor_package(
    package: Path, *, cross_module: bool = True, typed: bool = False
) -> Dict[str, Tuple[int, List[str]]]:
    """Refactor the directory ``package`` in place, as the package battery does; per-file results.

    ``cross_module`` is ``--cross-module``. ``typed`` runs with the checker
    the project around ``package`` configures, as ``towel dry`` does by
    default, and skips the calling test where it is not installed; without
    it no checker runs. Modified files are finished with the import sorter
    the project configures, if any, as the command line finishes them. The
    path is resolved first: the checker reports resolved paths, so under a
    symlinked temporary directory (macOS's ``/var``) a typed run would find
    none of the code reachable and decline every change.
    """
    package = package.resolve()
    oracle: Optional[TypeOracle] = None
    if typed:
        try:
            oracle = type_oracle_for_project(package).tool
        except CheckerNotInstalled as missing:
            pytest.skip(f"the checker this typed fixture configures is not installed: {missing}")
        if oracle is None:
            pytest.skip("no type checker is installed for this typed fixture")
    try:
        engine = UnificationRefactorEngine(
            min_lines=3,
            cross_module_helpers=cross_module,
            type_oracle=oracle,
            file_finisher=import_sorter_for_project(package).tool,
        )
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            results, _ = engine.refactor_directory_to_fixed_point(
                str(package), str(package), progress="none"
            )
    finally:
        if oracle is not None:
            oracle.close()
    return results


def with_known_defects(
    fixtures: Iterable[Fixture], known: Mapping[Fixture, str], name: Callable[[Fixture], str] = str
) -> List[ParameterSet]:
    """``fixtures``, then any of ``known`` not among them, each named by ``name``.

    A fixture in ``known`` is a strict expected failure whose reason is its
    entry there, ``"<audit id>: <the defect, in a line>"``, and only a failed
    assertion counts as the expected failure: an error in the harness still
    fails the run.
    """
    return [
        pytest.param(
            fixture,
            id=name(fixture),
            marks=(
                [pytest.mark.xfail(strict=True, raises=AssertionError, reason=known[fixture])]
                if fixture in known
                else []
            ),
        )
        for fixture in dict.fromkeys([*fixtures, *known])
    ]

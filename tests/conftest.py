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

import os
from pathlib import Path
import tempfile
from typing import Any, Iterator, Optional

import pytest

from tests.ecosystem_fixtures import offline_index_at
import towel.cli
import towel.import_model
from towel.import_model import OutsideProvider
from towel.unification.annotation_ladder import Verified
from towel.unification.annotation_wiring import HelperAnnotationWiring
from towel.unification.exceptions import UncheckedCodeError
from towel.unification.materialize import Materialization
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.source_files import is_environment

LOOKS_NOWHERE = "looks_nowhere"
"""The marker of a test whose typed runs are meant to verify nothing, the checker looking nowhere."""


STRICT_MODE = "strict_mode"
"""The marker of a test that pins exact object lifetimes, which only strict mode keeps."""

RELAXED_MODE = "relaxed_mode"
"""The marker of a module that pins the default itself, which ``--towel-strict`` leaves alone."""

ALL_STRICT = "--towel-strict"
"""The option that runs every test's engines strict, as every release through 1.949 did."""


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        ALL_STRICT,
        action="store_true",
        default=False,
        help="Run every module as if marked strict_mode: engines and in-process command lines "
        "that do not choose a mode are strict. Modules marked relaxed_mode, which pin the "
        "default itself, are left alone.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{LOOKS_NOWHERE}: every proposal of the test's typed runs is meant to be declined as "
        "code the type checker does not look at (see _typed_runs_are_not_vacuous)",
    )
    config.addinivalue_line(
        "markers",
        f"{STRICT_MODE}: the test pins exact object lifetimes, so the engines and in-process "
        "command lines it runs are strict unless they choose a mode "
        "(see _strict_where_the_module_is_marked)",
    )
    config.addinivalue_line(
        "markers",
        f"{RELAXED_MODE}: the module pins relaxed mode as the default, so --towel-strict "
        "does not change its defaults",
    )


def _strict_by_default(patch: pytest.MonkeyPatch) -> None:
    """Engines and in-process command lines that do not choose a mode are strict."""
    constructed = UnificationRefactorEngine.__init__
    if getattr(constructed, "strict_unless_chosen", False):
        return

    def strict_unless_chosen(self: UnificationRefactorEngine, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("strict", True)
        constructed(self, *args, **kwargs)

    setattr(strict_unless_chosen, "strict_unless_chosen", True)
    patch.setattr(UnificationRefactorEngine, "__init__", strict_unless_chosen)
    patch.setattr(towel.cli, "DEFAULT_STRICT", True)


@pytest.fixture(autouse=True, scope="module")
def _strict_where_the_module_is_marked(request: pytest.FixtureRequest) -> Iterator[None]:
    """Make strict mode the default of a module that pins what only strict mode preserves.

    Relaxed mode is the default (docs/DECISIONS.md, "Relaxed mode is the
    default; strict mode keeps exact lifetimes"), and strict mode is 1.949's
    behaviour exactly. The tests written before the switch pin that behaviour
    with the defaults of their day, so a module whose expectations depend on
    exact lifetimes says ``pytestmark = pytest.mark.strict_mode`` and keeps
    its expectations unchanged: an engine constructed without ``strict``, and
    a command line parsed in this process without ``--strict`` or
    ``--no-strict``, is then strict, in the module's tests and in its
    class- and module-level setup alike. A command run in a child process is
    out of this fixture's reach and passes ``--strict`` itself.
    ``pytest --towel-strict`` treats every module as marked, except one
    marked ``relaxed_mode`` because the default is what it pins.
    """
    everywhere = (
        request.config.getoption(ALL_STRICT)
        and request.node.get_closest_marker(RELAXED_MODE) is None
    )
    if request.node.get_closest_marker(STRICT_MODE) is None and not everywhere:
        yield
        return
    with pytest.MonkeyPatch.context() as patch:
        _strict_by_default(patch)
        yield


@pytest.fixture(autouse=True)
def _strict_where_the_test_is_marked(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same for a single test or class marked ``strict_mode`` in an unmarked module."""
    if request.node.get_closest_marker(STRICT_MODE) is not None:
        _strict_by_default(monkeypatch)


_TEST_ONLY_PLACES = (Path(__file__).resolve().parents[1], Path(tempfile.gettempdir()).resolve())
"""The Towel checkout under test, which pytest puts on ``sys.path`` to import ``tests``, and
the temporary directory the fixtures of earlier tests were imported from."""


def _only_this_process_finds(description: str) -> bool:
    """Whether what the probe found lies where only this test process looks.

    That is the checkout or the temporary directory, but not an environment
    inside either: ``uv sync`` makes the checkout's own ``.venv``, as CI does,
    and what is installed there is installed for every interpreter using it.
    """
    location = Path(description)
    for place in _TEST_ONLY_PLACES:
        if location.is_relative_to(place):
            between = [parent for parent in location.parents if parent.is_relative_to(place)]
            return not any(is_environment(directory) for directory in between)
    return False


@pytest.fixture(autouse=True)
def _no_outer_coverage_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the configuration of a ``coverage run`` measuring the suite out of every project it builds.

    ``coverage run`` exports ``COVERAGE_RCFILE`` naming Towel's own ``pyproject.toml``,
    and Towel reads coverage.py's configuration as coverage.py does, that variable
    first, so every test project took Towel's exclusions under CI's coverage run
    and never without it. A test about the variable sets it itself.
    """
    monkeypatch.delenv("COVERAGE_RCFILE", raising=False)


@pytest.fixture(autouse=True)
def _probe_as_a_projects_interpreter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Let the import model's interpreter probe see what a project's own interpreter would.

    The probe asks this interpreter what it would import for each top-level
    name outside the project. In this process that includes what no
    project's interpreter has: this checkout, which pytest puts on the path,
    so every fixture's ``tests`` package would look shadowed by Towel's own;
    and the modules earlier tests imported from their own fixtures and left
    in ``sys.modules``, a fixture's ``a.py`` among them. Both are set aside;
    everything else this interpreter can import, installed packages
    included, still counts.
    """
    real = towel.import_model.installed_outside

    def probe(name: str, root: Path) -> Optional[OutsideProvider]:
        found = real(name, root)
        if found is not None and _only_this_process_finds(found.description):
            return None
        return found

    monkeypatch.setattr(towel.import_model, "installed_outside", probe)
    yield


@pytest.fixture(autouse=True)
def _typed_runs_are_not_vacuous(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Fail a test whose typed runs declined proposals as code the checker does not look at, and verified none.

    A checker says nothing about code it does not look at, and a typed run
    declines every change there. So a test whose checker answered nowhere
    passes whatever it asserts about declines, and asserts nothing about
    verification: a project path through a symbolic link (every temporary
    directory on macOS) had mypy's every answer dropped, every proposal
    declined as unlooked, and every such test vacuous. A test that is about
    that decline says so with ``@pytest.mark.looks_nowhere``.
    """
    tally = {"unlooked": 0, "verified": 0}

    def counting(name: str) -> None:
        original = getattr(HelperAnnotationWiring, name)

        def wrapper(self: HelperAnnotationWiring, *args: object, **kwargs: object) -> object:
            try:
                return original(self, *args, **kwargs)
            except UncheckedCodeError:
                tally["unlooked"] += 1
                raise

        monkeypatch.setattr(HelperAnnotationWiring, name, wrapper)

    counting("_decline_what_the_checker_does_not_look_at")
    counting("_refuse_what_the_checker_does_not_look_at")
    attempt = Materialization._attempt

    def attempted(self: Materialization, *args: object, **kwargs: object) -> object:
        outcome = attempt(self, *args, **kwargs)  # type: ignore[arg-type]
        if isinstance(outcome, Verified) and self._type_run_oracle is not None:
            tally["verified"] += 1
        return outcome

    monkeypatch.setattr(Materialization, "_attempt", attempted)
    yield
    if (
        tally["unlooked"]
        and not tally["verified"]
        and request.node.get_closest_marker(LOOKS_NOWHERE) is None
    ):
        pytest.fail(
            f"every typed change of this test was declined as code the type checker does not "
            f"look at ({tally['unlooked']} declined, none verified), so it verified nothing; "
            f"mark it @pytest.mark.{LOOKS_NOWHERE} if that decline is what it tests",
            pytrace=False,
        )


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> Optional[bool]:
    """Prevent pytest from treating example and temporary directories as tests.

    We exclude:
    - test_examples/ and its variants (expected_output, skip, etc.)
    - tmp/ like mytmp*, tmp_out*, and output staging dirs created by scripts
    - the hostile batteries' fixtures, which are programs to refactor: a
      fixture project may hold its own ``tests/test_*.py``, there for its
      import graph

    Rationale: these directories contain demonstration inputs and generated
    artifacts whose basenames collide (e.g., edge_cases_stress_test.py) causing
    import file mismatch errors during collection.
    """
    # Normalize path string
    p = str(collection_path)
    # Quick substring checks to avoid expensive operations
    basename = os.path.basename(p)
    if "test_examples" in p:
        return True
    if collection_path.parent.name == "tests" and basename in (
        "hostile_cases",
        "hostile_crossfile",
    ):
        return True
    if basename.startswith("mytmp") or basename.startswith("tmp_out"):
        return True
    if "tmp_out" in p:
        return True
    # This is a first-result hook. Defer ordinary paths to pytest so explicit
    # --ignore/--ignore-glob selections and its normal exclusions still apply.
    return None


@pytest.fixture
def offline_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """uv with no index, every requirement from fixture wheels (see ``ecosystem_fixtures``)."""
    return offline_index_at(tmp_path, monkeypatch)

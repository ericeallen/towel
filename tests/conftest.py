import os
from pathlib import Path
import tempfile
from typing import Iterator, Optional

import pytest

from tests.ecosystem_fixtures import offline_index_at
import towel.import_model
from towel.import_model import OutsideProvider
from towel.unification.annotation_ladder import Verified
from towel.unification.annotation_wiring import HelperAnnotationWiring
from towel.unification.exceptions import UncheckedCodeError
from towel.unification.materialize import Materialization

LOOKS_NOWHERE = "looks_nowhere"
"""The marker of a test whose typed runs are meant to verify nothing, the checker looking nowhere."""


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{LOOKS_NOWHERE}: every proposal of the test's typed runs is meant to be declined as "
        "code the type checker does not look at (see _typed_runs_are_not_vacuous)",
    )


_TEST_ONLY_PLACES = tuple(
    f"{place}{os.sep}"
    for place in (Path(__file__).resolve().parents[1], Path(tempfile.gettempdir()).resolve())
)
"""The Towel checkout under test, which pytest puts on ``sys.path`` to import ``tests``, and
the temporary directory the fixtures of earlier tests were imported from."""


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
        if found is not None and found.description.startswith(_TEST_ONLY_PLACES):
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


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool:
    """Prevent pytest from treating example and temporary directories as tests.

    We exclude:
    - test_examples/ and its variants (expected_output, skip, etc.)
    - tmp/ like mytmp*, tmp_out*, and output staging dirs created by scripts

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
    if basename.startswith("mytmp") or basename.startswith("tmp_out"):
        return True
    if "tmp_out" in p:
        return True
    return False


@pytest.fixture
def offline_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """uv with no index, every requirement from fixture wheels (see ``ecosystem_fixtures``)."""
    return offline_index_at(tmp_path, monkeypatch)

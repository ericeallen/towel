import os
from pathlib import Path
import tempfile
from typing import Iterator, Optional

import pytest

import towel.import_model
from towel.import_model import OutsideProvider

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

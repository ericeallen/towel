import os
from pathlib import Path

import pytest

from tests.ecosystem_fixtures import offline_index_at


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

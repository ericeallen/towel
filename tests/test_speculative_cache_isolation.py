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

"""A rejected candidate must not be believed by the checks that follow it.

Nearly every candidate is rejected, and nothing it proposed reaches disk. Its
text was still checked, so mypy wrote a cache entry for each module from that
text, stamped with the *file's* mtime and size. mypy trusts a matching mtime
and size without hashing, so those entries answer for the files afterwards.

The next check is sparse -- it names the few modules it is about -- and every
other module is read from a cache that remembers a project that was never
written. A provider left speculatively returning ``str`` went on answering
``str`` while its file returned ``int``, and a candidate that would break the
project was accepted against one that did not exist.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Mapping

import pytest

from towel.type_inference import CheckFailure, CheckSuccess, MypyInferrer

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

PROVIDER_ON_DISK = "def f() -> int:\n    return 1\n"
CONSUMER_ON_DISK = "from .lib import f\n\nx: int = f()\n"


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "pkg/__init__.py": "",
            "pkg/lib.py": PROVIDER_ON_DISK,
            "pkg/consumer.py": CONSUMER_ON_DISK,
        },
    )
    return tmp_path


@requires_mypy
def test_a_rejected_candidates_provider_does_not_answer_the_next_check(project: Path) -> None:
    """The later check names only the consumer; the provider must come from disk."""
    lib, consumer = str(project / "pkg" / "lib.py"), str(project / "pkg" / "consumer.py")
    oracle = MypyInferrer()
    try:
        clean = oracle.check_project({lib: PROVIDER_ON_DISK, consumer: CONSUMER_ON_DISK})
        assert isinstance(clean, CheckSuccess) and clean.errors == (), clean

        # A candidate that changes the provider's return type and is rejected
        # for an unrelated error elsewhere in the same file. Nothing is written.
        rejected = oracle.check_project(
            {
                lib: "def f() -> str:\n    return 'x'\n",
                consumer: "from .lib import f\n\nx: str = f()\nbad: int = 'no'\n",
            }
        )
        assert isinstance(rejected, CheckSuccess) and rejected.errors, rejected

        # The next candidate is about the consumer alone. Against the provider
        # that is actually on disk, assigning its ``int`` to ``x: str`` is an
        # error; against the rejected candidate's provider it is not.
        sparse = oracle.check_project({consumer: "from .lib import f\n\nx: str = f()\n"})
    finally:
        oracle.close()
    assert isinstance(sparse, CheckSuccess), sparse
    assert sparse.errors, "the rejected candidate's provider was still answering for its file"
    assert all(error.path == consumer for error in sparse.errors), sparse


@requires_mypy
def test_a_warm_oracle_agrees_with_one_that_never_saw_the_candidate(project: Path) -> None:
    """The only check that settles it: the same question, asked of a fresh checker."""
    lib, consumer = str(project / "pkg" / "lib.py"), str(project / "pkg" / "consumer.py")
    candidate = {consumer: "from .lib import f\n\nx: str = f()\n"}
    warm = MypyInferrer()
    try:
        warm.check_project({lib: PROVIDER_ON_DISK, consumer: CONSUMER_ON_DISK})
        warm.check_project(
            {
                lib: "def f() -> str:\n    return 'x'\n",
                consumer: "from .lib import f\n\nx: str = f()\nbad: int = 'no'\n",
            }
        )
        warm_result = warm.check_project(candidate)
    finally:
        warm.close()
    cold = MypyInferrer()
    try:
        cold_result = cold.check_project(candidate)
    finally:
        cold.close()
    assert isinstance(warm_result, CheckSuccess) and isinstance(cold_result, CheckSuccess)
    assert {(e.path, e.message) for e in warm_result.errors} == {
        (e.path, e.message) for e in cold_result.errors
    }, (warm_result, cold_result)


@requires_mypy
def test_a_file_deleted_after_being_overlaid_is_reported_not_invented(project: Path) -> None:
    """Restoring it from disk is impossible; the checker says so rather than guessing."""
    lib, consumer = str(project / "pkg" / "lib.py"), str(project / "pkg" / "consumer.py")
    oracle = MypyInferrer()
    try:
        oracle.check_project({lib: "def f() -> str:\n    return 'x'\n", consumer: CONSUMER_ON_DISK})
        Path(lib).unlink()
        result = oracle.check_project({consumer: CONSUMER_ON_DISK})
    finally:
        oracle.close()
    assert isinstance(result, (CheckSuccess, CheckFailure)), result
    if isinstance(result, CheckSuccess):
        assert result.errors, "a module that no longer exists was answered from the cache"

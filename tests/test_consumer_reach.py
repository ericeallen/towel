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

"""Every consumer a change reaches is checked, whatever came before the check.

Each case is a clean project and a prospective provider that breaks one
unchanged consumer. mypy run fresh over the project reports that consumer, so
the complete check must too; one that does not has called a broken project
clean. The consumer is found by the scan in ``towel.consumers``, and each case
here is a way that scan once failed to find it.
"""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import textwrap
from typing import Dict, List, Mapping

import pytest

from towel import consumers
from towel.type_inference import CheckSuccess, MypyInferrer

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

STRICT = "[tool.mypy]\nstrict = true\n"
RETURNS_INT = "def f() -> int:\n    return 1\n"
RETURNS_STR = "def f() -> str:\n    return 's'\n"


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def _flagged(oracle: MypyInferrer, root: Path, replacements: Mapping[str, str]) -> List[str]:
    """The files the complete check reports errors in, relative to ``root``."""
    result = oracle.check_project({str(root / name): text for name, text in replacements.items()})
    assert isinstance(result, CheckSuccess), result
    return sorted({os.path.relpath(error.path, root) for error in result.errors})


def _on_disk(root: Path, *names: str) -> Dict[str, str]:
    return {name: (root / name).read_text(encoding="utf-8") for name in names}


def _sparse_check_after_baseline(
    root: Path, baseline: Mapping[str, str], provider: str, text: str
) -> List[str]:
    oracle = MypyInferrer()
    try:
        assert _flagged(oracle, root, baseline) == []
        return _flagged(oracle, root, {provider: text})
    finally:
        oracle.close()


def test_a_consumer_in_a_package_the_baseline_named_is_checked_later(tmp_path: Path) -> None:
    """``towel dry . .`` names every analysed package in its first, complete check.

    Those packages were walked by that check, so the scan left them out, and
    the answer was kept for every later request. A sparse candidate naming only
    ``pkg/lib.py`` then reused it, and ``app/c.py`` -- analysed, consuming the
    change, but not walked by a request that names only ``pkg`` -- was checked
    by nothing.
    """
    _write(
        tmp_path,
        {
            "pyproject.toml": STRICT,
            "pkg/__init__.py": "",
            "pkg/lib.py": RETURNS_INT,
            "app/__init__.py": "",
            "app/c.py": "from pkg.lib import f\nx: int = f()\n",
        },
    )
    flagged = _sparse_check_after_baseline(
        tmp_path, _on_disk(tmp_path, "pkg/lib.py", "app/c.py"), "pkg/lib.py", RETURNS_STR
    )
    assert flagged == ["app/c.py"]


@pytest.mark.parametrize("named_in_baseline", [True, False])
def test_a_consumer_the_run_itself_created_is_checked(
    tmp_path: Path, named_in_baseline: bool
) -> None:
    """An applied refactoring can add an import, and the scan must see it.

    The file did not import the package when the scan was made, and an
    in-place run writes each refactoring to disk before the next candidate is
    judged. A scan kept for the whole run never learned that the file had
    become a consumer.
    """
    _write(
        tmp_path,
        {
            "pyproject.toml": STRICT,
            "pkg/__init__.py": "",
            "pkg/lib.py": RETURNS_INT,
            "other.py": "def g() -> int:\n    return 2\n",
        },
    )
    baseline = _on_disk(tmp_path, "pkg/lib.py", *(["other.py"] if named_in_baseline else []))
    oracle = MypyInferrer()
    try:
        assert _flagged(oracle, tmp_path, baseline) == []
        (tmp_path / "other.py").write_text(
            "from pkg.lib import f\n\ndef g() -> int:\n    return 2\n\nx: int = f()\n",
            encoding="utf-8",
        )
        assert _flagged(oracle, tmp_path, {"pkg/lib.py": RETURNS_STR}) == ["other.py"]
    finally:
        oracle.close()


def test_following_the_project_reads_only_the_files_that_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check is made hundreds of times a run; the scan must not parse the tree each time."""
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/lib.py": RETURNS_INT,
            "a.py": "import pkg.lib\n",
            "b.py": "X = 1\n",
            "c.py": "Y = 2\n",
        },
    )
    parsed: List[str] = []
    original = ast.parse

    def counting(source: bytes, filename: str = "<unknown>", *rest: object, **kw: object) -> object:
        parsed.append(os.path.relpath(filename, tmp_path))
        return original(source, filename, *rest, **kw)  # type: ignore[call-overload]

    monkeypatch.setattr(ast, "parse", counting)
    scan = consumers.ImportScan(tmp_path, lambda path: path.stem)
    assert scan.consumers({"pkg"}) == [str((tmp_path / "a.py").resolve())]
    assert len(parsed) == 5
    parsed.clear()
    assert scan.consumers({"pkg"}) == [str((tmp_path / "a.py").resolve())]
    assert parsed == [], "an unchanged tree was parsed again"
    (tmp_path / "b.py").write_text("import pkg\nX = 1\n", encoding="utf-8")
    assert scan.consumers({"pkg"}) == sorted(
        str((tmp_path / name).resolve()) for name in ("a.py", "b.py")
    )
    assert parsed == ["b.py"]


@pytest.mark.parametrize(
    "files, consumer",
    [
        ({"top.py": "from app import helpers\nx: int = helpers.g()\n"}, "top.py"),
        ({"app/user.py": "from . import helpers\nx: int = helpers.g()\n"}, "app/user.py"),
        (
            {"app/sub/__init__.py": "from .. import helpers\nx: int = helpers.g()\n"},
            "app/sub/__init__.py",
        ),
    ],
    ids=["absolute", "relative", "relative-from-a-subpackage"],
)
def test_a_consumer_reached_through_a_submodule_imported_from_its_package_is_checked(
    tmp_path: Path, files: Mapping[str, str], consumer: str
) -> None:
    """``from app import helpers`` imports the module ``app.helpers``.

    Only ``app`` was recorded, so a file reaching the change through
    ``helpers`` was never found: ``app/__init__`` does not consume the change.
    """
    _write(
        tmp_path,
        {
            "pyproject.toml": STRICT,
            "pkg/__init__.py": "",
            "pkg/lib.py": RETURNS_INT,
            "app/__init__.py": "",
            "app/helpers.py": "from pkg.lib import f\ng = f\n",
            **files,
        },
    )
    oracle = MypyInferrer()
    try:
        assert _flagged(oracle, tmp_path, {"pkg/lib.py": RETURNS_STR}) == [consumer]
    finally:
        oracle.close()

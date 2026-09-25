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

"""A consumer that reaches its package through an editable install is judged against the copy.

An editable install leaves a ``.pth`` naming the directory its package lives in, and
pyright resolves an import of the package there wherever its own search roots -- the
project root, ``src`` by ``autoSearchPaths``, the configuration's ``extraPaths`` -- do
not reach the package first: in the user's tree, not the private copy Towel checks.
``autoSearchPaths`` covers a ``src`` layout. A package under ``python/`` or ``lib/``,
or a ``src`` that the configuration's ``extraPaths`` replace, was found only through
the install, and a candidate that broke a consumer of it read as clean to the server
and the command line alike; the project's own pyright, run once it was applied, failed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from typing import List, Mapping, Tuple

import pytest

from towel.type_inference import CheckSuccess, PyrightOracle, _copied_search_paths

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

CONSUMER = (
    "from typing import assert_type\n\nimport pkg\nfrom untyped_lib import number\n\n"
    'assert_type(pkg.edit("x"), str | None)\nvalue: int = number()\n'
)
"""Outside the package, and reaching an untyped library the environment holds besides."""

LAYOUTS = {
    "python-directory": ("python", {"pyproject.toml": "[tool.pyright]\n"}),
    "src-beside-configured-extra-paths": (
        "src",
        {"pyrightconfig.json": '{"extraPaths": ["lib"]}', "lib/helper.py": "HELP = 1\n"},
    ),
    "monorepo-member": ("packages/pkg/src", {"pyrightconfig.json": "{}"}),
}


def _project(root: Path, source_root: str, configuration: Mapping[str, str]) -> Path:
    files = {
        f"{source_root}/pkg/__init__.py": "from ._termui import edit as edit\n",
        f"{source_root}/pkg/_termui.py": (
            "def edit(text: str | None = None) -> str | None:\n    return text\n"
        ),
        "tests/typing/typing_edit.py": CONSUMER,
        **configuration,
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root / source_root / "pkg/__init__.py"


def _environment(location: Path, installed: Path) -> Path:
    """An environment at ``location`` with ``installed`` on its path, as ``pip install -e``."""
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(location)],
        check=True,
        capture_output=True,
    )
    (site,) = (location / "lib").glob("python*/site-packages")
    (site / "untyped_lib").mkdir()
    (site / "untyped_lib/__init__.py").write_text("def number():\n    return 1\n")
    (site / "_pkg_editable.pth").write_text(f"{installed}\n", encoding="utf-8")
    return location / "bin" / "python"


def _consumer_errors(
    init: Path, interpreter: Path, *, language_server: bool
) -> Tuple[List[str], List[str]]:
    """What the project reports as it stands, and in the consumer once ``edit`` is not exported."""
    text = init.read_text(encoding="utf-8")
    candidate = "".join(line for line in text.splitlines(True) if "import edit" not in line)
    oracle = PyrightOracle(language_server=language_server)
    oracle._interpreter = str(interpreter)
    try:
        baseline = oracle.check_project({str(init): text})
        after = oracle.check_project({str(init): candidate})
    finally:
        oracle.close()
    assert isinstance(baseline, CheckSuccess) and isinstance(after, CheckSuccess)
    return [error.message for error in baseline.errors], [
        error.message for error in after.errors if error.path.endswith("typing_edit.py")
    ]


@pytest.mark.parametrize("language_server", [True, False], ids=["session", "command-line"])
@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_a_consumer_of_an_editable_install_is_judged_against_the_candidate(
    tmp_path: Path, layout: str, language_server: bool
) -> None:
    source_root, configuration = LAYOUTS[layout]
    project = tmp_path / "project"
    init = _project(project, source_root, configuration)
    interpreter = _environment(tmp_path / "environment", project / source_root)
    baseline, consumer = _consumer_errors(init, interpreter, language_server=language_server)
    assert baseline == [], "the project as it stands checks clean"
    assert any('"edit" is not a known attribute of module "pkg"' in m for m in consumer), consumer


@pytest.mark.parametrize("language_server", [True, False], ids=["session", "command-line"])
def test_an_environment_kept_inside_the_project_is_used_where_it_is(
    tmp_path: Path, language_server: bool
) -> None:
    """A ``.venv`` in the project is not copied, so its site directory is not moved into the copy.

    The untyped library it holds still resolves, and the editable package is the copy's.
    """
    project = tmp_path / "project"
    init = _project(project, "python", {"pyproject.toml": '[tool.pyright]\nexclude = [".venv"]\n'})
    interpreter = _environment(project / ".venv", project / "python")
    baseline, consumer = _consumer_errors(init, interpreter, language_server=language_server)
    assert baseline == [], "the environment's library and the package both resolve"
    assert any('"edit" is not a known attribute of module "pkg"' in m for m in consumer), consumer


def test_only_directories_the_copy_holds_are_moved_into_it(tmp_path: Path) -> None:
    root, copy = tmp_path / "project", tmp_path / "copy"
    for directory in ("project/python", "project/src", "copy/python", "copy/src", "elsewhere"):
        (tmp_path / directory).mkdir(parents=True)
    search_path = [
        "",
        "relative/path",
        str(tmp_path / "elsewhere"),
        str(root),
        str(root / "python"),
        str(root / ".venv/lib/python3/site-packages"),
        str(root / "src"),
        str(root / "python"),
    ]
    assert _copied_search_paths(search_path, root, copy) == [
        str(copy / "python"),
        str(copy / "src"),
    ]

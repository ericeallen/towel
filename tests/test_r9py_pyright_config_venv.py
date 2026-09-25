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

"""pyright in Towel's private copy resolves imports in the environment the project names.

The copy leaves environments out, so a configuration's ``venvPath`` and ``venv``
named nothing there and pyright fell back, silently, on the interpreter it is
given, Towel's. With another version of a library there, a helper was
annotated with that version's types and the project's own pyright rejected it;
with none, every file importing the library was declined with the remedy to
install it where Towel runs. Now the copy's configuration names a mirror of
the environment, whose ``.pth`` lead into the copy where an editable install
leads into the project, and an environment pyright could not use refuses the
check instead. A search path into an environment, in ``extraPaths``, now names
the original rather than nothing.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import List, Mapping

import pytest

from towel.checker_project import CheckerSnapshot, UnusableConfiguration
from towel.pyright_session import pyright_scope
from towel.type_inference import CheckSuccess, PyrightOracle

requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

_R9PY_SITE = "myenv/lib/python3.12/site-packages"


def _r9py_project(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root.resolve()


def _with_environment(root: Path, configuration: Mapping[str, str], **extra: str) -> Path:
    """A project whose environment ``myenv`` holds ``typedlib`` 2.0 (``make() -> str``)."""
    return _r9py_project(
        root,
        {
            **configuration,
            "myenv/pyvenv.cfg": "home = /usr/bin\n",
            f"{_R9PY_SITE}/typedlib/__init__.py": "def make(n: int) -> str:\n    return str(n)\n",
            f"{_R9PY_SITE}/typedlib/py.typed": "",
            "pkg/__init__.py": "",
            "pkg/core.py": "from typedlib import make\n\nVALUE: int = make(1)\n",
            **extra,
        },
    )


def test_r9py_an_environment_pyright_cannot_use_refuses_the_check(tmp_path: Path) -> None:
    missing = _r9py_project(
        tmp_path / "r9py_missing", {"pyrightconfig.json": '{"venvPath": ".", "venv": "gone"}'}
    )
    with pytest.raises(UnusableConfiguration, match=r"gone .*does not exist.*Towel's"):
        CheckerSnapshot(missing)
    empty = _r9py_project(
        tmp_path / "r9py_empty",
        {"pyproject.toml": '[tool.pyright]\nvenvPath = "envs"\nvenv = "e"\n', "envs/e/bin/x": ""},
    )
    with pytest.raises(UnusableConfiguration, match="holds no site-packages"):
        CheckerSnapshot(empty)


def test_r9py_the_copy_names_a_mirror_of_the_environment(tmp_path: Path) -> None:
    elsewhere = tmp_path / "r9py_elsewhere"
    elsewhere.mkdir()
    project = _with_environment(
        tmp_path / "r9py_project",
        {"pyproject.toml": '[tool.pyright]\nvenvPath = "."\nvenv = "myenv"\nstrict = ["pkg"]\n'},
        **{"python/editable/__init__.py": ""},
    )
    site = project / _R9PY_SITE
    (site / "_editable.pth").write_text(
        f"{project / 'python'}\n# a comment\nimport os\n{elsewhere}\n", encoding="utf-8"
    )
    snapshot = CheckerSnapshot(project)
    try:
        written = json.loads((snapshot.tree / "pyrightconfig.json").read_text(encoding="utf-8"))
        mirror = Path(written["venvPath"]) / written["venv"]
        assert written["strict"] == ["pkg"], "the rest of [tool.pyright], as it was"
        assert not mirror.is_relative_to(snapshot.tree), "outside what pyright enumerates"
        lines = (mirror / "lib/python3.12/site-packages/_towel_environment.pth").read_text()
        assert lines.splitlines() == [str(site), str(snapshot.tree / "python"), str(elsewhere)]
    finally:
        snapshot.close()


def test_r9py_a_search_path_into_an_environment_names_the_original(tmp_path: Path) -> None:
    project = _with_environment(
        tmp_path / "r9py_project",
        {"pyrightconfig.json": json.dumps({"include": ["pkg"], "extraPaths": [_R9PY_SITE, "lib"]})},
        **{"lib/helper.py": "HELP = 1\n"},
    )
    snapshot = CheckerSnapshot(project)
    try:
        written = json.loads((snapshot.tree / "pyrightconfig.json").read_text(encoding="utf-8"))
        assert written == {"include": ["pkg"], "extraPaths": [str(project / _R9PY_SITE), "lib"]}
        scope = pyright_scope(snapshot.tree)
        assert scope.reports_on(snapshot.tree / "pkg/core.py"), "the copy's scope still reads"
        assert not scope.reports_on(snapshot.tree / "lib/helper.py")
    finally:
        snapshot.close()


def _messages(oracle: PyrightOracle, sources: Mapping[str, str]) -> List[str]:
    result = oracle.check_project(sources)
    assert isinstance(result, CheckSuccess), result
    return [error.message for error in result.errors]


@requires_pyright
@pytest.mark.parametrize("language_server", [True, False], ids=["session", "command-line"])
def test_r9py_pyright_resolves_in_the_configured_environment(
    tmp_path: Path, language_server: bool
) -> None:
    project = _with_environment(
        tmp_path / "r9py_project",
        {"pyrightconfig.json": '{"venvPath": ".", "venv": "myenv"}'},
        **{
            "python/editable/__init__.py": "def edit() -> int:\n    return 1\n",
            "tests/use.py": "from editable import edit\n\nTOTAL: int = edit() + 1\n",
        },
    )
    (project / _R9PY_SITE / "_editable.pth").write_text(f"{project / 'python'}\n")
    core, editable = project / "pkg/core.py", project / "python/editable/__init__.py"
    oracle = PyrightOracle(language_server=language_server)
    try:
        library = _messages(oracle, {str(core): core.read_text(encoding="utf-8")})
        broken = _messages(oracle, {str(editable): "def edit() -> str:\n    return ''\n"})
    finally:
        oracle.close()
    assert len(library) == 1 and '"str" is not assignable to "int"' in library[0], library
    assert any('Operator "+" not supported' in message for message in broken), broken

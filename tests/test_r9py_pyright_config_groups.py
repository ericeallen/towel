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

"""A change is checked from every pyright root that holds it, each showing all of it.

pyright run at a project's root checks everything beneath it, a member with a
``pyrightconfig.json`` of its own included; the member's configuration applies
only when pyright runs there. Towel checked each changed file from its nearest
root alone, and the outer root's copy held the member's files as they were on
disk. So a consumer in the outer project was judged against the member's
original text: a helper that widened what the member's unannotated
``keep_int`` returns broke ``keep_int(1, 2) + 1`` there, and Towel wrote it.
With one configuration, the control, the same break is caught.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable, Dict, List, Mapping

import pytest

from towel.type_inference import CheckSuccess, PyrightOracle, _pyright_groups

requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

_R9PY_MEMBER = "def keep_int(x: int, n: int):\n    print(n)\n    return x\n"
_R9PY_WIDENED = (
    "def _helper(n: int, x: 'int | str') -> 'int | str':\n    print(n)\n    return x\n\n\n"
    "def keep_int(x: int, n: int):\n    return _helper(n, x)\n"
)
_R9PY_BROKEN = 'Operator "+" not supported'


def _r9py_layout(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root.resolve()


def _configured(root: Path, *, nested: bool) -> Path:
    """The audit's layout: the root's configuration reaches ``sub`` through ``extraPaths``."""
    return _r9py_layout(
        root,
        {
            "pyrightconfig.json": '{"extraPaths": ["sub"]}',
            **({"sub/pyrightconfig.json": "{}"} if nested else {}),
            "app/__init__.py": "",
            "app/use.py": "from subpkg.mod import keep_int\n\n\ndef total() -> int:\n"
            "    return keep_int(1, 2) + 1\n",
            "sub/subpkg/__init__.py": "",
            "sub/subpkg/mod.py": _R9PY_MEMBER,
        },
    )


def _unconfigured(root: Path) -> Path:
    """No checker configured anywhere; ``sub`` a packaging member inside one repository."""
    project = _r9py_layout(
        root,
        {
            "pyproject.toml": "[project]\nname = 'root'\nversion = '0'\n",
            "sub/pyproject.toml": "[project]\nname = 'subpkg'\nversion = '0'\n",
            "app/__init__.py": "",
            "app/use.py": "from sub.subpkg.mod import keep_int\n\n\ndef total() -> int:\n"
            "    return keep_int(1, 2) + 1\n",
            "sub/subpkg/__init__.py": "",
            "sub/subpkg/mod.py": _R9PY_MEMBER,
        },
    )
    (project / ".git").mkdir()
    return project


def _broken_consumers(oracle: PyrightOracle, sources: Mapping[str, str]) -> List[str]:
    result = oracle.check_project(sources)
    assert isinstance(result, CheckSuccess), result
    return sorted(Path(error.path).name for error in result.errors if _R9PY_BROKEN in error.message)


def _candidates(project: Path) -> Dict[str, Dict[str, str]]:
    """The candidate as a run over ``sub`` gives it, and as a run over the whole project does."""
    member, consumer = project / "sub/subpkg/mod.py", project / "app/use.py"
    return {
        "member-only": {str(member): _R9PY_WIDENED},
        "whole-project": {
            str(member): _R9PY_WIDENED,
            str(consumer): consumer.read_text(encoding="utf-8"),
        },
    }


def test_r9py_a_member_change_is_grouped_with_every_root_that_holds_it(tmp_path: Path) -> None:
    project = _configured(tmp_path / "r9py_nested", nested=True)
    member = str(project / "sub/subpkg/mod.py")
    groups = _pyright_groups({member: _R9PY_WIDENED})
    assert list(groups) == [project / "sub", project]
    assert all(group == {member: _R9PY_WIDENED} for group in groups.values())
    unconfigured = _unconfigured(tmp_path / "r9py_unconfigured")
    member = str(unconfigured / "sub/subpkg/mod.py")
    assert list(_pyright_groups({member: ""})) == [unconfigured / "sub", unconfigured]


_R9PY_LAYOUTS: Dict[str, Callable[[Path], Path]] = {
    "one-group": lambda root: _configured(root, nested=False),
    "two-groups": lambda root: _configured(root, nested=True),
    "unconfigured": _unconfigured,
}


@requires_pyright
@pytest.mark.parametrize(
    "layout, language_server",
    [
        ("one-group", False),
        ("two-groups", False),
        ("unconfigured", False),
        ("two-groups", True),
    ],
    ids=["one-group", "two-groups", "unconfigured", "two-groups-session"],
)
def test_r9py_the_consumer_in_the_other_group_sees_the_change(
    tmp_path: Path, layout: str, language_server: bool
) -> None:
    project = _R9PY_LAYOUTS[layout](tmp_path / "r9py_project")
    oracle = PyrightOracle(language_server=language_server)
    try:
        found = {
            shape: _broken_consumers(oracle, candidate)
            for shape, candidate in _candidates(project).items()
        }
    finally:
        oracle.close()
    assert found == {"member-only": ["use.py"], "whole-project": ["use.py"]}, found

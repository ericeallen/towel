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

"""A host that some platform cannot import never hosts for a borrower that platform imports.

Round 4 found shop's ``_winconsole.py``, which imports ``msvcrt`` and which
``shop/__init__.py`` imports only under ``sys.platform == "win32"``, hosting
a helper for ``shop.cli``: ``import shop.cli`` then raised
``ModuleNotFoundError`` on macOS, and so did ``ctypes.windll``,
``signal.CTRL_C_EVENT`` and ``os.startfile``. Two rules refuse such a host:
what the documentation says a platform lacks is a requirement like any
absent package (``known_platforms``), and a module the program imports only
under a condition hosts only for a borrower whose import already loads it.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Dict, Optional

import pytest

from towel.unification.import_graph import (
    ImportChange,
    ImportGraphCache,
    conditionally_imported,
    import_change,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

_PROJECT = "[project]\nname = 'shop'\nversion = '0'\n"


def _tree(root: Path, files: Dict[str, str], project: str = _PROJECT) -> Path:
    (root / "pyproject.toml").write_text(project)
    for name, text in {"shop/__init__.py": "", **files}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(textwrap.dedent(text))
    return root / "shop"


def _change(host: str, files: Dict[str, str], tmp_path: Path, **kw: str) -> Optional[ImportChange]:
    package = _tree(
        tmp_path, {"shop/h.py": host, "shop/b.py": "def use():\n    return 1\n", **files}, **kw
    )
    return import_change(str(package / "h.py"), str(package / "b.py"), ImportGraphCache())


@pytest.mark.parametrize(
    "host",
    [
        "import msvcrt\n",
        "import winreg as registry\n",
        "from os import startfile\n",
        "from ctypes import windll\n",
        "from signal import CTRL_C_EVENT\n",
        "import curses.ascii\n",
        "import sys\nif sys.platform == 'win32':\n    import msvcrt\n",
        "try:\n    import msvcrt\nfinally:\n    pass\n",
        "try:\n    import msvcrt\nexcept ValueError:\n    pass\n",
        "try:\n    import ujson\nexcept ImportError:\n    import simplejson as ujson\n",
        "from distutils.util import strtobool\n",
    ],
    ids=[
        "msvcrt",
        "winreg-alias",
        "os.startfile",
        "ctypes.windll",
        "signal.CTRL_C_EVENT",
        "curses-submodule",
        "platform-branch",
        "try-finally",
        "try-other-error",
        "fallback-import",
        "removed-in-3.12",
    ],
)
def test_a_host_requiring_what_a_platform_or_version_lacks_is_refused(
    tmp_path: Path, host: str
) -> None:
    assert _change(host, {}, tmp_path) is ImportChange.NEW_REQUIREMENT


@pytest.mark.parametrize(
    "host",
    [
        "try:\n    import msvcrt\nexcept ImportError:\n    msvcrt = None\n",
        "try:\n    from os import startfile\nexcept (ImportError, AttributeError):\n    pass\n",
        "import os\nfrom signal import SIGINT\n",
    ],
    ids=["optional-msvcrt", "optional-startfile", "everywhere"],
)
def test_a_host_every_platform_imports_is_accepted(tmp_path: Path, host: str) -> None:
    assert _change(host, {}, tmp_path) is None


def test_a_borrower_that_already_requires_the_module_gains_nothing(tmp_path: Path) -> None:
    package = _tree(
        tmp_path,
        {
            "shop/h.py": "import msvcrt\n",
            "shop/b.py": "import msvcrt\n\ndef use():\n    return 1\n",
        },
    )
    assert import_change(str(package / "h.py"), str(package / "b.py"), ImportGraphCache()) is None


@pytest.mark.parametrize(
    "project",
    [
        _PROJECT + "dependencies = [\"colorama; sys_platform == 'win32'\", 'plain>=1']\n",
        "[tool.poetry]\nname = 'shop'\nversion = '0'\n[tool.poetry.dependencies]\n"
        "python = '^3.11'\nplain = '^1'\n"
        "colorama = {version = '*', markers = \"sys_platform == 'win32'\"}\n",
    ],
    ids=["pep508-marker", "poetry-markers"],
)
def test_a_dependency_a_marker_limits_is_not_installed_everywhere(
    tmp_path: Path, project: str
) -> None:
    files = {"shop/p.py": "import plain\n"}
    assert _change("import colorama\n", files, tmp_path, project=project) is (
        ImportChange.NEW_REQUIREMENT
    )
    package = tmp_path / "shop"
    assert import_change(str(package / "p.py"), str(package / "b.py"), ImportGraphCache()) is None


def _conditional(tmp_path: Path, files: Dict[str, str]) -> set[str]:
    package = _tree(tmp_path, files)
    cache = ImportGraphCache()
    program = cache.program_for(package / "__init__.py")
    return {
        path.relative_to(tmp_path.resolve()).as_posix()
        for path in conditionally_imported(program, cache)
    }


@pytest.mark.parametrize(
    "files, expected",
    [
        (
            {"shop/__init__.py": "import sys\nif sys.platform == 'win32':\n    from . import w\n"},
            {"shop/w.py"},
        ),
        (
            {"shop/__init__.py": "import os\nif os.name == 'nt':\n    from . import w\n"},
            {"shop/w.py"},
        ),
        (
            {
                "shop/__init__.py": "import platform\nif platform.system() == 'Windows':\n"
                "    from . import w\n"
            },
            {"shop/w.py"},
        ),
        (
            {"shop/__init__.py": "try:\n    from . import w\nexcept ImportError:\n    w = None\n"},
            {"shop/w.py"},
        ),
        ({"shop/cli.py": "def main():\n    from . import w\n"}, {"shop/w.py"}),
        (
            {
                "shop/__init__.py": "import sys\nif sys.platform == 'win32':\n    from . import wp\n",
                "shop/wp/__init__.py": "from . import console\n",
                "shop/wp/console.py": "",
            },
            {"shop/wp/__init__.py", "shop/wp/console.py"},
        ),
        (
            {
                "shop/__init__.py": "import sys\nif sys.platform == 'win32':\n    from . import w\n",
                "shop/tool.py": "from . import w\n",
            },
            set(),
        ),
        ({"shop/x.py": "from . import y\n", "shop/y.py": "from . import x\n"}, set()),
    ],
    ids=[
        "sys.platform",
        "os.name",
        "platform.system",
        "try-import-error",
        "function-body",
        "gated-subpackage",
        "also-imported-unconditionally",
        "cycle-without-condition",
    ],
)
def test_what_the_program_imports_only_under_a_condition(
    tmp_path: Path, files: Dict[str, str], expected: set[str]
) -> None:
    files.setdefault("shop/w.py", "")
    assert _conditional(tmp_path, files) == expected


def test_a_conditionally_imported_host_is_refused(tmp_path: Path) -> None:
    files = {"shop/__init__.py": "import sys\nif sys.platform == 'win32':\n    from . import h\n"}
    assert (
        _change("from ctypes import wintypes\n", files, tmp_path) is ImportChange.CONDITIONAL_HOST
    )


_BODY = (
    "def summarize_{n}(rows, factor):\n    marker = 0\n    total = 0\n    for r in rows:\n"
    "        total += len(r) * factor\n    label = f'n={{total}}'\n    marker += 1\n"
    "    return '{N}' + label.upper() + str(marker)\n"
)


@pytest.mark.parametrize(
    "first_line",
    ["import msvcrt", "from ctypes import windll", "from os import startfile", "import typing"],
    ids=["msvcrt", "ctypes.windll", "os.startfile", "gated-only"],
)
def test_r9xh_a_windows_only_host_leaves_the_cli_importable(
    tmp_path: Path, first_line: str
) -> None:
    package = _tree(
        tmp_path,
        {
            "shop/__init__.py": "import sys\nif sys.platform == 'win32':\n    from . import _win\n",
            "shop/_win.py": f"{first_line}\n\n\n" + _BODY.format(n="a", N="A"),
            "shop/cli.py": _BODY.format(n="b", N="B"),
        },
    )
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    assert "_win import" not in (package / "cli.py").read_text()
    ran = subprocess.run(
        [sys.executable, "-c", "import shop.cli; print(shop.cli.summarize_b(['ab', 'c'], 3))"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(tmp_path)},
    )
    assert ran.stdout == "BN=91\n", ran.stderr

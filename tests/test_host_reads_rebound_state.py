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

"""A host that binds, as it is imported, what the program rebinds is imported no earlier than it was.

``from time import sleep`` binds whatever ``time.sleep`` holds when it runs.
A helper's import into a borrower loads the host with the borrower, earlier
than the program did, so where any code of the project rebinds that
attribute (``time.sleep = patch``, ``monkeypatch.setattr(time, "sleep", ...)``,
``config.DEBUG = True``) the host binds another value. Round 4 found shop.b
patching ``time.sleep`` after its imports, and shop.a, hosting the helper,
kept the unpatched ``sleep``.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Dict

import pytest

from towel.unification.import_graph import (
    ImportChange,
    ImportGraphCache,
    _import_time_reads,
    import_change,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

_BODY = (
    "def summarize_{n}(rows, factor):\n    marker = 0\n    total = 0\n    for r in rows:\n"
    "        total += len(r) * factor\n    label = f'n={{total}}'\n    marker += 1\n"
    "    return '{N}' + label.upper() + str(marker)\n"
)


def _package(root: Path, files: Dict[str, str]) -> Path:
    (root / "pyproject.toml").write_text("[project]\nname = 'shop'\nversion = '0'\n")
    package = root / "shop"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(textwrap.dedent(text))
    return package


def _reads(tmp_path: Path, source: str) -> set[tuple[str, str]]:
    package = _package(tmp_path, {"shop/m.py": source})
    module = (package / "m.py").resolve()
    program = ImportGraphCache().program_for(module)
    found = _import_time_reads(module, module, program)
    assert found is not None
    return {(str(reference), name) for reference, name in found}


def test_what_importing_a_module_reads_from_others(tmp_path: Path) -> None:
    reads = _reads(
        tmp_path,
        """\
        import time
        import os.path as osp
        from typing import TYPE_CHECKING
        from signal import SIGINT, SIGTERM
        if TYPE_CHECKING:
            from checked import Only
        TICK = time.monotonic
        class Error(Exception):
            kind = osp.sep
        def f(x=len):
            return sorted(x)
        g = lambda y: print(y)
        from json import *
        """,
    )
    assert {
        ("time", "monotonic"),
        ("os.path", "sep"),
        ("signal", "SIGINT"),
        ("signal", "SIGTERM"),
        ("json", "*"),
        ("typing", "TYPE_CHECKING"),
        ("builtins", "Exception"),
        ("builtins", "len"),
    } <= reads
    # A function's and a lambda's bodies run when called; a TYPE_CHECKING body never.
    assert not {("builtins", "sorted"), ("builtins", "print"), ("checked", "Only")} & reads


def test_a_relative_import_reads_the_module_it_names(tmp_path: Path) -> None:
    reads = _reads(tmp_path, "from .config import DEBUG\nfrom . import util\n")
    shop = (tmp_path / "shop").resolve()
    assert (str(shop / "config.py"), "DEBUG") in reads
    assert (str(shop / "__init__.py"), "util") in reads


@pytest.mark.parametrize(
    "writer",
    [
        "import time\ntime.sleep = print\n",
        "import time\n\ndef test_it(monkeypatch):\n    monkeypatch.setattr(time, 'sleep', print)\n",
        "from unittest import mock\n\n@mock.patch('time.sleep')\ndef test_it(sleep):\n    pass\n",
    ],
    ids=["assignment", "monkeypatch", "mock-patch"],
)
def test_a_host_that_binds_a_rebound_attribute_is_refused(tmp_path: Path, writer: str) -> None:
    package = _package(
        tmp_path,
        {
            "shop/a.py": "from time import sleep\n",
            "shop/b.py": "def use():\n    return 1\n",
            "tests/test_patch.py": writer,
        },
    )
    change = import_change(str(package / "a.py"), str(package / "b.py"), ImportGraphCache())
    assert change is ImportChange.READS_REBOUND_STATE


def test_a_host_binding_what_nothing_rebinds_is_accepted(tmp_path: Path) -> None:
    package = _package(
        tmp_path,
        {"shop/a.py": "from time import sleep\n", "shop/b.py": "def use():\n    return 1\n"},
    )
    assert import_change(str(package / "a.py"), str(package / "b.py"), ImportGraphCache()) is None


def test_a_project_setting_rebound_at_startup_refuses_its_readers(tmp_path: Path) -> None:
    package = _package(
        tmp_path,
        {
            "shop/config.py": "DEBUG = False\n",
            "shop/a.py": "from .config import DEBUG\n",
            "shop/b.py": "def use():\n    return 1\n",
            "shop/main.py": "from . import config\nconfig.DEBUG = True\n",
        },
    )
    change = import_change(str(package / "a.py"), str(package / "b.py"), ImportGraphCache())
    assert change is ImportChange.READS_REBOUND_STATE


def _run(root: Path, code: str) -> str:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)},
    ).stdout


@pytest.mark.parametrize("patcher", ["shop/b.py", "shop/c.py"])
def test_r9xh_a_patch_the_host_reads_keeps_its_effect(tmp_path: Path, patcher: str) -> None:
    """The patch in the borrower (the round-4 case) and in a third module the position cannot reach."""
    patch = "import time\ntime.sleep = lambda s: print('patched sleep', s)\n"
    files = {
        "shop/a.py": "from time import sleep\n\n\ndef use_sleep():\n    sleep(0)\n\n\n"
        + _BODY.format(n="a", N="A"),
        "shop/b.py": (patch if patcher == "shop/b.py" else "") + _BODY.format(n="b", N="B"),
        "shop/c.py": "import shop.b\n"
        + (patch if patcher == "shop/c.py" else "")
        + "import shop.a\n",
    }
    package = _package(tmp_path, files)
    oracle = "import shop.c, shop.a; shop.a.use_sleep(); print(shop.b.summarize_b(['ab'], 2))"
    before = _run(tmp_path, oracle)
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    assert _run(tmp_path, oracle) == before == "patched sleep 0\nBN=41\n"

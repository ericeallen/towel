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

"""Which modules pytest rewrites the asserts of, as ``assert_rewriting`` reads a project.

Under ``--cross-module`` an assert moves between modules only when pytest
rewrites both alike, since a failing rewritten assert reports more than a
plain one. The table pins the verdict for each way a project configures
rewriting; the last test asks pytest itself which modules its import hook
loaded, and requires the same answer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, Optional, Tuple

import pytest

from towel.unification import assert_rewriting
from towel.unification.assert_rewriting import rewrites_asserts

_MODULE = "def check(x):\n    assert x\n"

# (name, files, the module asked about, verdict)
CASES: Tuple[Tuple[str, Dict[str, str], str, Optional[bool]], ...] = (
    ("a test module", {"pyproject.toml": ""}, "test_values.py", True),
    ("a module of the package", {"pyproject.toml": ""}, "pkg/checks.py", False),
    ("the other default pattern", {"pyproject.toml": ""}, "values_test.py", True),
    ("a conftest", {"pyproject.toml": ""}, "pkg/conftest.py", True),
    (
        "python_files of pytest.ini",
        {"pytest.ini": "[pytest]\npython_files = check_*.py\n"},
        "check_values.py",
        True,
    ),
    (
        "python_files replaces the default",
        {"pytest.ini": "[pytest]\npython_files = check_*.py\n"},
        "test_values.py",
        False,
    ),
    (
        "[tool.pytest.ini_options]",
        {"pyproject.toml": '[tool.pytest.ini_options]\npython_files = "*.py"\n'},
        "pkg/checks.py",
        True,
    ),
    (
        "[tool.pytest], native",
        {"pyproject.toml": '[tool.pytest]\npython_files = ["*.py"]\n'},
        "pkg/checks.py",
        True,
    ),
    (
        "tox.ini [pytest]",
        {"tox.ini": "[pytest]\npython_files = *.py\n", "setup.py": ""},
        "pkg/checks.py",
        True,
    ),
    (
        "setup.cfg [tool:pytest]",
        {"setup.cfg": "[tool:pytest]\npython_files = *.py\n"},
        "pkg/checks.py",
        True,
    ),
    (
        "--assert=plain",
        {"pytest.ini": "[pytest]\naddopts = --assert=plain\n"},
        "test_values.py",
        False,
    ),
    (
        "a plugin of addopts",
        {"pytest.ini": "[pytest]\naddopts = -p pkg.checks\n"},
        "pkg/checks.py",
        True,
    ),
    (
        "register_assert_rewrite in the root conftest",
        {
            "pyproject.toml": "",
            "conftest.py": "import pytest\npytest.register_assert_rewrite('pkg')\n",
        },
        "pkg/checks.py",
        True,
    ),
    (
        "pytest_plugins in the root conftest",
        {"pyproject.toml": "", "conftest.py": "pytest_plugins = ['pkg.checks']\n"},
        "pkg/checks.py",
        True,
    ),
    (
        "register_assert_rewrite elsewhere",
        {
            "pyproject.toml": "",
            "pkg/conftest.py": "import pytest\npytest.register_assert_rewrite('pkg.checks')\n",
        },
        "pkg/checks.py",
        None,
    ),
    (
        "a module no uncertain mark names",
        {
            "pyproject.toml": "",
            "pkg/conftest.py": "import pytest\npytest.register_assert_rewrite('pkg.other')\n",
        },
        "pkg/checks.py",
        False,
    ),
    (
        "a mark that is not a literal",
        {
            "pyproject.toml": "",
            "conftest.py": "import pytest\npytest.register_assert_rewrite(NAME)\n",
        },
        "pkg/checks.py",
        None,
    ),
    (
        "PYTEST_DONT_REWRITE",
        {"pyproject.toml": ""},
        "test_quiet.py",
        False,
    ),
    (
        "a file testpaths names",
        {"pytest.ini": "[pytest]\ntestpaths = pkg/checks.py\n"},
        "pkg/checks.py",
        True,
    ),
    (
        "configuration below the root",
        {"pyproject.toml": "", "pkg/pytest.ini": "[pytest]\n"},
        "pkg/checks.py",
        None,
    ),
    (
        "-o in addopts",
        {"pytest.ini": "[pytest]\naddopts = -o python_files=*.py\n"},
        "pkg/checks.py",
        None,
    ),
    (
        "the project is a pytest plugin",
        {"pyproject.toml": '[project.entry-points.pytest11]\nmine = "pkg.checks"\n'},
        "pkg/checks.py",
        None,
    ),
)


def _write(root: Path, files: Dict[str, str]) -> None:
    for relative, text in {
        "pkg/__init__.py": "",
        "pkg/checks.py": _MODULE,
        "pkg/other.py": _MODULE,
        "pkg/conftest.py": "",
        "test_values.py": _MODULE,
        "values_test.py": _MODULE,
        "check_values.py": _MODULE,
        "test_quiet.py": '"""PYTEST_DONT_REWRITE"""\n' + _MODULE,
        **files,
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_rewriting_verdict(
    tmp_path: Path, case: Tuple[str, Dict[str, str], str, Optional[bool]]
) -> None:
    _, files, module, verdict = case
    _write(tmp_path, files)
    assert rewrites_asserts(str(tmp_path / module)) is verdict


_REPORTER = """
import json, os, sys
from _pytest.assertion.rewrite import AssertionRewritingHook

def pytest_sessionfinish(session):
    import pkg.checks, pkg.other, check_values
    root = os.path.dirname(os.path.abspath(__file__))
    found = {}
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None) or ""
        if path.startswith(root) and path.endswith(".py"):
            found[os.path.relpath(path, root)] = isinstance(
                getattr(module, "__loader__", None), AssertionRewritingHook
            )
    with open(os.path.join(root, "rewritten.json"), "w") as out:
        json.dump(found, out)
"""


@pytest.mark.parametrize(
    "config",
    [
        "[pytest]\n",
        "[pytest]\npython_files = check_*.py test_*.py\n",
        "[pytest]\naddopts = -p pkg.other\n",
    ],
)
def test_pytest_rewrites_what_the_verdict_says(tmp_path: Path, config: str) -> None:
    # pytest itself is the oracle: which modules its import hook loaded.
    _write(tmp_path, {"pytest.ini": config, "conftest.py": textwrap.dedent(_REPORTER)})
    (tmp_path / "test_values.py").write_text(_MODULE + "\ndef test_it():\n    check(1)\n")
    assert_rewriting._SETUPS.clear()
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_values.py"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": ""},
        capture_output=True,
        check=False,
        timeout=120,
    )
    loaded = json.loads((tmp_path / "rewritten.json").read_text())
    assert {"pkg/checks.py", "pkg/other.py", "check_values.py", "test_values.py"} <= set(loaded)
    for relative, rewritten in loaded.items():
        assert rewrites_asserts(str(tmp_path / relative)) is rewritten, relative

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


# -- testpaths, file arguments and python_files, against pytest itself -------------

_EVERYTHING = "import tests.helpers_x, tests.sub.helpers_y, pkg.mod  # noqa: F401\n"
_LAYOUT: Dict[str, str] = {
    "tests/__init__.py": "",
    "tests/sub/__init__.py": "",
    "pkg/__init__.py": "",
    "pkg/mod.py": _MODULE,
    # Whichever module pytest collects imports the rest while it collects.
    "tests/helpers_x.py": _EVERYTHING + _MODULE,
    "tests/sub/helpers_y.py": _EVERYTHING + _MODULE,
    "tests/check_all.py": _EVERYTHING + "\ndef test_it():\n    pass\n",
    "tests/test_all.py": _EVERYTHING + "\ndef test_it():\n    pass\n",
}

# (name, pytest.ini, extra files, whether Towel may answer None for helpers_x)
_MATRIX: Tuple[Tuple[str, str, Dict[str, str], bool], ...] = (
    # Round 4's P1-11: the file is subsumed by the directory, so it is no initial path.
    (
        "file and its directory",
        "testpaths = tests/helpers_x.py tests\npython_files = check_*.py",
        {},
        False,
    ),
    (
        "directory and its file",
        "testpaths = tests tests/helpers_x.py\npython_files = check_*.py",
        {},
        False,
    ),
    ("the file alone", "testpaths = tests/helpers_x.py\npython_files = check_*.py", {}, False),
    (
        "the file twice",
        "testpaths = tests/helpers_x.py tests/helpers_x.py\npython_files = check_*.py",
        {},
        False,
    ),
    (
        "the file and a sibling directory",
        "testpaths = tests/helpers_x.py tests/sub\npython_files = check_*.py",
        {},
        False,
    ),
    (
        "--keep-duplicates keeps the file",
        "testpaths = tests/helpers_x.py tests\npython_files = check_*.py\naddopts = --keep-duplicates",
        {},
        False,
    ),
    ("every file", "testpaths = tests\npython_files = *.py", {}, False),
    # A package's __init__.py matches, but only a package named as an initial
    # path gets past the early bail-out.
    ("__init__.py by name", "testpaths = tests\npython_files = __init__.py test_*.py", {}, False),
    (
        "a pattern with a directory",
        "testpaths = tests\npython_files = tests/sub/*.py check_*.py",
        {},
        False,
    ),
    ("no testpaths", "python_files = test_*.py", {}, False),
    # A conftest.py that imports the file while pytest configures itself:
    # it is loaded before the session, and the file is not rewritten.
    (
        "imported by a conftest before collection",
        "testpaths = tests/helpers_x.py\npython_files = check_*.py",
        {"conftest.py": "import tests.helpers_x  # noqa: F401\n"},
        True,
    ),
)

_REPORTER_PLUGIN = """
import json, os, sys

def pytest_collection_finish(session):
    from _pytest.assertion.rewrite import AssertionRewritingHook
    root = str(session.config.rootpath)
    found = {}
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None) or ""
        if path.startswith(root + os.sep) and path.endswith(".py"):
            found[os.path.relpath(path, root)] = isinstance(
                getattr(module, "__loader__", None), AssertionRewritingHook
            )
    with open(os.environ["TOWEL_REWRITE_REPORT"], "w") as out:
        json.dump(found, out)
"""


def test_the_initial_paths_rewritten_are_those_pytest_rewrites(tmp_path: Path) -> None:
    """pytest 9.1 is the oracle: which of the project's modules its hook loaded, run from the root."""
    pytest.importorskip("_pytest.assertion.rewrite")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "towel_rewrite_reporter.py").write_text(textwrap.dedent(_REPORTER_PLUGIN))
    runs = []
    for index, (_, ini, extra, _) in enumerate(_MATRIX):
        root = tmp_path / f"project{index}"
        root.mkdir()
        _write_tree(root, {**_LAYOUT, "pytest.ini": f"[pytest]\n{ini}\n", **extra})
        report = root / "rewritten.json"
        environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": str(plugins),
            "TOWEL_REWRITE_REPORT": str(report),
        }
        command = [sys.executable, "-m", "pytest", "-q", "--collect-only"]
        command += ["-p", "no:cacheprovider", "-p", "towel_rewrite_reporter"]
        runs.append(
            (
                root,
                report,
                subprocess.Popen(
                    command,
                    cwd=root,
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ),
            )
        )
    for (name, _, _, doubtful), (root, report, process) in zip(_MATRIX, runs):
        process.wait(timeout=120)
        loaded = json.loads(report.read_text())
        assert "tests/helpers_x.py" in loaded, (name, sorted(loaded))
        assert_rewriting._SETUPS.clear()
        for relative, rewritten in sorted(loaded.items()):
            verdict = rewrites_asserts(str(root / relative))
            if doubtful and relative == "tests/helpers_x.py":
                assert verdict is None, (name, relative, rewritten)
            else:
                assert verdict is rewritten, (name, relative)


def _write_tree(root: Path, files: Dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.mark.parametrize(
    "ini",
    [
        "testpaths = tests/*.py",
        "testpaths = tests/helpers_[xy].py",
        "testpaths = tests/check_all.py::test_it",
        "testpaths = tests\naddopts = --pyargs",
    ],
)
def test_testpaths_pytest_reads_in_ways_not_modeled_leave_the_answer_unknown(
    tmp_path: Path, ini: str
) -> None:
    _write_tree(tmp_path, {**_LAYOUT, "pytest.ini": f"[pytest]\n{ini}\n"})
    assert_rewriting._SETUPS.clear()
    assert rewrites_asserts(str(tmp_path / "tests/helpers_x.py")) is None


def test_a_file_named_where_pytest_may_not_look_for_it_is_unknown(tmp_path: Path) -> None:
    """``testpaths`` of a configuration above the root apply only when pytest runs from there."""
    project = tmp_path / "proj"
    _write_tree(project, {**_LAYOUT, "pyproject.toml": "[project]\nname = 'proj'\n"})
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = proj/tests/helpers_x.py\n")
    assert_rewriting._SETUPS.clear()
    assert rewrites_asserts(str(project / "tests/helpers_x.py")) is None
    # A module rewritten for another reason is known whatever the invocation.
    assert rewrites_asserts(str(project / "tests/test_all.py")) is True


def test_a_file_named_through_a_symbolic_link_is_unknown(tmp_path: Path) -> None:
    """The hook compares unresolved paths, which a link makes differ from the module's."""
    _write_tree(tmp_path, {**_LAYOUT, "pytest.ini": "[pytest]\ntestpaths = linked/helpers_x.py\n"})
    (tmp_path / "linked").symlink_to(tmp_path / "tests")
    assert_rewriting._SETUPS.clear()
    assert rewrites_asserts(str(tmp_path / "tests/helpers_x.py")) is None

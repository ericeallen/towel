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

"""The project's coverage.py exclusions, read as coverage.py reads them.

Where coverage.py is installed, every case is also read by coverage.py's own
``read_coverage_config`` in the same directory and the two must agree,
unreadable configurations included.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
from pathlib import Path
from typing import Dict, Iterator, Mapping, Tuple, Union

import pytest

from towel.coverage_config import DEFAULT_EXCLUDE, coverage_exclusion
from towel.unification.block_comments import excluded_lines

requires_coverage = pytest.mark.skipif(
    importlib.util.find_spec("coverage") is None, reason="coverage absent"
)

# name -> (files, COVERAGE_RCFILE, the regexes expected, or None when unreadable)
_CASES: Dict[str, Tuple[Mapping[str, str], str, Union[Tuple[str, ...], None]]] = {
    "no configuration": ({}, "", DEFAULT_EXCLUDE),
    ".coveragerc [report]": (
        {".coveragerc": "[report]\nexclude_lines =\n    nocov\n    if __name__ == .__main__.:\n"},
        "",
        ("nocov", "if __name__ == .__main__.:"),
    ),
    ".coveragerc [coverage:report]": (
        {".coveragerc": "[coverage:report]\nexclude_also =\n    raise NotImplementedError\n"},
        "",
        DEFAULT_EXCLUDE + ("raise NotImplementedError",),
    ),
    ".coveragerc, empty, still decides": (
        {".coveragerc": "", "pyproject.toml": "[tool.coverage.report]\nexclude_lines = ['x']\n"},
        "",
        DEFAULT_EXCLUDE,
    ),
    ".coveragerc.toml": (
        {".coveragerc.toml": "[report]\nexclude_also = ['@overload']\n"},
        "",
        DEFAULT_EXCLUDE + ("@overload",),
    ),
    "setup.cfg": (
        {"setup.cfg": "[coverage:report]\nexclude_lines =\n    pragma: no cover\n    @overload\n"},
        "",
        ("pragma: no cover", "@overload"),
    ),
    "setup.cfg with only [coverage:run] decides": (
        {
            "setup.cfg": "[coverage:run]\nbranch = True\n",
            "pyproject.toml": "[tool.coverage.report]\nexclude_lines = ['x']\n",
        },
        "",
        DEFAULT_EXCLUDE,
    ),
    "setup.cfg without coverage passes to tox.ini": (
        {
            "setup.cfg": "[metadata]\nname = x\n",
            "tox.ini": "[coverage:report]\nexclude_also = tox\n",
        },
        "",
        DEFAULT_EXCLUDE + ("tox",),
    ),
    "setup.cfg's bare [report] is not coverage's": (
        {
            "setup.cfg": "[report]\nexclude_lines = nope\n",
            "pyproject.toml": "[tool.coverage.report]\nexclude_also = ['py']\n",
        },
        "",
        DEFAULT_EXCLUDE + ("py",),
    ),
    "tox.ini": (
        {"tox.ini": "[coverage:report]\nexclude_lines =\n    t1\n  t2  \n\n"},
        "",
        ("t1", "t2"),
    ),
    "pyproject.toml": (
        {"pyproject.toml": "[tool.coverage.report]\nexclude_also = ['if TYPE_CHECKING:']\n"},
        "",
        DEFAULT_EXCLUDE + ("if TYPE_CHECKING:",),
    ),
    "pyproject.toml without coverage": ({"pyproject.toml": "[tool.black]\n"}, "", DEFAULT_EXCLUDE),
    "exclude_lines and exclude_also": (
        {
            "pyproject.toml": (
                "[tool.coverage.report]\nexclude_lines = ['one']\nexclude_also = ['two']\n"
            )
        },
        "",
        ("one", "two"),
    ),
    "environment variables": (
        {"pyproject.toml": "[tool.coverage.report]\nexclude_also = ['${MARK}', 'plain$']\n"},
        "",
        DEFAULT_EXCLUDE + ("marked", "plain$"),
    ),
    "empty exclude_lines excludes nothing": (
        {"pyproject.toml": "[tool.coverage.report]\nexclude_lines = []\n"},
        "",
        (),
    ),
    "COVERAGE_RCFILE": (
        {
            "custom.ini": "[report]\nexclude_lines = custom\n",
            "pyproject.toml": "[tool.coverage.report]\nexclude_lines = ['py']\n",
        },
        "custom.ini",
        ("custom",),
    ),
    "COVERAGE_RCFILE absent": (
        {"pyproject.toml": "[tool.coverage.report]\nexclude_lines = ['py']\n"},
        "absent.ini",
        None,
    ),
    "TOML that does not parse": (
        {"pyproject.toml": "[tool.coverage.report\nexclude_lines = 1\n"},
        "",
        None,
    ),
    "ini that does not parse": ({"setup.cfg": "[coverage:report\nexclude_lines = x\n"}, "", None),
    "a regex that does not compile": (
        {"pyproject.toml": "[tool.coverage.report]\nexclude_also = ['(unclosed']\n"},
        "",
        None,
    ),
    "exclude_lines not a list": (
        {"pyproject.toml": "[tool.coverage.report]\nexclude_lines = 'pragma'\n"},
        "",
        None,
    ),
    "exclude_lines not strings": (
        {"pyproject.toml": "[tool.coverage.report]\nexclude_lines = [1]\n"},
        "",
        None,
    ),
}


def _environment(rcfile: str) -> Dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key != "COVERAGE_RCFILE"}
    environment["MARK"] = "marked"
    if rcfile:
        environment["COVERAGE_RCFILE"] = rcfile
    return environment


def _project(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


@pytest.mark.parametrize("name", list(_CASES))
def test_the_exclusions_are_read_from_each_source_in_coverages_order(
    tmp_path: Path, name: str
) -> None:
    files, rcfile, expected = _CASES[name]
    found = coverage_exclusion(_project(tmp_path, files), _environment(rcfile))
    if expected is None:
        assert found.problem is not None and found.regexes == DEFAULT_EXCLUDE
    else:
        assert found.problem is None
        assert found.regexes == expected


@contextlib.contextmanager
def _inside(root: Path, environment: Mapping[str, str]) -> Iterator[None]:
    """Run as coverage.py would from ``root``, with ``environment`` as the process's."""
    saved_directory, saved_environment = os.getcwd(), dict(os.environ)
    os.chdir(root)
    os.environ.clear()
    os.environ.update(environment)
    try:
        yield
    finally:
        os.chdir(saved_directory)
        os.environ.clear()
        os.environ.update(saved_environment)


@requires_coverage
@pytest.mark.parametrize("name", list(_CASES))
def test_coverage_itself_reads_the_same_exclusions(tmp_path: Path, name: str) -> None:
    from coverage.config import read_coverage_config
    from coverage.exceptions import ConfigError

    files, rcfile, _ = _CASES[name]
    root = _project(tmp_path, files)
    environment = _environment(rcfile)
    with _inside(root, environment):
        try:
            theirs: Union[Tuple[str, ...], None] = tuple(
                read_coverage_config(True, lambda message: None).exclude_list
            )
        except ConfigError:
            theirs = None
    found = coverage_exclusion(root, environment)
    assert (None if found.problem else found.regexes) == theirs


@requires_coverage
def test_the_defaults_are_coverages_own() -> None:
    from coverage.config import DEFAULT_EXCLUDE as THEIRS

    assert DEFAULT_EXCLUDE == tuple(THEIRS)


def test_lines_are_matched_as_coverage_matches_them() -> None:
    source = "a = 1\nif __name__ == '__main__':\n    run()\nx = [  # nocov\n  1]\n"
    assert excluded_lines(source, "if __name__ == .__main__.:") == {2}
    assert excluded_lines(source, "nocov") == {4}
    # A match spanning lines covers every line it touches.
    assert excluded_lines(source, r"x = \[.*\n\s*1\]") == {4, 5}
    assert excluded_lines(source, "") == frozenset()


@requires_coverage
def test_lines_matched_agree_with_coverages_parser() -> None:
    from coverage.misc import join_regex
    from coverage.parser import PythonParser

    source = (
        "import typing\n"
        "if typing.TYPE_CHECKING:\n"
        "    import os\n"
        "def f():  # pragma: no cover\n"
        "    return 1\n"
        "def g(): ...\n"
        "x = (1,\n"
        "     2)  # nocov\n"
    )
    regexes = list(DEFAULT_EXCLUDE) + ["nocov"]
    parser = PythonParser(text=source, exclude=join_regex(regexes))
    parser.parse_source()
    assert excluded_lines(source, join_regex(regexes)) == parser.raw_excluded


# -- Which files coverage.py measures and reports ---------------------------------

_MODULES = ("pkg/__init__.py", "pkg/a.py", "pkg/b.py", "tests/__init__.py", "tests/t.py")

# name -> (configuration files, {module: (measured, reported)} for those that differ from both)
_MEASURED: Dict[str, Mapping[str, str]] = {
    "no configuration": {},
    "[run] omit of one file": {".coveragerc": "[run]\nomit =\n    pkg/a.py\n"},
    "[run] omit by a pattern": {".coveragerc": "[run]\nomit = */tests/*\n"},
    "[run] omit by a file name": {".coveragerc": "[run]\nomit = b.py\n"},
    "[run] include": {".coveragerc": "[run]\ninclude =\n    pkg/*\n"},
    "[run] source, a directory": {".coveragerc": "[run]\nsource = pkg\n"},
    "[run] source_pkgs": {".coveragerc": "[run]\nsource_pkgs = tests\n"},
    "[run] source, and an include it overrides": {
        ".coveragerc": "[run]\nsource = tests\ninclude = pkg/*\n"
    },
    "[report] omit": {".coveragerc": "[report]\nomit = pkg/b.py\n"},
    "[report] include": {".coveragerc": "[report]\ninclude = tests/*\n"},
    "pyproject.toml": {
        "pyproject.toml": "[tool.coverage.run]\nomit = ['pkg/a.py']\n"
        "[tool.coverage.report]\nomit = ['tests/*']\n"
    },
    "setup.cfg": {"setup.cfg": "[coverage:run]\nsource = pkg, tests\nomit = */__init__.py\n"},
}

_DRIVER = """
import json, sys
import coverage

cov = coverage.Coverage(data_file=None)
cov.start()
import pkg.a, pkg.b, tests.t  # noqa: E401
cov.stop()
measured = sorted(cov.get_data().measured_files())
cov.json_report(outfile="report.json")
with open("report.json") as report:
    reported = sorted(json.load(report)["files"])
print(json.dumps({"measured": measured, "reported": reported}))
"""


def _measured_project(root: Path, files: Mapping[str, str]) -> Path:
    for name in _MODULES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("VALUE = 1\n", encoding="utf-8")
    (root / "driver.py").write_text(_DRIVER, encoding="utf-8")
    return _project(root, files)


@requires_coverage
@pytest.mark.parametrize("name", list(_MEASURED))
def test_the_files_measured_and_reported_are_those_coverage_measures_and_reports(
    tmp_path: Path, name: str
) -> None:
    """coverage.py 7.16 is the oracle: which of the modules it measured and reported, run from the root."""
    import json
    import subprocess
    import sys

    from towel.coverage_config import coverage_configuration

    root = _measured_project(tmp_path.resolve(), _MEASURED[name])
    completed = subprocess.run(
        [sys.executable, "driver.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "COVERAGE_RCFILE": ""},
    )
    found = json.loads(completed.stdout.strip().splitlines()[-1])
    measured = {os.path.relpath(path, root) for path in found["measured"]}
    reported = set(found["reported"])
    configuration = coverage_configuration(root, {})
    assert configuration.exclusion.problem is None
    for module in _MODULES:
        name_of = module[: -len(".py")].replace("/", ".").removesuffix(".__init__")
        status = configuration.measurement.status(root / module, root, name_of)
        assert status == (module in measured, module in reported), module


def test_a_source_package_needs_the_module_name() -> None:
    from towel.coverage_config import CoverageMeasurement

    measurement = CoverageMeasurement(source_pkgs=("pkg",))
    root = Path("/nonexistent")
    assert measurement.names_packages(root)
    assert measurement.status(root / "pkg/a.py", root, None) is None
    assert measurement.status(root / "pkg/a.py", root, "pkg.a") == (True, True)
    assert measurement.status(root / "other.py", root, "other") == (False, False)

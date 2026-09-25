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

"""A pyright configuration is read with pyright's own grammar, and one pyright rejects refuses the run.

pyright parses ``pyrightconfig.json`` as JSON with comments and one trailing
comma per object or array, and on any error its command line exits 3 while
its language server goes on checking with default settings. Towel's reader
stripped every comma that preceded a closing bracket, so it accepted a file
pyright rejects (``{"typeCheckingMode": "standard",, }``): the warm session
passed candidates under defaults, the cold confirmation failed after an
in-place write, and the message said the refactorings were "listed above"
with nothing listed. The grammar here is checked against the installed
pyright, and a checker failure now carries what the checker said.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import pytest

from towel import checker_project
from towel.checker_project import _json_config
from towel.type_inference import CheckFailure, PyrightOracle
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_cli_integration import invoke

requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

# What pyright 1.1.414 does with each text, established by running it on each.
ACCEPTED = {
    "trailing comma": '{"a": 1,}',
    "trailing comma in an array": '{"a": [1, 2,]}',
    "line comment": '// c\n{"a": 1}',
    "block comment": '/* c */ {"a": 1}',
    "comment before the trailing comma": '{"a": 1 /* c */ ,}',
    "comment after the trailing comma": '{"a": 1, // c\n}',
    "nested trailing commas": '{"a": {"b": 1,}, "c": [1,],}',
    "comment markers in a string": '{"a": "// not a comment /* */"}',
    "escaped slash": '{"a": "\\/"}',
    "exponent": '{"a": 1e5}',
    "carriage returns only": '{\r"a": 1\r}',
    "duplicate key": '{"a": 1, "a": 2}',
}
REJECTED = {
    "two commas": '{"typeCheckingMode": "standard",, }',
    "leading comma": "{,}",
    "two trailing commas in an array": '{"a": [1,,]}',
    "byte-order mark": '\ufeff{"a": 1}',
    "form feed": '{"a": 1}\x0c',
    "no-break space": '{"a":\u00a01}',
    "single quotes": "{'a': 1}",
    "unquoted key": "{a: 1}",
    "hash comment": '# c\n{"a": 1}',
    "unterminated block comment": '{"a": 1} /* never closed',
    "leading zero": '{"a": 01}',
    "hexadecimal": '{"a": 0x10}',
    "NaN": '{"a": NaN}',
    "tab inside a string": '{"a": "t\there"}',
    "invalid escape": '{"a": "\\x"}',
    "short unicode escape": '{"a": "\\u12"}',
    "empty file": "",
    "two documents": '{"a": 1} {"b": 2}',
    "stray slash": '{"a": 1} /',
}


@pytest.mark.parametrize("text", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_what_pyright_accepts_is_read(text: str) -> None:
    assert isinstance(_json_config(text), dict)


@pytest.mark.parametrize("text", REJECTED.values(), ids=REJECTED.keys())
def test_what_pyright_rejects_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match=r"line \d+, column \d+|expected"):
        _json_config(text)


def _pyright_rejects(directory: Path, text: str) -> bool:
    (directory / "pyrightconfig.json").write_bytes(text.encode("utf-8"))
    (directory / "m.py").write_text("x: int = 1\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "pyright", "--outputjson", "--project", str(directory)],
        capture_output=True,
        text=True,
        cwd=directory,
        check=False,
    )
    assert completed.returncode in (0, 1, 3), completed.stderr
    return "could not be parsed" in completed.stderr


@requires_pyright
@pytest.mark.parametrize(
    "name",
    [
        "two commas",
        "byte-order mark",
        "form feed",
        "trailing comma",
        "comment after the trailing comma",
    ],
)
def test_the_installed_pyright_still_agrees(tmp_path: Path, name: str) -> None:
    """The fixtures above are pyright's behaviour; a pyright that changes it fails here first."""
    text = {**ACCEPTED, **REJECTED}[name]
    assert _pyright_rejects(tmp_path, text) is (name in REJECTED)


def _project(root: Path, configuration: str) -> Path:
    root.mkdir(parents=True)
    (root / "pyrightconfig.json").write_bytes(configuration.encode("utf-8"))
    path = root / "m.py"
    path.write_text(
        textwrap.dedent("""
            def first(value: int) -> int:
                print("first")
                total = value + 1
                doubled = total * 2
                return doubled - 3


            def second(value: int) -> int:
                print("second")
                total = value + 1
                doubled = total * 2
                return doubled - 4
            """).lstrip(),
        encoding="utf-8",
    )
    return path


@requires_pyright
@pytest.mark.parametrize("configuration", [REJECTED["two commas"], REJECTED["byte-order mark"]])
def test_a_configuration_pyright_rejects_refuses_the_run_before_anything_is_written(
    tmp_path: Path, configuration: str
) -> None:
    path = _project(tmp_path / "project", configuration)
    before = path.read_bytes()
    result = invoke(
        ["dry", str(path.parent), str(path.parent), "--no-interactive", "--progress", "none"]
    )
    assert result.status == 1, result
    assert "Original project type check failed" in result.stderr
    assert str(path.parent / "pyrightconfig.json") in result.stderr
    assert "line 1, column" in result.stderr
    assert "--no-types" in result.stderr
    assert path.read_bytes() == before
    assert not (path.parent / ".towel-helpers.json").exists()


def test_the_error_names_the_file_and_is_its_own_kind(tmp_path: Path) -> None:
    (tmp_path / "pyrightconfig.json").write_text('{"a": 1,, }', encoding="utf-8")
    with pytest.raises(
        checker_project.UnusableConfiguration, match=r"pyrightconfig\.json.*line 1, column"
    ):
        checker_project.CheckerSnapshot(tmp_path)


def _failing_pyright(directory: Path) -> str:
    script = directory / "failing-pyright"
    script.write_text(
        "#!/bin/sh\necho '{}'\necho 'Config file \"x\" could not be parsed. Verify that format"
        " is correct.' >&2\nexit 3\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    return str(script)


def test_a_command_line_failure_says_what_pyright_said(tmp_path: Path) -> None:
    oracle = PyrightOracle.__new__(PyrightOracle)
    oracle._command = [_failing_pyright(tmp_path)]
    oracle._server = None
    oracle._warmed = {}
    oracle._probe_copies = {}
    oracle._interpreter = sys.executable
    oracle._search_path = ()
    oracle._owner_pid = os.getpid()
    path = tmp_path / "project" / "m.py"
    path.parent.mkdir()
    path.write_text("x = 1\n", encoding="utf-8")
    result = oracle.check_project({str(path): "x = 2\n"})
    assert isinstance(result, CheckFailure)
    assert "exit 3" in result.reason
    assert "could not be parsed" in result.reason


class _SessionThenFailingCommandLine(PyrightOracle):
    """A language server that answers, then a command line that exits 3 with a message."""

    def __init__(self, failing: str) -> None:
        super().__init__()
        self._failing = failing

    def stop_language_servers(self) -> None:
        super().stop_language_servers()
        self._command = [self._failing]


@requires_pyright
def test_a_confirmation_that_failed_quotes_the_checker(tmp_path: Path) -> None:
    path = _project(tmp_path / "project", '{"typeCheckingMode": "basic"}')
    before = path.read_bytes()
    oracle = _SessionThenFailingCommandLine(_failing_pyright(tmp_path))
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        with pytest.raises(RefactoringError) as refused:
            engine.refactor_to_fixed_point(str(path), progress="none")
    finally:
        oracle.close()
    message = str(refused.value)
    assert "could not be confirmed" in message
    assert "could not be parsed. Verify that format is correct." in message
    assert "Nothing was written." in message
    assert path.read_bytes() == before

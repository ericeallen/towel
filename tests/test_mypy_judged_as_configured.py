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

"""Towel's mypy builds judge configurations, files and module names as mypy itself does.

A typed run promises that the project's own mypy, run as the project
configures it, reports no error the original did not have, and that a checker
which could not answer never reads as a clean verdict. The third release audit
found five ways the mypy worker judged something differently from mypy:

- D2: a configuration mypy only warns about refused the typed run;
- D4: a per-module ``follow_imports`` was ignored, so errors mypy reports were
  dropped and a helper it rejects was accepted;
- D8: a run over ``tests/`` found the project's package in a stale installed
  copy, not in ``src``, and wrote an annotation for the stale API;
- D9: probes named modules unlike mypy, every probe build of a PEP 420 project
  failed, and each failure read as code the checker does not look at;
- D10: a changed file outside ``files`` was given a module name its importer
  does not use, and mypy refused the build.

These tests read mypy's own option and module resolution through its API and
never build anything.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
import sys
from typing import Mapping

import pytest

pytest.importorskip("mypy")

from towel import _mypy_worker as worker  # noqa: E402
from towel.type_inference import CheckFailure, MypyInferrer, _BuildMessages  # noqa: E402

# --- D2: what mypy says while reading a configuration ------------------------


@pytest.mark.parametrize(
    "extra, said",
    [
        ("towel_no_such_option = true\n", "Unrecognized option: towel_no_such_option = True"),
        ('python_version = "3.4"\n', "python_version: Python 3.4 is not supported"),
        (
            '[[tool.mypy.overrides]]\nmodule = "pkg.a"\npython_version = "3.12"\n',
            "Per-module sections should only specify per-module flags (python_version)",
        ),
    ],
)
def test_what_mypy_only_warns_about_is_said_and_the_rest_of_the_configuration_applies(
    tmp_path: Path, extra: str, said: str
) -> None:
    """mypy prints each of these and checks on, exiting 0; so does a typed run (D2)."""
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n" + extra)
    configured = worker._read_configuration(str(tmp_path / "pyproject.toml"))
    assert any(said in line for line in configured.said), configured.said
    assert configured.options.disallow_untyped_defs


def test_a_configuration_mypy_reads_cleanly_says_nothing(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    assert worker._read_configuration(str(tmp_path / "pyproject.toml")).said == ()


def test_a_configuration_mypy_will_not_start_from_is_refused_in_mypys_words(
    tmp_path: Path,
) -> None:
    """What stops mypy's own run stops the check, quoting mypy rather than an exit status."""
    with pytest.raises(ValueError, match="Cannot find config file"):
        worker._read_configuration(str(tmp_path / "missing.toml"))


def test_the_oracle_passes_on_each_configuration_warning_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    oracle = MypyInferrer()
    try:
        with caplog.at_level(logging.WARNING):
            oracle._pass_on(["an unknown option", "an unknown option", "a dropped version"])
            oracle._pass_on(["an unknown option"])
    finally:
        oracle.close()
    said = [record.getMessage() for record in caplog.records]
    assert said == [
        "mypy, reading the project's configuration: an unknown option",
        "mypy, reading the project's configuration: a dropped version",
    ]


@pytest.mark.parametrize(
    "answer, expected",
    [
        (
            {"messages": ["m.py:1: error: E"], "failure": None, "warnings": ["W"]},
            _BuildMessages(("m.py:1: error: E",), ("W",)),
        ),
        # A child that died answers without warnings at all.
        ({"messages": [], "failure": None}, _BuildMessages(())),
        (
            {"messages": [], "failure": None, "warnings": [1]},
            CheckFailure("mypy worker returned invalid configuration warnings"),
        ),
    ],
)
def test_the_worker_protocol_carries_configuration_warnings(
    answer: Mapping[str, object], expected: object
) -> None:
    reply = f"import sys; sys.stdin.readline(); print({json.dumps(json.dumps(answer))})"
    process = subprocess.Popen(
        [sys.executable, "-c", reply], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    oracle = MypyInferrer()
    try:
        assert oracle._exchange(process, {}) == expected
    finally:
        process.wait(timeout=10)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
        oracle.close()


def test_the_worker_answer_carries_what_mypy_said_and_never_a_failure_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker, "_request", lambda request, cache: worker._Answered(["m"], ("warned",))
    )
    assert json.loads(worker._answer("{}", "cache")) == {
        "messages": ["m"],
        "failure": None,
        "warnings": ["warned"],
    }

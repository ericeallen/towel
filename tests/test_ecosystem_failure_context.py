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

"""Captured tool errors and nested suites are not the outer runner's test IDs."""

from pathlib import Path
import sys

import pytest

from scripts import ecosystem_check as ecosystem


@pytest.mark.parametrize("nested_unittest", [False, True])
def test_real_pytest_output_distinguishes_pip_errors_from_another_runner(
    tmp_path: Path,
    nested_unittest: bool,
) -> None:
    (tmp_path / "test_cases.py").write_text(
        "import sys\nimport unittest\n\n"
        "def test_outer(tmp_path):\n"
        "    class Inner(unittest.TestCase):\n"
        "        def test_inner(self):\n            self.fail('nested diagnostic')\n"
        "    print(\"ERROR: Failed to build 'file://\" + str(tmp_path / 'console_app')\n"
        '          + "\' when installing build dependencies", file=sys.stderr)\n'
        + ("    unittest.TextTestRunner().run(Inner('test_inner'))\n" if nested_unittest else "")
        + "    assert False, 'outer failure'\n"
    )
    outputs = []
    for side in ("before", "after"):
        command = ecosystem._prepare_test_command(
            [sys.executable, "-m", "pytest", "-q", "--basetemp", str(tmp_path / side)]
        )
        phase = ecosystem.run(
            command,
            tmp_path,
            {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            20,
            tmp_path / f"{side}.log",
        )
        text = Path(phase.log).read_text()
        assert phase.returncode == 1
        assert "ERROR: Failed to build 'file://" in text
        identities = ecosystem.failed_tests(phase.log)
        assert "test_cases.py::test_outer" in identities
        outcome = ecosystem._completed_test_run(phase)
        if nested_unittest:
            assert "FAIL: test_inner" in text and "Ran 1 test" in text
            assert any(identity.startswith("unittest:test_inner ") for identity in identities)
            assert outcome is None
        else:
            assert identities == {"test_cases.py::test_outer"}
            assert outcome is not None and outcome.collected == 1
        outputs.append(text)
    assert outputs[0] != outputs[1]


@pytest.mark.parametrize("buffered", [False, True])
def test_nested_pytest_cannot_hide_changed_outer_unittest_failures(
    tmp_path: Path, buffered: bool
) -> None:
    (tmp_path / "test_nested.py").write_text("def test_inner():\n    assert False\n")
    runner = tmp_path / "runtests.py"
    runner.write_text(
        "import subprocess, sys, unittest\n"
        "class Outer(unittest.TestCase):\n"
        "    def nested(self):\n"
        "        result = subprocess.run([sys.executable, '-m', 'pytest', '-q',\n"
        "                                 'test_nested.py'], capture_output=True, text=True)\n"
        f"        print(result.stdout, flush={not buffered!r})\n"
        "        self.fail('outer changed failure')\n"
        "    def test_left(self):\n"
        "        if sys.argv[1] == 'left': self.nested()\n"
        "    def test_right(self):\n"
        "        if sys.argv[1] == 'right': self.nested()\n"
        f"result = unittest.TextTestRunner({'' if buffered else 'stream=sys.stdout'}).run(\n"
        "    unittest.defaultTestLoader.loadTestsFromTestCase(Outer))\n"
        "sys.exit(not result.wasSuccessful())\n"
    )
    identities = []
    for side in ("left", "right"):
        phase = ecosystem.run(
            [sys.executable, str(runner), side],
            tmp_path,
            {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            20,
            tmp_path / f"{side}.log",
        )
        text = Path(phase.log).read_text()
        assert phase.returncode == 1
        assert (text.index("FAILED (failures=1)") < text.index("1 failed in")) is buffered
        assert ecosystem._completed_test_run(phase) is None
        failures = ecosystem.failed_tests(phase.log)
        assert failures == {
            "test_nested.py::test_inner",
            f"unittest:test_{side} (__main__.Outer.test_{side})",
        }
        identities.append(failures)
    assert identities[0] != identities[1]


@pytest.mark.parametrize("closing", ["Ran 2 tests in 0.01s\n", "FAILED (failures=1)\n"])
@pytest.mark.parametrize("buffered", [False, True])
def test_incomplete_outer_unittest_is_not_certified_by_a_nested_pytest_result(
    tmp_path: Path, closing: str, buffered: bool
) -> None:
    nested = "FAILED test_inner.py::test_inner - assertion\n1 failed in 0.01s\n"
    outer = "FAIL: test_outer (fixture.Outer.test_outer)\n" + closing
    output = outer + nested if buffered else nested + outer
    log = tmp_path / "incomplete.log"
    log.write_text(output)
    phase = ecosystem.Phase(1, 0, ecosystem.summarize(output), str(log))
    assert ecosystem._test_outcome(output) is None
    assert ecosystem._completed_test_run(phase) is None


def test_real_custom_unittest_runner_retains_all_failure_kinds(tmp_path: Path) -> None:
    runner = tmp_path / "runtests.py"
    runner.write_text(
        "import sys\nimport unittest\n\n"
        "class Cases(unittest.TestCase):\n"
        "    def test_failure(self):\n        self.fail('fixture')\n"
        "    def test_error(self):\n        raise ValueError('fixture')\n"
        "    @unittest.expectedFailure\n"
        "    def test_unexpected(self):\n        pass\n"
        "result = unittest.TextTestRunner().run(\n"
        "    unittest.defaultTestLoader.loadTestsFromTestCase(Cases))\n"
        "sys.exit(2 if result.errors else 1 if not result.wasSuccessful() else 0)\n"
    )
    phase = ecosystem.run([sys.executable, str(runner)], tmp_path, {}, 20, tmp_path / "run.log")
    assert phase.returncode == 2
    outcome = ecosystem._completed_test_run(phase, (1, 2))
    assert outcome is not None and outcome.collected == 3
    assert ecosystem.failed_tests(phase.log) == {
        f"unittest:test_{name} (__main__.Cases.test_{name})"
        for name in ("failure", "error", "unexpected")
    }


@pytest.mark.parametrize("closing", ["", "FAILED (errors=1)\n", "Ran 1 test in 0.01s\n"])
def test_an_unfinished_unittest_shaped_diagnostic_is_not_a_test_identity(closing: str) -> None:
    assert ecosystem._failed_test_ids("ERROR: unavailable build dependency\n" + closing) == set()

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

"""Keep the audited corpus commands and declared test prerequisites executable.

These contracts come from the pinned projects' test configuration. Review them
alongside any future pin update; matching upstream failures cannot excuse a
missing plugin, fixture, or source tree.
"""

from __future__ import annotations

from pathlib import Path
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest

from scripts import ecosystem_check as ecosystem


def _project(name: str) -> ecosystem.Project:
    projects = ecosystem.load_manifest(ecosystem.REPO / "scripts/ecosystem/manifest.toml", [name])
    assert len(projects) == 1, f"Missing or ambiguous corpus entry: {name}"
    return projects[0]


@pytest.mark.parametrize(
    "name,dependency",
    [
        pytest.param("cheroot", "pytest-cov", id="cheroot-explicit-coverage-plugin"),
        pytest.param("cheroot", "pytest-xdist", id="cheroot-parallel-test-options"),
        pytest.param("cheroot", "pytest-rerunfailures", id="cheroot-strict-flaky-marker"),
        pytest.param("cheroot", "chardet", id="cheroot-requests-test-support"),
        pytest.param("simpy", "pytest-benchmark", id="simpy-benchmark-test-bodies"),
    ],
)
def test_manifest_supplies_declared_test_prerequisites(name: str, dependency: str) -> None:
    # Cheroot explicitly loads coverage, uses xdist/strict flaky markers, and
    # declares requests' chardet support. SimPy's tox environment declares the
    # benchmark fixture. Without these, selected test bodies never execute.
    declared = {canonicalize_name(Requirement(spec).name) for spec in _project(name).deps}
    assert (
        canonicalize_name(dependency) in declared
    ), f"{name} must install {dependency} so its declared test suite can run"


def test_ply_manifest_runs_both_scripts_against_each_source_copy(tmp_path: Path) -> None:
    project = _project("ply")
    command = [part.format(python=sys.executable) for part in project.test]
    imported: list[Path] = []
    for side in ("before", "after"):
        root = tmp_path / side
        package = root / project.package
        package.mkdir(parents=True)
        module = package / "__init__.py"
        module.write_text(f"TREE = {side!r}\n")
        tests = root / "tests"
        tests.mkdir()
        observations = root / "imports.tsv"
        for script in ("testlex.py", "testyacc.py"):
            # Each owned unittest script proves it imported its corresponding
            # tree, even if a different ply distribution happens to be installed.
            (tests / script).write_text(
                "from pathlib import Path\nimport unittest\nimport ply\n\n"
                "class SourceIdentity(unittest.TestCase):\n"
                "    def test_source_identity(self):\n"
                f"        self.assertEqual(ply.TREE, {side!r})\n"
                f"        self.assertEqual(Path(ply.__file__).resolve(), Path({str(module)!r}))\n"
                f"        with Path({str(observations)!r}).open('a') as stream:\n"
                "            stream.write(Path(__file__).name + '\\t'\n"
                "                         + str(Path(ply.__file__).resolve()) + '\\n')\n\n"
                "if __name__ == '__main__':\n    unittest.main()\n"
            )
        phase = ecosystem.run(
            command,
            root,
            ecosystem.base_env(project.pythonpath, Path(sys.executable).parent),
            20,
            root / "tests.log",
        )
        output = Path(phase.log).read_text()
        assert phase.returncode == 0, output
        outcome = ecosystem._completed_test_run(phase, project.failure_exit_codes)
        assert outcome is not None and outcome.collected == 2, output
        records = [line.split("\t") for line in observations.read_text().splitlines()]
        assert records == [["testlex.py", str(module)], ["testyacc.py", str(module)]]
        paths = [Path(record[1]).resolve() for record in records]
        assert paths == [module, module]
        imported.append(paths[0])
    assert imported[0] != imported[1], "Both phases imported the same source tree"

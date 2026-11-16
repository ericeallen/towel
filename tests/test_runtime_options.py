# Copyright 2026 Eric Allen
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

"""Runtime item 3: explicit workers, project defaults, and a usable automatic cap."""

from pathlib import Path

import pytest

from towel import cli
from towel.config import apply_project_defaults
from towel.diagnostics import Settings
from towel.unification.parallel import ParallelEvaluation
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.mark.parametrize("command", ["dry", "preview"])
def test_jobs_reaches_pipeline_options(command: str) -> None:
    """Runtime 3: both commands carry the requested job count into execution."""
    args = [command, "source.py"] + (["output.py"] if command == "dry" else [])
    namespace = cli._build_parser().parse_args([*args, "--jobs", "3"])
    if command == "dry":
        assert cli.DryOptions.from_namespace(namespace).jobs == 3
    else:
        options = cli.PreviewOptions.from_namespace(namespace)
        assert options.jobs == 3
        assert options.dry_options("output.py").jobs == 3


@pytest.mark.parametrize("value", ["0", "-2", "many", "1.5"])
def test_jobs_requires_positive_integer(value: str) -> None:
    """Runtime 3: a malformed pool limit is refused before starting analysis."""
    with pytest.raises(SystemExit) as error:
        cli._build_parser().parse_args(["preview", "source.py", "--jobs", value])
    assert error.value.code == 2


def test_project_jobs_and_explicit_override(tmp_path: Path) -> None:
    """Runtime 3: the automation loader honours jobs and explicit CLI precedence."""
    (tmp_path / "pyproject.toml").write_text("[tool.towel]\njobs = 3\n")
    source = tmp_path / "source.py"
    source.write_text("x = 1\n")
    parser = cli._build_parser()
    for flags, expected in (([], 3), (["--jobs", "4"], 4)):
        argv = ["preview", str(source), *flags]
        parsed = apply_project_defaults(parser, parser.parse_args(argv), argv)
        assert cli.PreviewOptions.from_namespace(parsed).jobs == expected


def test_jobs_override_preserves_environment_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime 3: jobs overrides the environment cap without mutating shared settings."""
    monkeypatch.setenv("TOWEL_WORKERS", "2")
    monkeypatch.setenv("TOWEL_DEBUG_TYPES", "1")
    monkeypatch.setattr(cli, "_SETTINGS", None)
    assert cli._execution_settings(None).workers == 2
    assert cli._execution_settings(3).workers == 3
    assert cli._execution_settings(3).debug_types is True
    assert cli._settings().workers == 2


@pytest.mark.parametrize("cores,memory,expected", [(18, 18, 8), (4, 18, 4), (18, 3, 3), (0, 0, 1)])
def test_automatic_workers_leave_machine_usable(
    monkeypatch: pytest.MonkeyPatch, cores: int, memory: int, expected: int
) -> None:
    """Runtime 3: automatic parallelism obeys the smallest resource cap and eight."""
    monkeypatch.setattr("towel.unification.parallel.os.cpu_count", lambda: cores)
    monkeypatch.setattr(
        ParallelEvaluation, "_workers_that_fit_in_memory", staticmethod(lambda: memory)
    )
    engine = UnificationRefactorEngine(settings=Settings.from_environ({}))
    assert engine._parallel_workers() == expected

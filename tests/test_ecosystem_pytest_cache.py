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

"""The consumer cache fixture remains available with fresh state on every run."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from scripts import ecosystem_check as ecosystem


@pytest.mark.parametrize(
    "arguments,clear,retest_clear",
    [
        (["tests"], True, True),
        (["--cache-clear", "tests"], True, True),
        (["-p", "no:cacheprovider", "tests"], False, False),
        (["-pno:cacheprovider", "tests"], False, False),
        (["-p", "no:cacheprovider", "-pcacheprovider", "tests"], True, True),
        (["-pcacheprovider", "-pno:cacheprovider", "tests"], False, False),
        (["-p", "other_plugin", "tests"], True, True),
        (["-k", "no:cacheprovider", "tests"], True, True),
        # Pytest scans plugin choices after -- too. Narrowing must decline
        # when replacing these selectors would change the plugin context.
        (["--", "-p", "no:cacheprovider"], False, None),
        (["-pno:cacheprovider", "--", "-p", "cacheprovider"], True, None),
        (["-pno:cacheprovider", "--", "tests"], False, False),
        (["-o", "cache_dir=consumer-cache", "--strict-config", "tests"], True, True),
    ],
)
def test_preparation_resets_cache_without_overriding_explicit_plugin_choices(
    arguments: list[str], clear: bool, retest_clear: bool | None
) -> None:
    original = [sys.executable, "-m", "pytest", *arguments]
    prepared = ecosystem._prepare_test_command(original)
    boundary = prepared.index("--") if "--" in prepared else len(prepared)
    assert ("--cache-clear" in prepared[:boundary]) == clear
    assert prepared.count("--cache-clear") == int(clear)
    assert ecosystem._prepare_test_command(prepared) == prepared
    assert original == [sys.executable, "-m", "pytest", *arguments]
    if "--" in arguments:
        assert prepared[boundary:] == arguments[arguments.index("--") :]
    narrowed = ecosystem._retest_command(prepared, ["test_case.py::test_result"])
    if retest_clear is None:
        assert narrowed is None
        return
    assert narrowed is not None
    assert narrowed.count("--cache-clear") == int(retest_clear)


@pytest.mark.parametrize("reenable", [False, True])
def test_pytest_plugin_preparser_also_reads_literal_directory_selections(
    tmp_path: Path, reenable: bool
) -> None:
    plugin = "cacheprovider" if reenable else "no:cacheprovider"
    for directory, name in (("-p", "left"), (plugin, "right")):
        target = tmp_path / directory
        target.mkdir()
        (target / f"test_{name}.py").write_text("def test_passes():\n    pass\n")
    original = [sys.executable, "-m", "pytest", "-q"]
    if reenable:
        original.append("-pno:cacheprovider")
    original += ["--", "-p", plugin]
    prepared = ecosystem._prepare_test_command(original)
    env = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    for label, command in (("original", original), ("prepared", prepared)):
        phase = ecosystem.run(command, tmp_path, env, 20, tmp_path / f"{label}.log")
        assert phase.returncode == 0, Path(phase.log).read_text()
        assert phase.summary == "2 passed"
    assert prepared[prepared.index("--") :] == original[original.index("--") :]
    # An isolated command could run a different plugin context even when it
    # parses successfully. Preserve the full-suite difference in that case.
    assert ecosystem._retest_command(prepared, [f"{plugin}/test_right.py::test_passes"]) is None


def test_manifest_keeps_cache_fixtures_available_in_every_pytest_selection() -> None:
    projects = ecosystem.load_manifest(ecosystem.REPO / "scripts/ecosystem/manifest.toml", [])
    commands = [(p.name, [part.format(python=sys.executable) for part in p.test]) for p in projects]
    pytest_projects = [
        (name, command)
        for name, command in commands
        if ecosystem._pytest_arguments_start(command) is not None
    ]
    assert len(projects) == 141 and len(pytest_projects) == 133
    for name, command in pytest_projects:
        assert "no:cacheprovider" not in command, name
        assert ecosystem._prepare_test_command(command).count("--cache-clear") == 1, name


@pytest.mark.parametrize("disable", [["-p", "no:cacheprovider"], ["-pno:cacheprovider"]])
def test_an_explicit_disabled_cache_provider_still_accepts_strict_config(
    tmp_path: Path, disable: list[str]
) -> None:
    (tmp_path / "test_case.py").write_text(
        "def test_no_cache(pytestconfig):\n"
        "    assert not pytestconfig.pluginmanager.hasplugin('cacheprovider')\n"
    )
    command = ecosystem._prepare_test_command(
        [sys.executable, "-m", "pytest", "--strict-config", *disable]
    )
    result = ecosystem.run(
        command, tmp_path, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}, 20, tmp_path / "tests.log"
    )
    assert result.returncode == 0, Path(result.log).read_text()
    assert result.summary == "1 passed"


@pytest.mark.parametrize("regression", [False, True])
def test_check_project_cannot_copy_cached_results_over_a_behavior_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, regression: bool
) -> None:
    source = tmp_path / "sample"
    source.mkdir()
    original = "def result():\n    return 42\n"
    (source / "package.py").write_text(original)
    (source / "test_case.py").write_text(
        "from package import result\n"
        "def test_result(cache):\n"
        "    previous = cache.get('calculation/result', None)\n"
        "    if previous is None:\n"
        "        previous = result()\n"
        "        cache.set('calculation/result', previous)\n"
        "    assert previous == 42\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    # A bare symlink outside the virtualenv loses its installed pytest. Exec
    # the actual test interpreter while giving the harness a private bin dir.
    python.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
    )
    python.chmod(0o755)
    towel = bin_dir / "towel"
    generated = (
        "def result():\n    return 0\n" if regression else "def result():\n    return 40 + 2\n"
    )
    towel.write_text(
        f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
        "if sys.argv[1:] == ['dry', '--help']:\n"
        "    print('usage: towel dry --cross-module')\n"
        "else:\n"
        f"    Path(sys.argv[3]).write_text({generated!r})\n"
        "    print('Applied 1 refactoring')\n"
    )
    towel.chmod(0o755)
    candidate = ecosystem.Candidate(tmp_path / "candidate.whl", "code-towel", "0", "0", (), (), ())
    installed = ecosystem.ProjectEnvironment(
        python, None, ecosystem.Environment(sys.version, "0", (), ())
    )
    monkeypatch.setattr(ecosystem, "clone", lambda *_: "pinned")
    monkeypatch.setattr(ecosystem, "environment", lambda *_: installed)
    monkeypatch.setattr(ecosystem, "changed", lambda *_: (1, "package.py changed"))
    result = ecosystem.check_project(
        ecosystem.Project("sample", "unused", "pinned", "package.py"), tmp_path, candidate, 30
    )
    assert result.verdict == ("BROKEN" if regression else "PASS")
    assert result.baseline is not None and result.baseline.returncode == 0
    assert result.after is not None and result.after.returncode == int(regression)
    assert (source / "package.py").read_text() == original
    assert (tmp_path / "sample-ready/package.py").read_text() == generated
    if regression:
        assert ecosystem.failed_tests(result.after.log) == {"test_case.py::test_result"}
        evidence = json.loads((tmp_path / "logs/sample-retest.json").read_text())
        assert evidence["before"]["returncode"] == 0
        assert evidence["after"]["returncode"] == 1
        assert "--cache-clear" in evidence["command"]


def test_every_isolated_and_full_retry_starts_with_empty_consumer_cache(tmp_path: Path) -> None:
    sources = [tmp_path / side for side in ("before", "after")]
    initial = []
    command = ecosystem._prepare_test_command([sys.executable, "-m", "pytest", "-q"])
    env = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    for root in sources:
        root.mkdir()
        (root / "pytest.ini").write_text("[pytest]\ncache_dir = consumer-cache\n")
        (root / "test_case.py").write_text(
            "def test_state(cache, pytestconfig):\n"
            "    assert pytestconfig.getini('cache_dir') == 'consumer-cache'\n"
            "    assert cache.get('run/seen', False) is False\n"
            "    cache.set('run/seen', True)\n"
            "def test_other():\n    pass\n"
        )
        phase = ecosystem.run(command, root, env, 20, tmp_path / f"{root.name}-initial.log")
        assert phase.returncode == 0, Path(phase.log).read_text()
        initial.append(phase)
    project = ecosystem.Project("sample", "unused", "pinned", "package.py")
    assert ecosystem._retest_agrees(
        command,
        ["test_case.py::test_state"],
        sources[0],
        sources[1],
        env,
        20,
        tmp_path,
        project,
        initial[0],
        initial[1],
    )
    evidence = json.loads((tmp_path / "sample-retest.json").read_text())
    for side in ("before", "after"):
        assert evidence[side]["returncode"] == 0 and evidence[side]["summary"] == "1 passed"
        assert evidence["full"][side]["returncode"] == 0
        assert evidence["full"][side]["summary"] == "2 passed"
    assert evidence["command"].count("--cache-clear") == 1
    assert evidence["full"]["command"].count("--cache-clear") == 1

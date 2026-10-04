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


def test_tabulate_ci_dependencies_retain_wide_character_and_data_tests() -> None:
    # This pin's .github/workflows/tabulate.yml installs all three optional
    # libraries, and tox's py312-extra repeats them. Without wcwidth, the
    # checker loses its concrete imported bindings and declines a useful helper;
    # numpy/pandas test bodies also skip. These are the selected test context,
    # rather than an invitation to install unrelated project extras.
    project = _project("tabulate")
    assert project.rev == "268615a5c27dc40e5c22454c07b44d5c50410da0"
    requirements = {
        canonicalize_name(requirement.name): requirement
        for requirement in map(Requirement, project.deps)
    }
    assert {"pytest-cov", "numpy", "pandas", "wcwidth"} <= requirements.keys()
    assert str(requirements[canonicalize_name("wcwidth")].specifier) == ">=0.6.0"
    assert not requirements[canonicalize_name("numpy")].specifier
    assert not requirements[canonicalize_name("pandas")].specifier


def test_markdown_it_ci_testing_and_linkify_requirements_are_complete() -> None:
    # The test CI job selects .[testing,linkify]; pytest-timeout makes the
    # configured timeout active instead of silently yielding an unknown option.
    # flit_core and requests are test prerequisites, not docs/plugin-job extras.
    project = _project("markdown-it-py")
    assert project.rev == "a5950caef3434ed83045b43311aefcf7e0578aa3"
    requirements = {
        canonicalize_name(requirement.name): requirement
        for requirement in map(Requirement, project.deps)
    }
    assert {
        "coverage",
        "flit-core",
        "pytest-cov",
        "pytest-regressions",
        "pytest-timeout",
        "requests",
        "linkify-it-py",
    } <= requirements.keys()
    assert str(requirements[canonicalize_name("flit-core")].specifier) == "<5,>=3.4"
    assert str(requirements[canonicalize_name("linkify-it-py")].specifier) == "<3,>=1"
    assert {"psutil", "pytest-benchmark"} <= requirements.keys()
    assert {"sphinx", "mdit-py-plugins", "panflute"}.isdisjoint(requirements)


def test_blinker_test_dependencies_preserve_its_declared_typing_target() -> None:
    # This pin's uv.lock selects pytest 8.3.5 and pytest-asyncio 1.0.0. The
    # corpus installed pytest 9.1.1, whose match syntax makes mypy abort for
    # Blinker's Python 3.9 target before checking the project. Merely accepting
    # the fallback would lose typed coverage. The locked pair restores both
    # declared checkers and all 25 runtime tests; a newer asyncio plugin also
    # requires a newer pytest. Review these constraints with the upstream pin,
    # not by changing this test to bless another checker-failed fallback.
    project = _project("blinker")
    assert project.rev == "c3364059663df1ddce32799d6b1922af89a345f6"
    requirements = {
        canonicalize_name(requirement.name): requirement
        for requirement in map(Requirement, project.deps)
    }
    assert str(requirements[canonicalize_name("pytest")].specifier) == "==8.3.5"
    assert str(requirements[canonicalize_name("pytest-asyncio")].specifier) == "==1.0.0"


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


_CONTEXT_SOURCES = Path(__file__).with_name("ecosystem_context_sources")


def _context_requirements(name: str) -> list[Requirement]:
    """Read actual selected source groups, including recursive project extras."""
    import json

    source = _CONTEXT_SOURCES / name
    index = ecosystem._table(json.loads((_CONTEXT_SOURCES / "index.json").read_text()))
    rows = index["projects"]
    assert isinstance(rows, list)
    row = next(ecosystem._table(value) for value in rows if ecosystem._table(value)["name"] == name)
    assert _project(name).rev == row["declared_pin"]
    assert isinstance(row["pin"], str) and len(row["pin"]) == 40
    data = ecosystem._read_toml(source / "pyproject.toml")
    project = ecosystem._table(data.get("project"))
    project_name = str(project.get("name", name))
    declarer = ecosystem._Declarer(source, ecosystem._canonical(project_name), data)
    requirements: list[str] = []
    selectors = row["selectors"]
    assert isinstance(selectors, list)
    for selector_value in selectors:
        selector = ecosystem._table(selector_value)
        kind, selected = str(selector["kind"]), str(selector["name"])
        if kind in ("group", "extra"):
            declarations = declarer.declared(
                "pinned selected test source",
                groups=(selected,) if kind == "group" else (),
                extras=(selected,) if kind == "extra" else (),
            )
            requirements.extend(
                declaration.requirement
                for declaration in declarations
                if canonicalize_name(Requirement(declaration.requirement).name)
                != canonicalize_name(project_name)
            )
        elif kind == "hatch-env":
            hatch = ecosystem._table(ecosystem._table(data.get("tool")).get("hatch"))
            selected_env = ecosystem._table(ecosystem._table(hatch.get("envs")).get(selected))
            requirements.extend(ecosystem._strings(selected_env["dependencies"]))
        else:
            assert kind == "poetry-legacy"
            poetry = ecosystem._table(ecosystem._table(data.get("tool")).get("poetry"))
            for dependency, version in ecosystem._table(poetry[selected]).items():
                assert isinstance(version, str)
                if version.startswith("^"):
                    lower = version[1:]
                    parts = [int(component) for component in lower.split(".")]
                    while len(parts) < 3:
                        parts.append(0)
                    first = next(index for index, component in enumerate(parts) if component)
                    upper = [*parts[:first], parts[first] + 1, *([0] * (2 - first))]
                    version = f">={lower},<{'.'.join(map(str, upper))}"
                elif version[0].isdigit():
                    version = "==" + version
                requirements.append(dependency + version)
    return list(map(Requirement, requirements))


@pytest.mark.parametrize(
    "name",
    [
        "markdown-it-py",
        "starlette",
        "jinja2",
        "rich",
        "networkx",
        "tenacity",
        "bottle",
        "soupsieve",
        "httpx",
        "towel-main",
    ],
)
def test_manifest_covers_actual_pinned_selected_source_context(name: str) -> None:
    """Read upstream declarations rather than restating a curated dependency list."""
    selected = _context_requirements(name)
    assert selected, name
    declared = list(map(Requirement, _project(name).deps))
    for requirement in selected:
        matching = [
            item
            for item in declared
            if canonicalize_name(item.name) == canonicalize_name(requirement.name)
        ]
        assert matching, (name, str(requirement))
        assert any(
            item.extras >= requirement.extras
            and (not requirement.specifier or item.specifier == requirement.specifier)
            and item.marker == requirement.marker
            for item in matching
        ), (name, str(requirement), list(map(str, matching)))


@pytest.mark.parametrize("name", ["jinja2", "rich", "starlette", "towel-main"])
def test_new_weak_tool_requirements_retain_the_actual_upstream_tool_selection(name: str) -> None:
    import json

    source = _CONTEXT_SOURCES / name
    index = ecosystem._table(json.loads((_CONTEXT_SOURCES / "index.json").read_text()))
    rows = index["projects"]
    assert isinstance(rows, list)
    row = next(ecosystem._table(value) for value in rows if ecosystem._table(value)["name"] == name)
    expected = ecosystem._table(row["pin_sources"])
    actual = ecosystem.tool_pins(Path(sys.executable), source, tuple(expected))
    assert {tool: list(choice) for tool, choice in actual.items()} == expected
    selected = {canonicalize_name(requirement.name) for requirement in _context_requirements(name)}
    declared = list(map(Requirement, _project(name).deps))
    for tool, (_, version) in actual.items():
        if canonicalize_name(tool) in selected:
            assert any(
                canonicalize_name(requirement.name) == canonicalize_name(tool)
                and str(requirement.specifier) == "==" + version
                for requirement in declared
            ), (name, tool, version)

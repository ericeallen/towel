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

"""Selected uv groups expose their checker dependencies without treating all dev groups alike."""

from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest

from scripts import ecosystem_check as ecosystem
from tests.ecosystem_fixtures import candidate_wheel, project_tree, uv_required, wheel


def _configuration(tree: Path, defaults: str = '["dev", "docs"]') -> None:
    with (tree / "pyproject.toml").open("a") as stream:
        stream.write(
            "[tool.uv]\ndefault-groups = " + defaults + "\n"
            "[dependency-groups]\n"
            'dev = ["mypy>=1", {include-group="stubs"}]\n'
            'stubs = ["types-example==1.0"]\n'
            'docs = ["unavailable-documentation"]\n'
        )


def test_selected_checker_group_expands_includes_with_shared_context(tmp_path: Path) -> None:
    _configuration(tmp_path)
    declarations = ecosystem.typing_declarations(tmp_path, None)
    assert [item.requirement for item in declarations] == ["mypy>=1", "types-example==1.0"]
    assert {item.context for item in declarations} == {
        "pyproject.toml [tool.uv] default-groups dev"
    }
    assert all(item.source.endswith(", group dev") for item in declarations)


@pytest.mark.parametrize(
    "selector", ["[]", '["docs"]', '["absent"]', '"all"', "false", "{}", '["dev", 1]']
)
def test_unselected_or_unsupported_defaults_do_not_infer_a_typing_group(
    tmp_path: Path, selector: str
) -> None:
    _configuration(tmp_path, selector)
    assert ecosystem.typing_declarations(tmp_path, None) == []


def test_group_names_alone_do_not_supply_an_implicit_uv_default(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[dependency-groups]\ndev=["mypy", "types-example"]\n')
    assert ecosystem.typing_declarations(tmp_path, None) == []


def test_selected_group_without_checker_does_not_install_its_dependencies(tmp_path: Path) -> None:
    _configuration(tmp_path)
    path = tmp_path / "pyproject.toml"
    path.write_text(path.read_text().replace('"mypy>=1"', '"not-mypy>=1"'))
    assert ecosystem.typing_declarations(tmp_path, None) == []


def test_group_cycle_and_duplicate_selection_are_finite_and_inputs_immutable(
    tmp_path: Path,
) -> None:
    _configuration(tmp_path, '["dev", "dev"]')
    path = tmp_path / "pyproject.toml"
    path.write_text(
        path.read_text().replace(
            '"types-example==1.0"', '"types-example==1.0", {include-group="dev"}'
        )
    )
    config = ecosystem._read_toml(path)
    original = copy.deepcopy(config)
    declarer = ecosystem._Declarer(tmp_path, None, config)
    assert [x.requirement for x in ecosystem._uv_default_group_declarations(declarer)] == [
        "mypy>=1",
        "types-example==1.0",
    ]
    assert config == original


def test_checker_and_dependency_markers_are_preserved_and_evaluated(tmp_path: Path) -> None:
    _configuration(tmp_path)
    path = tmp_path / "pyproject.toml"
    path.write_text(
        path.read_text()
        .replace(
            '"mypy>=1"',
            '''"mypy==1; python_version < '2'", "mypy==2; python_version >= '3'"''',
        )
        .replace(
            '"types-example==1.0"',
            '''"types-example==1.0; python_version < '2'"''',
        )
    )
    declarations = ecosystem.typing_declarations(tmp_path, None)
    assert [
        x.applies
        for x in ecosystem._parsed_declarations(Path(sys.executable), declarations)
        if x is not None
    ] == [False, True, False]
    choice = ecosystem._typing_tool_choices(Path(sys.executable), tmp_path, ["mypy"])["mypy"]
    assert choice.requirements == ("mypy==2; python_version >= '3'",)


def test_earlier_explicit_typing_context_precedes_uv_defaults(tmp_path: Path) -> None:
    _configuration(tmp_path)
    (tmp_path / "tox.ini").write_text("[testenv:type]\ncommands=mypy\ndeps=mypy==2\n")
    choice = ecosystem._typing_tool_choices(Path(sys.executable), tmp_path, ["mypy"])["mypy"]
    assert choice.source == "typing context: tox.ini [testenv:type]"
    assert choice.requirements == ("mypy==2",)


def test_default_group_reaches_checker_in_own_extra_without_losing_markers(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="example"\n'
        '[project.optional-dependencies]\nci=["mypy>=1", "types-example"]\n'
        '[tool.uv]\ndefault-groups=["dev"]\n'
        "[dependency-groups]\ndev=[\"example[ci]; python_version >= '3'\"]\n"
    )
    declarations = ecosystem.typing_declarations(tmp_path, None)
    assert [item.requirement for item in declarations] == [
        "example[ci]; python_version >= '3'",
        "mypy>=1",
        "types-example",
    ]
    assert all(
        item.prerequisites == ("example[ci]; python_version >= '3'",) for item in declarations[1:]
    )


@uv_required
def test_uv_selected_group_installs_its_stub_at_the_project_lock_pin(
    tmp_path: Path, offline_index: Path
) -> None:
    work = tmp_path / "work"
    tree = project_tree(work / "sample", "sample", "sample", "source")
    _configuration(tree)
    (tree / "uv.lock").write_text('[[package]]\nname="types-example"\nversion="1.0"\n')
    wheel(offline_index, "types-example", "1.0", {"example.pyi": "value: int\n"})
    result = ecosystem.environment(
        ecosystem.Project("sample", "unused", "pinned", "sample"),
        work,
        tree,
        ecosystem.load_candidate(candidate_wheel(tmp_path / "dist")),
        tmp_path / "log",
    )
    assert result.record.typing.added == ("types-example==1.0",)
    stub = next(
        item for item in result.record.typing.requirements if item.declared == "types-example==1.0"
    )
    assert stub.pinned_by == "uv.lock" and stub.source.endswith("default-groups dev, group dev")
    assert not result.record.typing.removed
    assert (
        ecosystem.probe_environment(result.python, ["types-example"]).versions["types-example"]
        == "1.0"
    )
    fingerprint = ecosystem._selection_inputs(tree)
    path = tree / "pyproject.toml"
    path.write_text(path.read_text().replace('["dev", "docs"]', '["docs"]'))
    assert ecosystem._selection_inputs(tree) != fingerprint

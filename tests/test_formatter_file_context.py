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

"""Generated snippets obey the policy of every modified source file."""

from pathlib import Path
from typing import Optional

import pytest

from tests.test_cli_integration import invoke
from tests.test_generated_code_formatting import DUPLICATED_STRINGS, _evaluate
from towel.formatting import (
    SnippetFormatter,
    formatter_factory_for_project,
    formatting_repeatability,
)
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.rehearing import renderer_context


def _dry(target: Path, output: Path, *, formatting: bool = True) -> None:
    flags = [] if formatting else ["--no-format"]
    result = invoke(
        [
            "dry",
            str(target),
            str(output),
            "--no-interactive",
            "--no-types",
            "--progress",
            "none",
            *flags,
        ]
    )
    assert result.status == 0, result


@pytest.mark.parametrize("tool", ["ruff", "black"])
def test_direct_and_directory_respect_each_modified_files_exclusion(
    tmp_path: Path, tool: str
) -> None:
    pytest.importorskip(tool)
    root = tmp_path / "project"
    root.mkdir()
    config = (
        '[tool.ruff]\nexclude=["m.py"]\n[tool.ruff.lint]\nselect=[]\n'
        if tool == "ruff"
        else '[tool.black]\nforce-exclude="m\\\\.py$"\n'
    )
    (root / "pyproject.toml").write_text(config)
    for filename in ("m.py", "included.py"):
        (root / filename).write_text(DUPLICATED_STRINGS)
    direct = tmp_path / "direct.py"
    directory = tmp_path / "directory"
    _dry(root / "m.py", direct)
    _dry(root, directory)
    excluded = (directory / "m.py").read_text()
    included = (directory / "included.py").read_text()
    assert "__extracted_func" in excluded and "__extracted_func" in included
    assert "'Mr. '" in direct.read_text() and "'Mr. '" in excluded
    assert '"Mr. "' in included
    assert _evaluate(excluded) == _evaluate(included) == _evaluate(DUPLICATED_STRINGS)


@pytest.mark.parametrize("tool", ["ruff", "black"])
def test_nested_configuration_controls_its_own_generated_calls(tmp_path: Path, tool: str) -> None:
    pytest.importorskip(tool)
    root = tmp_path / "project"
    nested = root / "nested"
    nested.mkdir(parents=True)
    section = "tool.ruff.format" if tool == "ruff" else "tool.black"
    outer = 'quote-style="double"' if tool == "ruff" else "skip-string-normalization=false"
    inner = 'quote-style="single"' if tool == "ruff" else "skip-string-normalization=true"
    (root / "pyproject.toml").write_text(
        f"[{section}]\n{outer}\n" + ("[tool.ruff.lint]\nselect=[]\n" if tool == "ruff" else "")
    )
    (nested / "pyproject.toml").write_text(
        f"[{section}]\n{inner}\n" + ("[tool.ruff.lint]\nselect=[]\n" if tool == "ruff" else "")
    )
    (root / "top.py").write_text(DUPLICATED_STRINGS)
    (nested / "m.py").write_text(DUPLICATED_STRINGS)
    output = tmp_path / "out"
    _dry(root, output)
    assert '"Mr. "' in (output / "top.py").read_text()
    assert "'Mr. '" in (output / "nested" / "m.py").read_text()
    plain = tmp_path / "plain"
    _dry(root, plain, formatting=False)
    assert "'Mr. '" in (plain / "top.py").read_text()
    assert "'Mr. '" in (plain / "nested" / "m.py").read_text()


def test_per_file_factory_receives_each_destination_and_preserves_public_callback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "m.py"
    path.write_text(DUPLICATED_STRINGS)
    seen: list[str] = []

    def factory(file_path: str) -> Optional[SnippetFormatter]:
        seen.append(file_path)
        return lambda text: text

    engine = UnificationRefactorEngine(min_lines=2, snippet_formatter_for_file=factory)
    proposal = engine.analyze_file(str(path))[0]
    result = engine.apply_refactoring(str(path), proposal)
    assert len(seen) >= 3 and set(seen) == {str(path)}
    assert _evaluate(result) == _evaluate(DUPLICATED_STRINGS)
    assert renderer_context(factory, [str(path)]) is None
    with pytest.raises(ValueError, match="either snippet_formatter"):
        UnificationRefactorEngine(
            snippet_formatter=lambda text: text, snippet_formatter_for_file=factory
        )


def test_builtin_per_file_factory_tracks_selected_worker_lifetime(
    tmp_path: Path,
) -> None:
    pytest.importorskip("black")
    (tmp_path / "pyproject.toml").write_text("[tool.black]\n")
    path = tmp_path / "m.py"
    path.write_text("value = 1\n")
    factory = formatter_factory_for_project(tmp_path)
    formatter = factory(str(path))
    assert formatter is not None and formatter("value=1") == "value = 1"
    capability = formatting_repeatability(factory)
    assert capability is not None and capability.workers
    worker = capability.workers[0]()
    assert worker is not None
    worker.close()
    assert formatting_repeatability(factory) is None


def test_cross_module_rendering_supplies_each_helpers_and_call_sites_destination(
    tmp_path: Path,
) -> None:
    from tests.test_cross_module_opt_in import _package

    root = _package(tmp_path / "project")
    paths = [str(root / "zzshared" / name) for name in ("first.py", "second.py")]
    seen: set[str] = set()

    def factory(file_path: str) -> Optional[SnippetFormatter]:
        seen.add(file_path)
        return lambda text: text

    engine = UnificationRefactorEngine(
        min_lines=3, cross_module_helpers=True, snippet_formatter_for_file=factory
    )
    proposals = engine.analyze_files(paths)
    assert proposals
    files = engine.apply_refactoring_multi_file(proposals[0])
    assert set(files) == set(paths)
    assert seen == set(paths)
    assert any("def __extracted_func" in source for source in files.values())


def test_builtin_factory_cannot_certify_a_custom_selected_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from towel import formatting
    from towel.project_tools import ToolChoice

    monkeypatch.setattr(
        formatting,
        "formatter_for_project",
        lambda path: ToolChoice(lambda text: text, "custom"),
    )
    factory = formatter_factory_for_project(tmp_path)
    assert factory(str(tmp_path / "m.py")) is not None
    assert formatting_repeatability(factory) is None

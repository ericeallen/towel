# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Other program files can replace a function's definition-time builtin mapping."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Callable, Iterator

import pytest

from tests.test_definition_time_builtins import BODY, _source
from tests.test_parameterize_builtins import _run, _write
from towel.unification import builtin_mapping_writes as writes
from towel.unification import refactor_engine
from towel.unification.exceptions import UnparsedProgramError
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("excluded", [False, True])
@pytest.mark.parametrize("parameterized_target", [False, True])
def test_circular_import_writer_preserves_captured_mapping(
    tmp_path: Path, flag: bool, excluded: bool, parameterized_target: bool
) -> None:
    replacement = "import target\n" + (
        "def configure(receiver):\n"
        '    receiver.__builtins__ = {"custom": lambda data: 100}\n'
        "configure(target)\n"
        if parameterized_target
        else 'target.__builtins__ = {"custom": lambda data: 100}\n'
    )
    source = _source(before="import replacement\n").replace("len(data)", "custom(data)")
    _write(
        tmp_path,
        {"target.py": source, "replacement.py": replacement, "driver.py": "import target\n"},
    )
    expected = _run(tmp_path, "driver.py")
    assert expected == "306 306\n"
    engine = UnificationRefactorEngine(
        min_lines=3,
        max_parameters=6,
        parameterize_builtins=flag,
        excluded_directories=("replacement.py",) if excluded else (),
    )
    final, applied, _ = engine.refactor_to_fixed_point(str(tmp_path / "target.py"), progress="none")
    (tmp_path / "target.py").write_text(final)
    assert _run(tmp_path, "driver.py") == expected
    assert applied == 0
    assert "builtin_mapping_replaced" in engine.declined_pairs
    assert (tmp_path / "replacement.py").read_text() == replacement


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("receiver.__builtins__ = {}\n", True),
        ("del receiver.__builtins__\n", True),
        ("receiver.__builtins__ += other\n", True),
        ("receiver.__builtins__: object = {}\n", True),
        ("for receiver.__builtins__ in mappings:\n    pass\n", True),
        ("with context() as receiver.__builtins__:\n    pass\n", True),
        ("receiver.__builtins__, other = mappings\n", True),
        ("receiver.__ｂuiltins__ = {}\n", True),
        ("mapping = receiver.__builtins__\n", False),
        ("receiver.__builtins__: object\n", False),
        ("receiver.other = {}\n", False),
        ('setattr(receiver, "__builtins__", {})\n', False),
    ],
)
def test_project_fact_tracks_ordinary_attribute_store_contexts(
    tmp_path: Path, source: str, expected: bool
) -> None:
    _write(tmp_path, {"writer.py": source})
    assert writes.project_may_replace_builtin_mapping(tmp_path) is expected


def test_project_fact_is_unknown_past_the_file_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, {"a.py": "VALUE = 1\n", "b.py": "VALUE = 2\n"})
    monkeypatch.setattr(writes, "MAXIMUM_FILES", 1)
    assert writes.project_may_replace_builtin_mapping(tmp_path)


def test_unreadable_project_file_cannot_prove_no_mapping_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "writer.py"
    path.write_text("VALUE = 1\n")
    original = Path.read_bytes

    def unreadable(candidate: Path) -> bytes:
        if candidate == path:
            raise PermissionError("unreadable writer")
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    assert writes.project_may_replace_builtin_mapping(tmp_path)


def test_unreadable_project_directory_cannot_prove_no_mapping_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable_walk(
        root: Path, *, onerror: Callable[[OSError], None] | None = None
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        assert onerror is not None
        onerror(PermissionError("unreadable program directory"))
        yield str(root), [], []

    monkeypatch.setattr(os, "walk", unreadable_walk)
    assert writes.project_may_replace_builtin_mapping(tmp_path)


def test_only_excluded_unparsed_writer_is_treated_as_nonprogram_input(tmp_path: Path) -> None:
    _write(tmp_path, {"writer.py": "receiver.__builtins__ = (\n"})
    with pytest.raises(UnparsedProgramError):
        writes.project_may_replace_builtin_mapping(tmp_path)
    assert not writes.project_may_replace_builtin_mapping(tmp_path, frozenset({"writer.py"}))


def test_new_writer_rechecks_unchanged_pairs_and_cached_cluster_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        "def first(data):\n" + BODY + "def second(data):\n" + BODY + "def third(data):\n" + BODY
    )
    _write(tmp_path, {"target.py": source, "writer.py": "VALUE = 1\n"})
    target = tmp_path / "target.py"
    writer = tmp_path / "writer.py"
    engine = UnificationRefactorEngine(min_lines=3, max_parameters=6, parameterize_builtins=True)
    scans: list[Path] = []
    original = writes.project_may_replace_builtin_mapping

    def observed(root: Path, excluded_names: frozenset[str]) -> bool:
        scans.append(root)
        return original(root, excluded_names)

    monkeypatch.setattr(refactor_engine, "project_may_replace_builtin_mapping", observed)
    first = engine.analyze_file(str(target))
    assert first and len(engine._cluster_scan_cache) > 0
    third = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "third"
    )

    def includes_third() -> bool:
        return any(
            replacement.line_range[0] > third.lineno
            for proposal in first
            for replacement in proposal.replacements
        )

    assert includes_third()
    assert scans == [tmp_path]
    writer.write_text("def configure(receiver):\n    receiver.__builtins__ = {}\n")
    second = engine.analyze_files([str(target)], changed_files=frozenset({str(writer)}))
    assert not second
    assert "builtin_mapping_replaced" in engine.declined_pairs
    # Cached membership admitted all three sites before the project fact
    # changed. It is no longer reusable, even though target.py is unchanged.
    assert not engine._cluster_scan_cache
    assert scans == [tmp_path, tmp_path]
    assert target.read_text() == source

    writer.write_text("VALUE = 1\n")
    first = engine.analyze_files([str(target)], changed_files=frozenset({str(writer)}))
    assert first and includes_third()
    assert scans == [tmp_path, tmp_path, tmp_path]


@pytest.mark.parametrize("flag", [False, True])
def test_project_mapping_writer_keeps_lexical_extraction_useful(tmp_path: Path, flag: bool) -> None:
    source = (
        "def first(data, custom):\n"
        + BODY
        + "def second(data, custom):\n"
        + BODY
        + "print(first([1], lambda data: 7), second([1], lambda data: 8))\n"
    ).replace("len(data)", "custom(data)")
    _write(
        tmp_path,
        {
            "target.py": source,
            "writer.py": "def configure(receiver):\n    receiver.__builtins__ = {}\n",
        },
    )
    expected = _run(tmp_path, "target.py")
    assert expected == "27 30\n"
    engine = UnificationRefactorEngine(min_lines=3, max_parameters=6, parameterize_builtins=flag)
    final, applied, _ = engine.refactor_to_fixed_point(str(tmp_path / "target.py"), progress="none")
    (tmp_path / "target.py").write_text(final)
    assert applied > 0
    assert _run(tmp_path, "target.py") == expected


def test_new_project_marker_refreshes_the_scanned_scope(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "inner/target.py": _source(),
            "writer.py": "def configure(receiver):\n    receiver.__builtins__ = {}\n",
        },
    )
    target = tmp_path / "inner/target.py"
    engine = UnificationRefactorEngine(min_lines=3, max_parameters=6)
    assert engine.analyze_file(str(target))
    (tmp_path / "pyproject.toml").write_text('[project]\nname="pkg"\nversion="0"\n')
    assert not engine.analyze_file(str(target))
    assert "builtin_mapping_replaced" in engine.declined_pairs

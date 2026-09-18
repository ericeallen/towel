"""Behavioral regressions for release-critical filesystem and cache defects."""

from pathlib import Path

import pytest

from towel.cli import _rename_function_in_directory
from towel.unification.pipeline import run_pipeline
from towel.unification.refactor_engine import UnificationRefactorEngine


def test_analysis_observes_external_edits(tmp_path: Path) -> None:
    path = tmp_path / "example.py"
    body = "    a = x + 1\n    b = a * 2\n    c = b + 3\n    return c\n"
    path.write_text("def first(x):\n" + body + "\ndef second(x):\n" + body)
    assert run_pipeline([str(path)], engine=UnificationRefactorEngine(), progress="none")
    path.write_text("def unrelated():\n    return None\n")
    assert run_pipeline([str(path)], engine=UnificationRefactorEngine(), progress="none") == []


def test_directory_api_rejects_nested_output_before_creation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="contain"):
        UnificationRefactorEngine().refactor_directory_to_fixed_point(
            str(source), str(source / "output"), progress="none"
        )
    assert not (source / "output").exists()


def test_discovery_ignores_symlinked_python_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    (source / "linked.py").symlink_to(outside)
    assert UnificationRefactorEngine()._find_python_files(str(source)) == []
    assert UnificationRefactorEngine()._find_python_files(str(source), False) == []


def test_rename_preserves_text_and_updates_bare_references(tmp_path: Path) -> None:
    path = tmp_path / "example.py"
    path.write_text(
        "def __extracted_func_0():\n    return 1\n"
        "# __extracted_func_0()\n"
        'text = "__extracted_func_0()"\n'
        "callback = __extracted_func_0\n"
        "answer = __extracted_func_0 ()\n"
    )
    assert (
        _rename_function_in_directory(tmp_path, "__extracted_func_0", "answer_function", False) == 3
    )
    content = path.read_text()
    assert "# __extracted_func_0()" in content
    assert 'text = "__extracted_func_0()"' in content
    assert "callback = answer_function" in content
    assert "answer = answer_function ()" in content


def test_rename_rejects_keywords_and_collisions_without_writes(tmp_path: Path) -> None:
    path = tmp_path / "example.py"
    source = "def helper():\n    return 1\nexisting = 2\n"
    path.write_text(source)
    for new_name in ("class", "existing"):
        with pytest.raises(ValueError):
            _rename_function_in_directory(tmp_path, "helper", new_name, False)
        assert path.read_text() == source


def test_helper_available_during_module_initialization(tmp_path: Path) -> None:
    path = tmp_path / "example.py"
    body = "    a = x + 1\n    b = a * 2\n    c = b + 3\n    return c\n"
    source = '"""Module docs."""\nfrom __future__ import annotations\n'
    source += "def first(x):\n" + body + "\ndef second(x):\n" + body + "\nRESULT = first(1)\n"
    path.write_text(source)
    engine = UnificationRefactorEngine()
    proposals = engine.analyze_file(str(path))
    assert [p.description for p in proposals] == [
        "Reuse first (example.py) for duplicated code in second"
    ]
    namespace: dict[str, object] = {}
    exec(engine.apply_refactoring(str(path), proposals[0]), namespace)
    assert namespace["RESULT"] == 7


def test_module_data_is_not_snapshotted_across_callbacks(tmp_path: Path) -> None:
    path = tmp_path / "example.py"
    body = "    before = value\n    update()\n    after = value\n    return before, after\n"
    source = "value = 1\ndef update():\n    global value\n    value += 1\n"
    source += "def first():\n" + body + "def second():\n" + body
    path.write_text(source)
    assert UnificationRefactorEngine().analyze_file(str(path)) == []


def test_global_and_local_with_same_spelling_are_not_conflated(tmp_path: Path) -> None:
    path = tmp_path / "example.py"
    body = "    x = value + 1\n    y = x * 2\n    z = y + 3\n    return z\n"
    path.write_text("value = 10\ndef first():\n" + body + "def second(value):\n" + body)
    # min_lines=4 makes the whole four-line block the only candidate; that block
    # reads ``value``, which is a module global in ``first`` and a parameter in
    # ``second``, so it must not be extracted. (At the default three-line minimum
    # the engine instead extracts the trailing ``y = x * 2; z = y + 3; return z``,
    # which never reads ``value`` and is safe -- a different, valid proposal.)
    assert UnificationRefactorEngine(min_lines=4).analyze_file(str(path)) == []


def test_cross_file_global_declarations_are_not_relocated(tmp_path: Path) -> None:
    body = "    global count\n    count += 1\n    value = count * 2\n    result = value + 3\n    return result\n"
    paths = [tmp_path / "a.py", tmp_path / "b.py"]
    for path, initial in zip(paths, (10, 100)):
        path.write_text(f"count = {initial}\ndef increment():\n" + body)
    assert UnificationRefactorEngine().analyze_files([str(path) for path in paths]) == []


def test_file_qualified_json_rename_is_applied(tmp_path: Path) -> None:
    from towel.cli import _apply_rename_file

    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    source = "def helper():\n    return 1\n"
    first.write_text(source)
    second.write_text(source)
    mappings = tmp_path / "names.json"
    mappings.write_text('{"first.py:helper": "chosen_name"}')
    _apply_rename_file(tmp_path, [], mappings, False)
    assert "def chosen_name" in first.read_text()
    assert second.read_text() == source

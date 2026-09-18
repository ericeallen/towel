"""Imports inside a block: they bind names, and what they import cannot be renamed."""

from __future__ import annotations

import ast
import pathlib

from tests.test_helpers import function_def
from towel.unification.assignment_analyzer import (
    _collect_block_binding_stats,
    analyze_assignments,
)
from towel.unification.instantiation import _alpha_normalize
from towel.unification.unifier import Unifier
from towel.unification.substitution import Substitution


def test_imports_inside_a_block_are_bindings() -> None:
    function = function_def(
        "def f():\n"
        "    import json\n"
        "    import os.path\n"
        "    from a import b as c\n"
        "    return json, os, c\n"
    )
    reassignments = analyze_assignments(function)
    bound, reassigned = _collect_block_binding_stats(function.body[:3], reassignments)
    assert bound == {"json", "os", "c"}
    assert reassigned == set()


def test_an_import_after_an_assignment_is_a_reassignment() -> None:
    function = function_def("def f():\n    json = None\n    import json\n    return json\n")
    reassignments = analyze_assignments(function)
    bound, reassigned = _collect_block_binding_stats(function.body[1:2], reassignments)
    assert bound == set()
    assert reassigned == {"json"}


def test_import_aliases_are_never_parameterized() -> None:
    first = ast.parse("from m import x").body[0]
    second = ast.parse("from m import y").body[0]
    assert isinstance(first, ast.ImportFrom) and isinstance(second, ast.ImportFrom)
    assert (
        Unifier()._try_parameterize([first.names[0], second.names[0]], Substitution(), [0, 1])
        is False
    )
    assert Unifier().unify_blocks([[first], [second]], [{}, {}]) is None


def _normalized(source: str) -> str:
    return ast.dump(_alpha_normalize(ast.parse(source)))


def test_alpha_normalization_keeps_imported_names_but_renames_as_targets() -> None:
    # What is imported is part of the meaning; the check must see the difference.
    assert _normalized("from m import x\nprint(x)\n") != _normalized("from m import y\nprint(y)\n")
    assert _normalized("import a.b\nprint(a)\n") != _normalized("import c.b\nprint(c)\n")
    # A name introduced with ``as`` is the block's own binder and may differ.
    assert _normalized("import a as x\nprint(x)\n") == _normalized("import a as y\nprint(y)\n")
    assert _normalized("from m import v as x\nprint(x)\n") == _normalized(
        "from m import v as y\nprint(y)\n"
    )


def test_annotations_in_function_bodies_are_inert() -> None:
    from towel.unification.scope_analyzer import ScopeAnalyzer

    source = "def f(items):\n    total: Money = 0\n    items[0]: Count = 1\n    return total\n"
    tree = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    assert "Money" not in analyzer.free_variables(function.body[:2])
    assert "Count" not in analyzer.free_variables(function.body[:2])
    first = ast.parse("total: Money = 0").body[0]
    second = ast.parse("total: Cents = 0").body[0]
    substitution = Unifier().unify_blocks([[first], [second]], [{}, {}])
    assert substitution is not None
    assert substitution.param_expressions == {}
    assert _normalized("x: A = 1\nprint(x)\n") == _normalized("x: B = 1\nprint(x)\n")


def test_directory_mode_can_leave_named_directories_out(tmp_path) -> None:
    from towel.unification.refactor_engine import UnificationRefactorEngine

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "tests").mkdir()
    (tmp_path / "pkg" / "tests" / "test_a.py").write_text("y = 2\n")
    (tmp_path / "pkg" / "tests.py").write_text("z = 3\n")
    found = UnificationRefactorEngine(excluded_directories=("tests",))._find_python_files(
        str(tmp_path / "pkg")
    )
    names = sorted(pathlib.Path(path).relative_to(tmp_path / "pkg").as_posix() for path in found)
    # The directory is skipped; a module that merely shares the name is kept.
    assert names == ["a.py", "tests.py"]

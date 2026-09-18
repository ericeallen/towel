"""The engine's node-keyed caches let a dropped tree go, and the digests come from the parse."""

from __future__ import annotations

import ast
import contextlib
import gc
import hashlib
import io
import weakref
from pathlib import Path

from towel.unification.refactor_engine import UnificationRefactorEngine

FIRST = """
def alpha(items):
    total = 0
    for item in items:
        total += item
    print(total)
    return total


def beta(values):
    total = 0
    for value in values:
        total += value
    print(total)
    return total
"""

SECOND = FIRST.replace("print(total)", "print(total, 'again')")


def _analyze(engine: UnificationRefactorEngine, path: Path) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        engine.analyze_files([str(path)], progress="none")


def test_structural_ids_do_not_pin_a_reparsed_tree(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(FIRST)
    engine = UnificationRefactorEngine(min_lines=3)
    _analyze(engine, path)
    # One entry per block-start statement: each function's def plus its two
    # three-line-or-longer suffixes (the assignment and the loop).
    assert len(engine._structural_ids) == 6
    old_functions = [weakref.ref(function) for function in list(engine._function_paths.keys())]
    assert old_functions

    path.write_text(SECOND)
    _analyze(engine, path)
    gc.collect()
    assert all(reference() is None for reference in old_functions)
    # Every remaining entry belongs to the tree now under analysis.
    current = engine.analysis_session.analyze_module(str(path)).module.tree
    current_nodes = {id(node) for node in ast.walk(current)}
    assert len(engine._structural_ids) == 6
    assert all(id(node) in current_nodes for node in engine._structural_ids)


def test_module_digest_is_the_parse_time_digest(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(FIRST)
    engine = UnificationRefactorEngine(min_lines=3)
    analysis = engine.analysis_session.analyze_module(str(path))
    expected = hashlib.sha256(FIRST.encode("utf-8")).hexdigest()
    assert analysis.module.source_digest == expected
    assert {artifact.module_digest for artifact in analysis.functions} == {expected}
    _analyze(engine, path)
    assert {engine._module_digest(artifact.node) for artifact in analysis.functions} == {expected}

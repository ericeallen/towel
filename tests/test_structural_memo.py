"""Positional storage of unification results survives re-parsing."""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Sequence

from towel.unification.structural_memo import (
    load_substitution,
    path_map,
    resolve_path,
    store_substitution,
    structural_id,
)
from towel.unification.substitution import Substitution

Unification = tuple[Sequence[Sequence[ast.AST]], Substitution, list[dict[str, str]]]

SOURCE = (Path(__file__).parent / "hostile_cases" / "r86_annotated_assignment_live.py").read_text()


def _blocks(tree: ast.Module) -> list[list[ast.AST]]:
    return [list(function.body) for function in tree.body if isinstance(function, ast.FunctionDef)]


def test_structural_id_ignores_positions_and_sees_content() -> None:
    blocks = _blocks(ast.parse(SOURCE))
    shifted = _blocks(ast.parse("\n\n\n" + SOURCE))
    assert structural_id(blocks[0]) == structural_id(shifted[0])
    assert structural_id(blocks[0]) != structural_id(blocks[1])


def test_every_node_has_a_resolvable_path() -> None:
    block = _blocks(ast.parse(SOURCE))[0]
    paths = path_map(block)
    for node, path in paths.items():
        assert resolve_path(block, path) is node


def _engine_unification(tmp_path: Path) -> Unification:
    """A genuine unification captured from the engine on SOURCE, with its blocks."""
    import contextlib
    import io

    from towel.unification.refactor_engine import UnificationRefactorEngine

    path = tmp_path / "m.py"
    path.write_text(SOURCE)
    engine = UnificationRefactorEngine(min_lines=3)
    captured: list[Unification] = []
    original = engine.unifier.unify_blocks

    def capture(blocks, renames):
        result = original(blocks, renames)
        if result is not None and not captured:
            captured.append((blocks, result, [dict(mapping) for mapping in renames]))
        return result

    engine.unifier.unify_blocks = capture  # type: ignore[method-assign]
    with contextlib.redirect_stdout(io.StringIO()):
        engine.analyze_files([str(path)], progress="none")
    assert captured, "SOURCE must unify inside the engine"
    return captured[0]


def test_stored_substitution_rehydrates_onto_a_reparsed_block(tmp_path) -> None:
    blocks, substitution, renames = _engine_unification(tmp_path)
    stored = store_substitution(substitution, blocks, renames)

    reparsed = [copy.deepcopy(block) for block in blocks]
    loaded, loaded_renames = load_substitution(stored, reparsed)
    assert loaded_renames == renames
    assert loaded.mappings == substitution.mappings
    assert loaded.function_params == substitution.function_params
    assert substitution.param_expressions, "the capture must have parameters"
    for name, expressions in substitution.param_expressions.items():
        loaded_expressions = loaded.param_expressions[name]
        assert [index for index, _ in loaded_expressions] == [index for index, _ in expressions]
        for (index, original), (_, replacement) in zip(expressions, loaded_expressions):
            # The rehydrated node is a node of the new block, not of the old one,
            # and it denotes the same expression.
            assert replacement in path_map(reparsed[index])
            assert replacement not in path_map(blocks[index])
            assert ast.dump(replacement) == ast.dump(original)
    # Loading again yields independent containers.
    again, _ = load_substitution(stored, reparsed)
    again.params_used_as_callee.add("x")
    assert "x" not in loaded.params_used_as_callee


def test_store_is_stable_under_deep_copy_of_the_blocks(tmp_path) -> None:
    blocks, substitution, renames = _engine_unification(tmp_path)
    stored = store_substitution(substitution, blocks, renames)
    copied = [copy.deepcopy(block) for block in blocks]
    loaded, _ = load_substitution(stored, copied)
    assert store_substitution(loaded, copied, renames) == stored


def test_structural_id_of_an_int_too_wide_for_decimal_conversion() -> None:
    # 16,000 bits: 4,817 decimal digits, more than an interpreter converts by
    # default; the key must neither raise nor confuse neighbouring widths.
    wide = ast.parse("x = " + "0x" + "f" * 4000).body
    wider = ast.parse("x = " + "0x" + "f" * 4001).body
    assert structural_id(wide) == structural_id(copy.deepcopy(wide))
    assert structural_id(wide) != structural_id(wider)

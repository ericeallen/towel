"""Unexpected compiler failures cannot masquerade as no refactoring opportunities."""

from pathlib import Path
from unittest.mock import patch

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.mark.parametrize(
    "component,method",
    [
        ("unifier", "unify_blocks"),
        ("extractor", "extract_function"),
        ("extractor", "generate_call"),
    ],
)
def test_unexpected_engine_error_propagates(tmp_path: Path, component: str, method: str) -> None:
    source = tmp_path / "example.py"
    source.write_text(
        "def first(x):\n    y=x+1\n    z=y*2\n    return z\n\ndef second(x):\n    y=x+1\n    z=y*2\n    return z\n"
    )
    engine = UnificationRefactorEngine(min_lines=3)
    assert engine.analyze_files([str(source)], progress="none")
    with patch.object(
        getattr(engine, component), method, side_effect=RuntimeError("invariant failed")
    ):
        with pytest.raises(RuntimeError, match="invariant failed"):
            engine.analyze_files([str(source)], progress="none")
    assert not engine._signed_block_cache

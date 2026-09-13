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
    # An unchanged file is served from the engine's caches, so the fixed-point
    # loop's invalidation is what makes the component run again.
    with patch.object(
        getattr(engine, component), method, side_effect=RuntimeError("invariant failed")
    ):
        with pytest.raises(RuntimeError, match="invariant failed"):
            engine.analyze_files([str(source)], progress="none", invalidate_paths=[str(source)])
    # The failure leaves the engine usable: the next analysis succeeds.
    assert engine.analyze_files([str(source)], progress="none", invalidate_paths=[str(source)])

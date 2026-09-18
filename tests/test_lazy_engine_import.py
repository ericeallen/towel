"""The package exposes the engine without loading it for the analysis modules."""

from __future__ import annotations

import subprocess
import sys

import pytest


def test_the_engine_is_reachable_from_the_package() -> None:
    from towel.unification import UnificationRefactorEngine
    from towel.unification.refactor_engine import UnificationRefactorEngine as direct

    assert UnificationRefactorEngine is direct


def test_other_attributes_are_still_errors() -> None:
    import towel.unification

    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        towel.unification.nope


def test_importing_an_analysis_module_leaves_the_engine_unloaded() -> None:
    probe = (
        "import sys\n"
        "import towel.unification.unifier\n"
        "import towel.unification.scope_analyzer\n"
        "print('towel.unification.refactor_engine' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"

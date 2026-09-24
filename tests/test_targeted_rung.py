"""After a refused ordinary signature, ``Any`` goes only where the checker's errors point.

The rung that made every annotation ``Any`` came next and, under strict mypy,
was refused wherever the helper's ``Any`` result was returned (packaging's
``_matches_literal``/``contains`` and ``__eq__`` pairs, declined). The targeted
rung keeps every other annotation and the return as precise as they were: the
one parameter whose narrowing the call boundary dropped becomes ``Any``, and a
helper returning ``NotImplemented`` returns ``bool | Any``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.typed_fixtures import apply_one, requires_mypy


@requires_mypy
def test_the_parameter_whose_narrowing_the_call_dropped_alone_becomes_any(
    tmp_path: Path,
) -> None:
    """packaging's ``_matches_literal``/``contains``: ``parsed is None`` narrowed the value passed on."""
    outcome = apply_one(
        tmp_path,
        """
        class Version:
            def __init__(self, parts: tuple[int, ...], pre: bool = False) -> None:
                self.parts = parts
                self.is_prerelease = pre


        def matches_bounds_only(bounds: tuple[int, int], version: Version) -> bool:
            return bounds[0] <= version.parts[0] < bounds[1]


        def _coerce(text: str) -> Version | None:
            return Version((int(text),)) if text.isdigit() else None


        class VersionRange:
            def __init__(self, bounds: tuple[int, int]) -> None:
                self._bounds = bounds

            def _arbitrary_active(self) -> bool:
                return self._bounds[0] == 0

            def _matches_literal(self, text: str) -> bool:
                parsed = _coerce(text)
                if parsed is None:
                    return self._arbitrary_active()
                return matches_bounds_only(self._bounds, parsed)

            def contains(self, item: Version, effective_pre: bool | None) -> bool:
                if effective_pre is False and item.is_prerelease:
                    return False
                return matches_bounds_only(self._bounds, item)
        """,
        pick="_matches_literal and contains",
    )
    assert outcome.error is None, outcome.error
    assert outcome.signature() == (
        "(self, __param_0: bool, __param_1: Callable[[], bool], __param_2: Any) -> bool"
    )
    assert outcome.prospective_checks == 2, outcome.checked_helpers


@requires_mypy
def test_a_helper_returning_notimplemented_returns_its_type_or_any(tmp_path: Path) -> None:
    """packaging's ``LowerBound``/``UpperBound.__eq__``: mypy allows the constant only in a dunder."""
    outcome = apply_one(
        tmp_path,
        """
        class LowerBound:
            def __init__(self, version: int, inclusive: bool) -> None:
                self.version = version
                self.inclusive = inclusive

            def __eq__(self, other: object) -> bool:
                if not isinstance(other, LowerBound):
                    return NotImplemented
                return self.version == other.version and self.inclusive == other.inclusive

            def __hash__(self) -> int:
                return hash((self.version, self.inclusive))


        class UpperBound:
            def __init__(self, version: int, inclusive: bool) -> None:
                self.version = version
                self.inclusive = inclusive

            def __eq__(self, other: object) -> bool:
                if not isinstance(other, UpperBound):
                    return NotImplemented
                return self.version == other.version and self.inclusive == other.inclusive

            def __hash__(self) -> int:
                return hash((self.version, self.inclusive))
        """,
        pick="__eq__ and __eq__",
    )
    assert outcome.error is None, outcome.error
    returns = outcome.helper().returns
    assert returns is not None
    spelled = returns.value if isinstance(returns, ast.Constant) else ast.unparse(returns)
    assert spelled == "bool | Any", outcome.signature()
    assert outcome.prospective_checks == 2, outcome.checked_helpers

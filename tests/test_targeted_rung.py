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

"""Keep caller refinements and checker-reported result alternatives distinct.

The 2026-09-27 decisions in docs/DECISIONS.md supersede this file's former
expectation that a lost Optional refinement could be hidden by an Any input.
That proposal must now be declined before signature search. By contrast,
mypy's Any alternative for returned NotImplemented belongs in the first
signature. A deliberately narrower staged signature still exercises allowed
fallback rendering and comment preservation, without disabling a safety guard.
"""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from tests.typed_fixtures import CountingMypy, STRICT, apply_one, requires_mypy
from towel.type_inference import CheckSuccess
from towel.unification.models import RefactoringProposal
from towel.unification.refactor_engine import UnificationRefactorEngine

OPTIONAL_CONSUMER = textwrap.dedent("""
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
    """).lstrip()


COMPARISONS = textwrap.dedent("""
    class LowerBound:
        def __init__(self, version: int, inclusive: bool) -> None:
            self.version = version
            self.inclusive = inclusive

        def __eq__(self, other: object) -> bool:
            if not isinstance(other, LowerBound):
                # Let the other operand handle an unrelated type.
                return NotImplemented
            return self.version == other.version and self.inclusive == other.inclusive  # both fields

        def __hash__(self) -> int:
            return hash((self.version, self.inclusive))


    class UpperBound:
        def __init__(self, version: int, inclusive: bool) -> None:
            self.version = version
            self.inclusive = inclusive

        def __eq__(self, other: object) -> bool:
            if not isinstance(other, UpperBound):
                # Let the other operand handle an unrelated type.
                return NotImplemented
            return self.version == other.version and self.inclusive == other.inclusive  # both fields

        def __hash__(self) -> int:
            return hash((self.version, self.inclusive))
    """).lstrip()


def _assert_comparison_behavior(after: str) -> None:
    """Check direct dunder results and Python's reflected-comparison fallback."""
    program = """
values = [LowerBound(1, True), LowerBound(1, False), LowerBound(2, True),
          UpperBound(1, True), UpperBound(1, False), UpperBound(2, True), object()]
for left in values[:-1]:
    for right in values:
        direct = left.__eq__(right)
        print(direct is NotImplemented, direct, left == right)
"""
    observed = [
        subprocess.run(
            [sys.executable, "-B", "-c", source + program],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        for source in (COMPARISONS, after)
    ]
    assert observed[0].stdout == observed[1].stdout
    assert observed[0].stderr == observed[1].stderr == ""


@requires_mypy
@pytest.mark.parametrize("typed", [False, True])
def test_a_lost_optional_refinement_is_not_hidden_by_any(tmp_path: Path, typed: bool) -> None:
    """The original strict project is valid; a Boolean cannot refine a separate Version|None.

    This formerly expected the Version parameter to become Any. The approved
    boundary policy requires refusal in typed and untyped discovery instead.
    """
    path = tmp_path / "module.py"
    path.write_text(OPTIONAL_CONSUMER)
    (tmp_path / "pyproject.toml").write_text(STRICT)
    checker = CountingMypy()
    try:
        assert checker.check_project({str(path): OPTIONAL_CONSUMER}) == CheckSuccess()
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=checker if typed else None
        )
        assert engine.analyze_file(str(path)) == []
        assert all(
            "extracted_func" not in source
            for sources in checker.checked
            for source in sources.values()
        )
        assert path.read_text() == OPTIONAL_CONSUMER
    finally:
        checker.close()


@requires_mypy
def test_notimplemented_has_its_result_alternative_on_the_first_check(tmp_path: Path) -> None:
    """Preserve the observed result alternative without erasing any input types."""
    outcome = apply_one(tmp_path, COMPARISONS, pick="__eq__ and __eq__")
    assert outcome.error is None, outcome.error
    assert outcome.prospective_checks == 1, outcome.checked_helpers
    assert len(outcome.checked_helpers) == 1, outcome.checked_helpers
    helper = outcome.helper()
    assert helper.returns is not None
    spelled = (
        helper.returns.value
        if isinstance(helper.returns, ast.Constant)
        else ast.unparse(helper.returns)
    )
    assert spelled == "bool | _typing.Any", outcome.signature()
    assert "Any" not in ast.unparse(helper.args), outcome.signature()
    assert outcome.module is not None
    _assert_comparison_behavior(outcome.module)


@requires_mypy
def test_a_targeted_return_fallback_carries_the_comments_of_the_moved_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage a narrow return to test rendering after a real, repairable rejection.

    Normal construction already retains NotImplemented's Any alternative, as
    the preceding test requires. Only its return annotation is staged here;
    discovery, refinement guards, input types and final checking stay active.
    The real checker must reject that annotation and accept the targeted union.
    """
    infer = UnificationRefactorEngine._infer_helper_annotations

    def stage_narrow_return(
        engine: UnificationRefactorEngine, proposal: RefactoringProposal
    ) -> None:
        infer(engine, proposal)
        assert proposal.extracted_function.returns is not None
        assert "Any" in ast.unparse(proposal.extracted_function.returns)
        proposal.extracted_function.returns = ast.Name(id="bool", ctx=ast.Load())
        proposal.observed_return_any = False

    monkeypatch.setattr(UnificationRefactorEngine, "_infer_helper_annotations", stage_narrow_return)
    checker = CountingMypy()
    outcome = apply_one(tmp_path, COMPARISONS, pick="__eq__ and __eq__", oracle=checker)
    assert outcome.error is None, outcome.error
    assert outcome.prospective_checks >= 2, outcome.checked_helpers
    assert outcome.checked_helpers[0].endswith(" -> bool"), outcome.checked_helpers
    assert "Any" not in outcome.checked_helpers[0], outcome.checked_helpers
    assert "bool | _typing.Any" in outcome.checked_helpers[-1], outcome.checked_helpers
    assert all("Any" not in signature.split(" -> ")[0] for signature in outcome.checked_helpers)
    assert outcome.module is not None
    helper = outcome.helper()
    written = outcome.module.splitlines()[helper.lineno - 1 : helper.end_lineno]
    text = "\n".join(written)
    assert "# Let the other operand handle an unrelated type." in text, text
    assert "# both fields" in text, text
    _assert_comparison_behavior(outcome.module)

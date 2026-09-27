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

"""Preserve refinement when constructing helpers, without declining valid work.

These are policy regressions: fewer checker retries is not an improvement if
achieved by refusing Optional-aware consumers, redundant tests, intact tests,
or the smaller valid computation after a refused boundary. See CONTRIBUTING.md,
"Preserve the intent of policy regressions" before changing those expectations.

The negative fixture reduces packaging's matches_bounds_only call to explicit
declarations. Unambiguous project imports preserve that proof; unknown
signatures still get the existing checker fallback, not a guessed refusal.
"""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest

from tests.typed_fixtures import CountingMypy, STRICT, apply_one, requires_mypy
from towel.type_inference import CheckSuccess
from towel.unification.function_index import FunctionIndex
from towel.unification.import_graph import ImportGraphCache
from towel.unification.models import FunctionArtifact, Replacement
from towel.unification.narrowing import parameterized_narrowing_lost
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer

MATCHES = """
class Version:
    def __init__(self, major: int) -> None:
        self.major = major

def coerce(text: str) -> Version | None:
    return Version(int(text)) if text.isdigit() else None

def matches_bounds_only(bounds: tuple[int, int], version: Version) -> bool:
    return bounds[0] <= version.major < bounds[1]

class Range:
    def __init__(self, bounds: tuple[int, int]) -> None:
        self._bounds = bounds
    def _arbitrary(self) -> bool:
        return self._bounds[0] == 0
    def matches_literal(self, text: str) -> bool:
        parsed = coerce(text)
        if parsed is None:
            return self._arbitrary()
        return matches_bounds_only(self._bounds, parsed)
    def contains(self, item: Version, pre: bool) -> bool:
        if pre and item.major == 0:
            return False
        return matches_bounds_only(self._bounds, item)
"""


def _imported_matches(tmp_path: Path, *, optional_consumer: bool = False) -> str:
    """Keep packaging's function import, type import and nominal base chain."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    version, rest = MATCHES.split("def coerce", 1)
    (package / "version.py").write_text(
        "class _BaseVersion:\n    pass\n"
        + version.replace("class Version:", "class Version(_BaseVersion):")
    )
    ranges, client = ("def coerce" + rest).split("class Range:", 1)
    if optional_consumer:
        ranges = ranges.replace(
            "version: Version) -> bool:", "version: Version | None) -> bool:"
        ).replace(
            "return bounds[0] <= version.major < bounds[1]",
            "return version is not None and bounds[0] <= version.major < bounds[1]",
        )
    (package / "_ranges.py").write_text("from .version import Version\n" + ranges)
    return (
        "from .version import Version\nfrom ._ranges import coerce, matches_bounds_only\n\nclass Range:"
        + client
    )


@requires_mypy
def test_imported_project_callee_preserves_the_same_proof(tmp_path: Path) -> None:
    """Production packaging imports both the optional producer and nonoptional consumer."""
    source = _imported_matches(tmp_path)
    path = tmp_path / "pkg" / "ranges.py"
    path.write_text(source)
    (tmp_path / "pyproject.toml").write_text(STRICT)
    checker = CountingMypy()
    try:
        sources = {str(file): file.read_text() for file in (tmp_path / "pkg").glob("*.py")}
        assert checker.check_project(sources) == CheckSuccess()
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=checker
        )
        assert engine.analyze_file(str(path)) == []
        assert all(
            "__extracted_func" not in text for check in checker.checked for text in check.values()
        )
    finally:
        checker.close()


@requires_mypy
def test_imported_optional_consumer_still_applies_precisely(tmp_path: Path) -> None:
    """Import resolution must not replace unknown or optional types with a refusal."""
    source = _imported_matches(tmp_path, optional_consumer=True)
    outcome = apply_one(tmp_path, source, pick="matches_literal and contains", name="pkg/ranges.py")
    assert outcome.error is None, outcome.error
    assert outcome.prospective_checks == 1, outcome.checked_helpers
    assert "Version | None" in outcome.signature()
    assert "Any" not in outcome.signature()


@requires_mypy
def test_imported_optional_alias_keeps_checker_fallback(tmp_path: Path) -> None:
    """An unresolved annotation alias is not evidence that a consumer rejects None."""
    source = _imported_matches(tmp_path, optional_consumer=True)
    path = tmp_path / "pkg" / "_ranges.py"
    path.write_text(
        path.read_text().replace(
            "def matches_bounds_only(bounds: tuple[int, int], version: Version | None)",
            "OptionalVersion = Version | None\n\ndef matches_bounds_only(bounds: tuple[int, int], version: OptionalVersion)",
        )
    )
    outcome = apply_one(tmp_path, source, pick="matches_literal and contains", name="pkg/ranges.py")
    assert outcome.error is None, outcome.error
    assert outcome.prospective_checks == 1, outcome.checked_helpers
    assert "Any" not in outcome.signature()


def test_imported_stub_prevents_claiming_the_runtime_signature(tmp_path: Path) -> None:
    """A consumer's .pyi can accept None even when the .py annotation excludes it."""
    source = _imported_matches(tmp_path)
    (tmp_path / "pkg" / "_ranges.pyi").write_text(
        "from .version import Version\n"
        "def coerce(text: str) -> Version | None: ...\n"
        "def matches_bounds_only(bounds: tuple[int, int], version: Version | None) -> bool: ...\n"
    )
    path = tmp_path / "pkg" / "ranges.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=None
    )
    assert engine.analyze_file(str(path))


@requires_mypy
def test_erased_none_test_never_reaches_signature_validation(tmp_path: Path) -> None:
    """A bool parameter cannot refine the separately passed Version|None.

    This fixture previously took four prospective checks, finally weakening the
    Version argument to Any. Validate the original strict project too: absence
    of a proposal alone must not hide an ill-typed regression fixture.
    """
    path = tmp_path / "module.py"
    path.write_text(textwrap.dedent(MATCHES))
    (tmp_path / "pyproject.toml").write_text(STRICT)
    checker = CountingMypy()
    try:
        assert checker.check_project({str(path): path.read_text()}) == CheckSuccess()
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=checker
        )
        assert engine.analyze_file(str(path)) == []
        assert all(
            "__extracted_func" not in source
            for check in checker.checked
            for source in check.values()
        )
    finally:
        checker.close()


@requires_mypy
def test_optional_consumer_keeps_the_same_extraction(tmp_path: Path) -> None:
    """A lost proof is harmless when the consumer accepts the original type."""
    source = MATCHES.replace(
        "version: Version) -> bool:", "version: Version | None) -> bool:"
    ).replace(
        "return bounds[0] <= version.major < bounds[1]",
        "return version is not None and bounds[0] <= version.major < bounds[1]",
    )
    outcome = apply_one(tmp_path, source, pick="matches_literal and contains")
    assert outcome.error is None, outcome.error
    assert outcome.prospective_checks == 1, outcome.checked_helpers
    assert "Version | None" in outcome.signature()
    assert "Any" not in outcome.signature()


def test_redundant_none_test_does_not_cost_an_extraction(tmp_path: Path) -> None:
    """Do not refuse a redundant predicate as lost narrowing during construction.

    Typed application separately refuses this fixture's unreachable original
    branch, since the checker cannot verify changes there. This test isolates
    construction and does not claim the whole extraction is typed-applicable.
    """
    source = MATCHES.replace(
        "def coerce(text: str) -> Version | None:", "def coerce(text: str) -> Version:"
    ).replace("return Version(int(text)) if text.isdigit() else None", "return Version(int(text))")
    path = tmp_path / "module.py"
    path.write_text(textwrap.dedent(source))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=None
    )
    assert engine.analyze_file(str(path))


OPERATIONS = """
def first(value: str | None) -> str:
    if value is None:
        return 'missing'
    return value.upper()

def second(value: str, stop: bool) -> str:
    if stop:
        return 'stopped'
    return value.upper()
"""


def _reason(consumer: str, source: str = OPERATIONS) -> str | None:
    """Exercise constructed helpers independently of discovery's size heuristics."""
    tree = ast.parse(textwrap.dedent(source))
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    guard = function.body[0]
    assert isinstance(guard, ast.If)
    helper = ast.parse(
        f"def extracted(condition, value):\n    if condition:\n        return None\n    {consumer}"
    ).body[0]
    assert isinstance(helper, ast.FunctionDef)
    call = ast.parse("extracted(condition, value)").body[0]
    assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
    call.value.args[0] = guard.test
    analyzer = ScopeAnalyzer()
    scope = analyzer.analyze(tree)
    index = FunctionIndex.build(
        [FunctionArtifact("module.py", function, source, analyzer, scope, None, None, [])]
    )
    site = Replacement(line_range=(guard.lineno, function.end_lineno or guard.lineno), node=call)
    return parameterized_narrowing_lost(
        helper, [site], index, "module.py", ast.parse, ImportGraphCache()
    )


def test_erased_predicate_with_direct_nonoptional_operation_is_refused() -> None:
    """Attribute lookup proves the missing refinement without resolving a callee."""
    assert _reason("return value.upper()") is not None


@requires_mypy
def test_smaller_valid_suffix_is_still_discovered_and_applies(tmp_path: Path) -> None:
    """Rejecting the larger bool-guard window must not hide its narrowed suffix."""
    source = MATCHES.replace(
        "return matches_bounds_only(self._bounds, parsed)",
        "matched = matches_bounds_only(self._bounds, parsed)\n        return matched and self._bounds[0] >= 0",
    ).replace(
        "return matches_bounds_only(self._bounds, item)",
        "matched = matches_bounds_only(self._bounds, item)\n        return matched and self._bounds[0] >= 0",
    )
    outcome = apply_one(tmp_path, source, pick="matches_literal and contains")
    assert outcome.error is None, outcome.error
    assert outcome.prospective_checks == 1, outcome.checked_helpers
    assert "Any" not in outcome.signature()
    assert not any(isinstance(node, ast.If) for node in ast.walk(outcome.helper()))
    assert outcome.module is not None
    first = next(
        node
        for node in ast.walk(ast.parse(outcome.module))
        if isinstance(node, ast.FunctionDef) and node.name == "matches_literal"
    )
    assert isinstance(first.body[1], ast.If)
    assert ast.unparse(first.body[1].test) == "parsed is None"


@requires_mypy
def test_class_thunks_keep_isinstance_refinement(tmp_path: Path) -> None:
    """mypy narrows against a thunk returning type[A]|type[B]; do not refuse it.

    The observed __eq__ retry instead came from NotImplemented's inferred
    return. False keeps this regression independent of that separate fix.
    """
    outcome = apply_one(
        tmp_path,
        """
        class LowerBound:
            def __init__(self, value: int) -> None:
                self.value = value
            def matches(self, other: object) -> bool:
                if not isinstance(other, LowerBound):
                    return False
                return self.value == other.value

        class UpperBound:
            def __init__(self, value: int) -> None:
                self.value = value
            def matches(self, other: object) -> bool:
                if not isinstance(other, UpperBound):
                    return False
                return self.value == other.value
        """,
        pick="matches and matches",
    )
    assert outcome.error is None, outcome.error
    assert outcome.prospective_checks == 1, outcome.checked_helpers
    assert "Any" not in outcome.signature()
    assert "isinstance(other," in ast.unparse(outcome.helper())


@pytest.mark.parametrize(
    "consumer",
    ["return value", "return value.__class__", "return value is not None"],
)
def test_bare_optional_reads_and_none_attributes_remain_eligible(consumer: str) -> None:
    """A use is not itself proof that the erased refinement was required."""
    assert _reason(consumer) is None

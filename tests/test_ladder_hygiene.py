"""The ladder spends no check a project's own configuration, or an earlier check, already answered.

From the corpus study: the unannotated rung was tried where the project
requires annotations; a helper inference left bare got no fallback at all
(rich's ``highlight_regex``, ``render``/``__rich_measure__`` and
``get_html_style``); a proposal heard again rendered the same variants and was
checked again from scratch (packaging: 28 of 68 checks were exact repeats);
and a site's declared annotation was copied even where the block saw a
narrower type (packaging's comparisons, rich's ``append``/``append_text``).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Mapping, Sequence

from towel.type_inference import CheckResult, CheckSuccess, RevealRequest, TypeDiagnostic

from tests.typed_fixtures import STRICT, CountingMypy, apply_one, requires_mypy


class _RefusedInsideTheHelper(CountingMypy):
    """The project's mypy for the original, and for every variant an error on the helper's own line."""

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        for path, source in sources.items():
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name:
                    self.checked.append(dict(sources))
                    return CheckSuccess(
                        (TypeDiagnostic(path, 'Explicit "Any" is not allowed', node.lineno),)
                    )
        return super().check_project(sources, excluded_paths=excluded_paths)


SUMS = """
def first(values: list[int]) -> int:
    total = sum(values)
    count = len(values)
    return total + count


def second(items: list[int]) -> int:
    total = sum(items)
    count = len(items)
    return total * count
"""


@requires_mypy
def test_the_unannotated_rung_is_not_tried_where_annotations_are_required(
    tmp_path: Path,
) -> None:
    outcome = apply_one(
        tmp_path,
        SUMS,
        pick="first and second",
        config="[tool.mypy]\ndisallow_untyped_defs = true\n",
        oracle=_RefusedInsideTheHelper(),
    )
    assert outcome.error is not None
    assert outcome.checked_helpers, outcome
    assert all(": " in signature for signature in outcome.checked_helpers), outcome.checked_helpers


@requires_mypy
def test_the_unannotated_rung_is_still_tried_where_annotations_are_optional(
    tmp_path: Path,
) -> None:
    outcome = apply_one(
        tmp_path,
        SUMS,
        pick="first and second",
        config="[tool.mypy]\ncheck_untyped_defs = true\n",
        oracle=_RefusedInsideTheHelper(),
    )
    assert outcome.error is not None
    assert ": " not in outcome.checked_helpers[-1], outcome.checked_helpers


class _RevealsNothing(CountingMypy):
    """The project's mypy, which reveals no type: inference leaves the helper bare."""

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[tuple[str, int, int], str]:
        return {}


@requires_mypy
def test_a_helper_inference_left_bare_still_gets_the_fallback_rungs(tmp_path: Path) -> None:
    outcome = apply_one(
        tmp_path,
        """
        def first(text: str) -> int:
            words = text.split()
            size = len(words)
            doubled = size * 2
            return doubled


        def second(line: str) -> int:
            parts = line.split(",")
            size = len(parts)
            doubled = size * 2
            return doubled + 1
        """,
        pick="first and second",
        config="[tool.mypy]\ndisallow_untyped_defs = true\n",
        oracle=_RevealsNothing(),
    )
    assert outcome.error is None, outcome.error
    assert outcome.checked_helpers[0].count(":") == 0, outcome.checked_helpers
    assert "Any" in outcome.signature(), outcome.signature()


VERSIONS = """
from helpers import key


class Version:
    def __init__(self, a: int, b: int) -> None:
        self.a = a
        self.b = b
        self._key_cache: tuple[int, int] | None = None

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Version):
            if self._key_cache is None:
                self._key_cache = key(self.a, self.b)
            if other._key_cache is None:
                other._key_cache = key(other.a, other.b)
            return self._key_cache < other._key_cache
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, Version):
            if self._key_cache is None:
                self._key_cache = key(self.a, self.b)
            if other._key_cache is None:
                other._key_cache = key(other.a, other.b)
            return self._key_cache <= other._key_cache
        return NotImplemented
"""


@requires_mypy
def test_a_refusal_is_replayed_until_something_it_read_changes(tmp_path: Path) -> None:
    from towel.unification.exceptions import RefactoringError
    from towel.unification.refactor_engine import UnificationRefactorEngine

    (tmp_path / "pyproject.toml").write_text(STRICT)
    (tmp_path / "helpers.py").write_text(
        "def key(a: int, b: int) -> tuple[int, int]:\n    return (a, b)\n"
    )
    (tmp_path / "unrelated.py").write_text("VALUE = 1\n")
    path = tmp_path / "versions.py"
    path.write_text(VERSIONS.lstrip())
    oracle = CountingMypy()
    try:
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        (proposal,) = [p for p in engine.analyze_file(str(path)) if "__lt__" in p.description]

        def hear() -> str:
            try:
                engine.apply_refactoring(str(path), proposal)
            except RefactoringError as error:
                return str(error)
            raise AssertionError("the proposal was applied")

        first = hear()
        heard_once = len(oracle.checked)
        assert hear() == first
        (tmp_path / "unrelated.py").write_text("VALUE = 2\n")
        assert hear() == first
        assert len(oracle.checked) == heard_once, "Nothing the refusal read had changed"
        (tmp_path / "helpers.py").write_text(
            "def key(a: int, b: int) -> tuple[int, int]:\n    return (b, a)\n"
        )
        hear()
        assert len(oracle.checked) == heard_once + 1, "A module the variant imports changed"
    finally:
        oracle.close()


@requires_mypy
def test_the_type_the_block_saw_replaces_a_wider_declared_one(tmp_path: Path) -> None:
    """rich's ``append``/``append_text``: ``text: Text | str``, but the block runs on a ``Text``."""
    outcome = apply_one(
        tmp_path,
        """
        class Text:
            def __init__(self, plain: str, style: str = "") -> None:
                self.plain = plain
                self.style = style

            def __len__(self) -> int:
                return len(self.plain)


        class Buffer:
            def __init__(self) -> None:
                self._spans: list[tuple[int, int, str]] = []
                self._text: list[str] = []
                self._length = 0

            def append(self, text: Text | str) -> None:
                if isinstance(text, str):
                    self._text.append(text)
                    return
                length = self._length
                if text.style:
                    self._spans.append((length, length + len(text), text.style))
                self._text.append(text.plain)

            def append_text(self, text: Text) -> None:
                length = self._length
                if text.style:
                    self._spans.append((length, length + len(text), text.style))
                self._text.append(text.plain)
        """,
        pick="append and append_text",
    )
    assert outcome.error is None, outcome.error
    assert outcome.signature() in {"(self, text: 'Text') -> None", "(self, text: Text) -> None"}
    assert outcome.prospective_checks == 1, outcome.checked_helpers

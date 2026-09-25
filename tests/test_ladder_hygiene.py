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
from typing import List, Mapping, Sequence

import pytest

from towel.type_inference import CheckResult, CheckSuccess, RevealRequest, TypeDiagnostic

from tests.probe_answers import answer_probes, only_probes
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
    """Optional in the configuration, and already absent from one of the module's functions."""
    outcome = apply_one(
        tmp_path,
        SUMS + "\n\ndef legacy(value):\n    return value\n",
        pick="first and second",
        config="[tool.mypy]\ncheck_untyped_defs = true\n",
        oracle=_RefusedInsideTheHelper(),
    )
    assert outcome.error is not None
    assert ": " not in outcome.checked_helpers[-1], outcome.checked_helpers


class _RevealsNothing(CountingMypy):
    """The project's mypy, which reveals no type: inference leaves the helper bare.

    Its probes of where the checker looks are still answered, as mypy answers
    them in checked code.
    """

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[tuple[str, int, int], str]:
        return answer_probes(requests) if only_probes(requests) else {}


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


@requires_mypy
def test_a_variant_accepted_after_a_replayed_refusal_is_still_probed_for_reachability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only refusals are replayed: a variant the checker accepts always has its lines probed.

    The same variant is refused, heard again and refused by replay, and then,
    once the module it imports is fixed, checked afresh and accepted, at which
    point the check of code the checker does not look at runs for it.
    """
    from towel.unification.annotation_wiring import HelperAnnotationWiring
    from towel.unification.exceptions import RefactoringError
    from towel.unification.refactor_engine import UnificationRefactorEngine

    probed: List[int] = []
    original = HelperAnnotationWiring._refuse_what_the_checker_does_not_look_at

    def probe(self: HelperAnnotationWiring, oracle: object, files: Mapping[str, str]) -> None:
        probed.append(len(files))
        original(self, oracle, files)  # type: ignore[arg-type]

    monkeypatch.setattr(HelperAnnotationWiring, "_refuse_what_the_checker_does_not_look_at", probe)
    (tmp_path / "pyproject.toml").write_text(STRICT)
    helpers = tmp_path / "helpers.py"
    helpers.write_text("def key(a: int, b: int) -> tuple[int, int]:\n    return (a, b)\n")
    path = tmp_path / "versions.py"
    path.write_text(VERSIONS.lstrip())
    oracle = CountingMypy()
    try:
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        (proposal,) = [p for p in engine.analyze_file(str(path)) if "__lt__" in p.description]
        for _ in range(2):
            with pytest.raises(RefactoringError):
                engine.apply_refactoring(str(path), proposal)
        assert probed == [], "a refused variant is not probed"
        refused_checks = len(oracle.checked)
        # ``key`` now returns a type ``<`` accepts on ``None`` too: the code after
        # the call no longer relies on the block's narrowing.
        helpers.write_text(
            "from typing import Any\n\n\ndef key(a: int, b: int) -> Any:\n    return (a, b)\n"
        )
        (tmp_path / "versions.py").write_text(
            VERSIONS.lstrip()
            .replace("tuple[int, int] | None", "Any")
            .replace("from helpers import key", "from typing import Any\n\nfrom helpers import key")
        )
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        (proposal,) = [p for p in engine.analyze_file(str(path)) if "__lt__" in p.description]
        engine.apply_refactoring(str(path), proposal)
        assert len(oracle.checked) > refused_checks
        assert probed, "the accepted variant's lines were probed"
    finally:
        oracle.close()

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

"""A typed run compares each change's check with the errors the project already has.

Typed mode refused a project whose check, as Towel runs it, reported any
error, and of 20 corpus projects that check was clean for 6 while all 17 that
type-check pass their own. The errors the original check reports are now left
as they are, and a change is rejected only for an error they do not account
for: in a file nothing has changed, the same error at the same line; in one a
change has, as many errors of each message as before, since lines move there.
A message that embeds a line fails closed. The reference follows the project
from change to change, the cold confirmation compares the same way, a checker
that cannot run still refuses, and a file whose check leaves a name the
checker cannot type is not changed at all.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
import importlib.util
import logging
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from towel.type_baseline import (
    KnownErrors,
    files_where_names_are_any,
    makes_names_any,
    names_any_warning,
    pre_existing_summary,
)
from towel.type_inference import (
    CheckFailure,
    CheckResult,
    CheckSuccess,
    CombinedOracle,
    MypyInferrer,
    RevealKey,
    RevealRequest,
    Subtyping,
    TypeDiagnostic,
    checks_in_turn,
)
from towel.unification.exceptions import RefactoringError, UnverifiableChangeError
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

A = "/project/a.py"
B = "/project/b.py"


def _as_is(path: str) -> str:
    return path


def _error(path: str, message: str, line: int | None = None) -> TypeDiagnostic:
    return TypeDiagnostic(path, message, line)


def _new(
    reference: Sequence[TypeDiagnostic],
    after: Sequence[TypeDiagnostic],
    changing: Sequence[str] = (),
    moved: Sequence[str] = (),
) -> tuple[TypeDiagnostic, ...]:
    known = KnownErrors(tuple(reference), frozenset(moved))
    return known.introduced(after, changing, where=_as_is)


# --- What counts as new ------------------------------------------------------


def test_the_same_errors_where_they_were_are_nothing_new() -> None:
    errors = [_error(A, "Incompatible types [assignment]", 3), _error(B, "Oops [misc]", 7)]
    assert _new(errors, list(reversed(errors))) == ()


def test_an_error_whose_message_the_reference_lacks_is_new() -> None:
    reference = [_error(A, "Incompatible types [assignment]", 3)]
    added = _error(A, "Argument 1 has incompatible type [arg-type]", 9)
    assert _new(reference, [*reference, added], changing=[A]) == (added,)


def test_in_a_file_left_alone_an_error_on_another_line_is_new_whatever_disappeared() -> None:
    """Lines there cannot have moved, so a new line holding the error is a new error."""
    reference = [_error(B, "Incompatible types [assignment]", 10)]
    moved = _error(B, "Incompatible types [assignment]", 50)
    assert _new(reference, [moved], changing=[A]) == (moved,)


def test_in_a_changed_file_errors_that_moved_down_are_nothing_new() -> None:
    reference = [_error(A, "Incompatible types [assignment]", 10), _error(A, "Oops [misc]", 12)]
    after = [_error(A, "Incompatible types [assignment]", 16), _error(A, "Oops [misc]", 18)]
    assert _new(reference, after, changing=[A]) == ()


def test_in_a_changed_file_a_message_seen_more_often_is_new_in_every_instance() -> None:
    """Nothing says which of three is the new one, so all three are reported."""
    reference = [_error(A, "Oops [misc]", 10), _error(A, "Oops [misc]", 20)]
    after = [
        _error(A, "Oops [misc]", 4),
        _error(A, "Oops [misc]", 16),
        _error(A, "Oops [misc]", 26),
    ]
    assert _new(reference, after, changing=[A]) == tuple(after)


def test_in_a_changed_file_an_error_that_moves_into_the_helper_is_not_new() -> None:
    """The same message gone from one line and found on another is the same error moved."""
    reference = [_error(A, "Unsupported operand [operator]", 30)]
    after = [_error(A, "Unsupported operand [operator]", 3)]
    assert _new(reference, after, changing=[A]) == ()


def test_a_message_that_embeds_its_line_fails_closed_when_the_line_moves() -> None:
    reference = [_error(A, 'Name "x" already defined on line 12  [no-redef]', 20)]
    after = [_error(A, 'Name "x" already defined on line 17  [no-redef]', 25)]
    assert _new(reference, after, changing=[A]) == tuple(after)


def test_one_error_of_the_reference_accounts_for_one_error_after() -> None:
    reference = [_error(B, "Oops [misc]", 5)]
    after = [_error(B, "Oops [misc]", 5), _error(B, "Oops [misc]", 5)]
    assert _new(reference, after, changing=[A]) == (after[1],)


def test_a_file_an_earlier_change_touched_is_compared_by_message() -> None:
    reference = [_error(B, "Oops [misc]", 5)]
    assert _new(reference, [_error(B, "Oops [misc]", 9)], changing=[A], moved=[B]) == ()


def test_errors_that_disappear_are_never_an_objection() -> None:
    reference = [_error(A, "Oops [misc]", 5), _error(B, "Other [misc]", 1)]
    assert _new(reference, [], changing=[A]) == ()


def test_the_checked_copy_is_compared_at_the_original_it_stands_for() -> None:
    reference = KnownErrors((_error(A, "Oops [misc]", 5),))
    stage = "/stage/a.py"
    after = [_error(stage, "Oops [misc]", 5)]

    def original(path: str) -> str:
        return path.replace("/stage/", "/project/")

    assert reference.introduced(after, where=original) == ()
    assert reference.introduced(after, where=_as_is) == tuple(after)


def test_a_change_that_is_accepted_leaves_its_files_compared_by_message() -> None:
    known = KnownErrors((_error(A, "Oops [misc]", 5),)).moving([A])
    assert known.introduced([_error(A, "Oops [misc]", 30)], where=_as_is) == ()


@pytest.mark.parametrize(
    "message,any_producing",
    [
        ('Cannot find implementation or library stub for module named "wcwidth"', True),
        (
            'Cannot find implementation or library stub for module named "x"  [import-not-found]',
            True,
        ),
        ('Library stubs not installed for "yaml"  [import-untyped]', True),
        (
            'Skipping analyzing "msgpack": module is installed, but missing library stubs or '
            "py.typed marker  [import-untyped]",
            True,
        ),
        ('Untyped decorator makes function "tests" untyped  [untyped-decorator]', True),
        ('Class cannot subclass "Base" (has type "Any")  [misc]', True),
        ('pyright: reportMissingImports: Import "hypothesis" could not be resolved', True),
        ('pyright: reportMissingTypeStubs: Stub file not found for "sortedcontainers"', True),
        ('Call to untyped function "has" in typed context  [no-untyped-call]', False),
        ('Returning Any from function declared to return "int"  [no-any-return]', False),
        (
            'pyright: reportMissingModuleSource: Import "yaml" could not be resolved from source',
            False,
        ),
        ('Incompatible types in assignment (expression has type "str")  [assignment]', False),
    ],
)
def test_the_errors_that_leave_a_name_the_checker_cannot_type(
    message: str, any_producing: bool
) -> None:
    assert makes_names_any(_error(A, message)) is any_producing


def test_the_report_counts_by_file_and_names_what_it_will_not_change(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    errors = [
        _error(
            str(root / "pkg/a.py"),
            'Cannot find implementation or library stub for module named "gone"',
            3,
        ),
        *(_error(str(root / f"tests/test_{index}.py"), "Oops [misc]", 1) for index in range(6)),
        _error(str(root / "tests/test_x.py"), 'Library stubs not installed for "yaml"', 2),
    ]
    summary = pre_existing_summary(errors, root)
    assert summary.splitlines()[0].startswith(
        "The original project's type check reports 8 error(s) in 8 file(s)."
    )
    assert "  pkg/a.py: 1" in summary and "... and 3 more file(s)" in summary
    names_any = files_where_names_are_any(errors)
    warning = names_any_warning(names_any, root, {str(root / "pkg/a.py")})
    assert warning is not None
    assert warning.startswith("warning: 2 of these error(s) leave a name the checker cannot type")
    assert "No change to these 1 file(s) is attempted" in warning
    assert (
        '  pkg/a.py:3: Cannot find implementation or library stub for module named "gone"'
        in warning
    )
    assert "The rest lie in 1 file(s) the run does not change (tests/test_x.py)" in warning
    assert names_any_warning({}, root, set()) is None
    untouched = names_any_warning(names_any, root, set())
    assert untouched is not None and "No change to these" not in untouched
    assert "They lie in 2 file(s) the run does not change (pkg/a.py, tests/test_x.py)" in untouched


# --- The engine, with checkers whose verdicts are fixed ------------------------


class _Oracle:
    """Answers from ``verdict``, remembering every project it was asked about."""

    def __init__(self, verdict: Callable[[Mapping[str, str]], CheckResult]) -> None:
        self.verdict = verdict
        self.checks: list[dict[str, str]] = []
        self.inferences = 0

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checks.append(dict(sources))
        return self.verdict(sources)

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        self.inferences += 1
        return {}

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[tuple[str, str]]
    ) -> Sequence[Subtyping]:
        self.inferences += 1
        return [Subtyping.UNKNOWN for _ in pairs]

    def close(self) -> None:
        pass


TWINS = textwrap.dedent("""
    def first(value: int) -> int:
        total = value + 1
        doubled = total * 2
        answer = doubled - 3
        return answer


    def second(value: int) -> int:
        total = value + 1
        doubled = total * 2
        answer = doubled - 3
        return answer
    """).lstrip()

LOOPS = textwrap.dedent("""
    def third(values: list[int]) -> int:
        count = 0
        for item in values:
            count += item * 3
        return count


    def fourth(values: list[int]) -> int:
        count = 0
        for item in values:
            count += item * 3
        return count
    """).lstrip()


def _text_of(sources: Mapping[str, str], name: str) -> str:
    """The text a check was given for the module called ``name``, wherever it was checked."""
    return next(text for path, text in sources.items() if Path(path).name == name)


def _resolved(path: Path) -> str:
    return str(path.resolve())


def test_a_project_whose_check_reports_errors_is_refactored_with_types(tmp_path: Path) -> None:
    """Refused before: 'Original project check reported 1 type error(s)'."""
    program, consumer = tmp_path / "program.py", tmp_path / "consumer.py"
    program.write_text(TWINS)
    consumer.write_text('broken: int = "wrong"\n')
    existing = TypeDiagnostic(_resolved(consumer), "Incompatible types [assignment]", 1)
    oracle = _Oracle(lambda _: CheckSuccess((existing,)))
    engine = UnificationRefactorEngine(type_oracle=oracle)
    proposal = engine.analyze_files([str(program), str(consumer)])[0]
    written = engine.apply_refactoring(str(program), proposal)
    assert "def __extracted_func_0(value: int)" in written
    assert oracle.inferences > 0, "the checker was asked what the helper's types are"
    assert len(oracle.checks) >= 2, "one baseline, then the candidate"
    assert program.read_text() == TWINS and consumer.read_text() == 'broken: int = "wrong"\n'


def test_a_change_that_adds_an_error_is_rejected_and_one_that_adds_none_is_accepted(
    tmp_path: Path,
) -> None:
    first, second = tmp_path / "first.py", tmp_path / "second.py"
    first.write_text(TWINS)
    second.write_text(LOOPS)
    existing = TypeDiagnostic(_resolved(first), "Incompatible types [assignment]", 2)

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        errors = [existing]
        if "_extracted_func" in _text_of(sources, "second.py"):
            errors.append(TypeDiagnostic(_resolved(second), "Unsupported operand [operator]", 1))
        return CheckSuccess(tuple(errors))

    oracle = _Oracle(verdict)
    engine = UnificationRefactorEngine(type_oracle=oracle)
    results, _ = engine.refactor_directory_to_fixed_point(
        str(tmp_path), str(tmp_path), progress="none"
    )
    assert {Path(path).name for path in results} == {"first.py"}
    assert "_extracted_func" in first.read_text()
    assert second.read_text() == LOOPS
    assert engine.run_report.declined_proposals == {"refused by the type checker": 1}


def test_what_the_original_check_reports_is_said_before_anything_is_changed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    program = tmp_path / "program.py"
    program.write_text(TWINS)
    errors = tuple(
        TypeDiagnostic(_resolved(tmp_path / f"module_{index % 7}.py"), f"diagnostic {index}", 1)
        for index in range(9)
    )
    engine = UnificationRefactorEngine(type_oracle=_Oracle(lambda _: CheckSuccess(errors)))
    caplog.set_level(logging.DEBUG, logger="towel.types")
    engine.begin_refactoring_run([str(program)])
    summary = next(r.getMessage() for r in caplog.records if "original project" in r.getMessage())
    assert summary.startswith(
        "The original project's type check reports 9 error(s) in 7 file(s). The run leaves them"
    )
    assert "  module_0.py: 2" in summary and "... and 2 more file(s)" in summary
    assert "rejects a change only for an error they do not account for" in summary
    assert all(error.message in caplog.text for error in errors), "TOWEL_DEBUG_TYPES lists all"


def test_a_checker_that_cannot_run_for_a_change_still_refuses_what_it_did_not_judge(
    tmp_path: Path,
) -> None:
    program = tmp_path / "program.py"
    program.write_text(TWINS)
    existing = TypeDiagnostic(_resolved(program), "Oops [misc]", 1)

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        if "_extracted_func" in _text_of(sources, "program.py"):
            return CheckFailure("checker timed out")
        return CheckSuccess((existing,))

    engine = UnificationRefactorEngine(type_oracle=_Oracle(verdict))
    with pytest.raises(RefactoringError, match=r"(?s)could not run for 1 proposal.*--no-types"):
        engine.refactor_to_fixed_point(str(program), progress="none")
    assert program.read_text() == TWINS


def test_a_configured_checker_whose_first_answer_is_old_errors_does_not_speak_for_the_second(
    tmp_path: Path,
) -> None:
    """Every checker must accept, and pre-existing errors from one are not a rejection."""
    program, consumer = tmp_path / "program.py", tmp_path / "consumer.py"
    program.write_text(TWINS)
    consumer.write_text("value = 1\n")
    existing = TypeDiagnostic(_resolved(consumer), "Old error [misc]", 1)
    primary = _Oracle(lambda _: CheckSuccess((existing,)))

    def dissent(sources: Mapping[str, str]) -> CheckResult:
        if "_extracted_func" in _text_of(sources, "program.py"):
            return CheckSuccess((TypeDiagnostic(_resolved(program), "pyright: new: no", 1),))
        return CheckSuccess()

    secondary = _Oracle(dissent)
    combined = CombinedOracle(primary, [secondary])
    engine = UnificationRefactorEngine(type_oracle=combined)
    proposal = engine.analyze_files([str(program), str(consumer)])[0]
    with pytest.raises(RefactoringError, match="introduces project type errors"):
        engine.apply_refactoring(str(program), proposal)
    assert len(secondary.checks) == len(primary.checks), "the second was asked every time"
    assert any("_extracted_func" in _text_of(check, "program.py") for check in secondary.checks)


def test_a_combined_check_reports_every_checker_and_lets_its_caller_stop(tmp_path: Path) -> None:
    """It used to stop at the first checker that reported anything."""
    path = str(tmp_path / "m.py")
    old = TypeDiagnostic(path, "Old error [misc]", 1)
    new = TypeDiagnostic(path, "pyright: new: no", 2)
    first, second = _Oracle(lambda _: CheckSuccess((old,))), _Oracle(lambda _: CheckSuccess((new,)))
    combined = CombinedOracle(first, [second])
    assert combined.check_project({path: ""}) == CheckSuccess((old, new))
    assert len(second.checks) == 1
    turns = checks_in_turn(combined, {path: ""})
    assert next(turns) == CheckSuccess((old,)) and len(second.checks) == 1
    assert next(turns) == CheckSuccess((new,)) and len(second.checks) == 2
    failing = CombinedOracle(_Oracle(lambda _: CheckFailure("crashed")), [second])
    assert failing.check_project({path: ""}) == CheckFailure("crashed")


def test_a_later_change_cannot_spend_an_error_an_earlier_one_removed(tmp_path: Path) -> None:
    """Against the original's errors the second change would pass; against the project's, it fails.

    The original reports one error in the module, which extracting the first
    pair removes. Extracting the second brings back an error with the very
    same message in the very same file: it is new to the project as it then
    stands, whatever the original had.
    """
    program = tmp_path / "program.py"
    program.write_text(TWINS + "\n\n" + LOOPS)
    message = "Unsupported operand [operator]"

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        text = _text_of(sources, "program.py")
        path = next(path for path in sources if Path(path).name == "program.py")
        errors = []
        if text.count("answer = doubled - 3") >= 2:  # the first pair, not yet extracted
            errors.append(TypeDiagnostic(path, message, 1))
        if text.count("count += item * 3") == 1:  # the second pair, extracted
            errors.append(TypeDiagnostic(path, message, 1))
        return CheckSuccess(tuple(errors))

    engine = UnificationRefactorEngine(type_oracle=_Oracle(verdict))
    _, applied, _ = engine.refactor_to_fixed_point(str(program), progress="none")
    result = program.read_text()
    assert applied == 1
    assert result.count("answer = doubled - 3") == 1, "the first pair was extracted"
    assert result.count("count += item * 3") == 2, "the second was not"


def test_a_file_whose_imports_the_checker_cannot_resolve_is_left_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    first, second = tmp_path / "first.py", tmp_path / "second.py"
    first.write_text("import gone\n\n\n" + TWINS)
    second.write_text(LOOPS)
    unresolved = TypeDiagnostic(
        _resolved(first),
        'Cannot find implementation or library stub for module named "gone"  [import-not-found]',
        1,
    )
    oracle = _Oracle(lambda _: CheckSuccess((unresolved,)))
    engine = UnificationRefactorEngine(type_oracle=oracle)
    caplog.set_level(logging.WARNING, logger="towel")
    results, _ = engine.refactor_directory_to_fixed_point(
        str(tmp_path), str(tmp_path), progress="none"
    )
    assert {Path(path).name for path in results} == {"second.py"}
    assert first.read_text() == "import gone\n\n\n" + TWINS
    assert engine.run_report.declined_proposals == {
        "not verifiable: its file holds a name the type checker cannot type": 1
    }
    assert "No change to these 1 file(s) is attempted" in caplog.text
    assert 'first.py:1: Cannot find implementation or library stub for module named "gone"' in (
        caplog.text
    )
    proposal = engine.analyze_file(str(first))[0]
    with pytest.raises(UnverifiableChangeError, match="cannot type"):
        engine.apply_refactoring(str(first), proposal)
    assert oracle.inferences == 0 or "_extracted_func" not in first.read_text()


# --- With mypy itself -----------------------------------------------------------


def _mypy_run(tmp_path: Path, source: str) -> tuple[str, int]:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
        _, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    finally:
        oracle.close()
    return path.read_text(encoding="utf-8"), applied


@requires_mypy
def test_mypy_an_error_below_the_helper_moves_with_it_and_is_still_the_same_error(
    tmp_path: Path,
) -> None:
    written, applied = _mypy_run(
        tmp_path,
        """
        def first(value: int) -> int:
            total = value + 1
            doubled = total * 2
            answer = doubled - 3
            return answer


        def second(value: int) -> int:
            total = value + 1
            doubled = total * 2
            answer = doubled - 3
            return answer


        WRONG: str = first(1)
        """,
    )
    assert applied == 1 and "def __extracted_func_0(value: int) -> int:" in written
    assert "WRONG: str = first(1)" in written


@requires_mypy
def test_mypy_a_message_naming_a_line_the_change_moves_fails_closed(tmp_path: Path) -> None:
    """``already defined on line 15`` becomes ``on line 21``: a new error, so no change."""
    source = """
        def first(value: int) -> int:
            total = value + 1
            doubled = total * 2
            answer = doubled - 3
            return answer


        def second(value: int) -> int:
            total = value + 1
            doubled = total * 2
            answer = doubled - 3
            return answer


        flag = 1


        def flag() -> None:
            pass
        """
    written, applied = _mypy_run(tmp_path, source)
    assert applied == 0 and written == textwrap.dedent(source).lstrip()


@requires_mypy
def test_mypy_a_message_naming_a_line_the_change_leaves_in_place_is_the_same_error(
    tmp_path: Path,
) -> None:
    """``already defined on line 1`` still says line 1, however far its own line moves."""
    written, applied = _mypy_run(
        tmp_path,
        """
        flag = 1


        def first(value: int) -> int:
            total = value + 1
            doubled = total * 2
            answer = doubled - 3
            return answer


        def second(value: int) -> int:
            total = value + 1
            doubled = total * 2
            answer = doubled - 3
            return answer


        def flag() -> None:
            pass
        """,
    )
    assert applied == 1 and "__extracted_func_0" in written


class _WithAnOldError(MypyInferrer):
    """mypy, with one error the project has always had, wherever the check starts from."""

    def __init__(self, *, only_cold: str = "") -> None:
        super().__init__()
        self.forgotten = False
        self.only_cold = only_cold

    def forget_warm_state(self) -> None:
        super().forget_warm_state()
        self.forgotten = True

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        real = super().check_project(sources, excluded_paths=excluded_paths)
        if isinstance(real, CheckFailure):
            return real
        path = next(iter(sources))
        invented = [TypeDiagnostic(path, "invented: always there", 1)]
        if self.forgotten and self.only_cold:
            invented.append(TypeDiagnostic(path, self.only_cold, 1))
        return CheckSuccess((*real.errors, *invented))


@requires_mypy
@pytest.mark.parametrize("missed_while_warm", [False, True])
def test_the_cold_confirmation_compares_with_what_the_project_already_reports(
    tmp_path: Path, missed_while_warm: bool
) -> None:
    """An error there all along confirms nothing wrong; one only the cold check sees still does."""
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
    path = tmp_path / "m.py"
    path.write_text(TWINS, encoding="utf-8")
    oracle = _WithAnOldError(only_cold="invented: missed while warm" if missed_while_warm else "")
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
        if missed_while_warm:
            with pytest.raises(
                RefactoringError, match="did not report while the run was in progress"
            ):
                engine.refactor_to_fixed_point(str(path), progress="none")
            assert path.read_text(encoding="utf-8") == TWINS, "nothing was written"
        else:
            _, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
            assert applied == 1 and oracle.forgotten, "confirmed, from nothing"
    finally:
        oracle.close()


PACKAGING = '[project]\nname = "pkg"\nversion = "0"\nrequires-python = ">=3.11"\n'


@requires_mypy
def test_mypy_a_module_importing_what_mypy_cannot_find_is_left_alone_and_named(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(PACKAGING, encoding="utf-8")
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "blind.py").write_text("import towel_absent_module\n\n\n" + TWINS, encoding="utf-8")
    (package / "seen.py").write_text(TWINS, encoding="utf-8")
    run = subprocess.run(
        [sys.executable, "-c", "from towel.cli import main; main()", "dry", "pkg", "pkg"]
        + ["--no-format", "--no-interactive", "--progress", "none"],
        cwd=tmp_path,
        env={**os.environ, "TOWEL_WORKERS": "1"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "The original project's type check reports 1 error(s) in 1 file(s)." in run.stderr
    assert "No change to these 1 file(s) is attempted" in run.stderr
    assert "pkg/blind.py:1: Cannot find implementation or library stub" in run.stderr
    assert (package / "blind.py").read_text(encoding="utf-8") == (
        "import towel_absent_module\n\n\n" + TWINS
    )
    seen = (package / "seen.py").read_text(encoding="utf-8")
    helper = next(
        node
        for node in ast.parse(seen).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    assert ast.unparse(helper).splitlines()[0].endswith("(value: int) -> int:")
    assert "not verifiable: its file holds a name the type checker cannot type 1" in run.stdout

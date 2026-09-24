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

"""A change's errors are accounted for by the errors that stood where they stand.

Merging two copies of a block into one helper leaves the helper one copy's
worth of errors: the other copy's are freed, and a comparison by message
across the file let them account for a new error with the same message
elsewhere in the helper (D6: ``int | str`` with ``p + p`` accepted beside two
pre-existing ``n + s`` errors). Now an error in the helper must be matched by
one copy's error at the same statement, an error at a call site by its own
copy's, and an error on a line the change left alone by that line's. No
checker runs here: the errors are synthetic, the texts real Python.
"""

from __future__ import annotations

import importlib.util
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from towel.type_baseline import NO_SHAPE, ChangeShape, KnownErrors, ReplacedCopy
from towel.type_inference import CheckSuccess, MypyInferrer, TypeDiagnostic
from towel.unification.refactor_engine import UnificationRefactorEngine

A = "/project/a.py"
PLUS_INT_STR = 'Unsupported operand types for + ("int" and "str")  [operator]'
PLUS_STR_INT = 'Unsupported operand types for + ("str" and "int")  [operator]'

BEFORE = """\
def f1(n: int, s: str, a: int) -> None:
    bad1 = n + s
    bad2 = s + n
    good = a + a
    print(bad1, bad2, good)


def f2(n: int, s: str, b: str) -> None:
    bad1 = n + s
    bad2 = s + n

    good = b + b
    print(bad1, bad2, good)


WRONG: int = "wrong"
"""
"""D6's module: both copies hold ``n + s`` and ``s + n``; the second has a blank line in it."""

AFTER = """\
def helper(p: int | str, n: int, s: str) -> None:
    bad1 = n + s
    bad2 = s + n
    good = p + p
    print(bad1, bad2, good)


def f1(n: int, s: str, a: int) -> None:
    helper(a, n, s)


def f2(n: int, s: str, b: str) -> None:
    helper(b, n, s)


WRONG: int = "wrong"
"""
"""The module with the copies (lines 2-5 and 9-13 before) merged into ``helper``."""

SHAPE = ChangeShape((ReplacedCopy(A, 2, 5, BEFORE), ReplacedCopy(A, 9, 13, BEFORE)), A, "helper")


def _error(line: Optional[int], message: str = PLUS_INT_STR) -> TypeDiagnostic:
    return TypeDiagnostic(A, message, line)


def _new(
    reference: Sequence[TypeDiagnostic],
    after: Sequence[TypeDiagnostic],
    shape: ChangeShape = SHAPE,
    now: str = AFTER,
) -> Tuple[TypeDiagnostic, ...]:
    known = KnownErrors(tuple(reference), texts={A: BEFORE})
    return known.introduced(
        after, [A], where=lambda path: path, texts_after={A: now}.get, shape=shape
    )


D6_REFERENCE = [
    _error(2, PLUS_INT_STR),
    _error(3, PLUS_STR_INT),
    _error(9, PLUS_INT_STR),
    _error(10, PLUS_STR_INT),
]


def test_d6_two_copies_merged_free_no_slot_for_a_new_error_of_the_same_message() -> None:
    """The union helper's ``p + p`` errors carry the freed messages, on a line both copies had clean."""
    moved = [_error(2, PLUS_INT_STR), _error(3, PLUS_STR_INT)]
    added = [_error(4, PLUS_INT_STR), _error(4, PLUS_STR_INT)]
    assert _new(D6_REFERENCE, [*moved, *added]) == tuple(added)


def test_d6_the_errors_that_moved_into_the_helper_are_the_same_errors() -> None:
    assert _new(D6_REFERENCE, [_error(2, PLUS_INT_STR), _error(3, PLUS_STR_INT)]) == ()


def test_one_copy_accounts_for_the_helper_and_each_of_its_errors_once() -> None:
    """Two copies each had one; one helper with two is one new, and nothing says which."""
    twice = [_error(2), _error(2)]
    assert _new([_error(2), _error(9)], twice) == tuple(twice)


def test_the_helper_is_compared_by_statement_however_either_is_laid_out() -> None:
    """``good = b + b`` is line 12 of f2, after a blank line, and the helper's third statement."""
    reference = [_error(12, "Oops [misc]")]
    assert _new(reference, [_error(4, "Oops [misc]")]) == ()
    elsewhere = _error(5, "Oops [misc]")
    assert _new(reference, [elsewhere]) == (elsewhere,)


def test_an_error_on_the_helpers_signature_is_new() -> None:
    signature = _error(1)
    assert _new(D6_REFERENCE, [signature]) == (signature,)


def test_a_call_site_is_accounted_for_by_its_own_copy_only() -> None:
    """An error the second copy had may move to its call; the first call gets none of it."""
    reference = [_error(12, "Argument 1 [arg-type]")]
    assert _new(reference, [_error(13, "Argument 1 [arg-type]")]) == ()
    first_call = _error(9, "Argument 1 [arg-type]")
    assert _new(reference, [first_call]) == (first_call,)


def test_an_error_spent_on_the_helper_accounts_for_nothing_at_its_call_site() -> None:
    """Copy one's error explains the helper's; copy two's explains copy two's call, not one's."""
    reference = [_error(2), _error(9)]
    assert _new(reference, [_error(2), _error(13)]) == ()
    first_call = _error(9)
    assert _new(reference, [_error(2), first_call, _error(13)]) == (first_call,)


def test_the_helper_takes_the_copy_its_call_sites_leave_it() -> None:
    """Copy one's error went to its call, so the helper's is copy two's, which its call lacks."""
    reference = [_error(2), _error(9)]
    assert _new(reference, [_error(9), _error(2)]) == ()


def test_a_message_that_embeds_a_line_fails_closed_in_the_helper() -> None:
    reference = [_error(9, 'Name "bad1" already defined on line 2  [no-redef]')]
    after = [_error(2, 'Name "bad1" already defined on line 1  [no-redef]')]
    assert _new(reference, after) == tuple(after)


def test_an_error_on_a_line_the_change_left_alone_is_matched_where_it_went() -> None:
    """``WRONG`` is line 16 before and 16 after; the error there is the same one."""
    reference = [_error(16, "Incompatible types [assignment]")]
    assert _new(reference, [_error(16, "Incompatible types [assignment]")]) == ()
    in_helper = _error(4, "Incompatible types [assignment]")
    assert _new(reference, [in_helper]) == (in_helper,)


def test_a_helper_that_does_not_follow_its_copies_is_accounted_for_by_none() -> None:
    """A statement the helper has before the block's leaves no statement standing for another."""
    reordered = AFTER.replace("    bad1 = n + s\n", "    if p:\n        pass\n    bad1 = n + s\n")
    moved = [_error(4, PLUS_INT_STR)]
    assert _new(D6_REFERENCE, moved, now=reordered) == tuple(moved)


def test_a_copy_whose_statements_the_helper_does_not_keep_accounts_for_none_of_it() -> None:
    annotated = BEFORE.replace(
        "    bad2 = s + n\n    good = a + a", "    bad2: str = s + n\n    good = a + a"
    )
    shape = ChangeShape((ReplacedCopy(A, 2, 5, annotated),), A, "helper")
    known = KnownErrors(tuple(D6_REFERENCE[:2]), texts={A: annotated})
    moved = [_error(2, PLUS_INT_STR)]
    found = known.introduced(
        moved, [A], where=lambda path: path, texts_after={A: AFTER}.get, shape=shape
    )
    assert found == tuple(moved)


def test_a_copy_whose_text_is_not_the_references_accounts_for_nothing() -> None:
    """Its lines number another text than the one the reference's errors were found in."""
    stale = ChangeShape((ReplacedCopy(A, 2, 5, "# older\n" + BEFORE),), A, "helper")
    moved = [_error(2, PLUS_INT_STR)]
    assert _new(D6_REFERENCE, [*moved, _error(4)], shape=stale) != ()


def test_told_nothing_of_the_shape_the_helper_is_a_stretch_that_replaced_nothing() -> None:
    """Without a shape the helper's lines align as any others: only an identical line pairs."""
    added = [_error(4, PLUS_INT_STR), _error(4, PLUS_STR_INT)]
    assert _new(D6_REFERENCE, added, shape=NO_SHAPE) == tuple(added)


def test_a_call_to_a_function_already_there_is_compared_at_its_call_sites() -> None:
    """A reused function writes no helper: the calls take the copies' places, and their errors."""
    reused = ChangeShape((ReplacedCopy(A, 9, 13, BEFORE),))
    now = BEFORE.replace(
        "    bad1 = n + s\n    bad2 = s + n\n\n    good = b + b\n    print(bad1, bad2, good)\n\n\nW",
        "    f1(n, s, 1)\n\n\nW",
    )
    assert _new(D6_REFERENCE, [*D6_REFERENCE[:2], _error(9, PLUS_STR_INT)], reused, now) == ()
    third = _error(9, "Oops [misc]")
    assert _new(D6_REFERENCE, [*D6_REFERENCE[:2], third], reused, now) == (third,)


# --- The class of defect, generated -------------------------------------------------

MESSAGES = ("M0", "M1", "M2")


@dataclass(frozen=True)
class _Case:
    """A module, a change merging its copies into a helper, and the errors around it."""

    before: str
    after: str
    shape: ChangeShape
    reference: Tuple[TypeDiagnostic, ...]
    after_errors: Tuple[TypeDiagnostic, ...]
    injected: Optional[str]


def _generate(seed: int) -> _Case:
    """A random module with copies of a block, merged into a helper, and errors with ground truth.

    Every error after the change is either one of the reference's, put where
    the change took it (a line left alone, the helper's statement from the
    one copy the helper stands for, the call that replaced its copy), or,
    when ``injected`` names its kind, one genuinely new: an error that no
    error of the reference could account for under any pairing, because more
    of its message stand there than any copy had there.
    """
    rng = random.Random(seed)
    size = rng.randint(1, 4)
    copies = rng.randint(2, 3)
    fillers_top = rng.randint(0, 2)
    imports = rng.randint(0, 2)
    block = [f"v{index} = {index}" for index in range(size)]

    before: List[str] = []
    filler_before: Dict[str, int] = {}
    statement_before: Dict[Tuple[int, int], int] = {}
    copy_ranges: List[Tuple[int, int]] = []
    for index in range(fillers_top):
        before.append(f"top_{index} = {index}")
        filler_before[f"top_{index}"] = len(before)
    for copy in range(copies):
        before += ["", ""] if before else []
        before.append(f"def c{copy}() -> None:")
        first = len(before) + 1
        for index, statement in enumerate(block):
            if index and rng.random() < 0.3:
                before.append("")  # a layout the helper does not keep
            before.append("    " + statement)
            statement_before[(copy, index)] = len(before)
        copy_ranges.append((first, len(before)))
        for index in range(rng.randint(0, 2)):
            name = f"after_{copy}_{index}"
            before.append(f"{name} = {index}")
            filler_before[name] = len(before)
    before_text = "\n".join(before) + "\n"

    after: List[str] = [f"import added_{index}" for index in range(imports)]
    filler_after: Dict[str, int] = {}
    helper_statement: Dict[int, int] = {}
    call_after: Dict[int, int] = {}
    helper_first = helper_last = 0
    for line in before:
        name = line.split(" = ")[0]
        if line.startswith("    v") or (line == "" and after and after[-1].startswith("    v")):
            continue
        if line.startswith("def c"):
            if not helper_first:
                after += ["", ""] if after else []
                after.append("def helper() -> None:")
                helper_first = len(after)
                for index, statement in enumerate(block):
                    after.append("    " + statement)
                    helper_statement[index] = len(after)
                if rng.random() < 0.5:
                    after.append("    return None")
                helper_last = len(after)
                after += ["", ""]
            after.append(line)
            after.append("    helper()")
            call_after[int(line[len("def c")])] = len(after)
            continue
        after.append(line)
        if name in filler_before:
            filler_after[name] = len(after)
    after_text = "\n".join(after) + "\n"

    reference: List[TypeDiagnostic] = []
    placed: List[Tuple[str, object, str]] = []  # (kind, where, message)
    for name in filler_before:
        for _ in range(rng.choice((0, 0, 1, 2))):
            message = rng.choice(MESSAGES)
            reference.append(TypeDiagnostic(A, message, filler_before[name]))
            placed.append(("filler", name, message))
    for copy in range(copies):
        for index in range(size):
            for _ in range(rng.choice((0, 1, 1, 2))):
                message = rng.choice(MESSAGES)
                reference.append(TypeDiagnostic(A, message, statement_before[(copy, index)]))
                placed.append(("copy", (copy, index), message))

    chosen = rng.randrange(copies)
    after_errors: List[TypeDiagnostic] = []
    in_helper: Dict[Tuple[int, str], int] = {}
    at_call: Dict[Tuple[int, str], int] = {}
    at_filler: Dict[Tuple[str, str], int] = {}
    for kind, where, message in placed:
        fate = rng.random()
        if kind == "filler":
            assert isinstance(where, str)
            if fate < 0.7:
                after_errors.append(TypeDiagnostic(A, message, filler_after[where]))
                at_filler[(where, message)] = at_filler.get((where, message), 0) + 1
            continue
        assert isinstance(where, tuple)
        copy, index = where
        if copy == chosen and fate < 0.5:
            after_errors.append(TypeDiagnostic(A, message, helper_statement[index]))
            in_helper[(index, message)] = in_helper.get((index, message), 0) + 1
        elif fate < 0.8:
            after_errors.append(TypeDiagnostic(A, message, call_after[copy]))
            at_call[(copy, message)] = at_call.get((copy, message), 0) + 1

    def had(kind: str, where: object, message: str) -> int:
        return sum(1 for k, w, m in placed if (k, w, m) == (kind, where, message))

    injected: Optional[str] = None
    if rng.random() < 0.6:
        # Bias towards messages the copies hold, the freed slots of D6.
        pool = [m for k, _, m in placed if k == "copy"] or list(MESSAGES)
        message = rng.choice(pool)
        kind = rng.choice(("helper", "call", "filler", "import", "signature"))
        if kind == "helper":
            index = rng.randrange(size)
            most = max(had("copy", (copy, index), message) for copy in range(copies))
            for _ in range(most + 1 - in_helper.get((index, message), 0)):
                after_errors.append(TypeDiagnostic(A, message, helper_statement[index]))
            injected = kind
        elif kind == "call":
            copy = rng.randrange(copies)
            total = sum(had("copy", (copy, index), message) for index in range(size))
            for _ in range(total + 1 - at_call.get((copy, message), 0)):
                after_errors.append(TypeDiagnostic(A, message, call_after[copy]))
            injected = kind
        elif kind == "filler" and filler_after:
            name = rng.choice(sorted(filler_after))
            for _ in range(had("filler", name, message) + 1 - at_filler.get((name, message), 0)):
                after_errors.append(TypeDiagnostic(A, message, filler_after[name]))
            injected = kind
        elif kind == "import" and imports:
            after_errors.append(TypeDiagnostic(A, message, rng.randint(1, imports)))
            injected = kind
        elif kind == "signature":
            after_errors.append(TypeDiagnostic(A, message, helper_first))
            injected = kind
    rng.shuffle(after_errors)
    shape = ChangeShape(
        tuple(ReplacedCopy(A, first, last, before_text) for first, last in copy_ranges),
        A,
        "helper",
    )
    assert helper_first <= helper_last
    return _Case(before_text, after_text, shape, tuple(reference), tuple(after_errors), injected)


@settings(max_examples=400, derandomize=True, deadline=None)
@given(st.integers(min_value=0, max_value=2**32 - 1))
def test_generated_changes_reject_every_genuinely_new_error_and_accept_the_rest(seed: int) -> None:
    case = _generate(seed)
    compile(case.before, "<before>", "exec")
    compile(case.after, "<after>", "exec")
    known = KnownErrors(case.reference, texts={A: case.before})
    found = known.introduced(
        case.after_errors,
        [A],
        where=lambda path: path,
        texts_after={A: case.after}.get,
        shape=case.shape,
    )
    if case.injected is not None:
        assert found, f"a genuinely new error ({case.injected}) was accepted"
    else:
        assert found == (), "every error stood where one of the reference's stood"


# --- With mypy itself -----------------------------------------------------------------


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_mypy_d6_the_union_that_fills_the_freed_slots_is_refused(tmp_path: Path) -> None:
    """The auditor's module: Towel wrote ``p: int | str`` with ``p + p``, and mypy then failed."""
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n", encoding="utf-8")
    module = tmp_path / "m.py"
    module.write_text(BEFORE.split("\n\nWRONG")[0] + "\n", encoding="utf-8")
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
        engine.refactor_to_fixed_point(str(module), progress="none")
        written = module.read_text(encoding="utf-8")
        result = oracle.check_project({str(module): written})
    finally:
        oracle.close()
    assert "int | str" not in written, written
    assert isinstance(result, CheckSuccess)
    lines = written.split("\n")
    errors = [
        (lines[error.line - 1].strip() if error.line else "", error.message)
        for error in result.errors
    ]
    assert sorted(errors) == [
        ("bad1 = n + s", 'Unsupported operand types for + ("int" and "str")'),
        ("bad2 = s + n", 'Unsupported operand types for + ("str" and "int")'),
    ], written

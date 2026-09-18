"""Two semantic guards in pair evaluation, reached and named in the rejection trace.

``_check_shape`` declines a pair when one block returns on every path and the
other does not; ``_free_variables`` declines a pair whose block reads a name
that the enclosing function binds only after the block, since the helper
would read it before it exists. Each test builds the smallest pair that
reaches the guard and asserts the ``RejectReason`` on the ``towel.rejections``
logger at DEBUG, which is what ``DEBUG_PROPOSAL_REJECTIONS`` turns on.

The first block's coverage guard cannot fire: block enumeration already
drops every value-producing block without complete return coverage (see
``_extract_code_blocks``), and the guard tests the same predicate. A test
pins that invariant so the shadowing stays visible. The second block's
guard is still reachable, because a block that binds a variable read after
it counts as value-producing without containing a return.
"""

from __future__ import annotations

import ast
import contextlib
import io
import logging
import textwrap
from pathlib import Path
from typing import List

import pytest

from towel.unification.extractor import has_complete_return_coverage, is_value_producing
from towel.unification.models import RejectReason
from towel.unification.refactor_engine import UnificationRefactorEngine

RETURNING = """
def returning(value):
    total = value + 1
    if total > 10:
        return total
    else:
        return total * 2
"""

BINDING = """
def binding(value):
    total = value + 1
    if total > 10:
        label = total
    else:
        label = total * 2
    return label
"""

PARTIAL_RETURN = """
def partial(value):
    total = value + 1
    scaled = total * 2
    if total > 10:
        return total
    else:
        if total:
            return total * 2
"""


def _rejections(
    tmp_path: Path, source: str, caplog: pytest.LogCaptureFixture, *, expect_proposals: bool = False
) -> List[str]:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent(source))
    engine = UnificationRefactorEngine(min_lines=2)
    with (
        caplog.at_level(logging.DEBUG, logger="towel.rejections"),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        proposals = engine.analyze_files([str(path)], progress="none")
    assert bool(proposals) == expect_proposals, [p.description for p in proposals]
    return [r.getMessage() for r in caplog.records if r.name == "towel.rejections"]


def _reasons(messages: List[str]) -> List[str]:
    return [m[len("REJECT[") : m.index("]")] for m in messages if m.startswith("REJECT[")]


def test_second_block_without_complete_return_coverage_is_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    messages = _rejections(tmp_path, RETURNING + BINDING, caplog)
    hits = [m for m in messages if RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK2 in m]
    assert hits, messages
    assert all("returning@" in m and "binding@" in m for m in hits), hits


def test_coverage_guards_are_skipped_when_the_first_block_returns_variables(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Reversed, the first block's value comes from return variables and neither coverage check runs."""
    reasons = _reasons(_rejections(tmp_path, BINDING + RETURNING, caplog))
    assert reasons, "the pair is still formed and declined later"
    assert RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK1 not in reasons
    assert RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK2 not in reasons


def test_first_block_coverage_guard_is_shadowed_by_block_enumeration(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No enumerated block is value-producing without complete coverage, so the guard never fires."""
    path = tmp_path / "m.py"
    path.write_text(
        textwrap.dedent(PARTIAL_RETURN + PARTIAL_RETURN.replace("partial", "partial_twin"))
    )
    engine = UnificationRefactorEngine(min_lines=2)
    module = ast.parse(path.read_text())
    for function in module.body:
        assert isinstance(function, ast.FunctionDef)
        blocks = engine._extract_code_blocks(function)
        shapes = [(len(block), type(block[-1]).__name__) for _, block in blocks]
        assert shapes == [(2, "Assign")], "only the prefix without a return is enumerated"
        for _span, block in blocks:
            assert not (is_value_producing(block) and not has_complete_return_coverage(block))
    # The twins' prefixes pair and are extracted; no coverage guard is ever consulted.
    source = PARTIAL_RETURN + PARTIAL_RETURN.replace("partial", "partial_twin")
    reasons = _reasons(_rejections(tmp_path, source, caplog, expect_proposals=True))
    assert RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK1 not in reasons
    assert RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK2 not in reasons


LATER_BOUND_AFTER_IN_BOTH = """
def first(items):
    total = len(items) + 1
    print(total, later)
    scaled = total * 2
    later = 5
    return scaled + later


def second(items):
    total = len(items) + 1
    print(total, later)
    scaled = total * 2
    later = 7
    return scaled + later
"""

LATER_BOUND_AFTER_IN_SECOND_ONLY = """
later = 3


def first(items):
    total = len(items) + 1
    print(total, later)
    scaled = total * 2
    return scaled + later


def second(items):
    total = len(items) + 1
    print(total, later)
    scaled = total * 2
    later = 7
    return scaled + later
"""


@pytest.mark.parametrize(
    "source, reason, other_blocks_extracted",
    [
        # Both functions bind ``later`` after the block: the tail ``scaled = ...; later = N``
        # is still a legitimate pair, extracted with the constant as a parameter.
        (LATER_BOUND_AFTER_IN_BOTH, RejectReason.INCOMPLETE_LIFETIME_BLOCK1, True),
        # Only the second binds it: the tails differ in shape and nothing is extracted.
        (LATER_BOUND_AFTER_IN_SECOND_ONLY, RejectReason.INCOMPLETE_LIFETIME_BLOCK2, False),
    ],
    ids=["block1", "block2"],
)
def test_free_variable_bound_after_the_block_is_rejected(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    source: str,
    reason: RejectReason,
    other_blocks_extracted: bool,
) -> None:
    messages = _rejections(tmp_path, source, caplog, expect_proposals=other_blocks_extracted)
    hits = [m for m in messages if f"REJECT[{reason}]" in m]
    assert hits, messages
    assert all(m.endswith(":: {'later'}") for m in hits), hits

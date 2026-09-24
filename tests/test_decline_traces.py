"""Every declined pair is traced, under its reason, naming the files its blocks are in.

``DEBUG_PROPOSAL_REJECTIONS`` turns on the ``towel.rejections`` logger, and
``_debug_reject`` is its only writer: it keeps the reason for the count and
traces ``REJECT[reason]: path::function@(start, end) <-> ...``. The third
audit found the trace naming no file, so a trace over many files could not be
located. These tests hold that format for every reason there is, and keep
anything else from setting a pair's reason behind the trace's back.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import textwrap
from typing import Callable, List, cast

import pytest

from towel.unification.models import CodeBlockPair, RejectReason
from towel.unification.refactor_engine import UnificationRefactorEngine

UNIFICATION = Path(__file__).resolve().parents[1] / "src" / "towel" / "unification"

PAIR = cast(
    CodeBlockPair,
    SimpleNamespace(
        file_path="/project/a.py",
        function1_name="first",
        block1_range=(2, 5),
        file_path2="/project/b.py",
        function2_name="second",
        block2_range=(9, 12),
    ),
)
"""The fields of a pair the trace reads, in two files."""


def _traced(caplog: pytest.LogCaptureFixture, action: Callable[[], object]) -> List[str]:
    with caplog.at_level(logging.DEBUG, logger="towel.rejections"):
        action()
    return [record.getMessage() for record in caplog.records if record.name == "towel.rejections"]


@pytest.fixture(scope="module")
def engine() -> UnificationRefactorEngine:
    return UnificationRefactorEngine()


@pytest.mark.parametrize("reason", list(RejectReason), ids=lambda reason: reason.value)
def test_every_reason_is_traced_naming_the_file_of_each_block(
    engine: UnificationRefactorEngine, caplog: pytest.LogCaptureFixture, reason: RejectReason
) -> None:
    lines = _traced(caplog, lambda: engine._debug_reject(reason, PAIR, detail="why"))
    assert lines == [
        f"REJECT[{reason}]: /project/a.py::first@(2, 5) <-> /project/b.py::second@(9, 12) :: why"
    ]
    assert engine._pair_rejection is reason


def test_a_real_analysis_traces_every_decline_with_its_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
        def first(rows):
            total = 0
            for row in rows:
                total += row
            total = total + 1
            return total

        def second(rows):
            total = 0
            for row in rows:
                total += row
            total = total + 2
            return total * 2
        """))
    engine = UnificationRefactorEngine(min_lines=2)
    lines = _traced(caplog, lambda: engine.analyze_files([str(path)], progress="none"))
    rejections = [line for line in lines if line.startswith("REJECT[")]
    assert rejections, lines
    assert all(line.count(f"{path}::") == 2 for line in rejections), rejections


def test_only_the_trace_sets_a_pairs_reason() -> None:
    """``_judge_pair`` resets it; ``_debug_reject`` alone records one, and always traces it."""
    writers = sorted(
        (module.name, function.name)
        for module in UNIFICATION.glob("*.py")
        for function in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Attribute) and target.attr == "_pair_rejection"
    )
    assert writers == [
        ("refactor_engine.py", "__init__"),
        ("refactor_engine.py", "_debug_reject"),
        ("refactor_engine.py", "_judge_pair"),
    ]

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

import towel.unification
from towel.unification.models import CodeBlockPair, RejectReason
from towel.unification.refactor_engine import UnificationRefactorEngine

UNIFICATION = Path(towel.unification.__file__).resolve().parent
"""The engine's modules, as imported: what the static checks below read."""

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


# -- Declines by exception -----------------------------------------------------

THREE_ALIKE = """
def first(rows):
    total = 0
    for row in rows:
        total += row * 2
    total = total + 1
    return total

def second(rows):
    total = 0
    for row in rows:
        total += row * 2
    total = total + 2
    return total

def third(rows):
    total = 0
    for row in rows:
        total += row * 2
    total = total + 3
    return total
"""

TRACERS = frozenset({"_debug_reject", "_reject", "_debug_decline_site"})
"""The calls that trace a decline: of a pair, or of a further site a pair's helper cannot take."""

ACCOUNTED = {
    ("pair_evaluation.py", "_module_namespace_names", "SyntaxError"): (
        "returns None, which _host_namespace_reads passes on and the pair declines as"
        " bare_name_differs_by_module"
    ),
    ("pair_evaluation.py", "_builtin_evidence", "(OSError, UnicodeError, ValueError)"): (
        "the unreadable file becomes the evidence that declines the pair as"
        " builtin_may_differ_by_module"
    ),
}
"""Handlers that trace nothing themselves, and the traced decline that accounts for each."""

RAISING = frozenset({"extract_function", "generate_call"})
"""The extractor's methods that raise ``UnsupportedExtraction``."""


def _handlers(module: Path) -> List[tuple[str, ast.ExceptHandler]]:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    return [
        (function.name, handler)
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for handler in ast.walk(function)
        if isinstance(handler, ast.ExceptHandler)
    ]


@pytest.mark.parametrize("module", ["pair_evaluation.py", "clustering.py"])
def test_every_exception_caught_while_judging_a_pair_is_traced(module: str) -> None:
    """A new handler that declines without a trace fails here until it traces or is accounted for."""
    silent = [
        (module, function, ast.unparse(handler.type) if handler.type else "")
        for function, handler in _handlers(UNIFICATION / module)
        if not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in TRACERS
            for statement in handler.body
            for node in ast.walk(statement)
        )
    ]
    assert [key for key in silent if key not in ACCOUNTED] == []


@pytest.mark.parametrize("module", ["pair_evaluation.py", "clustering.py"])
def test_every_extraction_that_can_raise_is_caught(module: str) -> None:
    """Uncaught, ``UnsupportedExtraction`` would end the whole analysis rather than decline."""
    tree = ast.parse((UNIFICATION / module).read_text(encoding="utf-8"))
    guarded = {
        id(call)
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(
            handler.type is not None and "UnsupportedExtraction" in ast.unparse(handler.type)
            for handler in node.handlers
        )
        for statement in node.body
        for call in ast.walk(statement)
    }
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in RAISING
    ]
    assert calls, "the extractor is called here"
    assert [ast.unparse(call.func) for call in calls if id(call) not in guarded] == []


@pytest.mark.parametrize(
    "method, caller, prefix",
    [
        ("extract_function", "_render_helper", "REJECT"),
        ("generate_call", "_call_for_block", "REJECT"),
        ("extract_function", "_cluster_candidate_call", "DECLINE-SITE"),
        ("generate_call", "_cluster_candidate_call", "DECLINE-SITE"),
    ],
)
def test_an_extraction_the_extractor_cannot_render_is_traced_under_its_reason(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    caller: str,
    prefix: str,
) -> None:
    import sys

    from towel.unification.extractor import HygienicExtractor, UnsupportedExtraction

    real = getattr(HygienicExtractor, method)

    def raising(self: HygienicExtractor, *args: object, **kwargs: object) -> object:
        if sys._getframe(1).f_code.co_name == caller:
            raise UnsupportedExtraction("probe")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(HygienicExtractor, method, raising)
    path = tmp_path / "m.py"
    path.write_text(THREE_ALIKE)
    engine = UnificationRefactorEngine(min_lines=3)
    lines = _traced(caplog, lambda: engine.analyze_files([str(path)], progress="none"))
    traced = [line for line in lines if line.startswith(f"{prefix}[unsupported_extraction]: ")]
    assert traced and all(line.endswith("probe") for line in traced), lines
    assert all(f"{path}::" in line for line in traced)
    assert "other" not in engine.declined_pairs
    if prefix == "REJECT":
        assert engine.declined_pairs.get("unsupported_extraction", 0) == len(traced)

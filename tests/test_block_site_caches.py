"""A memoized verdict about a block answers only for the block it was computed at.

Every block guard and per-block analysis is memoized, and each reads more
than the block's own code: the scopes around its function and the module's
hazards (``rebound_external_names``), or where in its function the block
stands (what is bound before and after it, whether a loop holds it). Keyed
by the structure of the block and its function alone, a verdict computed for
one copy of some code answered for every copy: a benign nested function's
"nothing rebinds this" stood for an identical one whose enclosing function
rebinds the name with ``nonlocal``, in the same module and across modules
(the round-3 audit's P1-1), and a top-level copy's bindings stood for a copy
inside a loop of the same function. The engine is asked directly, in both
orders; one run through the CLI compares what the programs print. The
batteries hold the audit's reproducers (the ``r7c_`` and ``xf7c_`` fixtures),
and ``test_memoization_is_invisible`` the property behind them.
"""

from __future__ import annotations

import ast
import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Dict, List, Tuple
from unittest.mock import patch

import pytest

from towel import cli
from towel.unification.assignment_analyzer import has_reassignments_without_bindings
from towel.unification.models import FunctionNode
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import nested_bindings_escape

NESTED = """
    def f():
        print("start", x)
        cb()
        print("after", x)

    def g():
        print("start", x)
        cb()
        print("after", x)

    f()
    g()
"""
BENIGN = "def benign(cb):\n    x = 0\n" + NESTED
# ``cb`` rebinds the ``x`` that ``f`` reads after calling it: passed to a
# helper as an argument, ``x`` would be read before ``cb`` runs.
HAZARD = "def hazard():\n    x = 0\n\n    def cb():\n        nonlocal x\n        x += 1\n" + NESTED

# The loop's copy binds ``w``, which the next iteration reads before it runs
# again; the top-level copy is the same code.
LOOP_COPY = """
def f(items):
    w = len(items)
    print("w", w)
    print("x")
    for _ in range(3):
        print("before", w)
        items.append(0)
        w = len(items)
        print("w", w)
        print("x")
"""


def _engine_over(tmp_path: Path, sources: Dict[str, str]) -> Tuple[
    UnificationRefactorEngine,
    Dict[Tuple[str, str], FunctionNode],
    Dict[str, ScopeAnalyzer],
]:
    """An engine that has read ``sources`` as one analysis would, without judging a pair.

    Its functions, by module and name, and each module's scope analyzer.
    """
    engine = UnificationRefactorEngine(min_lines=3)
    functions: Dict[Tuple[str, str], FunctionNode] = {}
    analyzers: Dict[str, ScopeAnalyzer] = {}
    for name, source in sources.items():
        path = tmp_path / name
        path.write_text(source)
        analysis = engine.analysis_session.analyze_module(str(path))
        engine._record_function_paths(analysis.functions)
        analyzers[name] = analysis.module.scope_analyzer
        for entry in analysis.functions:
            enclosing = entry.enclosing_function
            qualified = f"{enclosing}.{entry.node.name}" if enclosing else entry.node.name
            functions[name, qualified] = entry.node
    return engine, functions, analyzers


@pytest.mark.parametrize("benign_first", [True, False])
@pytest.mark.parametrize("modules", ["one", "two"])
def test_a_rebinding_hazard_is_answered_for_its_own_site(
    tmp_path: Path, benign_first: bool, modules: str
) -> None:
    files = (
        {"m.py": BENIGN + "\n\n" + HAZARD} if modules == "one" else {"a.py": BENIGN, "b.py": HAZARD}
    )
    engine, functions, analyzers = _engine_over(tmp_path, files)
    benign_module, hazard_module = ("m.py", "m.py") if modules == "one" else ("a.py", "b.py")
    asked = [
        (benign_module, functions[benign_module, "benign.f"], frozenset()),
        (hazard_module, functions[hazard_module, "hazard.f"], frozenset({"x"})),
    ]
    benign, hazard = asked[0][1], asked[1][1]
    # Structurally the two are one function; they stand in different places.
    assert engine._sid([benign]) == engine._sid([hazard])
    assert engine._block_site(benign, benign.body) != engine._block_site(hazard, hazard.body)
    for module, function, expected in asked if benign_first else asked[::-1]:
        site = engine._block_site(function, function.body)
        assert site is not None
        found = engine._rebound_external_names(function, function.body, analyzers[module], site)
        assert found == expected


@pytest.mark.parametrize("top_level_first", [True, False])
def test_each_copy_of_a_block_is_judged_where_it_stands(
    tmp_path: Path, top_level_first: bool
) -> None:
    engine, functions, _ = _engine_over(tmp_path, {"m.py": LOOP_COPY})
    function = functions["m.py", "f"]
    loop = function.body[3]
    assert isinstance(loop, ast.For)
    top_level: List[ast.stmt] = function.body[:3]
    in_loop: List[ast.stmt] = loop.body[2:]
    assert engine._sid(top_level) == engine._sid(in_loop)
    reassignments = engine._get_assignment_reuse(function)
    # The loop's copy binds ``w`` read outside it, and rebinds the ``w`` the
    # top-level copy bound first.
    copies = [(top_level, False, {"w"}), (in_loop, True, set())]
    for block, escapes, initially_bound in copies if top_level_first else copies[::-1]:
        site = engine._block_site(function, block)
        assert engine._block_rejected(nested_bindings_escape, block, function, site=site) is escapes
        span = engine._block_line_span(block)
        assert span is not None
        snapshot = engine._build_block_binding_snapshot(
            function, block, span, reassignments, site=site
        )
        assert snapshot.initially_bound == initially_bound
        unsafe, _ = engine._per_block(
            "reassignments",
            lambda: has_reassignments_without_bindings(function, block, reassignments),
            site=site,
        )
        assert unsafe is escapes


def test_a_block_of_a_function_the_analysis_did_not_record_is_judged_afresh() -> None:
    engine = UnificationRefactorEngine(min_lines=3)
    function = ast.parse(LOOP_COPY).body[0]
    assert isinstance(function, ast.FunctionDef)
    assert engine._block_site(function, function.body[:3]) is None
    answers = iter([1, 2])
    assert engine._per_block("probe", lambda: next(answers), site=None) == 1
    assert engine._per_block("probe", lambda: next(answers), site=None) == 2
    assert not engine._per_block_cache


def test_a_rebinding_hazard_beside_a_benign_twin_keeps_its_behavior(tmp_path: Path) -> None:
    source = tmp_path / "m.py"
    source.write_text(BENIGN + "\n\n" + HAZARD)
    output = tmp_path / "out" / "m.py"
    arguments = ["towel", "dry", str(source), str(output), "--no-interactive", "--no-types"]
    with (
        patch.object(sys, "argv", [*arguments, "--progress", "none"]),
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()),
    ):
        cli.main()
    program = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('m', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(m)\n"
        "m.benign(lambda: None)\nm.hazard()\n"
    )

    def prints(path: Path) -> str:
        return subprocess.run(
            [sys.executable, "-c", program, str(path)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        ).stdout

    assert prints(output) == prints(source)
    refactored = output.read_text()
    # The benign twins share a helper; the hazardous ones keep their code.
    assert refactored.count("__extracted_func_") >= 3
    hazard = refactored[refactored.index("def hazard") :]
    assert "__extracted_func_" not in hazard

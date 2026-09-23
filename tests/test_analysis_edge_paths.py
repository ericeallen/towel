"""Analysis branches the suite did not reach, each driven through the real code.

A scan for ``nonlocal`` without a scope analyzer, f-string format specs whose
literal text differs, ``try`` blocks of imports ahead of a helper, attribute
reflection on modules and unresolved names, a variable passed by keyword to a
call, the validation trace, and mypy configuration found in ``setup.cfg``.
"""

from __future__ import annotations

import ast
import contextlib
import io
import logging
from pathlib import Path
from typing import Optional

import pytest

from tests.test_helpers import parse_block
from towel.diagnostics import Settings
from towel.type_inference import project_configures_mypy
from towel.unification.hof_promotion import _is_used_as_callable_or_value_later
from towel.unification.insertion import InsertionPoints
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.unifier import Unifier


def _inner_function(source: str) -> ast.FunctionDef:
    """The last function defined inside ``outer`` in ``source``."""
    outer = ast.parse(source).body[0]
    assert isinstance(outer, ast.FunctionDef)
    inner = outer.body[-1]
    assert isinstance(inner, ast.FunctionDef)
    return inner


@pytest.mark.parametrize(
    "inner, declares",
    [
        ("    def f():\n        nonlocal x\n        x = 1\n", True),
        ("    def f():\n        if x:\n            nonlocal x\n            x = 2\n", True),
        (
            "    def f():\n        def g():\n            nonlocal x\n            x = 1\n        return g\n",
            False,
        ),
        ("    def f():\n        return x\n", False),
    ],
    ids=["own", "own-nested-in-if", "only-in-nested-function", "none"],
)
def test_nonlocal_scan_without_a_scope_analyzer_reads_only_the_function_itself(
    inner: str, declares: bool
) -> None:
    function = _inner_function("def outer():\n    x = 0\n" + inner)
    engine = UnificationRefactorEngine()
    assert engine._declares_nonlocal(function, None) is declares
    assert engine._declares_nonlocal(None, None) is False


def test_format_specs_with_different_literal_text_do_not_unify() -> None:
    unifier = Unifier(max_parameters=5, parameterize_constants=True)
    right_aligned = parse_block("x = f'{v:>{w}}'\ny = x + 'a'\n")
    left_aligned = parse_block("x = f'{v:<{w}}'\ny = x + 'a'\n")
    assert unifier.unify_blocks([right_aligned, left_aligned], [{}, {}]) is None
    also_right = parse_block("x = f'{v:>{w}}'\ny = x + 'a'\n")
    assert unifier.unify_blocks([right_aligned, also_right], [{}, {}]) is not None


@pytest.mark.parametrize(
    "prefix, expected",
    [
        ("try:\n    import ujson as json\nexcept ImportError:\n    import json\n", {"CONST", "f"}),
        (
            "try:\n    import a\nexcept ImportError:\n    import b\nelse:\n    import c\n"
            "finally:\n    FLAG = True\n",
            {"CONST", "f"},
        ),
        ("try:\n    configure()\nexcept RuntimeError:\n    pass\n", set()),
    ],
    ids=["optional-import", "all-four-clauses", "call-inside"],
)
def test_a_try_of_definitions_can_precede_a_helper_but_a_call_in_it_cannot(
    prefix: str, expected: set[str]
) -> None:
    assert InsertionPoints.placeable_after(prefix + "CONST = 1\ndef f():\n    pass\n") == expected


@pytest.mark.parametrize(
    "source, reflective",
    [
        ("def f(obj):\n    setattr(obj, 'a', 1)\n", False),
        ("class Box:\n    pass\ndef f():\n    box = Box()\n    delattr(box, 'a')\n", False),
        ("import os\ndef f():\n    setattr(os, 'a', 1)\n", True),
        ("def f():\n    return getattr(unknown, 'a')\n", True),
        ("def f():\n    return getattr(f(), 'a')\n", True),
    ],
    ids=["parameter", "local", "module", "unresolved-name", "call-result"],
)
def test_attribute_reflection_is_a_hazard_on_modules_and_unresolved_targets(
    source: str, reflective: bool
) -> None:
    analyzer = ScopeAnalyzer()
    analyzer.analyze(ast.parse(source))
    summary = analyzer.external_binding_hazards
    assert summary is not None
    assert summary.reflective is reflective


@pytest.mark.parametrize(
    "later, used",
    [
        ("run(callback=fn)", True),
        ("run(fn)", True),
        ("fn()", True),
        ("run(other=x)", False),
        ("run(callback=fn.attr)", False),
        ("class C:\n    run(callback=fn)", False),
    ],
    ids=["keyword", "positional", "callee", "another-name", "attribute", "nested-class"],
)
def test_a_variable_passed_by_keyword_counts_as_a_later_call_use(later: str, used: bool) -> None:
    block = parse_block("fn = make()\n" + later + "\n")
    assert _is_used_as_callable_or_value_later(block, 0, "fn") is used


def test_validation_debugging_traces_both_blocks_of_a_pair(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "m.py"
    body = "    y = x + 1\n    z = y * 2\n    return z\n"
    path.write_text("def first(x):\n" + body + "\ndef second(x):\n" + body)
    settings = Settings(**{**Settings.from_environ().__dict__, "debug_validation": True})
    engine = UnificationRefactorEngine(min_lines=2, settings=settings)
    with (
        caplog.at_level(logging.DEBUG, logger="towel.validation"),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        proposals = engine.analyze_file(str(path))
    assert [p.description for p in proposals] == ["Extract common code from first and second"]
    messages = [r.getMessage() for r in caplog.records if r.name == "towel.validation"]
    assert "\n=== Block1 Validation Debug ===" in messages
    assert "\n=== Block2 Validation Debug ===" in messages
    assert "Function: first" in messages and "Function: second" in messages
    assert "  Value-producing check: block1=True, block2=True" in messages


@pytest.mark.parametrize(
    "setup_cfg, configured",
    [
        (None, False),
        ("[mypy]\nstrict = true\n", True),
        ("[flake8]\nmax-line-length = 100\n", False),
        ("[mypy\nbroken", False),
    ],
    ids=["absent", "mypy-section", "other-section", "corrupt"],
)
def test_mypy_configuration_is_read_from_setup_cfg(
    tmp_path: Path, setup_cfg: Optional[str], configured: bool
) -> None:
    if setup_cfg is not None:
        (tmp_path / "setup.cfg").write_text(setup_cfg)
    assert project_configures_mypy(tmp_path) is configured

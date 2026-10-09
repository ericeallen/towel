# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Owner-scoped return-read summaries preserve lazy fallbacks, output and lifetime."""

from __future__ import annotations

import ast
from contextlib import ExitStack, nullcontext
import gc
import os
import signal
import sys
import traceback
from typing import Literal, Set, cast
from unittest.mock import patch
import weakref

import pytest
from towel.canonical_ast import exact_dump
from towel.diagnostics import VALIDATION
from towel.unification import block_analysis
from towel.unification import return_source_uses as usage
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.semantic_safety import walk_own_scope


def _function(source: str = "def f():\n before=a\n middle=b\n return c\n") -> ast.FunctionDef:
    return cast(ast.FunctionDef, ast.parse(source).body[0])


def _names(node: ast.AST) -> Set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _scope() -> usage._SourceUses:
    scope = usage._SOURCE_USES.get()
    assert scope is not None
    return scope


def test_usage_rows_keep_original_traversal_and_skip_missing_locations() -> None:
    function = _function(
        "def f():\n if flag:\n  body(a)\n else:\n  alternative(b)\n def inner():\n  hidden(c)\n return result\n"
    )
    function.body.insert(1, ast.Pass())
    expected = [
        node
        for statement in function.body
        for node in walk_own_scope(statement)
        if isinstance(node, ast.stmt) and hasattr(node, "lineno")
    ]
    owner = object()
    with usage.sharing_return_source_uses(owner):
        rows = list(usage.return_statement_usages(owner, function, _names, 0))
    assert [(row.lineno, row.kind, row.names) for row in rows] == [
        (node.lineno, type(node).__name__, frozenset(_names(node))) for node in expected
    ]
    assert not any(row.lineno == 7 for row in rows)
    assert len(rows) == len(expected)


def test_readonly_function_builds_rows_once_and_query_filters_stay_fresh() -> None:
    function = _function()
    owner = object()
    seen: list[int] = []
    before = exact_dump(function)

    def read(node: ast.AST) -> Set[str]:
        assert isinstance(node, ast.stmt)
        seen.append(node.lineno)
        return _names(node)

    with (
        usage.sharing_return_source_uses(owner),
        patch.object(usage, "walk_own_scope", wraps=walk_own_scope) as walked,
    ):
        first = list(usage.return_statement_usages(owner, function, read, 2))
        second = list(usage.return_statement_usages(owner, function, read, 3))
        assert seen == [2, 3, 4]
        assert walked.call_count == len(function.body)
        assert [row.lineno for row in first] == [3, 4]
        assert [row.lineno for row in second] == [4]
        assert all(type(row.names) is frozenset for row in first)
    assert exact_dump(function) == before


@pytest.mark.parametrize("mode", ["outside", "disabled", "mismatch", "zero"])
def test_fallback_keeps_lazy_line_filter_before_reader(
    mode: Literal["outside", "disabled", "mismatch", "zero"],
) -> None:
    function = _function()
    owner = object()
    seen: list[int] = []

    def read(node: ast.AST) -> Set[str]:
        assert isinstance(node, ast.stmt)
        assert node.lineno > 3
        seen.append(node.lineno)
        return _names(node)

    with (
        nullcontext()
        if mode == "outside"
        else usage.sharing_return_source_uses(owner, entries=0 if mode == "zero" else 2048)
    ), (memoization_disabled() if mode == "disabled" else nullcontext()):
        actual_owner = object() if mode == "mismatch" else owner
        iterator = usage.return_statement_usages(actual_owner, function, read, 3)
        assert not seen
        assert [row.lineno for row in iterator] == [4]
        assert seen == [4]


def test_each_return_set_is_fresh_and_caller_mutation_cannot_change_rows() -> None:
    engine = UnificationRefactorEngine()
    function = _function()
    with usage.sharing_return_source_uses(engine):
        first = engine._find_return_variables(function, (2, 2), {"b", "c"})
        assert first == {"b", "c"}
        first.clear()
        first.add("poison")
        assert engine._find_return_variables(function, (2, 2), {"b", "c"}) == {"b", "c"}
        assert engine._find_return_variables(function, (2, 3), {"c"}) == {"c"}


def test_lifetime_requirement_is_computed_for_each_call() -> None:
    engine = UnificationRefactorEngine()
    function = _function()
    block = [function.body[0]]
    with (
        usage.sharing_return_source_uses(engine),
        patch.object(engine, "_is_value_producing", return_value=False),
        patch.object(
            block_analysis,
            "lifetime_bound_names",
            side_effect=[{"first_lifetime"}, {"second_lifetime"}],
        ) as lifetime,
    ):
        assert engine._find_return_variables(function, (2, 2), {"b", "c"}, block) == {
            "b",
            "c",
            "first_lifetime",
        }
        assert engine._find_return_variables(function, (2, 2), {"b", "c"}, block) == {
            "b",
            "c",
            "second_lifetime",
        }
        assert lifetime.call_count == 2


def test_debug_records_match_original_loop_and_emit_every_call() -> None:
    engine = UnificationRefactorEngine()
    function = _function(
        "def f():\n before=0\n return (" + ",".join(f"name_{n}" for n in range(80)) + ")\n"
    )
    initially = {f"name_{n}" for n in range(80)}
    expected: list[str] = []
    result: Set[str] = set()
    for statement in function.body:
        for node in walk_own_scope(statement):
            if not isinstance(node, ast.stmt) or not hasattr(node, "lineno") or node.lineno <= 2:
                continue
            names = engine._used_names(node)
            expected.append(f"  SOURCE: stmt@{node.lineno} ({type(node).__name__}) uses {names}")
            overlap = names & initially
            if overlap:
                result.update(overlap)
                expected.append(
                    f"    RETURN NEEDED (SOURCE): Variable(s) {overlap} will be returned from extracted function"
                )
    expected.append(f"SOURCE requires returning: {result}")
    actual: list[str] = []

    def debug(message: object) -> None:
        assert isinstance(message, str)
        actual.append(message)

    with (
        usage.sharing_return_source_uses(engine),
        patch.object(block_analysis, "debugging", return_value=True),
        patch.object(VALIDATION, "debug", debug),
    ):
        assert (
            engine._find_return_variables(function, (2, 2), initially, debug_label="SOURCE")
            == result
        )
        assert (
            engine._find_return_variables(function, (2, 2), initially, debug_label="SOURCE")
            == result
        )
    assert actual == expected + expected


def test_nested_context_and_exception_restore_and_clear_rows() -> None:
    owner = object()
    function = _function()
    original = usage._SOURCE_USES.get()
    with usage.sharing_return_source_uses(owner):
        outer = _scope()
        list(usage.return_statement_usages(owner, function, _names, 0))
        with pytest.raises(ValueError, match="scope failure"):
            with usage.sharing_return_source_uses(owner):
                inner = _scope()
                assert inner is not outer
                list(usage.return_statement_usages(owner, function, _names, 0))
                raise ValueError("scope failure")
        assert not inner.rows and _scope() is outer and len(outer.rows) == 1
    assert not outer.rows and usage._SOURCE_USES.get() is original


def test_strong_function_keys_are_entry_bounded_and_released_on_exit() -> None:
    owner = object()
    with usage.sharing_return_source_uses(owner, entries=1):
        first = _function()
        reference = weakref.ref(first)
        list(usage.return_statement_usages(owner, first, _names, 0))
        del first
        gc.collect()
        assert reference() is not None
        second = _function("def g():\n return value\n")
        second_reference = weakref.ref(second)
        list(usage.return_statement_usages(owner, second, _names, 0))
        del second
        gc.collect()
        assert reference() is None and second_reference() is not None and len(_scope().rows) == 1
    gc.collect()
    assert second_reference() is None


@pytest.mark.parametrize("mode", ["direct", "new-scope"])
def test_mutation_between_readonly_scopes_gets_fresh_rows(
    mode: Literal["direct", "new-scope"],
) -> None:
    owner = object()
    function = _function("def f():\n return before\n")
    with usage.sharing_return_source_uses(owner) if mode == "new-scope" else nullcontext():
        assert next(usage.return_statement_usages(owner, function, _names, 0)).names == frozenset(
            {"before"}
        )
    returned = cast(ast.Return, function.body[0])
    returned.value = ast.Name(id="after", ctx=ast.Load())
    with usage.sharing_return_source_uses(owner) if mode == "new-scope" else nullcontext():
        assert next(usage.return_statement_usages(owner, function, _names, 0)).names == frozenset(
            {"after"}
        )


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Requires native fork")
def test_fork_filters_inherited_immutable_rows_and_clears_child_scope() -> None:
    owner = object()
    function = _function()
    with usage.sharing_return_source_uses(owner):
        expected = tuple(usage.return_statement_usages(owner, function, _names, 3))
        scope = _scope()
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            status = 1
            os.close(read_fd)
            signal.alarm(5)
            try:
                assert tuple(usage.return_statement_usages(owner, function, _names, 3)) == expected
                with usage.sharing_return_source_uses(owner):
                    child_scope = _scope()
                    assert child_scope is not scope
                    assert (
                        tuple(usage.return_statement_usages(owner, function, _names, 3)) == expected
                    )
                assert not child_scope.rows and _scope() is scope
                assert os.write(write_fd, b"1") == 1
                status = 0
            except BaseException:
                traceback.print_exc()
                sys.stderr.flush()
            finally:
                os.close(write_fd)
                os._exit(status)
        os.close(write_fd)
        try:
            response = os.read(read_fd, 1)
        finally:
            os.close(read_fd)
            waited, status = os.waitpid(pid, 0)
        assert (
            waited == pid
            and os.WIFEXITED(status)
            and os.WEXITSTATUS(status) == 0
            and response == b"1"
        )
        assert tuple(usage.return_statement_usages(owner, function, _names, 3)) == expected


def test_empty_binding_requirement_never_walks_source_or_lifetime() -> None:
    engine = UnificationRefactorEngine()
    function = _function()
    with (
        usage.sharing_return_source_uses(engine),
        patch.object(engine, "_used_names", side_effect=AssertionError("Unexpected source walk")),
        patch.object(
            block_analysis,
            "lifetime_bound_names",
            side_effect=AssertionError("Unexpected lifetime scan"),
        ),
    ):
        first = engine._find_return_variables(function, (2, 2), set(), [function.body[0]])
        second = engine._find_return_variables(function, (2, 2), set(), [function.body[0]])
        assert not first and not second and first is not second
        assert not _scope().rows


def test_scope_exit_releases_function_keys_even_when_memoization_is_disabled() -> None:
    owner = object()
    manager = ExitStack()
    manager.enter_context(usage.sharing_return_source_uses(owner))
    try:
        function = _function()
        reference = weakref.ref(function)
        list(usage.return_statement_usages(owner, function, _names, 0))
        scope = _scope()
        del function
        gc.collect()
        assert reference() is not None
        with memoization_disabled():
            manager.close()
        gc.collect()
        assert len(scope.rows) == 0 and reference() is None
    finally:
        manager.close()

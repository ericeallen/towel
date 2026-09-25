"""The binding, read and observability collectors on the smallest code that exercises each.

Four defects of 1.772 lived here. A ``for`` target, a ``match`` capture or a
nested ``def`` that rebound a name bound before the block was taken for a
fresh binding (the reassignment collector recorded none of them); a later
``count += 1`` or ``del count`` was not a read of ``count`` (the read
collectors took only loads); a block that read a local before binding it
passed the name from a call site where it was no longer local; and blocks
equal up to their binders' names shared a helper whose spelling reached
``UnboundLocalError``. Each construct has its test here, and
``test_binding_collectors_match_cpython`` checks the collectors against
CPython on generated code.
"""

from __future__ import annotations

import ast
import contextlib
import io
import logging
import sys
import textwrap
from pathlib import Path
from typing import List, Set, Tuple

import pytest

from towel.unification.assignment_analyzer import (
    analyze_assignments,
    has_reassignments_without_bindings,
    own_scope_bindings,
    scope_declarations,
)
from towel.unification.instantiation import observable_renamings
from towel.unification.models import RejectReason
from towel.unification.orphan_detector import orphaned_variables
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.semantic_safety import unbinds_external_name
from towel.unification.statement_facts import bindings_of, loaded_names
from towel.unification.visitors import NameCollector


def _function(source: str) -> ast.FunctionDef:
    node = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def _classified(source: str) -> List[Tuple[str, bool]]:
    function = _function(source)
    result = analyze_assignments(function)
    return [
        (binding.name, result[binding.node_id]) for binding in own_scope_bindings(function.body)
    ]


def _rebinds(source: str, start: int, end: int) -> Set[str]:
    """The names bound before ``body[start:end]`` that the block's guards decline it for rebinding.

    ``has_reassignments_without_bindings`` flags a rebinding; an ``except
    ... as`` rebinding also deletes the name, which ``unbinds_external_name``
    declines against the names the engine finds bound before the block.
    """
    function = _function(source)
    block = function.body[start:end]
    reassignments = analyze_assignments(function)
    unsafe, names = has_reassignments_without_bindings(function, block, reassignments)
    assert unsafe == bool(names)
    engine = UnificationRefactorEngine()
    span = engine._block_line_span(block)
    assert span is not None
    before = engine._compute_block_binding_snapshot(
        function, block, span, reassignments
    ).bound_before_block
    if unbinds_external_name(function, block, before):
        handlers = {
            node.name
            for statement in block
            for node in ast.walk(statement)
            if isinstance(node, ast.ExceptHandler) and node.name
        }
        names = names | (handlers & before)
    return names


# -- the reassignment collector: every construct that binds a name -------------------


@pytest.mark.parametrize(
    "statement",
    [
        "for i in xs:\n        pass",
        "for i, j in xs:\n        pass",
        "with open(xs) as i:\n        pass",
        "try:\n        pass\n    except E as i:\n        pass",
        "try:\n        pass\n    except* E as i:\n        pass",
        "match xs:\n        case [i]:\n            pass",
        "match xs:\n        case [*i]:\n            pass",
        "match xs:\n        case {'k': 1, **i}:\n            pass",
        "match xs:\n        case [1] as i:\n            pass",
        "import i",
        "import i.sub",
        "import os as i",
        "from os import i",
        "from os import sep as i",
        "if xs:\n        def i():\n            pass",
        "if xs:\n        async def i():\n            pass",
        "if xs:\n        class i:\n            pass",
        "(i := xs)",
        "[(i := x) for x in xs]",
        "i: int = xs",
        "i += xs",
        "i = j = xs",
        "[a, *i] = xs",
    ],
)
def test_every_binding_construct_rebinding_a_name_bound_before_the_block_is_flagged(
    statement: str,
) -> None:
    source = f"def f(xs):\n    i = -1\n    {statement}\n    return i\n"
    assert "i" in _rebinds(source, 1, 2), source


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type aliases are Python 3.12 syntax")
def test_a_type_alias_rebinding_a_name_bound_before_the_block_is_flagged() -> None:
    assert "i" in _rebinds("def f(xs):\n    i = -1\n    type i = int\n    return i\n", 1, 2)


def test_an_except_name_in_a_nested_block_is_not_bound_before_it() -> None:
    """The clause deletes its name as it ends: it binds nothing a later block could lose."""
    source = """
    def f(xs):
        if xs:
            try:
                r = 1 / xs[0]
            except ZeroDivisionError as error:
                print(error)
            print(r)
        return xs
    """
    function = _function(source)
    outer = function.body[0]
    assert isinstance(outer, ast.If)
    engine = UnificationRefactorEngine()
    block = outer.body[:1]
    span = engine._block_line_span(block)
    assert span is not None
    snapshot = engine._compute_block_binding_snapshot(
        function, block, span, analyze_assignments(function)
    )
    assert "error" not in snapshot.bound_before_block
    assert not unbinds_external_name(function, block, snapshot.bound_before_block)


def test_the_first_binding_of_a_name_in_the_block_is_not_flagged() -> None:
    source = """
    def f(xs):
        print(xs)
        for i in xs:
            pass
        match xs:
            case [j]:
                pass
        if xs:
            def g():
                pass
        return i, j, g
    """
    assert _rebinds(source, 1, 4) == set()


def test_the_three_rebindings_the_audit_reported() -> None:
    loop = "def f(xs):\n    i = -1\n    for i in xs:\n        print(i)\n    last = i * 2\n    return last\n"
    capture = (
        "def f(v):\n    x = -1\n    match v:\n        case [x]:\n            print(x)\n"
        "        case _:\n            print(0)\n    y = x * 2\n    return y\n"
    )
    definition = (
        "def f(flag):\n    def g():\n        return 1\n    print(flag)\n    if flag:\n"
        "        def g():\n            return 2\n    r = g()\n    return r\n"
    )
    assert _rebinds(loop, 1, 4) == {"i"}
    assert _rebinds(capture, 1, 4) == {"x"}
    assert _rebinds(definition, 1, 5) == {"g"}


def test_bindings_are_classified_in_evaluation_order() -> None:
    assert _classified("""
        def f(p):
            for p in p:
                x = p
            x = (y := x)
            y += 1
        """) == [("p", True), ("x", False), ("y", False), ("x", True), ("y", True)]


def test_what_a_nested_scope_binds_is_its_own_but_its_head_runs_here() -> None:
    assert (
        _classified("""
        def f():
            class C(base := object):
                attribute = 1
            @(decorator := staticmethod)
            def g(a=(default := 1)) -> (returned := int):
                inner = a
            h = lambda b=(lambda_default := 2): (inside := b)
            [(walrus := k) for k in range(3)]
        """)
        == [
            ("base", False),
            ("C", False),
            ("decorator", False),
            ("default", False),
            ("returned", False),
            ("g", False),
            ("lambda_default", False),
            ("h", False),
            ("walrus", False),
        ]
    )


def test_an_annotation_without_a_value_binds_nothing() -> None:
    assert _classified("def f():\n    x: int\n    y: int = 1\n") == [("y", False)]


def test_declarations_anywhere_in_the_scope_relax_the_rule() -> None:
    source = """
    def f(xs):
        if xs:
            global counter
        counter = 1
        for counter in xs:
            pass
        def g():
            nonlocal other
    """
    function = _function(source)
    assert scope_declarations(function) == {"counter"}
    assert _rebinds(source, 2, 3) == set()


def test_bindings_of_counts_what_a_definition_evaluates_where_it_stands() -> None:
    statement = ast.parse(
        "def g(a=(x := 1)) -> (y := int):\n    z = a\nlambda b=(w := 2): (v := b)\n"
    ).body
    names = set().union(*(bindings_of(node, into_nested_scopes=False) for node in statement))
    assert names == {"g", "x", "y", "w"}


# -- the read collectors: += and del need the binding ----------------------------------


@pytest.mark.parametrize(
    "statement, reads",
    [
        ("count += 1", {"count"}),
        ("del count", {"count"}),
        ("del (count, other)", {"count", "other"}),
        ("del count[key]", {"count", "key"}),
        ("count.attribute += step", {"count", "step"}),
        ("count: int", {"int"}),
        ("count: int = value", {"int", "value"}),
    ],
)
def test_loaded_names_counts_augmented_targets_and_deletions(
    statement: str, reads: Set[str]
) -> None:
    assert loaded_names(ast.parse(statement)) == reads


def _collected(source: str) -> Set[str]:
    collector = NameCollector()
    for statement in ast.parse(textwrap.dedent(source)).body:
        collector.visit(statement)
    return collector.used


def test_the_name_collector_counts_augmented_targets_and_deletions() -> None:
    assert _collected("count += 1\ndel total\n") == {"count", "total"}


def test_the_name_collector_reads_definition_heads_and_class_annotations() -> None:
    source = """
    @decorator
    def g(a: annotation = default) -> returned:
        return body_only
    class C(base):
        attribute: class_annotation = value
    local: never_evaluated = assigned
    """
    assert _collected(source) == {
        "decorator",
        "annotation",
        "default",
        "returned",
        "base",
        "class_annotation",
        "value",
        "assigned",
    }


@pytest.mark.parametrize("later", ["count += 1", "del count", "print(count)"])
def test_a_later_augmented_assignment_or_del_orphans_what_the_block_binds(later: str) -> None:
    function = _function(
        f"def f(xs, flag):\n    count = len(xs)\n    print(count)\n    if flag:\n"
        f"        {later}\n    return xs\n"
    )
    assert orphaned_variables(function.body, (0, 1)) == {"count"}


def test_a_later_rebinding_before_the_read_is_not_an_orphan() -> None:
    function = _function(
        "def f(xs):\n    count = len(xs)\n    print(count)\n    count = 0\n"
        "    count += 1\n    return xs\n"
    )
    assert orphaned_variables(function.body, (0, 1)) == set()


# -- which renamed binders the running block can observe -------------------------------


def _observable(source: str, *renamed: str) -> Set[str]:
    return set(observable_renamings(ast.parse(textwrap.dedent(source)).body, set(renamed)))


@pytest.mark.parametrize(
    "source, renamed, observable",
    [
        # The audit's case: a del on one path, then a read.
        ("item = xs[0]\nif flag:\n    del item\nr = item * 2\n", "item", {"item"}),
        ("item = xs[0]\nr = item * 2\n", "item", set()),
        ("for item in xs:\n    print(item)\n", "item", set()),
        ("for item in xs:\n    pass\nprint(item)\n", "item", {"item"}),
        ("while xs:\n    print(item)\n    item = 1\n", "item", {"item"}),
        ("item = 1\nwhile xs:\n    print(item)\n    del item\n", "item", {"item"}),
        ("try:\n    r = 1\nexcept E as err:\n    r = 0\nprint(err)\n", "err", {"err"}),
        ("try:\n    r = 1\nexcept E as err:\n    print(err)\n", "err", set()),
        ("try:\n    v = f()\nexcept E:\n    v = 0\nprint(v)\n", "v", set()),
        ("try:\n    v = f()\nfinally:\n    print(v)\n", "v", {"v"}),
        ("if (m := f()) is not None:\n    print(m)\n", "m", set()),
        ("if xs and (m := f()):\n    print(m)\nelse:\n    print(m)\n", "m", {"m"}),
        ("if xs and (m := f()):\n    print(m)\n", "m", set()),
        ("if not (m := f()):\n    pass\nprint(m)\n", "m", set()),
        ("r = xs or (m := 1)\nprint(m)\n", "m", {"m"}),
        ("r = m if (m := f()) else 0\n", "m", set()),
        ("r = a < (m := f()) < m\n", "m", set()),
        ("r = a < b < (m := f())\nprint(m)\n", "m", {"m"}),
        ("match v:\n    case [x]:\n        print(x)\n", "x", set()),
        ("match v:\n    case [x]:\n        pass\nprint(x)\n", "x", {"x"}),
        (
            "match v:\n    case [x]:\n        pass\n    case x:\n        pass\nprint(x)\n",
            "x",
            set(),
        ),
        ("with suppress(E):\n    v = f()\nprint(v)\n", "v", {"v"}),
        ("with open(p) as fh:\n    v = fh.read()\nprint(v, fh)\n", "v", set()),
        ("get = lambda: v\nv = 1\nprint(get())\n", "v", {"v"}),
        ("v = 1\nget = lambda: v\nprint(get())\n", "v", set()),
        ("v = 1\nget = lambda: v\ndel v\n", "v", {"v"}),
        ("v = 1\nget = lambda v: v\ndel v\n", "v", set()),
        ("ys = [k * 2 for k in xs]\n", "k", set()),
        ("ys = [[c for c in row] for row in xs]\n", "c", set()),
        ("ys = [k for k in xs]\nprint(k)\n", "k", {"k"}),
        ("v += 1\n", "v", {"v"}),
        ("del v\n", "v", {"v"}),
        ("global v\nv = 1\n", "v", {"v"}),
        ("def g():\n    nonlocal v\n    v = 2\nv = 1\n", "v", {"v"}),
        ("import json as j\nprint(j)\n", "j", set()),
        ("assert (m := f()), m\nprint(m)\n", "m", set()),
    ],
)
def test_a_renamed_binder_is_observable_exactly_where_it_may_be_read_unbound(
    source: str, renamed: str, observable: Set[str]
) -> None:
    assert _observable(source, renamed) == observable


def test_only_renamed_binders_are_asked_about() -> None:
    assert _observable("print(unbound)\n") == set()
    assert _observable("print(unbound)\n", "other") == set()


# -- the pipeline, end to end ------------------------------------------------------------


def _analysis(
    tmp_path: Path, source: str, caplog: pytest.LogCaptureFixture
) -> Tuple[List[str], List[str]]:
    """The helpers the engine proposes for ``source``, unparsed, and its rejection trace."""
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent(source))
    engine = UnificationRefactorEngine(min_lines=2)
    with (
        caplog.at_level(logging.DEBUG, logger="towel.rejections"),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        proposals = engine.analyze_files([str(path)], progress="none")
    helpers = [ast.unparse(proposal.extracted_function) for proposal in proposals]
    messages = [
        record.getMessage() for record in caplog.records if record.name == "towel.rejections"
    ]
    return helpers, messages


def test_a_block_reading_a_local_before_binding_it_is_declined(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = """
    def scale(x):
        return x * 2
    def f1(xs):
        print("start", len(xs))
        scale = scale(len(xs))
        print("after", scale)
        return scale
    def f2(xs):
        print("begin", len(xs) * 2)
        scale = scale(len(xs))
        print("after", scale)
        return scale
    """
    helpers, messages = _analysis(tmp_path, source, caplog)
    # ``scale`` is bound only after the block starts, as in the lifetime guard's other case.
    early = [
        message
        for message in messages
        if f"REJECT[{RejectReason.INCOMPLETE_LIFETIME_BLOCK1}]" in message
        and message.endswith(":: {'scale'}")
    ]
    assert early, messages
    # Only what follows the failing read may move: the name is bound there.
    assert not any("scale(" in helper for helper in helpers), helpers


def test_a_renamed_binder_read_after_its_deletion_is_declined(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = """
    def f1(xs, flag):
        item = xs[0]
        if flag:
            del item
        r = item * 2
        print("f1", r)
        return r
    def f2(ys, flag):
        elem = ys[0]
        if flag:
            del elem
        r = elem * 2
        print("f2", r)
        return r
    """
    helpers, messages = _analysis(tmp_path, source, caplog)
    assert any("renamed binder observable: " in message for message in messages), messages
    # What is still extracted leaves the deletion, and the spelling, with each site.
    assert not any("del " in helper for helper in helpers), helpers

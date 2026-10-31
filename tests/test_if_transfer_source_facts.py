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

"""Scoped source facts preserve fresh ownership proofs, diagnostics and cache lifetime."""

import ast
import copy
import gc
import os
import pickle
import sys
import traceback
import warnings
import weakref
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Literal, Optional, Tuple, TypeAlias, cast
from unittest.mock import patch
import pytest
from towel.unification import if_binding_transfer as m
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.models import source_digest_of
from towel.canonical_ast import exact_dump
from towel.unification.ast_paths import resolve_path
from towel.unification.refactor_engine import UnificationRefactorEngine

SIMPLE = "FLAG=True\ndef f():\n if FLAG:\n  x=1\n  y=x+1\n  z=y+2\n return z\n"


SourceInputs: TypeAlias = Tuple[ast.Module, ast.FunctionDef, list[ast.stmt], m.IfTransferSite]
Mutation: TypeAlias = Literal["inside", "outside", "fake", "shared", "cycle", "marker"]
OwnerVariant: TypeAlias = Literal["copy", "reparse", "comments"]
CacheMode: TypeAlias = Literal["disabled", "outside", "zero-entries", "zero-bytes", "oversized"]


def _parts(source: str = SIMPLE, comments: bool = False) -> SourceInputs:
    tree = ast.parse(source, type_comments=comments)
    function = cast(ast.FunctionDef, tree.body[1])
    nodes = cast(ast.If, function.body[0]).body
    site = m.IfTransferSite(
        source_digest_of(source),
        (function.lineno, function.col_offset),
        (nodes[0].lineno, nodes[0].col_offset, len(nodes)),
    )
    return tree, function, nodes, site


def _certify(source: str, inputs: SourceInputs) -> Optional[m.IfBindingTransfer]:
    return m.certify_if_binding_transfer(source, *inputs, ("z",))


def _active_cache() -> m._SourceCache:
    cache = m._SOURCE_CACHE.get()
    assert cache is not None
    return cache


def test_scoped_repeated_admissions_reprove_actual_tree() -> None:
    tree, fn, nodes, site = _parts()
    before = exact_dump(tree)
    with (
        m.sharing_if_transfer_sources(),
        patch.object(m, "_source_corresponds", wraps=m._source_corresponds) as proof,
        patch.object(m, "_local_transfer", wraps=m._local_transfer) as local,
        patch.object(ast, "parse", wraps=ast.parse) as parse,
    ):
        results = [_certify(SIMPLE, (tree, fn, nodes, site)) for _ in range(8)]
        assert all(r == results[0] and r is not None for r in results)
        assert proof.call_count == 8 and parse.call_count == 2 and local.call_count == 2
        assert exact_dump(tree) == before


def test_owned_analysis_reuses_only_source_correspondence() -> None:
    inputs = _parts()
    tree, fn, nodes, site = inputs
    with (
        m.sharing_if_transfer_sources(),
        m.sharing_if_transfer_proofs(((tree, SIMPLE),)),
        patch.object(
            m, "_source_corresponds_uncached", wraps=m._source_corresponds_uncached
        ) as proof,
        patch.object(m, "resolve_path", wraps=resolve_path) as identity,
        patch.object(m, "_selected_suite", wraps=m._selected_suite) as suite,
    ):
        results = [_certify(SIMPLE, inputs) for _ in range(8)]
        assert all(result == results[0] and result is not None for result in results)
        assert proof.call_count == 1
        # The current function and selected nodes are still checked on every admission.
        assert identity.call_count >= 8 and suite.call_count >= 8
        fake = copy.deepcopy(fn)
        fake_nodes = cast(ast.If, fake.body[0]).body
        assert _certify(SIMPLE, (tree, fake, fake_nodes, site)) is None
        assert proof.call_count == 1
        assert m.verify_if_binding_transfer(cast(m.IfBindingTransfer, results[0]), SIMPLE, tree)
        assert proof.call_count == 2  # Materialization never borrows the analysis proof.


def test_engine_registers_only_its_current_analysis_trees(tmp_path: Path) -> None:
    source = SIMPLE + SIMPLE[SIMPLE.index("def f():") :].replace("def f():", "def g():")
    path = tmp_path / "branches.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(min_lines=3)
    with (
        patch.object(m, "_source_corresponds", wraps=m._source_corresponds) as calls,
        patch.object(
            m, "_source_corresponds_uncached", wraps=m._source_corresponds_uncached
        ) as proofs,
    ):
        assert engine.analyze_files([str(path)], progress="none")
        assert calls.call_count > 1 and proofs.call_count == 1
        assert m._CORRESPONDENCE_PROOFS.get() is None
        before = proofs.call_count
        assert engine.analyze_files([str(path)], progress="none")
        assert proofs.call_count > before


def test_owned_proof_does_not_certify_other_source_or_another_tree() -> None:
    tree = _parts()[0]
    other = copy.deepcopy(tree)
    with (
        m.sharing_if_transfer_proofs(((tree, SIMPLE),)),
        patch.object(
            m, "_source_corresponds_uncached", wraps=m._source_corresponds_uncached
        ) as proof,
    ):
        assert m._source_corresponds(tree, SIMPLE)
        assert not m._source_corresponds(tree, SIMPLE.replace("x=1", "x=9"))
        assert m._source_corresponds(other, SIMPLE)
        cast(ast.Constant, cast(ast.Assign, other.body[0]).value).value = False
        assert not m._source_corresponds(other, SIMPLE)
        assert m._source_corresponds(tree, SIMPLE)
        assert proof.call_count == 4


def test_owned_proofs_respect_parser_limits_and_disabled_memoization() -> None:
    tree = _parts()[0]
    old = sys.getrecursionlimit()
    with (
        m.sharing_if_transfer_proofs(((tree, SIMPLE),)),
        patch.object(
            m, "_source_corresponds_uncached", wraps=m._source_corresponds_uncached
        ) as proof,
    ):
        try:
            assert m._source_corresponds(tree, SIMPLE)
            assert m._source_corresponds(tree, SIMPLE)
            with memoization_disabled():
                assert m._source_corresponds(tree, SIMPLE)
                assert m._source_corresponds(tree, SIMPLE)
            sys.setrecursionlimit(old + 100)
            assert m._source_corresponds(tree, SIMPLE)
            assert m._source_corresponds(tree, SIMPLE)
            assert proof.call_count == 4
        finally:
            sys.setrecursionlimit(old)


@pytest.mark.parametrize("target", ["inside", "outside", "shared", "cycle", "marker"])
def test_owned_tree_immutability_check_rejects_mutations(target: Mutation) -> None:
    tree, fn, nodes, site = _parts()
    with m.sharing_if_transfer_proofs(((tree, SIMPLE),), check_ast_immutable=True):
        assert m._source_corresponds(tree, SIMPLE)
        if target == "inside":
            cast(ast.Constant, cast(ast.Assign, nodes[0]).value).value = 99
        elif target == "outside":
            cast(ast.Constant, cast(ast.Assign, tree.body[0]).value).value = False
        else:
            expression = cast(ast.BinOp, cast(ast.Assign, nodes[1]).value)
            if target == "shared":
                expression.left = cast(ast.BinOp, cast(ast.Assign, nodes[2]).value).left
            elif target == "cycle":
                expression.left = expression
            else:
                operator = type(expression.op)()
                expression.op = operator
                setattr(operator, "_fields", ("child",))
                setattr(operator, "child", operator)
        with pytest.raises(RuntimeError, match="Analysis mutated"):
            m._source_corresponds(tree, SIMPLE)


def test_owned_proofs_end_with_analysis_and_nested_scopes_restore_parent() -> None:
    tree = _parts()[0]
    reference = weakref.ref(tree)
    with (
        m.sharing_if_transfer_proofs(((tree, SIMPLE),)),
        patch.object(
            m, "_source_corresponds_uncached", wraps=m._source_corresponds_uncached
        ) as proof,
    ):
        owner = m._CORRESPONDENCE_PROOFS.get()
        assert owner is not None
        assert m._source_corresponds(tree, SIMPLE)
        with m.sharing_if_transfer_proofs(((tree, SIMPLE),)):
            assert m._source_corresponds(tree, SIMPLE)
        assert m._CORRESPONDENCE_PROOFS.get() is owner
        assert m._source_corresponds(tree, SIMPLE)
        assert proof.call_count == 2
    assert not owner.owners and not owner.proofs
    assert m._source_corresponds(tree, SIMPLE)
    del tree, proof
    gc.collect()
    assert reference() is None


def test_owned_warning_capable_source_keeps_fresh_native_diagnostics() -> None:
    source = SIMPLE + "W='\\q'\n"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tree = _parts(source)[0]
    with m.sharing_if_transfer_proofs(((tree, source),)):
        with warnings.catch_warnings(record=True) as got:
            warnings.simplefilter("always")
            assert m._source_corresponds(tree, source)
            assert m._source_corresponds(tree, source)
            assert len(got) == 2
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert not m._source_corresponds(tree, source)


def test_cached_local_refusal_has_no_current_tree_proof() -> None:
    source = SIMPLE.replace("x=1", "x=x+1") + SIMPLE[SIMPLE.index("def f():") :].replace(
        "def f():", "def g():"
    )
    input = _parts(source)
    tree = input[0]
    prime = cast(ast.FunctionDef, tree.body[2])
    prime_nodes = cast(ast.If, prime.body[0]).body
    prime_site = m.IfTransferSite(
        source_digest_of(source),
        (prime.lineno, prime.col_offset),
        (prime_nodes[0].lineno, prime_nodes[0].col_offset, len(prime_nodes)),
    )
    with m.sharing_if_transfer_sources():
        assert _certify(source, (tree, prime, prime_nodes, prime_site)) is not None
        with (
            patch.object(m, "_source_corresponds", wraps=m._source_corresponds) as proof,
            patch.object(m, "_local_transfer", wraps=m._local_transfer) as local,
        ):
            assert [_certify(source, input) for _ in range(4)] == [None] * 4
            assert proof.call_count == 0 and local.call_count == 1
        assert len(_active_cache().locals) == 2


@pytest.mark.parametrize("target", ["inside", "outside", "fake", "shared", "cycle", "marker"])
def test_warmed_source_fact_never_validates_mutated_owner(target: Mutation) -> None:
    tree, fn, nodes, site = _parts()
    with (
        m.sharing_if_transfer_sources(),
        patch.object(m, "_source_corresponds", wraps=m._source_corresponds) as proof,
    ):
        assert _certify(SIMPLE, (tree, fn, nodes, site)) is not None
        if target == "inside":
            cast(ast.Constant, cast(ast.Assign, nodes[0]).value).value = 99
        elif target == "outside":
            cast(ast.Constant, cast(ast.Assign, tree.body[0]).value).value = False
        elif target == "fake":
            fn = copy.deepcopy(fn)
            nodes = cast(ast.If, fn.body[0]).body
        elif target == "shared":
            cast(ast.BinOp, cast(ast.Assign, nodes[1]).value).left = cast(
                ast.BinOp, cast(ast.Assign, nodes[2]).value
            ).left
        elif target == "cycle":
            expression = cast(ast.BinOp, cast(ast.Assign, nodes[1]).value)
            expression.left = expression
        else:
            expression = cast(ast.BinOp, cast(ast.Assign, nodes[1]).value)
            operator = type(expression.op)()
            expression.op = operator
            setattr(operator, "_fields", ("child",))
            setattr(operator, "child", operator)
        assert _certify(SIMPLE, (tree, fn, nodes, site)) is None
        assert proof.call_count == (1 if target in ("shared", "cycle", "marker") else 2)


@pytest.mark.parametrize("variant", ["copy", "reparse", "comments"])
def test_reparsed_and_copied_actual_owners_resolve_by_path(variant: OwnerVariant) -> None:
    input = _parts()
    tree, fn, nodes, site = input
    with m.sharing_if_transfer_sources():
        cert = _certify(SIMPLE, input)
        assert cert is not None
        changed = (
            copy.deepcopy(tree)
            if variant == "copy"
            else ast.parse(SIMPLE, type_comments=variant == "comments")
        )
        fn = cast(ast.FunctionDef, changed.body[1])
        nodes = cast(ast.If, fn.body[0]).body
        assert _certify(SIMPLE, (changed, fn, nodes, site)) == cert
        assert m.verify_if_binding_transfer(cert, SIMPLE, changed)


def test_real_type_comments_keep_variant_correspondence() -> None:
    source = SIMPLE.replace("x=1", "x=1 # type: int")
    with m.sharing_if_transfer_sources():
        first = _certify(source, _parts(source, False))
        second = _certify(source, _parts(source, True))
        assert first is not None and first == second


def test_false_valid_true_type_comment_syntaxerror_keeps_false_admission() -> None:
    source = SIMPLE + "# type: int\n"
    input = _parts(source)
    with pytest.raises(SyntaxError):
        ast.parse(source, type_comments=True)
    with m.sharing_if_transfer_sources():
        assert _certify(source, input) is not None


def test_parser_configuration_is_part_of_source_fact_key() -> None:
    input = _parts()
    old = sys.getrecursionlimit()
    with m.sharing_if_transfer_sources(), patch.object(ast, "parse", wraps=ast.parse) as parsed:
        try:
            assert _certify(SIMPLE, input) is not None
            sys.setrecursionlimit(old + 100)
            assert _certify(SIMPLE, input) is not None
            assert parsed.call_count == 4
        finally:
            sys.setrecursionlimit(old)


@pytest.mark.parametrize("mode", ["disabled", "outside", "zero-entries", "zero-bytes", "oversized"])
def test_cache_bypass_keeps_native_uncached_path(mode: CacheMode) -> None:
    input = _parts()
    context: AbstractContextManager[None]
    if mode == "outside":
        context = nullcontext()
    elif mode == "zero-entries":
        context = m.sharing_if_transfer_sources(entries=0)
    elif mode == "zero-bytes":
        context = m.sharing_if_transfer_sources(source_bytes=0)
    elif mode == "oversized":
        context = m.sharing_if_transfer_sources(source_bytes=1)
    else:
        context = m.sharing_if_transfer_sources()
    with (
        context,
        memoization_disabled() if mode == "disabled" else nullcontext(),
        patch.object(ast, "parse", wraps=ast.parse) as parsed,
    ):
        assert _certify(SIMPLE, input) is not None and _certify(SIMPLE, input) is not None
        assert parsed.call_count == 2


def test_warning_capable_source_preserves_native_diagnostic_order() -> None:
    source = SIMPLE + "W='\\q'\n"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        input = _parts(source)
    with m.sharing_if_transfer_sources():
        with warnings.catch_warnings(record=True) as got:
            warnings.simplefilter("always")
            assert _certify(source, input) is not None
            assert _certify(source, input) is not None
            assert len(got) == 2
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert _certify(source, input) is None
        assert len(_active_cache().sources) == 0
        assert len(_active_cache().locals) == 0
    source = source.replace("z=y+2", "z=(lambda:y)()")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        input = _parts(source)
    with m.sharing_if_transfer_sources(), warnings.catch_warnings(record=True) as got:
        warnings.simplefilter("always")
        assert _certify(source, input) is None
        assert not got


def test_context_exit_releases_private_syntax_and_serialized_cert_has_no_owner() -> None:
    input = _parts()
    tree, fn, nodes, site = input
    current_ref = weakref.ref(tree)
    with m.sharing_if_transfer_sources():
        cert = _certify(SIMPLE, input)
        cache = _active_cache()
        private_refs = [weakref.ref(v.tree) for src in cache.sources.values() for v in src.variants]
        frozen = cast(m.IfBindingTransfer, pickle.loads(pickle.dumps(cert)))
        assert frozen == cert
        with m.sharing_if_transfer_sources():
            assert m._SOURCE_CACHE.get() is not cache
            assert _certify(SIMPLE, input) == cert
        assert m._SOURCE_CACHE.get() is cache
    del tree, fn, nodes, input, cache
    gc.collect()
    assert current_ref() is None and all(ref() is None for ref in private_refs)
    assert frozen is not None and m.verify_if_binding_transfer(frozen, SIMPLE, ast.parse(SIMPLE))


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Requires fork inheritance")
def test_context_fork_reuses_only_source_facts_and_reproves_child_tree() -> None:
    inputs = _parts()
    with m.sharing_if_transfer_sources():
        expected = _certify(SIMPLE, inputs)
        assert expected is not None
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            exit_status = 1
            try:
                child = copy.deepcopy(inputs[0])
                function = cast(ast.FunctionDef, child.body[1])
                nodes = cast(ast.If, function.body[0]).body
                with patch.object(m, "_source_corresponds", wraps=m._source_corresponds) as proof:
                    result = _certify(SIMPLE, (child, function, nodes, inputs[3]))
                    assert result == expected
                    assert proof.call_count == 1
                assert os.write(write_fd, b"1") == 1
                exit_status = 0
            except BaseException:
                traceback.print_exc()
                sys.stderr.flush()
            finally:
                os.close(write_fd)
                os._exit(exit_status)
        os.close(write_fd)
        try:
            response = os.read(read_fd, 1)
        finally:
            os.close(read_fd)
            waited, status = os.waitpid(pid, 0)
        assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        assert response == b"1"
        assert _certify(SIMPLE, inputs) == expected

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

"""The memos that make a run cheaper must not make it different.

Each test here warms a cache and checks that the warm answer is the cold
answer, or that a cache holds what it must for the next pass to reuse it.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
from typing import List, Tuple

import pytest

from tests.test_cli_integration import BODY as LITERAL_BODY
from towel.unification.exceptions import RefactoringError
from towel.unification.instantiation import instantiation_mismatch
from towel.unification.orphan_detector import orphaned_variables
from towel.unification.pipeline import AnalysisSession
from towel.unification.models import CodeBlockPair, RefactoringProposal
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.semantic_safety import uses_class_private_names

# The memo contract must exercise real proposals on runtimes whose caller
# lifetime-transfer proofs are not yet certified.
SIMILAR_FUNCTION = "def f{index}():\n    x = {index}\n" + LITERAL_BODY


def _similar_module(count: int) -> str:
    return "".join(SIMILAR_FUNCTION.format(index=index) for index in range(count))


def _analyze(engine: UnificationRefactorEngine, paths: List[Path]) -> List[RefactoringProposal]:
    with contextlib.redirect_stdout(io.StringIO()):
        return engine.analyze_files([str(path) for path in paths], progress="none")


def _sites(
    proposals: List[RefactoringProposal],
) -> List[Tuple[str, Tuple[Tuple[Tuple[int, int], str], ...]]]:
    """Each proposal as its description and every replacement's range and call text."""
    return sorted(
        (
            proposal.description,
            tuple((r.line_range, ast.unparse(r.node)) for r in proposal.replacements),
        )
        for proposal in proposals
    )


# --- clustering scan memo -----------------------------------------------------


def test_clustered_proposals_are_the_same_cold_and_warm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The memo is this process's: pairs judged in forked workers fill the
    # workers' copies of it. With callbacks (byvalue) and pairs inside one
    # function (reach) this module forms over PARALLEL_PAIR_THRESHOLD pairs,
    # so under TOWEL_WORKERS=2 the cold run forked and the warm run scanned
    # what the workers had cached. One worker keeps the contract observable.
    monkeypatch.setenv("TOWEL_WORKERS", "1")
    path = tmp_path / "similar.py"
    path.write_text(_similar_module(5))
    engine = UnificationRefactorEngine(annotate_helpers=False)
    cold = _sites(_analyze(engine, [path]))
    scans_after_warmup = 0
    original = engine._scan_clustered_sites

    def counting_scan(*args, **kwargs):
        nonlocal scans_after_warmup
        scans_after_warmup += 1
        return original(*args, **kwargs)

    engine._scan_clustered_sites = counting_scan  # type: ignore[method-assign]
    warm = _sites(_analyze(engine, [path]))
    fresh = _sites(_analyze(UnificationRefactorEngine(annotate_helpers=False), [path]))
    assert cold, "the similar functions must pair"
    assert warm == cold == fresh
    # The count below pins the memo's contract rather than any behaviour the
    # output shows: the fixed-point loop re-analyzes every file per iteration,
    # and the clustering scan of an unchanged file is meant to be reused, not
    # repeated. If the memo is restructured, drop this line; the equality
    # above is the observable requirement.
    assert scans_after_warmup == 0, "an unchanged file is not scanned again"


def test_each_pair_takes_every_other_site_from_the_shared_scan(tmp_path: Path) -> None:
    """The scan is shared, but each proposal must leave out its own pair's blocks."""
    path = tmp_path / "similar.py"
    path.write_text(_similar_module(4))
    engine = UnificationRefactorEngine(annotate_helpers=False)
    analysis = engine.analysis_session.analyze_module(str(path))
    functions = list(analysis.functions)
    classes = list(analysis.module.class_infos)
    engine._record_function_paths(functions)
    uncached_engine = UnificationRefactorEngine(annotate_helpers=False)
    uncached_engine._record_function_paths(functions)
    pairs = engine.find_block_pairs(functions, progress="none")
    proposals = []
    for pair in pairs:
        # Judge each pair independently of proposal deduplication, while
        # retaining the shared site-scan memo this test exercises.
        engine._seen_proposals.clear()
        proposal = engine._try_refactor_pair_multi_file(pair, functions, classes)
        uncached_engine._seen_proposals.clear()
        uncached_engine._cluster_scan_cache.clear()
        uncached = uncached_engine._try_refactor_pair_multi_file(pair, functions, classes)
        assert _sites([proposal] if proposal is not None else []) == _sites(
            [uncached] if uncached is not None else []
        ), "sharing the scan must preserve the exact sites and calls for each pair"
        if proposal is not None:
            proposals.append((pair, proposal))
    assert engine._cluster_scan_cache, "the test must exercise a shared scan"
    assert len(proposals) > 1, "every pair of similar functions proposes"
    for pair, proposal in proposals:
        starts = sorted(r.line_range[0] for r in proposal.replacements)
        assert len(set(starts)) == len(starts), "a site appears once per proposal"
        # The arithmetic fixture repeats patterns within each function too.
        # Every function must join; one may contribute several disjoint sites.
        represented = {
            function.node.lineno
            for function in functions
            if any(
                function.node.lineno <= start <= (function.node.end_lineno or start)
                for start in starts
            )
        }
        assert represented == {function.node.lineno for function in functions}
        assert len(starts) == _similar_sites(
            engine, pair
        ), "every similar block joins each proposal"


def _similar_sites(engine: UnificationRefactorEngine, pair: CodeBlockPair) -> int:
    """How many blocks of ``_similar_module(4)`` the pair's helper is called from.

    Strict mode pairs only alignments of nearly the whole body, one site in
    each of the four functions; it declines the shorter windows. Relaxed mode
    also pairs those windows, and the literal body repeats one three-statement
    shape (``d, e, f`` and ``h, i, j``, with different constants). When the
    pair joins the two windows, the scan finds every window spelled like the
    second block, which includes the first block's own function's other
    window, so that function calls the helper twice. (A window spelled like
    the first block does not join: its constants equal the template's, so it
    would need a different helper.)
    """
    if engine.strict:
        return 4
    lines_per_function = SIMILAR_FUNCTION.count("\n")

    def offset(function_name: str, block_range: Tuple[int, int]) -> int:
        return block_range[0] - int(function_name.removeprefix("f")) * lines_per_function

    windows_differ = offset(pair.function1_name, pair.block1_range) != offset(
        pair.function2_name, pair.block2_range
    )
    return 4 + int(windows_differ)


def test_shared_call_nodes_survive_being_applied(tmp_path: Path) -> None:
    """Applying one proposal must not alter a call node another proposal shares."""
    path = tmp_path / "similar.py"
    path.write_text(_similar_module(4))
    engine = UnificationRefactorEngine(annotate_helpers=False)
    proposals = _analyze(engine, [path])
    before = _sites(proposals)
    engine.apply_refactoring_multi_file(proposals[0])
    assert _sites(proposals) == before


# --- structural memos of pure functions --------------------------------------

BODY = """
def f(items):
    total = 0
    for item in items:
        total += item
    print(total)
    return total
"""


def _function_body(source: str) -> List[ast.stmt]:
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    return function.body


def test_orphaned_variables_gives_a_fresh_set_per_call() -> None:
    body = _function_body(BODY)
    first = orphaned_variables(body, (0, 1))
    assert first == {"total"}
    first.clear()
    assert orphaned_variables(body, (0, 1)) == {"total"}
    assert orphaned_variables(_function_body(BODY), (0, 1)) == {"total"}, "a re-parse agrees"


def test_uses_class_private_names_accepts_any_iterable_and_agrees_on_a_reparse() -> None:
    source = "def f(self):\n    return self.__secret\n"
    assert uses_class_private_names(iter(ast.parse(source).body))
    assert uses_class_private_names(ast.parse(source).body)
    assert not uses_class_private_names(
        ast.parse("def f(self):\n    return self.__dunder__\n").body
    )


def test_instantiation_verdict_agrees_on_a_reparsed_block() -> None:
    helper = ast.parse("def __extracted_func(x):\n    y = x + 1\n    print(y)\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    call = ast.parse("__extracted_func(a)").body[0]
    block = ast.parse("y = a + 1\nprint(y)\n").body
    verdict = instantiation_mismatch(
        helper,
        call,
        block,
        {},
        {},
        site_function_names=frozenset(),
        preamble_length=0,
        returns_variables=False,
    )
    assert verdict is None
    again = instantiation_mismatch(
        helper,
        call,
        ast.parse("y = a + 1\nprint(y)\n").body,
        {},
        {},
        site_function_names=frozenset(),
        preamble_length=0,
        returns_variables=False,
    )
    assert again is None
    other = ast.parse("y = a + 2\nprint(y)\n").body
    mismatch = instantiation_mismatch(
        helper,
        call,
        other,
        {},
        {},
        site_function_names=frozenset(),
        preamble_length=0,
        returns_variables=False,
    )
    assert mismatch is not None and mismatch.startswith("body: ")


# --- analysis session sized to the project -----------------------------------


def test_session_holds_every_file_of_a_large_package(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for index in range(129):
        (package / f"m{index:03d}.py").write_text(f"def f{index}(x):\n    return x + {index}\n")
    engine = UnificationRefactorEngine()
    with contextlib.redirect_stdout(io.StringIO()):
        engine.analyze_directory(str(package), progress="none")
    session = engine.analysis_session
    assert session.max_entries == 130
    assert session.entry_count == 130
    paths = sorted(str(path) for path in package.glob("*.py"))
    trees = [session.analyze_module(path).module.tree for path in paths]
    with contextlib.redirect_stdout(io.StringIO()):
        engine.analyze_directory(str(package), progress="none")
    assert all(session.analyze_module(path).module.tree is tree for path, tree in zip(paths, trees))


def test_hold_at_least_never_lowers_and_leaves_a_disabled_session_disabled() -> None:
    session = AnalysisSession(max_entries=200)
    session.hold_at_least(3)
    assert session.max_entries == 200
    session.hold_at_least(300)
    assert session.max_entries == 300
    disabled = AnalysisSession(max_entries=0)
    disabled.hold_at_least(300)
    assert disabled.max_entries == 0


# --- shared parses ------------------------------------------------------------


def test_class_insertion_position_uses_the_engine_parse_memo() -> None:
    engine = UnificationRefactorEngine()
    source = "class C:\n    def m(self):\n        return 1\n"
    assert engine._find_class_insert_position(source, "C") is not None
    # The memo has no file to mutate; what it promises is one tree per
    # source text, observable as the same object on every parse of it.
    assert engine._parse_source(source) is engine._parse_source(source)
    with pytest.raises(RefactoringError, match="does not parse"):
        engine._find_class_insert_position("class C(:\n", "C")


def test_cluster_helper_construction_survives_a_refresh_with_new_site_positions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build each helper once, while resolving every replacement in the current file."""
    import sys

    from towel.source_files import source_policy_context
    from towel.unification.extractor import HygienicExtractor

    path = tmp_path / "shifted.py"
    source = _similar_module(3)
    engine = UnificationRefactorEngine(annotate_helpers=False, strict=False)
    monkeypatch.setenv("TOWEL_WORKERS", "1")
    constructions = 0
    original = HygienicExtractor.extract_function

    def counting(self, **kwargs):
        nonlocal constructions
        if sys._getframe(1).f_code.co_name == "_cluster_candidate_call":
            constructions += 1
        return original(self, **kwargs)

    monkeypatch.setattr(HygienicExtractor, "extract_function", counting)
    run = source_policy_context(strict=False)
    path.write_text(source)
    first = run.run(_analyze, engine, [path])
    assert constructions and first
    constructions = 0
    path.write_text("\n" + source)
    shifted = run.run(_analyze, engine, [path])
    assert shifted
    assert [[(r.line_range, ast.unparse(r.node)) for r in p.replacements] for p in shifted] == [
        [((r.line_range[0] + 1, r.line_range[1] + 1), ast.unparse(r.node)) for r in p.replacements]
        for p in first
    ]
    assert constructions == 0
    # The same engine in an independent run starts cold again.
    path.write_text("\n\n" + source)
    assert _analyze(engine, [path])
    assert constructions > 0

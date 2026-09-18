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

from towel.unification.instantiation import instantiation_mismatch
from towel.unification.orphan_detector import orphaned_variables
from towel.unification.pipeline import AnalysisSession
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.semantic_safety import uses_class_private_names

SIMILAR_FUNCTION = """
def f{index}(order):
    items = [i for i in order.items if i.in_stock]
    subtotal = sum(i.price for i in items)
    total = round(subtotal * 1.08, 2)
    return "F{index} " + str(total)
"""


def _similar_module(count: int) -> str:
    return "".join(SIMILAR_FUNCTION.format(index=index) for index in range(count))


def _analyze(engine: UnificationRefactorEngine, paths: List[Path]) -> list:
    with contextlib.redirect_stdout(io.StringIO()):
        return engine.analyze_files([str(path) for path in paths], progress="none")


def _sites(proposals: list) -> List[Tuple[str, Tuple[Tuple[int, int], str], ...]]:
    """Each proposal as its description and every replacement's range and call text."""
    return sorted(
        (
            proposal.description,
            *((r.line_range, ast.unparse(r.node)) for r in proposal.replacements),
        )
        for proposal in proposals
    )


# --- clustering scan memo -----------------------------------------------------


def test_clustered_proposals_are_the_same_cold_and_warm(tmp_path: Path) -> None:
    path = tmp_path / "similar.py"
    path.write_text(_similar_module(5))
    engine = UnificationRefactorEngine(annotate_helpers=False)
    cold = _sites(_analyze(engine, [path]))
    scans_after_warmup = 0
    original = engine._scan_clustered_sites

    def counting_scan(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal scans_after_warmup
        scans_after_warmup += 1
        return original(*args, **kwargs)

    engine._scan_clustered_sites = counting_scan  # type: ignore[method-assign]
    warm = _sites(_analyze(engine, [path]))
    fresh = _sites(_analyze(UnificationRefactorEngine(annotate_helpers=False), [path]))
    assert cold, "the similar functions must pair"
    assert warm == cold == fresh
    assert scans_after_warmup == 0, "an unchanged file is not scanned again"


def test_each_pair_takes_every_other_site_from_the_shared_scan(tmp_path: Path) -> None:
    """The scan is shared, but each proposal must leave out its own pair's blocks."""
    path = tmp_path / "similar.py"
    path.write_text(_similar_module(4))
    engine = UnificationRefactorEngine(annotate_helpers=False)
    analysis = engine.analysis_session.analyze_module(str(path))
    functions = list(analysis.functions)
    classes = list(analysis.module.class_infos)
    pairs = engine.find_block_pairs(functions, progress="none")
    proposals = [
        proposal
        for pair in pairs
        if (proposal := engine._try_refactor_pair_multi_file(pair, functions, classes))
    ]
    assert len(proposals) > 1, "every pair of similar functions proposes"
    for proposal in proposals:
        starts = sorted(r.line_range[0] for r in proposal.replacements)
        assert len(set(starts)) == len(starts), "a site appears once per proposal"
        assert len(starts) == 4, "every similar block joins each proposal"


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
        helper, call, block, {}, {}, preamble_length=0, returns_variables=False
    )
    assert verdict is None
    again = instantiation_mismatch(
        helper,
        call,
        ast.parse("y = a + 1\nprint(y)\n").body,
        {},
        {},
        preamble_length=0,
        returns_variables=False,
    )
    assert again is None
    other = ast.parse("y = a + 2\nprint(y)\n").body
    mismatch = instantiation_mismatch(
        helper, call, other, {}, {}, preamble_length=0, returns_variables=False
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

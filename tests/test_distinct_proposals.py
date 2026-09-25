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

"""Pair evaluation keeps one proposal per distinct refactoring.

A file of many similar functions proposes the same helper over the same
clustered sites from nearly every pair; holding each pair's copy until the
overlap filter took sixty uniform functions to 33 GB. The first pair keeps
its proposal, later duplicates add nothing, and the overlap filter sees the
same candidates it would have chosen from anyway.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import List, Tuple

from towel.diagnostics import Settings
from towel.unification.models import RefactoringProposal, Replacement
from towel.unification.models import proposal_identity
from towel.unification.parallel import _DistinctProposals
from towel.unification.refactor_engine import UnificationRefactorEngine

SERIAL = Settings.from_environ({"TOWEL_WORKERS": "1"})


def _proposal(name: str, sites: List[Tuple[int, int]]) -> RefactoringProposal:
    helper = ast.parse(f"def {name}(a):\n    return a + 1\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    return RefactoringProposal(
        file_path="m.py",
        extracted_function=helper,
        replacements=[
            Replacement(line_range=span, node=ast.parse(f"x = {name}(a)").body[0]) for span in sites
        ],
        description=f"from {name}",
        parameters_count=1,
    )


def test_the_helper_name_is_not_part_of_the_identity() -> None:
    first = _proposal("__extracted_func_0", [(1, 3), (7, 9)])
    second = _proposal("__extracted_func_1", [(7, 9), (1, 3)])
    assert proposal_identity(first) == proposal_identity(second)
    other_sites = _proposal("__extracted_func_0", [(1, 3), (8, 9)])
    assert proposal_identity(first) != proposal_identity(other_sites)
    other_body = copy.deepcopy(first)
    other_body.extracted_function.body[0] = ast.parse("return a + 2").body[0]
    assert proposal_identity(first) != proposal_identity(other_body)


def test_the_lowest_pair_index_keeps_its_proposal_whatever_the_arrival_order() -> None:
    held = _DistinctProposals()
    late = _proposal("__extracted_func_1", [(1, 3), (7, 9)])
    early = _proposal("__extracted_func_0", [(1, 3), (7, 9)])
    held.add(5, late)
    held.add(2, early)
    held.add(9, _proposal("__extracted_func_2", [(1, 3), (7, 9)]))
    assert held.in_order() == [early]


def test_a_file_of_similar_functions_yields_each_helper_once(tmp_path: Path) -> None:
    body = "\n".join(f"    v{i} = data[{i}] + {i}" for i in range(6))
    source = "\n\n".join(f"def f{n}(data):\n{body}\n    return v5\n" for n in range(5))
    path = tmp_path / "m.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(min_lines=3, settings=SERIAL)
    proposals = engine.analyze_file(str(path))
    identities = [proposal_identity(proposal) for proposal in proposals]
    assert len(identities) == len(set(identities))


def test_a_weighted_cache_drops_the_oldest_entries_past_its_weight() -> None:
    from towel.unification.bounded_cache import BoundedCache

    cache: BoundedCache[str, tuple[int, ...]] = BoundedCache(10, weight=len, weight_limit=5)
    cache["a"] = (1, 2, 3)
    cache["b"] = (4, 5)
    assert list(cache) == ["a", "b"]
    cache["c"] = (6,)
    assert list(cache) == ["b", "c"], "the oldest goes when the sites held exceed the limit"
    cache["d"] = tuple(range(9))
    assert list(cache) == ["d"], "one entry may exceed the limit alone"
    cache["d"] = (1,)
    cache["e"] = (2, 3)
    assert list(cache) == ["d", "e"], "rewriting a key replaces its weight"
    del cache["e"]
    cache["f"] = (1, 2, 3, 4)
    assert list(cache) == ["d", "f"]

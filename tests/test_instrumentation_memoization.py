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

"""Instrumentation safety must not repeatedly resolve the same project facts.

The 1.792 release candidate made an unchanged extraction test about 2.5 times
slower than 1.772: every project scope now participates in instrumentation
flow, and repeated names were resolved afresh. These operation counts protect
the reason for the caches without imposing machine-dependent timing limits.
Safety is equally intentional: scope, recursion budget and source dependencies
must survive memoization, and disabling it must preserve the exact applications.
Do not weaken these expectations merely because the implementation changes.
"""

from __future__ import annotations

import ast
import contextlib
import gc
from pathlib import Path
from unittest.mock import patch
import weakref

from towel.unification.bounded_cache import memoization_disabled
from towel.unification.decorator_reach import _MOST_HOPS, _Resolver, _Slot
from towel.unification.import_graph import ImportGraphCache
from towel.unification.namespace_writes import _WriteScanner


def test_repeated_applications_share_resolution_but_keep_every_site(tmp_path: Path) -> None:
    path = tmp_path / "subject.py"
    path.write_text(
        "from typeguard import typechecked as checked\n"
        "class C:\n    def f(self): return 1\n" + "checked(C)\n" * 40
    )
    results: list[tuple[tuple[str | None, str | None], ...]] = []
    reads: list[int] = []
    for memoized in (True, False):
        resolver = _Resolver(ImportGraphCache())
        with contextlib.ExitStack() as stack:
            if not memoized:
                stack.enter_context(memoization_disabled())
            calls = stack.enter_context(
                patch.object(
                    resolver, "_read_flow_denotations", wraps=resolver._read_flow_denotations
                )
            )
            index = resolver._read_hand_index(tmp_path)
        assert index.complete
        applications = index.by_target[(str(path), "C")]
        results.append(tuple((item.instrumenter, item.site) for item in applications))
        reads.append(calls.call_count)
    assert results[0] == results[1]
    assert len(results[0]) == 40
    assert len({site for _, site in results[0]}) == 40
    assert reads[0] == 1
    assert reads[1] == 40


def test_resolution_keeps_scope_and_recursion_budget_in_its_key(tmp_path: Path) -> None:
    path = tmp_path / "subject.py"
    path.write_text("class C: pass\ndef shadow():\n    C = None\n    return C\nAlias = C\n")
    resolver = _Resolver(ImportGraphCache())
    module = resolver._load(str(path))
    assert module is not None
    before = ast.dump(module.tree, include_attributes=True)
    function = module.tree.body[1]
    assert isinstance(function, ast.FunctionDef)
    expected = frozenset({(str(path), "C")})
    for _ in range(2):
        assert resolver._argument_targets(module, "C", _Slot(None, 0, True)) == expected
        assert resolver._argument_targets(module, "C", _Slot(function, 0, True)) is None
        assert resolver._module_targets(module, "Alias", _MOST_HOPS) is None
        assert resolver._module_targets(module, "Alias", 0) == expected
    assert ast.dump(module.tree, include_attributes=True) == before


def test_resolution_keeps_dependencies_and_does_not_outlive_its_resolver(tmp_path: Path) -> None:
    path = tmp_path / "subject.py"
    dependency = tmp_path / "support.py"
    path.write_text("from support import C\n")
    dependency.write_text("class C: pass\n")
    resolver = _Resolver(ImportGraphCache())
    module = resolver._load(str(path))
    assert module is not None
    expected = frozenset({(str(dependency), "C")})
    assert resolver._module_targets(module, "C", 0) == expected
    stamps = frozenset(resolver.stamps)
    assert any(stamp[0] == str(dependency) for stamp in stamps)
    assert resolver._module_targets(module, "C", 0) == expected
    assert frozenset(resolver.stamps) == stamps
    reference = weakref.ref(resolver)
    del resolver
    gc.collect()
    assert reference() is None

    # A new question must read the new definition, even for the same spelling.
    dependency.write_text("C = 42\n")
    fresh = _Resolver(ImportGraphCache())
    assert fresh._module_targets(module, "C", 0) is None


def test_namespace_indexes_share_one_immutable_tree_walk(tmp_path: Path) -> None:
    """Five indexes need the same traversal, not five traversals of each file."""
    path = tmp_path / "subject.py"
    tree = ast.parse(
        'import support as module\nPREFIX = "pkg"\nTARGET = PREFIX + ".support.value"\n'
        "alias = module\nalias.other = 1\npatch(TARGET, 2)\n"
    )
    before = ast.dump(tree, include_attributes=True)
    with patch.object(ast, "walk", wraps=ast.walk) as walks:
        scanner = _WriteScanner(path, tree, path.name)
        scanner.visit(tree)
    assert walks.call_count == 1
    assert [write.name for write in scanner.by_name["support"]] == ["other"]
    assert [write.name for write in scanner.by_name["pkg.support"]] == ["value"]
    assert ast.dump(tree, include_attributes=True) == before

"""The per-analysis function index answers what the pair stages used to scan for."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List

from towel.unification.function_index import FunctionIndex
from towel.unification.models import FunctionArtifact
from towel.unification.pipeline import analyze_scopes, collect_functions, parse_modules

ALPHA = """
def outer():
    def inner():
        return 1
    return inner()


class First:
    def shared(self):
        global counter
        return 1


class Second:
    def shared(self):
        return 2
"""

BETA = """
def lone():
    return 3
"""


def _functions(tmp_path: Path) -> List[FunctionArtifact]:
    alpha, beta = tmp_path / "alpha.py", tmp_path / "beta.py"
    alpha.write_text(ALPHA)
    beta.write_text(BETA)
    modules = analyze_scopes(parse_modules([str(alpha), str(beta)]))
    return collect_functions(modules)


def test_lookups_keep_discovery_order_and_stay_per_file(tmp_path: Path) -> None:
    functions = _functions(tmp_path)
    index = FunctionIndex.build(functions)
    alpha = str(tmp_path / "alpha.py")

    assert [a.node.name for a in index.in_file(alpha)] == ["outer", "inner", "shared", "shared"]
    assert [a.class_name for a in index.named(alpha, "shared")] == ["First", "Second"]
    assert index.named(alpha, "lone") == ()
    assert index.in_file(str(tmp_path / "missing.py")) == ()
    assert index.functions == tuple(functions)


def test_innermost_function_at_prefers_the_nested_definition(tmp_path: Path) -> None:
    index = FunctionIndex.build(_functions(tmp_path))
    alpha = str(tmp_path / "alpha.py")

    inner = index.innermost_at(alpha, (4, 4))
    assert inner is not None and inner.node.name == "inner"
    outer = index.innermost_at(alpha, (5, 5))
    assert outer is not None and outer.node.name == "outer"
    assert index.innermost_at(alpha, (200, 201)) is None


def test_global_declarations_and_digests_are_per_file(tmp_path: Path) -> None:
    index = FunctionIndex.build(_functions(tmp_path))
    alpha, beta = str(tmp_path / "alpha.py"), str(tmp_path / "beta.py")

    assert index.declares_global(alpha)
    assert not index.declares_global(beta)
    assert not index.declares_global(str(tmp_path / "missing.py"))
    assert index.source_digest(beta) == hashlib.sha256(BETA.encode("utf-8")).hexdigest()
    assert index.source_digest(str(tmp_path / "missing.py")) is None

# Copyright 2026 Eric Allen
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

"""Work bounds for re-exports, including the first relaxed-mode query on NetworkX."""

import ast
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
import multiprocessing
import os
import shutil
from pathlib import Path
import sys
from types import FrameType
from typing import Iterator
from unittest.mock import patch

import pytest

from tests.test_parallel_evaluation import _lower_thresholds, _module, _settings
from tests.test_run_report_engine import DUPLICATES
from towel.analysis_sources import parse_analysis_source, sharing_analysis_sources
from towel.cli_reporting import census_line
from towel.unification import parallel
from towel.unification.import_graph import ImportGraphCache, ImportLocation, import_location
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.resource_guard import (
    ResourceImports,
    resource_guard,
    sharing_resource_answers,
)


@pytest.fixture
def package(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="reexport_graph"\nversion="0"\n')
    package = tmp_path / "reexport_graph"
    package.mkdir()
    (package / "__init__.py").write_text("")
    return package


@contextmanager
def _bounded_search_calls(limit: int) -> Iterator[None]:
    """Bound Python calls in the resource guard and its traversal, including generator resumptions."""
    count = 0

    def observe(frame: FrameType, event: str, arg: object) -> None:
        nonlocal count
        if event == "call" and frame.f_globals.get("__name__", "").startswith(
            "towel.unification.resource_"
        ):
            count += 1
            assert count <= limit, f"re-export search exceeded {limit:,} calls"

    previous = sys.getprofile()
    sys.setprofile(observe)
    try:
        yield
    finally:
        sys.setprofile(previous)


def test_reexport_star_cycle_expands_a_finite_graph_once(package: Path) -> None:
    # This possible constructor name survives the terminal index. Every miss
    # can take another lap around the same eight modules.
    width = 8
    for index in range(width):
        (package / f"ring{index}.py").write_text(
            "".join(
                f"from reexport_graph.ring{other} import *\n"
                for other in range(width)
                if other != index
            )
        )
    client = package / "client.py"
    client.write_text("from reexport_graph.ring0 import TemporaryDirectory\n")
    graph = ImportGraphCache()
    with sharing_analysis_sources(), sharing_resource_answers(graph, package.parent):
        with _bounded_search_calls(6000):
            assert (
                ResourceImports(str(client), graph).follow(
                    frozenset({"reexport_graph.ring0.TemporaryDirectory"})
                )
                == frozenset()
            )


def _check_absolute_importers(
    package: Path, queries: dict[str, bool], max_module_probes: int
) -> None:
    clients = [package / f"client{i}.py" for i in range(16)]
    for client in clients:
        client.write_text(
            "".join(f"from reexport_graph.{name} import factory\n" for name in queries)
        )
    graph = ImportGraphCache()
    with sharing_analysis_sources(), sharing_resource_answers(graph, package.parent):
        with patch.object(
            ImportLocation, "definition", autospec=True, side_effect=ImportLocation.definition
        ) as probes:
            for client in clients:
                imports = ResourceImports(str(client), graph)
                for name, is_resource in queries.items():
                    answer = imports.follow(frozenset({f"reexport_graph.{name}.factory"}))
                    assert answer == ({"tempfile.TemporaryDirectory"} if is_resource else set())
            assert probes.call_count <= max_module_probes


def test_reexport_diamonds_share_their_join_across_importers(package: Path) -> None:
    layers = 14
    for layer in range(layers):
        for side in ("left", "right"):
            (package / f"{side}{layer}.py").write_text(
                "".join(
                    f"from reexport_graph.{next_side}{layer + 1} import *\n"
                    for next_side in ("left", "right")
                )
                if layer + 1 < layers
                else "from tempfile import TemporaryDirectory as factory\n"
            )
    _check_absolute_importers(package, {"left0": True}, max_module_probes=8 * layers)


def test_reexport_long_chain_shares_answers_at_the_depth_boundary(package: Path) -> None:
    length = 60
    for index in range(length):
        (package / f"link{index}.py").write_text(
            f"from reexport_graph.link{index + 1} import factory\n"
            if index + 1 < length
            else "from tempfile import TemporaryDirectory as factory\n"
        )
    _check_absolute_importers(
        package, {"link0": False, "link27": False, "link28": True}, max_module_probes=6 * length
    )


def _networkx_getattr(package: Path, limit: int) -> None:
    file = package / "__init__.py"
    module = ast.parse(file.read_text())
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "__getattr__"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AttributeError"
        for node in ast.walk(function)
    )
    graph = ImportGraphCache(excluded_names=("tests",))
    location = import_location(file, "networkx", graph)
    assert location is not None and location.path == package
    with sharing_analysis_sources(), sharing_resource_answers(graph, package.parent):
        with _bounded_search_calls(limit):
            verdict = resource_guard(function.body, module, ResourceImports(str(file), graph))
    assert verdict.names == set() and verdict.refusal is None


def test_networkx_reexport_builtin_reduced(tmp_path: Path) -> None:
    # Reduce the pinned source to its top-level imports, a subpackage, and the failing call.
    (tmp_path / "pyproject.toml").write_text('[project]\nname="networkx"\nversion="0"\n')
    package = tmp_path / "networkx"
    package.mkdir()
    names = "classes convert convert_matrix relabel generators readwrite algorithms linalg drawing".split()
    (package / "__init__.py").write_text(
        "from networkx.exception import *\n"
        + "".join(f"from networkx import {name}\nfrom networkx.{name} import *\n" for name in names)
        + "def __getattr__(name):\n    raise AttributeError(name)\n"
    )
    for name in {"exception", *names} - {"algorithms"}:
        (package / f"{name}.py").write_text("")
    algorithms = package / "algorithms"
    algorithms.mkdir()
    (algorithms / "__init__.py").write_text("from networkx.algorithms.clique import *\n")
    (algorithms / "clique.py").write_text("")
    _networkx_getattr(package, limit=2_000)


def test_networkx_reexport_builtin_native() -> None:
    """TOWEL_NETWORKX_SOURCE is the package directory from scripts/ecosystem/manifest.toml."""
    source = os.environ.get("TOWEL_NETWORKX_SOURCE")
    if source is None:
        pytest.skip("Set TOWEL_NETWORKX_SOURCE to run against the full native corpus checkout")
    _networkx_getattr(Path(source).resolve(), limit=100_000)


def test_reexported_builtin_rebound_through_a_star(package: Path) -> None:
    (package / "leaf.py").write_text("from tempfile import TemporaryDirectory as AttributeError\n")
    (package / "middle.py").write_text("from .leaf import *\n")
    file = package / "client.py"
    file.write_text("from .middle import *\ndef work():\n    held = AttributeError()\n")
    module = ast.parse(file.read_text())
    function = module.body[-1]
    assert isinstance(function, ast.FunctionDef)
    graph = ImportGraphCache()
    with sharing_analysis_sources(), sharing_resource_answers(graph, package.parent):
        guard = resource_guard(function.body, module, ResourceImports(str(file), graph))
    assert guard.names == {"held"} and guard.refusal is None


def test_recognized_reexport_with_a_suffix_stops_at_the_literal(
    package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every production constructor is in the standard library. Give one local
    # spelling the same status to exercise a physical module at that spelling.
    (package / "facade.py").write_text("import reexport_graph.leaf as makers\n")
    (package / "leaf.py").write_text("from weakref import ref as create\n")
    client = package / "client.py"
    client.write_text("from .facade import makers\nvalue = makers.create(target)\n")
    imports = ResourceImports(str(client), ImportGraphCache())
    origin = frozenset({".facade.makers.create"})
    assert imports.follow(origin) == {"weakref.ref"}

    literal = "reexport_graph.leaf.create"
    monkeypatch.setattr(
        "towel.unification.resource_guard._RECOGNIZED", frozenset({literal, "weakref.ref"})
    )
    assert imports.follow(origin) == {literal}


def test_reexported_builtin_can_change_outside_the_stage(package: Path) -> None:
    stage = package / "stage"
    stage.mkdir()
    (stage / "__init__.py").write_text("")
    file = stage / "client.py"
    file.write_text("from ..provider import *\ndef work():\n    held = AttributeError()\n")
    provider = package / "provider.py"
    provider.write_text("class AttributeError(Exception): pass\n")
    module = ast.parse(file.read_text())
    function = module.body[-1]
    assert isinstance(function, ast.FunctionDef)
    graph = ImportGraphCache()
    with sharing_analysis_sources(), sharing_resource_answers(graph, stage):
        imports = ResourceImports(str(file), graph)
        assert resource_guard(function.body, module, imports).names == set()
        provider.write_text("from tempfile import TemporaryDirectory as AttributeError\n")
        guard = resource_guard(function.body, module, imports)
        assert guard.names == {"held"} and guard.refusal is None


def test_reexport_budget_is_shared_by_calls_and_declines_unknown_ownership(
    package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each ordinary factory resolves in one hop. Only their total can exceed this budget.
    monkeypatch.setattr("towel.unification.resource_budget._WORK_LIMIT", 128)
    names = [f"factory{n}" for n in range(256)]
    (package / "provider.py").write_text("".join(f"def {name}(): pass\n" for name in names))
    file = package / "client.py"
    file.write_text(
        "from .provider import "
        + ", ".join(names)
        + "\n"
        + "".join(f"with {name}(): pass\n" for name in names)
    )
    module = ast.parse(file.read_text())
    graph = ImportGraphCache()
    with sharing_analysis_sources(), sharing_resource_answers(graph, package.parent):
        imports = ResourceImports(str(file), graph)
        answers = [resource_guard([block], module, imports) for block in module.body[1:]]
    assert answers[0].refusal is None
    last_reason = answers[-1].refusal
    assert last_reason and "may be a resource" in last_reason
    assert graph.resource_budget.exhausted


def test_run_census_reports_reexport_budget_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("towel.unification.resource_budget._WORK_LIMIT", 1)
    target = tmp_path / "project"
    target.mkdir()
    (target / "provider.py").write_text("def factory(x): return x + 1\n")
    (target / "client.py").write_text(
        "from provider import factory\n" + DUPLICATES.replace("a = x + 1", "a = factory(x)")
    )
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False, strict=False)
    engine.refactor_directory_to_fixed_point(str(target), str(tmp_path / "out"), progress="none")
    report = engine.run_report
    assert "may be resources" in census_line(report)
    assert report.declined_pairs["resource_lifetime_unproven"] > 0

    # A later run on the same engine has its own allowance and its own census.
    monkeypatch.setattr("towel.unification.resource_budget._WORK_LIMIT", 1_000_000)
    engine.refactor_directory_to_fixed_point(str(target), str(tmp_path / "fresh"), progress="none")
    assert "re-export work limit" not in census_line(engine.run_report)
    assert "resource_lifetime_unproven" not in engine.run_report.declined_pairs


def test_reexport_budget_inside_a_managed_helper_keeps_the_call(
    package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("towel.unification.resource_budget._WORK_LIMIT", 1)
    (package / "provider.py").write_text("def watch(value): pass\n")
    file = package / "client.py"
    file.write_text(
        "from .provider import watch\n"
        "def __extracted_func_0(value):\n    with watch(value): pass\n"
        "def client(value):\n    __extracted_func_0(value)\n"
    )
    module = ast.parse(file.read_text())
    function = module.body[-1]
    assert isinstance(function, ast.FunctionDef)
    guard = resource_guard(function.body, module, ResourceImports(str(file), ImportGraphCache()))
    assert guard.refusal and "generated helper" in guard.refusal


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="needs fork")
def test_reexport_budget_spent_in_a_pair_worker_reaches_the_parents_census(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("towel.unification.resource_budget._WORK_LIMIT", 1024)
    _lower_thresholds(monkeypatch)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="probe"\nversion="0"\n')
    (tmp_path / "provider.py").write_text("def factory(x): return x + 1\n")
    file = tmp_path / "client.py"
    file.write_text(
        "from provider import factory\n"
        + _module(10).replace("a = 1 +", "a = factory(1) +").replace("a = 2 *", "a = factory(2) *")
    )
    engine = UnificationRefactorEngine(
        min_lines=3, skip_trivial_helpers=False, strict=False, settings=_settings(2)
    )
    # Between forking and collecting the results only the children judge pairs.
    # Observe the real pool at both ends to exclude exhaustion by the serial probe.
    reports: list[str] = []
    at_fork = parallel._alone_at_fork
    stop = parallel._stop_pool

    @contextmanager
    def before_fork() -> Iterator[tuple[str, ...]]:
        with at_fork() as running:
            if not running:
                reports.append(census_line(engine.run_report))
            yield running

    def after_results(pool: ProcessPoolExecutor) -> None:
        try:
            reports.append(census_line(engine.run_report))
        finally:
            stop(pool)

    monkeypatch.setattr(parallel, "_alone_at_fork", before_fork)
    monkeypatch.setattr(parallel, "_stop_pool", after_results)
    engine.analyze_files([str(file)], progress="none")
    assert ["may be resources" in report for report in reports] == [False, True]


def test_uncertain_reexport_source_preserves_import_and_assignment_names() -> None:
    import warnings

    from towel.unification.resource_budget import ResourceBudget
    from towel.unification.resource_terminal_source import terminal_syntax

    source = (
        "from ...lazy_provider.core import lazy_factory as lazy_alias\n"
        "import lazy_top.branch as maker\n"
        "((renamed)) = maker.lazy_attr\n"
        "(typed): object = lazy_alias\n"
        "if (assigned := lazy_alias): pass\n"
        "K = lazy_alias\n"  # Python normalizes this identifier to K.
        r"label = '\q'" + "\n"
    )
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        module = terminal_syntax(source, ResourceBudget())
    assert emitted == []
    assert module is not None
    first, second = module.body[:2]
    assert (
        isinstance(first, ast.ImportFrom)
        and first.module == "lazy_provider.core"
        and first.level == 3
    )
    assert [(alias.name, alias.asname) for alias in first.names] == [("lazy_factory", "lazy_alias")]
    assert isinstance(second, ast.Import)
    assert [(alias.name, alias.asname) for alias in second.names] == [("lazy_top.branch", "maker")]
    names = {node.id for node in ast.walk(module) if isinstance(node, ast.Name)}
    assert {"renamed", "maker", "typed", "lazy_alias", "assigned", "K"} <= names
    assert {node.attr for node in ast.walk(module) if isinstance(node, ast.Attribute)} == {
        "lazy_attr"
    }


@pytest.mark.parametrize(
    "source",
    [
        'value = f"{(kept := factory)}"',  # A binding inside a literal.
        r'value = f"\{1}"',  # A C tokenizer can warn before yielding this token.
        "lazy import provider",  # The soft keyword here is part of the grammar.
        "import lazy_top as _towel_terminal_name_0",  # A fresh name would collide.
    ],
)
def test_uncertain_reexport_source_declines_unproven_projections(source: str) -> None:
    import warnings

    from towel.unification.resource_budget import ResourceBudget
    from towel.unification.resource_terminal_source import terminal_syntax

    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        assert terminal_syntax(source, ResourceBudget()) is None
    assert emitted == []


def test_reexport_missing_external_provider_is_checked_for_the_next_importer(
    tmp_path: Path,
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    graph = ImportGraphCache()
    origins = frozenset({"..external.factory"})
    with sharing_analysis_sources(), sharing_resource_answers(graph, stage):
        assert ResourceImports(str(stage / "first.py"), graph).follow(origins) == set()
        (tmp_path / "external.py").write_text("from weakref import ref as factory\n")
        assert ResourceImports(str(stage / "second.py"), graph).follow(origins) == {"weakref.ref"}


def test_reexport_terminal_bindings_follow_current_contents_across_analyses(package: Path) -> None:
    from towel.unification import resource_terminals

    provider = package / "provider.py"
    source = "from tempfile import TemporaryDirectory as factory\n"
    provider.write_text(source)
    client = package / "client.py"
    client.write_text("from reexport_graph.provider import factory\n")
    graph = ImportGraphCache()
    origins = frozenset({"reexport_graph.provider.factory"})
    original_stat = provider.stat()
    with patch.object(
        resource_terminals, "parse_analysis_source", wraps=parse_analysis_source
    ) as parses:
        for current, expected in [
            (source, {"tempfile.TemporaryDirectory"}),
            (source, {"tempfile.TemporaryDirectory"}),
            (source.replace("factory", "another"), set()),
        ]:
            provider.write_text(current)
            os.utime(provider, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            with sharing_analysis_sources(), sharing_resource_answers(graph, package.parent):
                assert ResourceImports(str(client), graph).follow(origins) == expected
        assert sum(args[0] == source for args, _ in parses.call_args_list) == 1


@pytest.fixture
def fruit(tmp_path: Path) -> Path:
    package = tmp_path / "original" / "fruit"
    package.mkdir(parents=True)
    (package.parent / "pyproject.toml").write_text('[project]\nname="fruit"\nversion="0"\n')
    (package / "__init__.py").write_text("")
    return package


@pytest.mark.parametrize("change", ["new_module", "new_initializer", "linked_module", "linked_dir"])
def test_absolute_reexport_observes_original_layout_for_a_new_importer(
    tmp_path: Path, fruit: Path, change: str
) -> None:
    stage = tmp_path / "stage"
    original, package = fruit.parent, fruit
    outside = tmp_path / "external"
    outside.mkdir()
    (package / "anchor.py").write_text("value = 1\n")
    for client in ("first.py", "second.py"):
        (package / client).write_text("from fruit.anchor import value\n")
    origin = "fruit.late.factory"
    provider = package / "late.py"
    if change == "new_initializer":
        (package / "late").mkdir()
        provider = package / "late" / "__init__.py"
    elif change == "linked_module":
        provider.write_text("def factory(): pass\n")
    elif change == "linked_dir":
        (package / "link").symlink_to(outside, target_is_directory=True)
        origin = "fruit.link.late.factory"
        provider = outside / "late.py"
    stage.mkdir()
    shutil.copytree(package, stage / package.name, symlinks=True)
    graph = ImportGraphCache()
    graph.begin_run(original, stage)
    clients = stage / package.name
    location = import_location(clients / "first.py", package.name, graph)
    assert location is not None and location.path == package
    with sharing_analysis_sources(), sharing_resource_answers(graph, stage):
        assert (
            ResourceImports(str(clients / "first.py"), graph).follow(frozenset({origin})) == set()
        )
        if change == "linked_module":
            link = package / "replacement"
            link.symlink_to(outside / "late.py")
            link.replace(provider)
            provider = outside / "late.py"
        provider.write_text("from weakref import ref as factory\n")
        assert ResourceImports(str(clients / "second.py"), graph).follow(frozenset({origin})) == {
            "weakref.ref"
        }


def test_reexport_observes_a_dangling_stage_link_becoming_a_provider(package: Path) -> None:
    provider = package.parent / "external.py"
    (package / "late.py").symlink_to(provider)
    graph = ImportGraphCache()
    origins = frozenset({".late.factory"})
    with sharing_analysis_sources(), sharing_resource_answers(graph, package):
        assert ResourceImports(str(package / "first.py"), graph).follow(origins) == set()
        provider.write_text("from weakref import ref as factory\n")
        assert ResourceImports(str(package / "second.py"), graph).follow(origins) == {"weakref.ref"}


def test_reexport_redirected_importer_can_change_its_relative_base(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    first, second = stage / "first", stage / "second"
    for directory in (first, second):
        directory.mkdir(parents=True)
        (directory / "client.py").write_text("")
    (second / "provider.py").write_text("from weakref import ref as factory\n")
    redirect = tmp_path / "redirect"
    redirect.symlink_to(first / "client.py")
    importer = stage / "client.py"
    importer.symlink_to(redirect)
    graph = ImportGraphCache()
    with sharing_analysis_sources(), sharing_resource_answers(graph, stage):
        calls = ResourceImports(str(importer), graph)
        assert calls.follow(frozenset({".missing.factory"})) == set()
        replacement = tmp_path / "replacement"
        replacement.symlink_to(second / "client.py")
        replacement.replace(redirect)
        assert calls.follow(frozenset({".provider.factory"})) == {"weakref.ref"}


def test_reexport_original_provider_can_have_a_redirected_staged_copy(
    tmp_path: Path, fruit: Path
) -> None:
    original, stage = fruit.parent, tmp_path / "stage"
    for name in ("first", "second"):
        (fruit / f"{name}.py").write_text("from fruit.provider import factory\n")
    plain = "def factory(): pass\n"
    shutil.copytree(original, stage)
    (fruit / "provider.py").write_text(plain)
    safe, weak = stage / "safe.py", stage / "weak.py"
    safe.write_text(plain)
    weak.write_text("from weakref import ref as factory\n")
    redirect = tmp_path / "redirect"
    redirect.symlink_to(safe)
    (stage / "fruit" / "provider.py").symlink_to(redirect)
    graph = ImportGraphCache()
    graph.begin_run(original, stage)
    origins = frozenset({"fruit.provider.factory"})
    with sharing_analysis_sources(), sharing_resource_answers(graph, stage):
        assert ResourceImports(str(stage / "fruit" / "first.py"), graph).follow(origins) == set()
        replacement = tmp_path / "replacement"
        replacement.symlink_to(weak)
        replacement.replace(redirect)
        assert ResourceImports(str(stage / "fruit" / "second.py"), graph).follow(origins) == {
            "weakref.ref"
        }


def test_reexport_same_importer_rechecks_original_layout_after_an_uncertain_parse(
    tmp_path: Path,
    fruit: Path,
) -> None:
    original, stage, package = fruit.parent, tmp_path / "stage", fruit
    (package / "__init__.py").write_text(r"banner = '\q'" + "\n")
    (package / "anchor.py").write_text("value = 1\n")
    (package / "client.py").write_text("from fruit.anchor import value\n")
    shutil.copytree(original, stage)
    graph = ImportGraphCache()
    graph.begin_run(original, stage)
    imports = ResourceImports(str(stage / "fruit" / "client.py"), graph)
    origins = frozenset({"fruit.late.factory"})
    with sharing_analysis_sources(), sharing_resource_answers(graph, stage):
        assert imports.follow(origins) == set()
        (package / "late.py").write_text("from weakref import ref as factory\n")
        assert imports.follow(origins) == {"weakref.ref"}


def test_reexport_layout_limit_declines_even_with_graph_allowance_left(
    tmp_path: Path, fruit: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("towel.unification.resource_budget._LAYOUT_LIMIT", 1, raising=False)
    original, stage, package = fruit.parent, tmp_path / "stage", fruit
    (package / "__init__.py").write_text("")
    (package / "provider.py").write_text("def factory(): pass\n")
    source = "from fruit.provider import factory\nwith factory(): pass\n"
    (package / "client.py").write_text(source)
    shutil.copytree(original, stage)
    graph = ImportGraphCache()
    graph.begin_run(original, stage)
    module = ast.parse(source)
    with sharing_analysis_sources(), sharing_resource_answers(graph, stage):
        guard = resource_guard(
            module.body[1:], module, ResourceImports(str(stage / "fruit" / "client.py"), graph)
        )
    assert guard.refusal and "may be a resource" in guard.refusal
    assert graph.resource_budget.remaining > 0

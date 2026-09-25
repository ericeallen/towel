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

"""An inline-snapshot call keeps its place: its callee reads the source and position of the call.

The fourth audit round found rich-click's tests going from pass to fail, 68
of 151: two tests doing ``assert out == snapshot("<item 1>")`` and
``snapshot("<item 2>")`` became one helper doing ``snapshot(__param_1)``, and
inline-snapshot, which keys each ``snapshot()`` by its position and reads its
argument where it stands, raised ``UsageError``. A block that refers to a
callee of ``KNOWN_SOURCE_READERS`` is now declined (``source_reading_callee``),
and the code around the call is still shared. Every other callee that reads
its caller's frame is reflection, which Towel does not model.

These tests hold how a name resolves to a listed reader (``source_readers``),
that the list names what inline-snapshot defines, and that the battery's
``r9sr_inline_snapshot`` fixture, which stands a stub beside ``pkg``, behaves
alike with inline-snapshot itself wherever it is installed.
"""

from __future__ import annotations

import ast
from collections import Counter
import contextlib
import importlib
import importlib.metadata
import importlib.util
import io
from pathlib import Path
import shutil
import textwrap
from typing import Optional, Tuple

import pytest

from tests.hostile_execution import observe
from tests.hostile_refactoring import refactor_package, refactor_script
from towel.diagnostics import Settings
from towel.unification import source_readers
from towel.unification.known_source_readers import KNOWN_SOURCE_READERS
from towel.unification.models import RejectReason
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.source_readers import module_source_readers, source_reader_in

FIXTURE = Path(__file__).parent / "hostile_crossfile" / "r9sr_inline_snapshot"


def _reader(source: str) -> Optional[str]:
    """The reader the body of the module's last function, at any depth, refers to."""
    tree = ast.parse(textwrap.dedent(source))
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = max(
        (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)),
        key=lambda node: node.lineno,
    )
    return source_reader_in(analyzer, function.body)


SPELLINGS: Tuple[Tuple[str, str, Optional[str]], ...] = (
    (
        "direct",
        """
        from inline_snapshot import snapshot
        def test(out):
            assert out == snapshot("<item 1>")
        """,
        "inline_snapshot.snapshot",
    ),
    (
        "aliased_module",
        """
        import inline_snapshot as ins
        def test(out):
            assert out == ins.snapshot("<item 1>")
        """,
        "inline_snapshot.snapshot",
    ),
    (
        "module_attribute",
        """
        import inline_snapshot
        def test(out):
            assert out == inline_snapshot.snapshot("<item 1>")
        """,
        "inline_snapshot.snapshot",
    ),
    (
        "from_import_as",
        """
        from inline_snapshot import snapshot as snap
        def test(out):
            assert out == snap("<item 1>")
        """,
        "inline_snapshot.snapshot",
    ),
    (
        "defining_module",
        """
        from inline_snapshot._inline_snapshot import snapshot
        def test(out):
            assert out == snapshot("<item 1>")
        """,
        "inline_snapshot._inline_snapshot.snapshot",
    ),
    (
        "nested_in_an_expression",
        """
        from inline_snapshot import snapshot
        def test(rows):
            assert [len(row) for row in rows] == list(map(int, snapshot(["1", "2"])))
        """,
        "inline_snapshot.snapshot",
    ),
    (
        "inside_a_lambda",
        """
        from inline_snapshot import snapshot
        def test(out):
            check = lambda: out == snapshot("<item 1>")
            assert check()
        """,
        "inline_snapshot.snapshot",
    ),
    (
        "assigned_alias",
        """
        from inline_snapshot import snapshot
        snap = snapshot
        def test(out):
            assert out == snap("<item 1>")
        """,
        "inline_snapshot.snapshot",
    ),
    (
        "local_import",
        """
        def test(out):
            from inline_snapshot import snapshot
            assert out == snapshot("<item 1>")
        """,
        "inline_snapshot.snapshot",
    ),
    (
        "parameter_default",
        """
        from inline_snapshot import snapshot
        def test(out, expect=snapshot):
            assert out == expect("<item 1>")
        """,
        "inline_snapshot.snapshot",
    ),
    (
        "star_import",
        """
        from inline_snapshot import *
        def test(out):
            assert out == snapshot("<item 1>")
        """,
        "inline_snapshot.snapshot",
    ),
    (
        "external",
        """
        from inline_snapshot import external
        def test(data):
            assert data == external("hash:0123.txt")
        """,
        "inline_snapshot.external",
    ),
    (
        "function_calling_snapshot_arg",
        """
        from inline_snapshot import snapshot_arg
        def get_stats(numbers, expected_sum=...):
            assert sum(numbers) == snapshot_arg(expected_sum)
        def test():
            get_stats([1, 2, 3], expected_sum=6)
        """,
        "inline_snapshot.snapshot_arg",
    ),
    (
        "through_another_function",
        """
        import inline_snapshot
        def get_stats(numbers, expected_sum=...):
            assert sum(numbers) == inline_snapshot.snapshot_arg(expected_sum)
        def check(numbers, total):
            get_stats(numbers, expected_sum=total)
        def test():
            check([1, 2, 3], 6)
        """,
        "inline_snapshot.snapshot_arg",
    ),
    (
        "method_reached_through_a_receiver",
        """
        from inline_snapshot import snapshot_arg
        class Stats:
            def check(self, numbers, expected_sum=...):
                assert sum(numbers) == snapshot_arg(expected_sum)
        def test(stats):
            stats.check([1, 2, 3], expected_sum=6)
        """,
        "inline_snapshot.snapshot_arg",
    ),
    (
        "class_whose_constructor_reads",
        """
        from inline_snapshot import snapshot_arg
        class Stats:
            def __init__(self, numbers, expected_sum=...):
                assert sum(numbers) == snapshot_arg(expected_sum)
        def test():
            Stats([1, 2, 3], expected_sum=6)
        """,
        "inline_snapshot.snapshot_arg",
    ),
    # What is not a reader.
    (
        "same_name_from_another_library",
        """
        from mylib import snapshot
        def test(out):
            assert out == snapshot("<item 1>")
        """,
        None,
    ),
    (
        "values_that_read_no_frame",
        """
        from inline_snapshot import Is, outsource
        def test(out, data):
            assert out == Is(out) and outsource(data) == data
        """,
        None,
    ),
    (
        "a_function_calling_snapshot_reads_only_its_own_call",
        """
        from inline_snapshot import snapshot
        def check(out):
            assert out == snapshot("<item 1>")
        def test(out):
            check(out)
        """,
        None,
    ),
    (
        "other_frame_readers_are_reflection",
        """
        from icecream import ic
        from collections import namedtuple
        def show(width):
            ic(width)
            return namedtuple("Point", "x y")
        """,
        None,
    ),
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [pytest.param(source, expected, id=case) for case, source, expected in SPELLINGS],
)
def test_r9sr_each_spelling_resolves_to_its_reader(source: str, expected: Optional[str]) -> None:
    assert _reader(source) == expected


def test_r9sr_an_alias_rebound_in_a_loop_reaches_a_fixed_point() -> None:
    """``node = node.next`` would lengthen the chain it denotes forever; names are kept finitely.

    An early version of the analysis hung on any module that walked a chain
    rooted in an import, as ``path = path.parent`` does after ``pathlib``.
    """
    readers = module_source_readers(ast.parse(textwrap.dedent("""
        import pathlib
        import inline_snapshot
        node = inline_snapshot.snapshot
        while node:
            node = node.next
        path = pathlib.Path(".")
        while path.name:
            path = path.parent
        """)))
    assert readers.origins["node"] == {"inline_snapshot.snapshot"}
    assert "path" not in readers.origins


def test_r9sr_the_analysis_finishes_on_every_module_of_towel_and_its_fixtures() -> None:
    """Every module of Towel's source and of the hostile battery is analyzed, and none of
    Towel's own reads its call."""
    root = Path(__file__).resolve().parents[1]
    paths = [
        *sorted((root / "src" / "towel").rglob("*.py")),
        *sorted((root / "tests" / "hostile_cases").glob("*.py")),
    ]
    assert len(paths) > 100
    for path in paths:
        try:
            tree = ast.parse(path.read_bytes())
        except SyntaxError:
            continue  # A fixture in syntax this Python does not have.
        readers = module_source_readers(tree)
        if path.is_relative_to(root / "src"):
            assert not readers.project_readers, path


def test_r9sr_a_module_is_read_once_and_a_statement_judged_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard's cost, counted rather than timed: a statement belongs to every block spanning it.

    Eight functions share a five-line body, so every pair of them, and every
    block within each, is judged; the module is still analyzed once, and
    each statement resolved at most once.
    """
    body = "\n".join(f"    total_{index} = width * {index} + height" for index in range(5))
    path = tmp_path / "check_shapes.py"
    path.write_text(
        "from inline_snapshot import snapshot\n\n"
        + "".join(
            f"\ndef test_{name}(width, height):\n{body}\n"
            f"    assert total_4 == snapshot({name})\n    return total_4\n\n"
            for name in range(8)
        )
    )
    counts: Counter[str] = Counter()
    analyzed, first = source_readers._analyzed, source_readers._Resolution.first

    def counted_analyzed(module: ast.AST) -> source_readers.ModuleSourceReaders:
        counts["modules"] += 1
        return analyzed(module)

    def counted_first(self: source_readers._Resolution, statement: ast.AST) -> Optional[str]:
        counts["statements"] += 1
        return first(self, statement)

    monkeypatch.setattr(source_readers, "_analyzed", counted_analyzed)
    monkeypatch.setattr(source_readers._Resolution, "first", counted_first)
    engine = UnificationRefactorEngine(
        min_lines=3, settings=Settings.from_environ({"TOWEL_WORKERS": "1"})
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.analyze_file(str(path))
    statements = sum(isinstance(node, ast.stmt) for node in ast.walk(ast.parse(path.read_text())))
    assert engine.declined_pairs, "the pairs must have been judged"
    assert counts["modules"] == 1
    assert 0 < counts["statements"] <= statements


REPRODUCER = """
from inline_snapshot import snapshot


def render(n):
    return f"<item {n}>"


def test_one():
    out = render(1)
    assert out.startswith("<item")
    assert out.endswith(">")
    assert out == snapshot("<item 1>")


def test_two():
    out = render(2)
    assert out.startswith("<item")
    assert out.endswith(">")
    assert out == snapshot("<item 2>")
"""


def test_r9sr_the_reproducer_keeps_each_snapshot_where_it_stands(tmp_path: Path) -> None:
    path = tmp_path / "check_render.py"
    path.write_text(REPRODUCER.lstrip())
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.analyze_file(str(path))
    reason = f"{RejectReason.SOURCE_READING_CALLEE}[inline_snapshot.snapshot]"
    assert engine.declined_pairs.get(reason, 0) > 0, engine.declined_pairs
    assert refactor_script(path) == 1
    functions = {
        node.name: ast.unparse(node)
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef)
    }
    assert "snapshot('<item 1>')" in functions["test_one"]
    assert "snapshot('<item 2>')" in functions["test_two"]
    assert not any("__param" in text and "snapshot" in text for text in functions.values())


def _installed() -> None:
    if importlib.util.find_spec("inline_snapshot") is None:
        pytest.skip("inline-snapshot is not installed")


def test_r9sr_every_listed_name_is_what_inline_snapshot_defines() -> None:
    """Each entry is a callable of the installed inline-snapshot, reached as an import reaches it.

    A release that moves or renames one fails here, and the entry returns
    for review against the new source.
    """
    _installed()
    version = importlib.metadata.version("inline-snapshot")
    for entry in KNOWN_SOURCE_READERS:
        module, _, name = entry.origin.rpartition(".")
        found = getattr(importlib.import_module(module), name, None)
        assert callable(found), f"{entry.origin} is not defined in inline-snapshot {version}"


def test_r9sr_the_fixture_behaves_alike_with_inline_snapshot_itself(tmp_path: Path) -> None:
    """``r9sr_inline_snapshot``, its stub taken away, run under the installed library's plugin.

    Skipped where inline-snapshot is not installed, as in the suite's own
    environments. Where it is, the refactored tests pass as the originals do.
    """
    _installed()
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        shutil.copytree(FIXTURE, root)
        (root / "inline_snapshot.py").unlink()
    results = refactor_package(after / "pkg", cross_module=False)
    assert sum(applied for applied, _ in results.values()) > 0
    assert observe("run.py", after) == observe("run.py", before)

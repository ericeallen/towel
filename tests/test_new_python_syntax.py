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

"""Python lane: new syntax is analyzed safely and refused before extraction.

These hostile programs observe interpolation text, iteration/cleanup order,
exception handling and delayed imports. Source-preserving output must retain
constructs that have no extraction proof while processing the rest of the file.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
import subprocess
import sys
import textwrap
import warnings

import pytest

from towel.unification.import_graph import (
    ImportGraphCache,
    ImportChange,
    _import_edges,
    import_change,
    leading_imports,
)
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.resource_guard import resource_guard
from towel.unification.models import CodeBlockPair
from towel.unification.pipeline import analyze_scopes, collect_functions, parse_modules
from towel.unification.unsupported_syntax import unsupported_syntax

NEW_EXPRESSIONS = [
    pytest.param(
        't"{mark(value)!r}"', "result.interpolations[0].expression", (3, 14), id="template"
    ),
    pytest.param("[*part for part in mark(value)]", "result", (3, 15), id="list-unpacking"),
    pytest.param("{*part for part in mark(value)}", "sorted(result)", (3, 15), id="set-unpacking"),
    pytest.param("{**part for part in mark(value)}", "result", (3, 15), id="dict-unpacking"),
    pytest.param(
        "(*part for part in mark(value))", "list(result)", (3, 15), id="generator-unpacking"
    ),
]


def _run(path: Path) -> str:
    return subprocess.run(
        [sys.executable, "-I", "-B", str(path)], capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.parametrize("expression,observe,version", NEW_EXPRESSIONS)
def test_cluster_admission_explains_unsupported_syntax(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    expression: str,
    observe: str,
    version: tuple[int, int],
) -> None:
    """Python audit: a further cluster site gets the same syntax reason as a pair."""
    if sys.version_info < version:
        pytest.skip("syntax is newer than this interpreter")
    source = "\n".join(
        f"def {name}(value):\n    result = {expression}\n    return {observe}\n"
        for name in ("first", "second")
    )
    path = tmp_path / "m.py"
    path.write_text(source)
    first, second = collect_functions(analyze_scopes(parse_modules([str(path)])))
    pair = CodeBlockPair(
        str(path),
        first.node.name,
        second.node.name,
        (first.node.body[0].lineno, first.node.end_lineno or first.node.lineno),
        (second.node.body[0].lineno, second.node.end_lineno or second.node.lineno),
        first.node.body,
        second.node.body,
        str(path),
        None,
        None,
        None,
        None,
        [],
        [],
        first.scope_analyzer,
        second.scope_analyzer,
        first.root_scope,
        second.root_scope,
        source,
        source,
        first.node,
        second.node,
    )
    engine = UnificationRefactorEngine()
    with caplog.at_level(logging.DEBUG, logger="towel.rejections"):
        assert engine._admissible_candidate(first, first.node.body, None, pair=pair) is None
    explanation = unsupported_syntax(first.node.body)
    assert explanation is not None
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "DECLINE-SITE[unsupported_syntax]" in message and explanation in message
        for message in messages
    )
    assert path.read_text() == source


@pytest.mark.parametrize("expression,observe,version", NEW_EXPRESSIONS)
def test_new_expressions_keep_source_and_effects(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    expression: str,
    observe: str,
    version: tuple[int, int],
) -> None:
    """Python audit: refuse unknown iteration/interpolation semantics, including effects."""
    if sys.version_info < version:
        pytest.skip("syntax is newer than this interpreter")
    source = "events = []\ndef mark(value):\n    events.append('mark')\n    return value\n\n"
    for name in ("first", "second"):
        source += (
            f"def {name}(value):\n    output = {expression}\n"
            "    events.append('made')\n    return output\n\n"
        )
    # Dictionary keys make all four unpacking forms legal, with stable observation.
    source += (
        "for operation in (first, second):\n"
        "    result = operation([{'a': 1}, {'b': 2}])\n"
        f"    print({observe})\nprint(events)\n"
    )
    project = tmp_path / "project"
    project.mkdir()
    path = project / "m.py"
    path.write_text(source)
    before = _run(path)
    engine = UnificationRefactorEngine(min_lines=2, skip_trivial_helpers=False)
    with caplog.at_level(logging.DEBUG, logger="towel.rejections"):
        proposals = engine.analyze_files([str(path)], progress="none")
    assert engine.declined_pairs.get("unsupported_syntax", 0) > 0
    assert any("must keep" in record.getMessage() for record in caplog.records)
    # The ordinary suffix remains analyzable even though the new expression cannot move.
    assert proposals
    assert all(
        unsupported_syntax(proposal.extracted_function.body) is None for proposal in proposals
    )
    out = tmp_path / "out"
    engine.refactor_directory_to_fixed_point(
        str(project), str(out), max_iterations=1, progress="none"
    )
    rewritten = out / "m.py"
    assert rewritten.read_text().count(f"output = {expression}") == 2
    assert _run(rewritten) == before


@pytest.mark.skipif(sys.version_info < (3, 15), reason="lazy imports need Python 3.15")
@pytest.mark.parametrize("import_line", ["lazy import host", "lazy from host import value"])
def test_lazy_import_is_never_an_eager_graph_edge(tmp_path: Path, import_line: str) -> None:
    """Python audit: a lazy import cannot justify loading a helper's host early."""
    host = tmp_path / "host.py"
    host.write_text("print('host loaded')\nvalue = 1\n")
    borrower = tmp_path / "borrower.py"
    source = f"{import_line}\ndef read():\n    return 1\n"
    borrower.write_text(source)
    tree = ast.parse(source)
    assert unsupported_syntax(tree.body) == (
        "Lazy imports must keep their deferred loading at the original site."
    )
    assert leading_imports(tree) == []
    cache = ImportGraphCache()
    assert _import_edges(borrower, cache.program_for(borrower), cache, "leading") is None
    assert import_change(str(host), str(borrower), cache) is ImportChange.UNKNOWN


@pytest.mark.skipif(sys.version_info < (3, 15), reason="lazy controls need Python 3.15")
@pytest.mark.parametrize(
    "control",
    [
        "__lazy_modules__ = ['host']",
        "import sys; sys.set_lazy_imports('all')",
        "from sys import set_lazy_imports; set_lazy_imports('all')",
        "from sys import set_lazy_imports as enable; enable('all')",
    ],
)
def test_lazy_import_controls_do_not_establish_eager_reach(tmp_path: Path, control: str) -> None:
    """Python audit: implicit lazy imports have the same timing hazard as the keyword."""
    host = tmp_path / "host.py"
    host.write_text("value = 1\n")
    borrower = tmp_path / "borrower.py"
    borrower.write_text(f"{control}\nimport host\ndef read():\n    return host.value\n")
    cache = ImportGraphCache()
    assert import_change(str(host), str(borrower), cache) is ImportChange.UNKNOWN


@pytest.mark.skipif(sys.version_info < (3, 14), reason="parenthesis-free except needs 3.14")
@pytest.mark.parametrize("star", ["", "*"])
def test_parenthesis_free_except_preserves_ast_and_effects(tmp_path: Path, star: str) -> None:
    """Python audit: PEP 758 is existing tuple-handler AST, including except*."""
    source = textwrap.dedent(f"""
        events = []
        def first():
            try:
                events.append('body')
                raise ValueError('expected')
            except{star} ValueError, TypeError:
                events.append('caught')
            finally:
                events.append('finally')
            return events
        def second():
            try:
                events.append('body')
                raise ValueError('expected')
            except{star} ValueError, TypeError:
                events.append('caught')
            finally:
                events.append('finally')
            return events
        print(first(), second())
    """)
    parenthesized = source.replace("ValueError, TypeError:", "(ValueError, TypeError):")
    assert ast.dump(ast.parse(source)) == ast.dump(ast.parse(parenthesized))
    project = tmp_path / "project"
    project.mkdir()
    path = project / "m.py"
    path.write_text(source)
    before = _run(path)
    engine = UnificationRefactorEngine(min_lines=2, skip_trivial_helpers=False)
    engine.refactor_directory_to_fixed_point(
        str(project), str(tmp_path / "out"), max_iterations=1, progress="none"
    )
    assert _run(tmp_path / "out" / "m.py") == before


@pytest.mark.skipif(sys.version_info < (3, 15), reason="unary-plus patterns need Python 3.15")
def test_unary_plus_match_patterns_decline_with_reason(tmp_path: Path) -> None:
    """Python audit: new pattern grammar cannot bypass conservative extraction."""
    source = "\n".join(
        f"def {name}(value):\n    print('match')\n    match value:\n        case +1:\n            print(7)\n"
        "        case _:\n            print(8)\n    return value\n"
        for name in ("first", "second")
    )
    path = tmp_path / "m.py"
    path.write_text(source)
    assert unsupported_syntax(ast.parse(source).body) == (
        "Unary-plus match patterns have no verified extraction rule yet."
    )
    engine = UnificationRefactorEngine(min_lines=2, skip_trivial_helpers=False)
    engine.analyze_files([str(path)], progress="none")
    assert engine.declined_pairs.get("unsupported_syntax", 0) > 0


@pytest.mark.skipif(sys.version_info < (3, 14), reason="finally diagnostics need Python 3.14")
def test_finally_return_keeps_effects_and_native_warning(tmp_path: Path) -> None:
    """Python audit: PEP 765 warns; it must not crash analysis or change finally effects."""
    source = (
        "events = []\n"
        + "\n".join(
            f"def {name}():\n    try:\n        raise ValueError('hidden')\n"
            "    finally:\n        events.append('finally')\n        return 7\n"
            for name in ("first", "second")
        )
        + "\nprint(first(), second(), events)\n"
    )
    project = tmp_path / "project"
    project.mkdir()
    path = project / "m.py"
    path.write_text(source)
    before = _run(path)
    with warnings.catch_warnings(record=True) as native:
        warnings.simplefilter("always")
        compile(source, str(path), "exec")
    assert any("finally" in str(warning.message) for warning in native)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        engine = UnificationRefactorEngine(min_lines=2, skip_trivial_helpers=False)
        engine.refactor_directory_to_fixed_point(
            str(project), str(tmp_path / "out"), max_iterations=1, progress="none"
        )
    assert _run(tmp_path / "out" / "m.py") == before


def test_an_unsupported_syntax_count_has_a_plain_cli_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Python audit: a reason code alone must not be the only user-facing explanation."""
    from towel.cli_reporting import reason_lines
    from towel.unification.fixed_point import RunReport

    report = RunReport(declined_pairs={"unsupported_syntax": 1})
    text = "\n".join(reason_lines(report))
    assert "unsupported_syntax" in text
    assert "syntax whose evaluation Towel cannot yet preserve" in text


@pytest.mark.skipif(sys.version_info < (3, 14), reason="deferred annotations need Python 3.14")
def test_deferred_annotations_keep_evaluation_when_requested(tmp_path: Path) -> None:
    """Python audit: PEP 649/749 must neither evaluate nor copy effects into helpers."""
    source = "events = []\ndef mark(name):\n    events.append(name)\n    return int\n"
    for name in ("first", "second"):
        source += (
            f"def {name}(value: mark('{name}')) -> mark('{name}-return'):\n"
            "    result = value + 1\n    result *= 2\n    return result\n"
        )
    source += (
        "print('before', events)\nprint(first(2), second(3))\n"
        "print('called', events)\n"
        "print(first.__annotations__, second.__annotations__)\nprint('read', events)\n"
    )
    project = tmp_path / "project"
    project.mkdir()
    path = project / "m.py"
    path.write_text(source)
    before = _run(path)
    assert "before []" in before and "called []" in before
    assert "['first', 'first-return', 'second', 'second-return']" in before
    engine = UnificationRefactorEngine(min_lines=2, skip_trivial_helpers=False)
    engine.refactor_directory_to_fixed_point(
        str(project), str(tmp_path / "out"), max_iterations=1, progress="none"
    )
    assert _run(tmp_path / "out" / "m.py") == before


@pytest.mark.parametrize("format_spec", ["", ":>10"])
def test_template_optional_format_spec_keeps_resource_checks(format_spec: str) -> None:
    """Integration 3: Interpolation's optional format spec must not hide its value.

    On older Python the equivalent f-string exercises the shared child walk;
    the template spelling exercises Interpolation as soon as it is available.
    """
    prefix = "t" if sys.version_info >= (3, 14) else "f"
    module = ast.parse(f'def work():\n    return {prefix}"{{open(path){format_spec}}}"\n')
    function = module.body[0]
    assert isinstance(function, ast.FunctionDef)
    assert resource_guard(function.body, module).refusal is not None

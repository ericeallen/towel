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

"""An explicit header marker makes a function opaque without disabling its neighbors."""

from __future__ import annotations

import ast
import copy
import dataclasses
from pathlib import Path
import textwrap

import pytest

from towel.unification.exceptions import RefactoringError
from towel.unification.extraction_policy import (
    protected_definitions,
    require_protected_definitions_unchanged,
)
from towel.unification.models import ReusedFunction
from towel.unification.pipeline import analyze_scopes, collect_functions, parse_modules
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import refactor_to_fixed_point_silently

BODY = """    total = 0
    for item in values:
        total += item * 2
    total += 7
    return total
"""


def _function(name: str, marker: str = "") -> str:
    return f"def {name}(values):{marker}\n{BODY}\n"


def _reusable_function(name: str, marker: str = "") -> str:
    """The same arithmetic provider over a module value, without caller-owned parameters."""
    return f"def {name}():{marker}\n{BODY}\n"


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "example.py"
    path.write_text(textwrap.dedent(source))
    return path


def _protected(source: str) -> tuple[tuple[tuple[str, ...], str], ...]:
    return tuple((d.ancestry, d.text) for d in protected_definitions(source, ast.parse(source)))


@pytest.mark.parametrize(
    "source,expected",
    [
        ("def f(x):  # towel: no-extract\n    return x\n", True),
        ("async def f(x):  # towel: no-extract\n    return x\n", True),
        ("def f(\n    x,\n):  # towel: no-extract\n    return x\n", True),
        ("def f(x) -> int:  # towel: no-extract\n    return x\n", True),
        ("class C:\n    def f(self):  # towel: no-extract\n        return 1\n", True),
        ("# towel: no-extract\ndef f(x):\n    return x\n", False),
        ("@decorate  # towel: no-extract\ndef f(x):\n    return x\n", False),
        ("def f(  # towel: no-extract\n    x,\n):\n    return x\n", False),
        ("def f(\n    x: int,  # towel: no-extract\n):\n    return x\n", False),
        ("def f(): return 1  # towel: no-extract\n", False),
        ("def f(x):  # towel: no-extraction\n    return x\n", False),
        ("def f(x):  # towel: no-extract because reflection\n    return x\n", False),
        ('def f(x="# towel: no-extract"):\n    return x\n', False),
        ('def f(x) -> "# towel: no-extract":\n    return x\n', False),
        ('def f():\n    return "# towel: no-extract"\n', False),
        ('def f():\n    """def x():  # towel: no-extract"""\n    return 1\n', False),
        ("def f():\n    # towel: no-extract\n    return 1\n", False),
    ],
)
def test_only_an_exact_comment_on_the_final_header_colon_marks_a_function(
    source: str, expected: bool
) -> None:
    tree = ast.parse(source)
    before = ast.dump(tree, include_attributes=True)
    assert bool(protected_definitions(source, tree)) is expected
    assert ast.dump(tree, include_attributes=True) == before


def test_protected_text_includes_decorators_and_trailing_body_comments() -> None:
    source = """@decorate
def f():  # towel: no-extract
    return 1

    # retained body comment
# outside comment

def other():
    return 2
"""
    (definition,) = protected_definitions(source, ast.parse(source))
    assert definition.line_range == (1, 5)
    assert definition.text == source[: source.index("# outside")]
    require_protected_definitions_unchanged(source, "# shifted\n" + source)
    for after in (
        source.replace("retained body comment", "changed body comment"),
        source.replace("# towel: no-extract", "# marker removed"),
        source.replace("return 1", "return 3"),
        source.replace("@decorate", "@other_decorator"),
    ):
        with pytest.raises(RefactoringError, match="no-extract"):
            require_protected_definitions_unchanged(source, after)


def test_tab_indented_trailing_comments_are_protected() -> None:
    source = (
        "class C:\n\tdef f(self):  # towel: no-extract\n\t\treturn 1\n\t\t# retained\n\t# outside\n"
    )
    (definition,) = protected_definitions(source, ast.parse(source))
    assert definition.line_range == (2, 4)
    assert definition.text.endswith("\t\t# retained\n")


@pytest.mark.parametrize(
    "decorator",
    [
        "@(\n    decorate\n)\n",
        "@(\n    # @ is only a comment here\n    decorate\n)\n",
        "@\\\n    decorate\n",
        "@(\n    decorate @ decorate\n)\n",
    ],
)
def test_protected_decorators_start_at_the_lexical_opener(decorator: str) -> None:
    source = decorator + _function("protected", "  # towel: no-extract")
    tree = ast.parse(source)
    (definition,) = protected_definitions(source, tree)
    assert definition.line_range[0] == 1
    assert definition.text == source.rstrip("\n") + "\n"
    rewritten = source.replace("@", "@  ", 1)
    assert ast.dump(ast.parse(rewritten), include_attributes=False) == ast.dump(
        tree, include_attributes=False
    )
    with pytest.raises(RefactoringError, match="no-extract"):
        require_protected_definitions_unchanged(source, rewritten)


def test_a_parenthesized_decorator_survives_neighbors_and_rejects_finisher_changes(
    tmp_path: Path,
) -> None:
    source = (
        "def decorate(function):\n    return function\n\n@(\n    decorate\n)\n"
        + _function("protected", "  # towel: no-extract")
        + _function("alpha")
        + _function("beta")
    )
    path = _write(tmp_path, source)
    engine = UnificationRefactorEngine(min_lines=3)
    proposal = engine.analyze_file(str(path))[0]
    original = copy.deepcopy(proposal)
    output = engine.apply_refactoring_multi_file(proposal)[str(path)]
    assert _protected(output) == _protected(source)
    assert any(
        isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func_")
        for node in ast.parse(output).body
    )
    for node in ast.parse(output).body:
        if isinstance(node, ast.FunctionDef) and node.name in {"alpha", "beta"}:
            assert any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id.startswith("__extracted_func_")
                for call in ast.walk(node)
            )
    engine.file_finisher = lambda _path, text: text.replace("@(\n", "@  (\n", 1)
    with pytest.raises(RefactoringError, match="no-extract"):
        engine.apply_refactoring_multi_file(proposal)
    assert _protected(output)[0][1].startswith("@(\n")
    assert path.read_text() == source
    assert ast.dump(proposal.extracted_function, include_attributes=True) == ast.dump(
        original.extracted_function, include_attributes=True
    )


def test_pairing_and_clustering_keep_marked_code_but_extract_all_unmarked_twins(
    tmp_path: Path,
) -> None:
    source = _function("protected", "  # towel: no-extract") + "".join(
        _function(name) for name in ("alpha", "beta", "gamma")
    )
    path = _write(tmp_path, source)
    engine = UnificationRefactorEngine(min_lines=3)
    functions = collect_functions(analyze_scopes(parse_modules([str(path)])))
    assert [f.node.name for f in functions] == ["protected", "alpha", "beta", "gamma"]
    pairs = engine.find_block_pairs(functions, progress="none")
    assert pairs
    assert all(
        p.function1_node.name != "protected" and p.function2_node.name != "protected" for p in pairs
    )
    final, applied = refactor_to_fixed_point_silently(str(path), min_lines=3)
    assert applied > 0
    assert _protected(final) == _protected(source)
    for node in ast.parse(final).body:
        if isinstance(node, ast.FunctionDef) and node.name in {"alpha", "beta", "gamma"}:
            assert any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id.startswith("__extracted_func_")
                for call in ast.walk(node)
            )


def test_marked_outer_keeps_nested_functions_and_methods_opaque(tmp_path: Path) -> None:
    nested = """def outer():  # towel: no-extract
    def inner_one(values):
        total = 0
        for item in values:
            total += item * 2
        total += 7
        return total
    class Nested:
        def inner_two(self, values):
            total = 0
            for item in values:
                total += item * 2
            total += 7
            return total
    return inner_one, Nested

"""
    source = nested + _function("alpha") + _function("beta")
    path = _write(tmp_path, source)
    final, applied = refactor_to_fixed_point_silently(str(path), min_lines=3)
    assert applied > 0
    assert _protected(final) == _protected(source)
    assert nested.rstrip() in final


def test_unmarked_ancestor_cannot_move_a_compound_statement_holding_a_marked_def(
    tmp_path: Path,
) -> None:
    source = """def first(flag):
    if flag:
        def sensitive():  # towel: no-extract
            return 1
        value = sensitive()
        value += 2
        return value
    return 0

def second(flag):
    if flag:
        def sensitive():
            return 1
        value = sensitive()
        value += 2
        return value
    return 0
"""
    path = _write(tmp_path, source)
    engine = UnificationRefactorEngine(min_lines=3)
    proposals = engine.analyze_file(str(path))
    protected = _protected(source)
    for proposal in proposals:
        assert _protected(engine.apply_refactoring(str(path), proposal)) == protected
    functions = collect_functions(analyze_scopes(parse_modules([str(path)])))
    engine.find_block_pairs(functions, progress="none")
    first = next(f.node for f in functions if f.node.name == "first")
    assert all(not (span[0] <= 3 <= span[1]) for span, _ in engine._extract_code_blocks(first))


def test_marked_async_multiline_method_preserves_its_body_with_method_extraction(
    tmp_path: Path,
) -> None:
    source = """class C:
    @staticmethod
    async def protected(
        values,
    ):  # towel: no-extract
        total=0
        for item in values:
            total+=item*2
        total+=7
        return total

""" + textwrap.indent(_function("alpha") + _function("beta"), "    ")
    path = _write(tmp_path, source)
    final, applied = refactor_to_fixed_point_silently(str(path), min_lines=3)
    assert applied > 0
    assert _protected(final) == _protected(source)


def test_cross_module_extraction_survives_a_protected_neighbor(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'pkg'\nversion = '0'\n")
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    alpha = package / "alpha.py"
    beta = package / "beta.py"
    source = _function("protected", "  # towel: no-extract") + _function("alpha")
    alpha.write_text(source)
    beta.write_text("import pkg.alpha\n\n" + _function("beta"))
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    proposals = engine.analyze_files([str(alpha), str(beta)], progress="none")
    assert proposals
    proposal = next(p for p in proposals if len({r.file_path for r in p.replacements}) > 1)
    outputs = engine.apply_refactoring_multi_file(proposal)
    assert set(outputs) == {str(alpha), str(beta)}
    assert _protected(outputs[str(alpha)]) == _protected(source)
    assert any(
        isinstance(node, ast.ImportFrom)
        for text in outputs.values()
        for node in ast.parse(text).body
    )


@pytest.mark.parametrize("bypass", ["insertion", "reuse", "trailing_comment", "finisher"])
def test_materialization_rejects_manual_bypasses_without_writing_or_mutating(
    tmp_path: Path, bypass: str
) -> None:
    function = _reusable_function if bypass == "reuse" else _function
    source = (
        function("__extracted_func_0", "  # towel: no-extract").rstrip()
        + "\n    # retained trailing comment\n\n"
        + function("alpha")
        + function("beta")
    )
    if bypass == "reuse":
        source = "values = [1, 3]\n\n" + source
    path = _write(tmp_path, source)
    engine = UnificationRefactorEngine(min_lines=3)
    proposal = engine.analyze_file(str(path))[0]
    marked = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "__extracted_func_0"
    )
    if bypass == "insertion":
        proposal.insert_into_function = marked.name
    elif bypass == "reuse":
        proposal.reused_function = ReusedFunction(
            marked.name, str(path), (marked.lineno, marked.end_lineno or marked.lineno)
        )
    elif bypass == "trailing_comment":
        (definition,) = protected_definitions(source, ast.parse(source))
        proposal.replacements[0].line_range = (definition.line_range[1], definition.line_range[1])
    else:
        engine.file_finisher = lambda _path, text: text.replace(
            "retained trailing comment", "rewritten"
        )
    original = copy.deepcopy(proposal)
    with pytest.raises(RefactoringError, match="no-extract"):
        engine.apply_refactoring_multi_file(proposal)
    assert path.read_text() == source
    assert ast.dump(proposal.extracted_function, include_attributes=True) == ast.dump(
        original.extracted_function, include_attributes=True
    )
    for replacement, saved in zip(proposal.replacements, original.replacements, strict=True):
        assert ast.dump(replacement.node, include_attributes=True) == ast.dump(
            saved.node, include_attributes=True
        )
        assert dataclasses.replace(replacement, node=saved.node) == saved


def test_adding_or_removing_a_marker_invalidates_discovery_even_with_identical_ast(
    tmp_path: Path,
) -> None:
    source = _function("alpha") + _function("beta")
    path = _write(tmp_path, source)
    engine = UnificationRefactorEngine(min_lines=3)
    assert engine.analyze_file(str(path))
    marked = source.replace("def alpha(values):", "def alpha(values):  # towel: no-extract")
    assert ast.dump(ast.parse(source)) == ast.dump(ast.parse(marked))
    path.write_text(marked)
    assert not engine.analyze_file(str(path))
    path.write_text(source)
    assert engine.analyze_file(str(path))


@pytest.mark.parametrize("separator", ["\f", "\x1c", "\x85", "\u2028", "\u2029"])
def test_literal_non_lf_characters_do_not_shift_exclusion_spans(
    tmp_path: Path, separator: str
) -> None:
    prefix = f"banner = {repr(separator)}\n".replace(repr(separator), '"' + separator + '"')
    source = (
        prefix
        + _function("protected", "  # towel: no-extract")
        + _function("alpha")
        + _function("beta")
    )
    (definition,) = protected_definitions(source, ast.parse(source))
    assert definition.line_range == (2, 7)
    assert definition.text == _function("protected", "  # towel: no-extract").rstrip() + "\n"
    path = _write(tmp_path, source)
    final, applied = refactor_to_fixed_point_silently(str(path), min_lines=3)
    assert applied > 0
    assert _protected(final) == _protected(source)


def test_lower_body_indent_comment_is_outside_the_preserved_definition() -> None:
    source = "def f():  # towel: no-extract\n    return 1\n  # outside body indentation\n"
    (definition,) = protected_definitions(source, ast.parse(source))
    assert definition.line_range == (1, 2)


def test_form_feed_indentation_resets_before_trailing_body_comments() -> None:
    source = "def f():  # towel: no-extract\n\f    return 1\n\f    # retained\n# outside\n"
    (definition,) = protected_definitions(source, ast.parse(source))
    assert definition.line_range == (1, 3)
    assert definition.text.endswith("\f    # retained\n")


def test_introduced_generated_helper_can_be_reused_only_until_explicitly_protected(
    tmp_path: Path,
) -> None:
    """A marker removes a real provider without disabling the useful caller extraction."""
    origin, stage = tmp_path / "original", tmp_path / "stage"
    for directory in (origin, stage):
        directory.mkdir()
        (directory / "pyproject.toml").write_text("[project]\nname = 'probe'\nversion = '0'\n")
    callers = _reusable_function("alpha") + _reusable_function("beta")
    (origin / "module.py").write_text("values = [1, 3]\n\n" + callers)
    path = stage / "module.py"
    path.write_text("values = [1, 3]\n\n" + _reusable_function("__extracted_func_0") + callers)

    control = UnificationRefactorEngine(min_lines=3, annotate_helpers=False)
    control._output_origin = (origin, stage)
    control.import_graph.begin_run(origin, stage)
    proposals = control.analyze_file(str(path))
    reused = next(
        proposal
        for proposal in proposals
        if proposal.reused_function is not None
        and proposal.reused_function.name == "__extracted_func_0"
    )
    unmarked_output = control.apply_refactoring(str(path), reused)
    assert (
        len([node for node in ast.parse(unmarked_output).body if isinstance(node, ast.FunctionDef)])
        == 3
    )

    unmarked_namespace: dict[str, object] = {}
    exec(unmarked_output, unmarked_namespace)
    unmarked_namespace["__extracted_func_0"] = lambda: "patched"
    for name in ("alpha", "beta"):
        caller = unmarked_namespace[name]
        assert callable(caller)
        assert caller() == "patched"

    source = (
        "values = [1, 3]\n\n"
        + _reusable_function("__extracted_func_0", "  # towel: no-extract")
        + callers
    )
    path.write_text(source)
    protected = UnificationRefactorEngine(min_lines=3, annotate_helpers=False)
    protected._output_origin = (origin, stage)
    protected.import_graph.begin_run(origin, stage)
    proposals = protected.analyze_file(str(path))
    assert proposals and all(proposal.reused_function is None for proposal in proposals)
    marked_output = protected.apply_refactoring(str(path), proposals[0])
    assert _protected(marked_output) == _protected(source)
    assert (
        len([node for node in ast.parse(marked_output).body if isinstance(node, ast.FunctionDef)])
        == 4
    )
    namespace: dict[str, object] = {}
    exec(marked_output, namespace)
    namespace["__extracted_func_0"] = lambda: "patched"
    for name in ("alpha", "beta"):
        caller = namespace[name]
        assert callable(caller)
        assert caller() == 15
    assert path.read_text() == source

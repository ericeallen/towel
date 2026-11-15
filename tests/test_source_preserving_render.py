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

"""Audit shape items 2 and 4: preserve the user's spelling and indentation."""

import ast
from pathlib import Path

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine


def render(tmp_path: Path, source: str, *, strict: bool = False) -> str:
    path = tmp_path / "m.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(min_lines=2, skip_trivial_helpers=False, strict=strict)
    proposals = engine.analyze_file(str(path))
    assert proposals
    return engine.apply_refactoring(str(path), proposals[0])


@pytest.mark.parametrize("strict", [False, True])
def test_unchanged_and_parameterized_statements_keep_their_source(
    tmp_path: Path, strict: bool
) -> None:
    """Audit shape 2: moving statements must not respell literals or parentheses."""
    source = """def first(x):
    amount = 12.50  # deliberately precise spelling

    text = "abc" "def"
    result = (amount + x)
    return text, result * 2

def second(y):
    amount = 12.50  # deliberately precise spelling

    text = "abc" "def"
    result = (amount + y)
    return text, result * 3
"""
    result = render(tmp_path, source, strict=strict)
    assert "amount = 12.50  # deliberately precise spelling\n\n" in result
    assert 'text = "abc" "def"' in result
    assert "result = (amount +" in result
    assert result.count("deliberately precise spelling") == 1


@pytest.mark.parametrize("strict", [False, True])
def test_call_arguments_keep_multiline_literal_text_and_values(
    tmp_path: Path, strict: bool
) -> None:
    """Audit shape 2 / petl C1: multiline fixtures remain multiline and unchanged."""
    source = '''def first():
    text = """first
    second
last"""
    text = text.strip()
    text = text.upper()
    return text + "!"

def second():
    text = """other
    sample
end"""
    text = text.strip()
    text = text.upper()
    return text + "?"
'''
    result = render(tmp_path, source, strict=strict)
    assert '"""first\n    second\nlast"""' in result
    assert '"""other\n    sample\nend"""' in result
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    exec(source, before)
    exec(result, after)
    for name in ("first", "second"):
        original, changed = before[name], after[name]
        assert callable(original) and callable(changed)
        assert original() == changed()


def test_unicode_arguments_and_stale_byte_locations_fall_back_safely() -> None:
    """Audit shape 2: inherited locations cannot split UTF-8 or select another value."""
    from towel.unification.source_render import preserve_call_source

    source = 'café = target("héllo" "世界", 12.50)'
    call = ast.parse(source).body[0]
    rendered = preserve_call_source(call, source)
    assert '"héllo" "世界"' in rendered
    assert "12.50" in rendered
    assert ast.dump(ast.parse(rendered)) == ast.dump(ast.parse(source))
    argument = ast.Name(
        id="value", ctx=ast.Load(), lineno=1, col_offset=1, end_lineno=1, end_col_offset=2
    )
    generated = ast.Expr(
        value=ast.Call(func=ast.Name(id="helper", ctx=ast.Load()), args=[argument], keywords=[])
    )
    assert preserve_call_source(generated, "é = 1") == "helper(value)"
    argument.col_offset, argument.end_col_offset = 0, 2
    assert preserve_call_source(generated, "é = 1") == "helper(value)"


def test_arguments_keep_precedence_and_internal_comments() -> None:
    """Audit shape 2: restoring argument text preserves grouping and comments."""
    from towel.unification.source_render import preserve_call_source

    source = 'f((1 +  # two\n   2\n), ("a" "b", 12.50))'
    generated = ast.parse(source)
    statement = generated.body[0]
    assert isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
    statement.value.func = ast.Name(id="helper", ctx=ast.Load())
    result = preserve_call_source(generated, source)
    assert '"a" "b"' in result and "12.50" in result and "# two" in result
    assert ast.dump(ast.parse(result)) == ast.dump(generated)


def test_reindent_preserves_multiline_fstrings_and_rejects_changed_trees() -> None:
    """Audit shape 2/4: the final indentation step also verifies the proved AST."""
    from towel.unification.source_render import indent_rendered

    source = (
        'def helper(value):\n    text = f"""first {value}\n    content\nlast"""\n    return text'
    )
    for unit in ("\t", "  ", "        "):
        result = indent_rendered(source, "", unit)
        assert 'f"""first {value}\n    content\nlast"""' in result
        assert ast.dump(ast.parse(result)) == ast.dump(ast.parse(source))
        nested = indent_rendered(source, unit, unit)
        outer = ast.parse("if True:\n" + nested).body[0]
        assert isinstance(outer, ast.If)
        assert ast.dump(ast.Module(body=outer.body, type_ignores=[])) == ast.dump(ast.parse(source))
    # A form-feed indentation unit resets the lexer column; do not publish its changed tree.
    assert indent_rendered("if True:\n    value = 1", "", "\f") == "if True:\n    value = 1"

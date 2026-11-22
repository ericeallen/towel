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

"""Small source-placement reproductions for polish item 6."""

import ast
from pathlib import Path

import pytest

from tests.test_helpers import lifetime_order_certified
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.mark.parametrize(
    "definition", ["class Error(Exception):\n    pass\n", "@decorate\ndef work():\n    pass\n"]
)
@pytest.mark.parametrize(
    "prefix",
    ["", "# Copyright example\n\n", "import sys\n\n", "#!/usr/bin/python\n# coding: utf-8\n"],
)
def test_helper_does_not_split_definition_comments(prefix: str, definition: str) -> None:
    """Polish 6: a comment block remains attached to its class or decorated function."""
    comment = "# Exceptions\n# Raised by this module.\n"
    source = prefix + comment + definition
    lines = source.splitlines(keepends=True)
    engine = UnificationRefactorEngine()
    position = engine._find_insert_position(lines)
    lines[position:position] = ["def helper():\n", "    pass\n", "\n", "\n"]
    rendered = "".join(lines)
    assert comment + definition in rendered
    assert rendered.startswith(prefix)
    ast.parse(rendered)


def test_comment_stays_with_class_after_real_extraction(tmp_path: Path) -> None:
    """Polish 6: the public engine keeps a class section header after materialization."""
    source = "import math\n\n# Exceptions\nclass Error(Exception):\n    pass\n\n" + "\n".join(
        f"def {name}(x):\n    a = x + {number}\n    b = a * 2\n    c = b - 3\n    d = c / 4\n    return d\n"
        for name, number in (("first", 1), ("second", 2))
    )
    path = tmp_path / "example.py"
    path.write_text(source)
    # Isolate placement from the strict helper's larger profitability cost.
    engine = UnificationRefactorEngine(annotate_helpers=False, skip_trivial_helpers=False)
    proposals = engine.analyze_file(str(path))
    if not lifetime_order_certified():
        assert proposals == []
        assert engine.declined_pairs.get("lifetime_order_uncertified", 0) > 0
        assert path.read_text() == source
        return
    assert proposals
    rendered = engine.apply_refactoring(str(path), proposals[0])
    assert "# Exceptions\nclass Error(Exception):" in rendered


def test_class_comment_stays_attached_after_expression_extraction(tmp_path: Path) -> None:
    """Polish 4: a real helper is inserted before the class comment even on 3.14+."""
    from tests.test_expression_extraction import CONDITION

    comment = "# Exceptions\nclass Error(Exception):"
    source = (
        f"import sys\n\n{comment}\n    pass\n\n"
        f"def can_post(member, now):\n    if {CONDITION}:\n        return True\n    return False\n\n"
        f"def visible(members, now):\n    return [member for member in members if {CONDITION}]\n"
    )
    path = tmp_path / "example.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(min_lines=3, annotate_helpers=False)
    engine.refactor_to_fixed_point(str(path))
    rendered = path.read_text()
    helper, error, first, second = ast.parse(rendered).body[1:]
    assert isinstance(helper, ast.FunctionDef) and helper.name.startswith("__extracted_func")
    assert isinstance(error, ast.ClassDef) and error.name == "Error"
    for caller in (first, second):
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == helper.name
            for node in ast.walk(caller)
        )
    assert comment in rendered

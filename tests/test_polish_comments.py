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

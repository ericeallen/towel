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

"""A receiver used as a generalized operand is still an explicit argument."""

import ast
from pathlib import Path
from typing import Any

from towel.unification.refactor_engine import UnificationRefactorEngine


def test_receiver_operand_is_not_dropped(tmp_path: Path) -> None:
    source = """class Box:
    def reset(self):
        self.first = []
        self.second = []
        self.third = []

    def clone(self):
        other = Box()
        other.first = []
        other.second = []
        other.third = []
        return other
"""
    path = tmp_path / "box.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(min_lines=2)
    proposals = engine.analyze_file(str(path))
    assert [p.description for p in proposals] == ["Extract common code from reset and clone"]
    for proposal in proposals:
        result = engine.apply_refactoring(str(path), proposal)
        scope: dict[str, Any] = {}
        exec(compile(result, str(path), "exec"), scope)
        box = scope["Box"]()
        box.reset()
        clone = box.clone()
        assert box.first == clone.first == []
        assert box.first is not clone.first
        assert box.second == clone.second == []
        assert box.third == clone.third == []
        ast.parse(result)

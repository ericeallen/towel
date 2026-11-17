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

"""Small reproductions of polish item 3, naming methods and their parameters."""

import ast
from pathlib import Path

from tests.test_cli_integration import invoke
from towel.cli import _find_extracted_helpers, _generate_llm_prompt, helper_inventory

SOURCE = """class Box:
    def _extracted_func_0(self, _param_0):
        if _param_0:
            return self.width * _param_0
        return 0

    def area(self):
        return self._extracted_func_0(2)
"""


def test_method_source_keeps_relative_indentation(tmp_path: Path) -> None:
    """Polish 3: --list and inventory must show a method with normal body indentation."""
    (tmp_path / "box.py").write_text(SOURCE)
    helpers = _find_extracted_helpers(tmp_path, None, None)
    expected = SOURCE.split("\n\n", 1)[0].split("\n", 1)[1]
    expected = "\n".join(line[4:] for line in expected.splitlines())
    assert helpers[0][3] == expected
    inventory = helper_inventory(tmp_path, helpers)
    assert inventory["helpers"][0]["source"] == expected
    ast.parse(expected)
    listing = invoke(["rename-helpers", str(tmp_path), "--list"])
    assert listing.status == 0
    assert "      def _extracted_func_0(self, _param_0):\n          if _param_0:" in listing.stdout


def test_prompt_includes_method_parameter_keys(tmp_path: Path) -> None:
    """Polish 3: the prompt must include generated single-underscore parameters too."""
    (tmp_path / "box.py").write_text(SOURCE)
    helpers = _find_extracted_helpers(tmp_path, None, None)
    prompt = _generate_llm_prompt(tmp_path, helpers, "claude")
    assert "- 'box.py:_extracted_func_0._param_0'; passed 2" in prompt

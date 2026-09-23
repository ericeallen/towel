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

"""Which helper names a project's other files already claim.

A file outside the refactored package can take a helper's place by giving a
subclass a member of that name, or by assigning the attribute. The program-level
evidence is ``xf16_consumer_outside_target_owns_helper_name`` in
``tests/hostile_crossfile``; these tests pin down what counts as a claim, so
that a name merely mentioned (a test asserting on it, a call) leaves the
numbering alone.
"""

from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from towel.unification.exceptions import ProjectScanLimitError
from towel.unification import materialize
from towel.unification.materialize import _helper_shaped_identifiers


def _claims(tmp_path: Path, source: str) -> frozenset[str]:
    (tmp_path / "consumer.py").write_text(textwrap.dedent(source))
    return _helper_shaped_identifiers(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "class Child(Base):\n    def _extracted_func_0(self):\n        return 1\n",
        "class Child(Base):\n    _extracted_func_0 = staticmethod(len)\n",
        "class Child(Base):\n    if True:\n        _extracted_func_0 = None\n",
        "import lib\nlib._extracted_func_0 = print\n",
        "def patch(obj):\n    obj._extracted_func_0 = None\n",
        "setattr(Base, '_extracted_func_0', None)\n",
        "Child = type('Child', (Base,), {'_extracted_func_0': None})\n",
        "namespace = {}\nnamespace['_extracted_func_0'] = None\n",
        # A file that does not parse cannot be read for what it defines.
        "class Child(Base:\n    def _extracted_func_0(self): ...\n",
    ],
)
def test_a_member_or_attribute_of_the_name_claims_it(tmp_path: Path, source: str) -> None:
    assert _claims(tmp_path, source) == {"_extracted_func_0"}


@pytest.mark.parametrize(
    "source",
    [
        "result = obj._extracted_func_0(3)\n",
        "assert helper.name == '_extracted_func_0'\n",
        "def _extracted_func_0():\n    return 1\n",  # a module's own function is its own
        "# _extracted_func_0 is mentioned only in a comment\n",
    ],
)
def test_a_mention_claims_nothing(tmp_path: Path, source: str) -> None:
    assert _claims(tmp_path, source) == frozenset()


def test_tool_directories_are_not_read(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("class C:\n    def _extracted_func_4(self): ...\n")
    assert _helper_shaped_identifiers(tmp_path) == frozenset()


def test_a_tree_too_large_to_read_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(materialize, "MAXIMUM_FILES", 2)
    for index in range(3):
        (tmp_path / f"m{index}.py").write_text("")
    with pytest.raises(ProjectScanLimitError, match="pyproject.toml"):
        _helper_shaped_identifiers(tmp_path)

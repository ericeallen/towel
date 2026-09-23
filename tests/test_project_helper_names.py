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

"""Which helper names a project's other files already claim, and where.

A file outside the refactored package can take a helper's place. A
module-level helper is an attribute of its module, so an attribute store, a
``setattr`` or a namespace write of its name replaces it. A method helper is
class-private, stored as ``_A__extracted_func_0``, so only a member stored
under that name reaches it; a subclass's ``_extracted_func_0``, which the
reservation was first written against
(``xf16_consumer_outside_target_owns_helper_name`` in
``tests/hostile_crossfile``), can no longer. These tests pin down what counts
as a claim of each kind, so that a name merely mentioned (a test asserting on
it, a call) leaves the numbering alone.
"""

from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from towel.unification.engine_state import HelperNameClaims
from towel.unification.exceptions import ProjectScanLimitError
from towel.unification import materialize
from towel.unification.materialize import _claimed_helper_names


def _claims(tmp_path: Path, source: str) -> HelperNameClaims:
    (tmp_path / "consumer.py").write_text(textwrap.dedent(source))
    return _claimed_helper_names(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "import lib\nlib._extracted_func_0 = print\n",
        "def patch(obj):\n    obj._extracted_func_0 = None\n",
        "setattr(Base, '_extracted_func_0', None)\n",
        "namespace = {}\nnamespace['_extracted_func_0'] = None\n",
    ],
)
def test_a_namespace_write_of_the_name_claims_it_everywhere(tmp_path: Path, source: str) -> None:
    claims = _claims(tmp_path, source)
    assert claims.namespace == {"_extracted_func_0"}
    assert claims.members == {"_extracted_func_0"}


@pytest.mark.parametrize(
    "source",
    [
        "class Child(Base):\n    def _extracted_func_0(self):\n        return 1\n",
        "class Child(Base):\n    _extracted_func_0 = staticmethod(len)\n",
        "class Child(Base):\n    if True:\n        _extracted_func_0 = None\n",
        "Child = type('Child', (Base,), {'_extracted_func_0': None})\n",
    ],
)
def test_a_class_member_claims_the_name_only_for_method_helpers(
    tmp_path: Path, source: str
) -> None:
    """A member of a class cannot replace a module-level helper, which is its module's."""
    assert _claims(tmp_path, source) == HelperNameClaims(
        frozenset(), frozenset({"_extracted_func_0"})
    )


@pytest.mark.parametrize(
    "source, stored",
    [
        (
            "class Box(Base):\n    def __extracted_func_0(self):\n        return 1\n",
            "_Box__extracted_func_0",
        ),
        ("class _Box:\n    __extracted_func_0 = None\n", "_Box__extracted_func_0"),
        ("class __:\n    def __extracted_func_0(self):\n        return 1\n", "__extracted_func_0"),
        (
            "class Outer:\n    class Inner:\n        def __extracted_func_0(self):\n            return 1\n",
            "_Inner__extracted_func_0",
        ),
        (
            "class Other:\n    def _Box__extracted_func_0(self):\n        return 1\n",
            "_Box__extracted_func_0",
        ),
    ],
    ids=["mangled", "leading-underscores", "only-underscores", "nested", "spelled-mangled"],
)
def test_a_private_member_claims_the_name_its_class_stores(
    tmp_path: Path, source: str, stored: str
) -> None:
    assert _claims(tmp_path, source).members == {stored}


def test_a_private_attribute_stored_in_a_method_claims_the_mangled_name(tmp_path: Path) -> None:
    source = "class Box:\n    def reset(self):\n        self.__extracted_func_0 = None\n"
    claims = _claims(tmp_path, source)
    assert claims.namespace == claims.members == {"_Box__extracted_func_0"}


def test_a_file_that_does_not_parse_claims_every_word_in_every_class(tmp_path: Path) -> None:
    claims = _claims(tmp_path, "class Box(Base:\n    def __extracted_func_0(self): ...\n")
    assert claims.namespace == {"__extracted_func_0"}
    assert claims.members == {"__extracted_func_0", "_Box__extracted_func_0"}


@pytest.mark.parametrize(
    "source",
    [
        "result = obj._extracted_func_0(3)\n",
        "assert helper.name == '_extracted_func_0'\n",
        "def _extracted_func_0():\n    return 1\n",  # a module's own function is its own
        "# _extracted_func_0 is mentioned only in a comment\n",
        "class Box:\n    def run(self):\n        return self.__extracted_func_0()\n",
    ],
)
def test_a_mention_claims_nothing(tmp_path: Path, source: str) -> None:
    assert _claims(tmp_path, source) == HelperNameClaims()


def test_tool_directories_are_not_read(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("class C:\n    def _extracted_func_4(self): ...\n")
    assert _claimed_helper_names(tmp_path) == HelperNameClaims()


def test_a_tree_too_large_to_read_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(materialize, "MAXIMUM_FILES", 2)
    for index in range(3):
        (tmp_path / f"m{index}.py").write_text("")
    with pytest.raises(ProjectScanLimitError, match="pyproject.toml"):
        _claimed_helper_names(tmp_path)

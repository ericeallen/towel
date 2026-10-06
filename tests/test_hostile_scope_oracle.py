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

"""Scope exemptions require a complete ownership transfer, never a name prefix."""

from __future__ import annotations

import pytest

from tests.hostile_execution import scope_changes

BEFORE = """def f(a,b):
    local=make()
    return consume(a,b,local)
"""
AFTER = """def f(a,b):
    _towel_arguments = [(b,a)]
    del a
    del b
    return __extracted_func(_towel_arguments[0][1], _towel_arguments[0][0], _towel_arguments.pop())
"""


def test_complete_hygienic_box_is_accounted_for_by_the_scope_oracle() -> None:
    assert scope_changes(BEFORE, AFTER) == {}
    factory = AFTER.replace(
        "_towel_arguments[0][1],", "(lambda cap: lambda: cap.name)(_towel_arguments[0][1]),", 1
    )
    assert scope_changes(BEFORE, factory) == {}


@pytest.mark.parametrize(
    "after",
    [
        AFTER.replace("[(b,a)]", "[(a,b)]"),
        AFTER.replace("[(b,a)]", "[(a,)]"),
        AFTER.replace("    del b\n", ""),
        AFTER.replace("del a", "del b"),
        AFTER.replace("_towel_arguments.pop()", "None"),
        AFTER.replace("_towel_arguments[0][1]", "_towel_arguments"),
        AFTER.replace("_towel_arguments[0][1]", "a"),
        AFTER.replace("_towel_arguments[0][1]", "lambda: _towel_arguments[0][1]"),
        AFTER.replace("    del b", "    del b\n    extra = 1"),
    ],
)
def test_incomplete_or_escaping_transfer_does_not_hide_a_gained_local(after: str) -> None:
    assert "gains local _towel_arguments" in scope_changes(BEFORE, after)["f#0"]


def test_box_spelling_cannot_shadow_an_original_import_binder() -> None:
    before = "import math as _towel_arguments\n" + BEFORE
    assert "gains local _towel_arguments" in scope_changes(before, AFTER)["f#0"]


def test_prefix_alone_never_exempts_a_new_local() -> None:
    after = BEFORE.replace("    local=make()", "    _towel_arguments = []\n    local=make()")
    assert "gains local _towel_arguments" in scope_changes(BEFORE, after)["f#0"]


RETAINED_BEFORE = """def f():
    owner = make()
    result = observe(owner)
    return result
"""
RETAINED_AFTER = """def __extracted_func_0():
    owner = make()
    result = observe(owner)
    return owner, result
def f():
    _towel_keep_owner, result = __extracted_func_0()
    return result
"""


def test_unread_hygienic_slot_retains_an_original_local() -> None:
    assert scope_changes(RETAINED_BEFORE, RETAINED_AFTER) == {}
    before = "def f():\n    owner=make()\n    consume(owner)\n"
    after = (
        "def __extracted_func_0():\n    owner=make()\n    consume(owner)\n    return owner\n"
        "def f():\n    _towel_keep_owner=__extracted_func_0()\n"
    )
    assert scope_changes(before, after) == {}


@pytest.mark.parametrize(
    "after",
    [
        RETAINED_AFTER.replace("return owner, result", "return result, owner"),
        RETAINED_AFTER.replace("return owner, result", "return None, result"),
        RETAINED_AFTER.replace("    return result\n", "    return _towel_keep_owner\n"),
        RETAINED_AFTER.replace(
            "    return result\n", "    _towel_keep_owner = None\n    return result\n"
        ),
        RETAINED_AFTER.replace(
            "    return result\n", "    import math as _towel_keep_owner\n    return result\n"
        ),
        RETAINED_AFTER.replace("    return result\n", "    del owner\n    return result\n"),
    ],
)
def test_unproved_retained_slot_does_not_hide_a_gained_local(after: str) -> None:
    assert "gains local _towel_keep_owner" in scope_changes(RETAINED_BEFORE, after)["f#0"]


def test_retained_slot_cannot_shadow_an_original_module_binding() -> None:
    before = "import math as _towel_keep_owner\n" + RETAINED_BEFORE
    assert "gains local _towel_keep_owner" in scope_changes(before, RETAINED_AFTER)["f#0"]

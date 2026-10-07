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

from pathlib import Path

import pytest

from tests.hostile_execution import ScopeWatch, observe, scope_changes

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


ALPHA_AFTER = (
    RETAINED_AFTER.replace("owner = make()", "owner_1 = make()")
    .replace("result = observe(owner)", "result_1 = observe(owner_1)")
    .replace("return owner, result", "return owner_1, result_1")
)


def test_alpha_renamed_producer_preserves_scope_value_and_owner_cleanup(tmp_path: Path) -> None:
    assert scope_changes(RETAINED_BEFORE, ALPHA_AFTER) == {}
    prefix = """LOG=[]
class Owner:
    def __init__(self): LOG.append('make')
    def __del__(self): LOG.append('drop')
def make(): return Owner()
def observe(owner):
    LOG.append('observe')
    return 7
"""
    outputs = []
    for name, body in (("before", RETAINED_BEFORE), ("after", ALPHA_AFTER)):
        script = tmp_path / (name + ".py")
        script.write_text(prefix + body + "print(f(), LOG)\n")
        outputs.append(observe(str(script), tmp_path))
    assert outputs[0] == outputs[1] == (0, "7 ['make', 'observe', 'drop']\n", [])


@pytest.mark.parametrize("input_local", [False, True], ids=["parameter", "local"])
def test_readonly_caller_name_inputs_keep_their_binding(input_local: bool) -> None:
    before = RETAINED_BEFORE.replace("def f():", "def f(a):").replace("make()", "make(a)")
    after = ALPHA_AFTER.replace("def __extracted_func_0():", "def __extracted_func_0(supplied):")
    after = after.replace("make()", "make(supplied)").replace("def f():", "def f(a):")
    after = after.replace("result = __extracted_func_0()", "result = __extracted_func_0(a)")
    if input_local:
        before = before.replace("def f(a):", "def f():\n    a = 7")
        after = after.replace("def f(a):", "def f():\n    a = 7")
    assert scope_changes(before, after) == {}


@pytest.mark.parametrize(
    "after",
    [
        ALPHA_AFTER.replace("make()", "other_make()"),
        ALPHA_AFTER.replace("return owner_1, result_1", "return result_1, owner_1"),
        ALPHA_AFTER.replace("return owner_1, result_1", "return None, result_1"),
        ALPHA_AFTER.replace("return owner_1, result_1", "return owner_1, 9"),
        ALPHA_AFTER.replace("def __extracted_func_0():", "@redirect\ndef __extracted_func_0():"),
        ALPHA_AFTER + "__extracted_func_0 = other_helper\n",
        ALPHA_AFTER + "from other import __extracted_func_0\n",
        ALPHA_AFTER.replace(
            "def __extracted_func_0():", "def __extracted_func_0(unused=effect()):"
        ),
        ALPHA_AFTER.replace("    return result\n", "    return _towel_keep_owner\n"),
        ALPHA_AFTER.replace(
            "    return result\n", "    del _towel_keep_owner\n    return result\n"
        ),
        ALPHA_AFTER.replace(
            "    return result\n", "    later = lambda: _towel_keep_owner\n    return result\n"
        ),
    ],
)
def test_unproved_alpha_retention_stays_visible(after: str) -> None:
    assert "gains local _towel_keep_owner" in scope_changes(RETAINED_BEFORE, after)["f#0"]


@pytest.mark.parametrize(
    "before_expression, after_expression",
    [
        ("lambda y: z", "lambda z: z"),
        ("[z for y in [1]]", "[z for z in [1]]"),
        ("lambda y: y", "lambda z: y"),
        ("[y for y in [1]]", "[y for z in [1]]"),
    ],
)
def test_child_binder_changes_cannot_capture_a_free_name(
    before_expression: str, after_expression: str
) -> None:
    before = RETAINED_BEFORE.replace("make()", "make(" + before_expression + ")")
    after = ALPHA_AFTER.replace("make()", "make(" + after_expression + ")")
    assert "gains local _towel_keep_owner" in scope_changes(before, after)["f#0"]


@pytest.mark.parametrize(
    "before_expression, after_expression",
    [("lambda y: x", "lambda z: x"), ("[x for y in [1]]", "[x for z in [1]]")],
)
def test_a_new_child_binder_cannot_erase_the_outer_binding_correspondence(
    before_expression: str, after_expression: str, tmp_path: Path
) -> None:
    before = RETAINED_BEFORE.replace("def f():", "def f():\n    x = 3")
    before = before.replace("make()", "make(" + before_expression + ")")
    after = ALPHA_AFTER.replace("def __extracted_func_0():", "def __extracted_func_0(z):")
    after = after.replace("make()", "make(" + after_expression + ")")
    after = after.replace("def f():", "def f():\n    x = 3")
    after = after.replace("result = __extracted_func_0()", "result = __extracted_func_0(x)")
    assert "gains local _towel_keep_owner" in scope_changes(before, after)["f#0"]
    prefix = "x=9\ndef make(value): return value\ndef observe(value): return value(1) if callable(value) else value\n"
    outputs = []
    for name, body in (("before", before), ("after", after)):
        script = tmp_path / (name + ".py")
        script.write_text(prefix + body + "print(f())\n")
        outputs.append(observe(str(script), tmp_path))
    expected = ("3\n", "9\n") if before_expression.startswith("lambda") else ("[3]\n", "[9]\n")
    assert outputs == [(0, text, []) for text in expected]


def test_a_later_store_cannot_capture_an_earlier_global_read() -> None:
    before = RETAINED_BEFORE.replace("owner = make()", "owner = make(z)\n    local = 1")
    after = ALPHA_AFTER.replace("owner_1 = make()", "owner_1 = make(z)\n    z = 1")
    assert "gains local _towel_keep_owner" in scope_changes(before, after)["f#0"]


def test_original_caller_prefixes_are_not_a_retention_exemption() -> None:
    before = "import math as _towel_keep_owner\n" + RETAINED_BEFORE
    assert "gains local _towel_keep_owner" in scope_changes(before, ALPHA_AFTER)["f#0"]


@pytest.mark.parametrize("conditional", [False, True], ids=["later", "conditional"])
def test_wildcard_redirected_helpers_stay_visible_to_scope_watch(
    tmp_path: Path, conditional: bool
) -> None:
    wildcard = "if flag:\n    from other import *\n" if conditional else "from other import *\n"
    before = RETAINED_BEFORE + wildcard
    after = ALPHA_AFTER + wildcard
    path = tmp_path / "module.py"
    path.write_text(before)
    watch = ScopeWatch()
    assert watch(str(path), after) == after
    assert watch.found == [("module.py", {"f#0": ["gains local _towel_keep_owner"]})]


def test_a_plain_fresh_helper_overwrites_an_earlier_unconditional_wildcard_import(
    tmp_path: Path,
) -> None:
    wildcard = "from other import *\n"
    before, after = wildcard + RETAINED_BEFORE, wildcard + ALPHA_AFTER
    path = tmp_path / "module.py"
    path.write_text(before)
    watch = ScopeWatch()
    assert watch(str(path), after) == after
    assert watch.found == []


def _literal_retention_pair(literal: str, supplied: str | None = None) -> tuple[str, str]:
    before = RETAINED_BEFORE.replace("make()", "make(" + literal + ")")
    after = ALPHA_AFTER.replace("def __extracted_func_0():", "def __extracted_func_0(supplied):")
    after = after.replace("make()", "make(supplied)")
    after = after.replace(
        "result = __extracted_func_0()",
        "result = __extracted_func_0(" + (literal if supplied is None else supplied) + ")",
    )
    return before, after


@pytest.mark.parametrize("literal", ["7", "'value'", "None", "True"])
def test_readonly_literal_inputs_preserve_retention_and_values(
    literal: str, tmp_path: Path
) -> None:
    before, after = _literal_retention_pair(literal)
    assert scope_changes(before, after) == {}
    prefix = (
        "def make(value): return value\ndef observe(value): return type(value).__name__, value\n"
    )
    outputs = []
    for name, body in (("before", before), ("after", after)):
        script = tmp_path / (name + ".py")
        script.write_text(prefix + body + "print(f())\n")
        outputs.append(observe(str(script), tmp_path))
    assert outputs[0] == outputs[1]
    assert outputs[0][0] == 0


@pytest.mark.parametrize(
    "literal, supplied", [("True", "1"), ("1", "True"), ("1", "1.0"), ("'a'", "'b'")]
)
def test_wrong_literal_values_or_types_cannot_certify_retention(
    literal: str, supplied: str
) -> None:
    before, after = _literal_retention_pair(literal, supplied)
    assert "gains local _towel_keep_owner" in scope_changes(before, after)["f#0"]


@pytest.mark.parametrize(
    "replacement",
    [
        "supplied = 8\n    owner_1 = make(supplied)",
        "del supplied\n    owner_1 = make(supplied)",
        "owner_1 = make(lambda supplied: supplied)",
        "owner_1 = make([supplied for supplied in [8]])",
    ],
)
def test_rebound_or_child_masked_literal_inputs_cannot_certify_retention(
    replacement: str,
) -> None:
    before, after = _literal_retention_pair("7")
    if "lambda" in replacement:
        before = before.replace("make(7)", "make(lambda value: 7)")
    elif "for supplied" in replacement:
        before = before.replace("make(7)", "make([7 for value in [8]])")
    after = after.replace("owner_1 = make(supplied)", replacement)
    assert "gains local _towel_keep_owner" in scope_changes(before, after)["f#0"]


@pytest.mark.parametrize(
    "binding",
    [
        "import math as supplied",
        "try:\n    pass\nexcept Exception as supplied:\n    pass",
        "match 1:\n    case supplied:\n        pass",
        "match [1]:\n    case [*supplied]:\n        pass",
        "match {}:\n    case {**supplied}:\n        pass",
    ],
)
def test_string_binders_cannot_rebind_a_literal_helper_input(binding: str) -> None:
    before, after = _literal_retention_pair("7")
    statements = "\n".join("    " + line for line in binding.splitlines()) + "\n"
    before = before.replace("    owner = make(7)", statements + "    owner = make(7)")
    after = after.replace(
        "    owner_1 = make(supplied)", statements + "    owner_1 = make(supplied)"
    )
    assert "gains local _towel_keep_owner" in scope_changes(before, after)["f#0"]

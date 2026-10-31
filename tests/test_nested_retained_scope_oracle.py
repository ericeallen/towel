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


"""Nested retention exemptions require exact branch, producer and return-slot evidence."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

from tests.hostile_execution import scope_changes

_FIXTURES = Path(__file__).parent / "fixtures"


def _generated() -> tuple[str, str]:
    return (
        (_FIXTURES / "scope_retained_match_before.py.txt").read_text(),
        (_FIXTURES / "scope_retained_match_after.py.txt").read_text(),
    )


def _helper(module: ast.Module) -> ast.FunctionDef:
    return next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func_")
    )


def _inner(module: ast.Module) -> ast.FunctionDef:
    outer = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "f1"
    )
    return next(
        node for node in outer.body if isinstance(node, ast.FunctionDef) and node.name == "inner"
    )


def test_actual_generated_match_retention_keeps_the_original_binding_scope() -> None:
    before, after = _generated()
    assert scope_changes(before, after) == {}


@pytest.mark.parametrize(
    "change",
    (
        "wrong_producer",
        "wrong_return_slot",
        "helper_redirected",
        "effectful_default",
        "changed_match_subject",
        "changed_case_pattern",
        "changed_other_case",
        "keeper_read",
        "keeper_captured",
        "keeper_wrong_spelling",
        "unrelated_statement_change",
        "original_global_keeper",
    ),
)
def test_nested_keeper_lookalikes_do_not_hide_a_changed_scope(change: str) -> None:
    before, after = _generated()
    module = ast.parse(after)
    helper = _helper(module)
    inner = _inner(module)
    match = next(node for node in inner.body if isinstance(node, ast.Match))
    assignment = match.cases[1].body[0]
    assert isinstance(assignment, ast.Assign)
    target = assignment.targets[0]
    assert isinstance(target, ast.Name)
    if change == "wrong_producer":
        producer = helper.body[0]
        assert isinstance(producer, ast.Assign) and isinstance(producer.value, ast.Call)
        producer.value.func = ast.Name(id="different_producer", ctx=ast.Load())
    elif change == "wrong_return_slot":
        returned = helper.body[-1]
        assert isinstance(returned, ast.Return)
        returned.value = ast.Constant(value=None)
    elif change == "helper_redirected":
        module.body.append(
            ast.Assign(
                targets=[ast.Name(id=helper.name, ctx=ast.Store())],
                value=ast.Name(id="redirected", ctx=ast.Load()),
            )
        )
    elif change == "effectful_default":
        helper.args.defaults = [
            ast.Call(func=ast.Name(id="effect", ctx=ast.Load()), args=[], keywords=[])
        ]
    elif change == "changed_match_subject":
        match.subject = ast.Constant(value=1)
    elif change == "changed_case_pattern":
        match.cases[1].pattern = ast.MatchValue(value=ast.Constant(value=9))
    elif change == "changed_other_case":
        match.cases[0].body = [ast.Pass()]
    elif change == "keeper_read":
        inner.body.append(ast.Expr(value=ast.Name(id=target.id, ctx=ast.Load())))
    elif change == "keeper_captured":
        inner.body.append(ast.parse("later = lambda: _towel_keep_v1").body[0])
    elif change == "keeper_wrong_spelling":
        target.id = "_towel_keep_unrelated"
    elif change == "unrelated_statement_change":
        inner.body[0] = ast.Pass()
    elif change == "original_global_keeper":
        before = "_towel_keep_v1 = 19\n" + before
    else:
        raise AssertionError(change)
    ast.fix_missing_locations(module)
    changes = scope_changes(before, ast.unparse(module))
    assert any(
        "gains local _towel_keep_" in message
        for messages in changes.values()
        for message in messages
    )


_PRELUDE = """import gc
EVENTS=[]
class Owner:
    def __init__(self): EVENTS.append('new-owner')
    def __del__(self): EVENTS.append('drop-owner')
def make(): return Owner()
def consume(owner, fail):
    EVENTS.append('consume')
    if fail: raise ValueError('failed')
def report(): EVENTS.append('after')
"""
_DRIVER = """for mode in (0,1,2):
    for fail in (False,True):
        EVENTS.clear()
        try: f(mode,fail)
        except Exception as error: EVENTS.append((type(error).__name__,str(error)))
        gc.collect()
        print(mode,fail,EVENTS)
"""


@pytest.mark.parametrize("branch", ("if", "match", "nested", "single_pass"))
def test_exact_nested_retention_preserves_executed_lifetimes(tmp_path: Path, branch: str) -> None:
    if branch == "single_pass":
        original = "def f(mode,fail):\n    while True:\n        owner=make()\n        consume(owner,fail)\n        break\n    report()\n"
        rewritten = "def f(mode,fail):\n    while True:\n        _towel_keep_owner=__extracted_func_0(fail)\n        break\n    report()\n"
    elif branch == "if":
        original = "def f(mode,fail):\n    if mode:\n        owner=make()\n        consume(owner,fail)\n    report()\n"
        rewritten = "def f(mode,fail):\n    if mode:\n        _towel_keep_owner=__extracted_func_0(fail)\n    report()\n"
    elif branch == "match":
        original = "def f(mode,fail):\n    match mode:\n        case 1|2:\n            owner=make()\n            consume(owner,fail)\n        case _:\n            pass\n    report()\n"
        rewritten = "def f(mode,fail):\n    match mode:\n        case 1|2:\n            _towel_keep_owner=__extracted_func_0(fail)\n        case _:\n            pass\n    report()\n"
    else:
        original = "def f(mode,fail):\n    if mode:\n        match mode:\n            case 1|2:\n                owner=make()\n                consume(owner,fail)\n            case _:\n                pass\n    report()\n"
        rewritten = "def f(mode,fail):\n    if mode:\n        match mode:\n            case 1|2:\n                _towel_keep_owner=__extracted_func_0(fail)\n            case _:\n                pass\n    report()\n"
    helper = "def __extracted_func_0(supplied):\n    owner=make()\n    consume(owner,supplied)\n    return owner\n"
    before = _PRELUDE + original + _DRIVER
    after = _PRELUDE + helper + rewritten + _DRIVER
    assert scope_changes(before, after) == {}
    results = []
    for name, source in (("before", before), ("after", after)):
        path = tmp_path / (name + ".py")
        path.write_text(source)
        ran = subprocess.run(
            [sys.executable, "-B", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert ran.returncode == 0 and not ran.stderr
        results.append((ran.returncode, ran.stdout, ran.stderr))
    assert results[0] == results[1]
    assert "drop-owner" in results[0][1] and "ValueError" in results[0][1]


@pytest.mark.parametrize("boundary", ("for", "while", "try", "with"))
def test_uncertified_nested_suites_remain_visible(boundary: str) -> None:
    guards = {
        "for": "for item in items:",
        "while": "while flag:",
        "try": "try:",
        "with": "with manager:",
    }
    ending = "    finally:\n        pass\n" if boundary == "try" else ""
    original = (
        "def f():\n    "
        + guards[boundary]
        + "\n        owner=make()\n        consume(owner)\n"
        + ending
    )
    rewritten = (
        "def f():\n    "
        + guards[boundary]
        + "\n        _towel_keep_owner=__extracted_func_0()\n"
        + ending
    )
    after = (
        "def __extracted_func_0():\n    owner=make()\n    consume(owner)\n    return owner\n"
        + rewritten
    )
    changes = scope_changes(original, after)
    assert "gains local _towel_keep_owner" in changes["f#0"]


def _single_pass_seed() -> tuple[str, str]:
    """Actual before/after output for binding seed 19009312961665790893874724517."""
    return (
        (_FIXTURES / "scope_retained_single_pass_before.py.txt").read_text(),
        (_FIXTURES / "scope_retained_single_pass_after.py.txt").read_text(),
    )


def test_single_pass_seed_has_certified_retention() -> None:
    assert scope_changes(*_single_pass_seed()) == {}


@pytest.mark.parametrize("change", ("wrong_producer", "keeper_read", "keeper_captured"))
def test_single_pass_seed_still_rejects_changed_values_and_keeper_reads(change: str) -> None:
    before, after = _single_pass_seed()
    if change == "wrong_producer":
        after = after.replace("v1 = c.get('k', 0)", "v1 = c.get('k', 1)")
    else:
        statement = (
            "print(_towel_keep_v1)" if change == "keeper_read" else "later = lambda: _towel_keep_v1"
        )
        after = after.replace(
            "            break", "            " + statement + "\n            break"
        )
    assert after != _single_pass_seed()[1]
    expected = ["gains local _towel_keep_v1"]
    if change == "keeper_captured":
        expected.append("gains local later")
    assert scope_changes(before, after) == {
        "f1#0.inner#0": expected,
        "f2#0.inner#0": expected,
    }


@pytest.mark.parametrize(
    "change",
    (
        "variable_guard",
        "truthy_guard",
        "false_guard",
        "no_break",
        "conditional_break",
        "earlier_break",
        "continue",
        "nested_loop",
        "handler",
        "manager",
        "else_suite",
    ),
)
def test_single_pass_exemption_does_not_admit_unproved_loop_suites(change: str) -> None:
    def alter(source: str) -> str:
        module = ast.parse(source)
        for outer in module.body:
            if not isinstance(outer, ast.FunctionDef) or outer.name not in ("f1", "f2"):
                continue
            inner = next(node for node in outer.body if isinstance(node, ast.FunctionDef))
            loop = next(node for node in inner.body if isinstance(node, ast.While))
            if change == "variable_guard":
                loop.test = ast.Name(id="flag", ctx=ast.Load())
            elif change in ("truthy_guard", "false_guard"):
                loop.test = ast.Constant(value=1 if change == "truthy_guard" else False)
            elif change == "no_break":
                loop.body.pop()
            elif change == "conditional_break":
                loop.body[-1] = ast.parse("if flag: break").body[0]
            elif change == "else_suite":
                loop.orelse = [ast.Pass()]
            else:
                statements = {
                    "earlier_break": "if flag: break",
                    "continue": "if flag: continue",
                    "nested_loop": "for item in items: pass",
                    "handler": "try:\n    pass\nfinally:\n    pass",
                    "manager": "with manager: pass",
                }
                loop.body.insert(0, ast.parse(statements[change]).body[0])
        return ast.unparse(ast.fix_missing_locations(module))

    before, after = (alter(source) for source in _single_pass_seed())
    assert scope_changes(before, after) == {
        "f1#0.inner#0": ["gains local _towel_keep_v1"],
        "f2#0.inner#0": ["gains local _towel_keep_v1"],
    }


def test_extraction_cannot_hide_an_unproved_original_loop_suite() -> None:
    # Moving a nested loop into a helper leaves the new outer loop simple;
    # original-loop certification must still refuse that broader context.
    before = "def f():\n    while True:\n        for item in items: pass\n        owner=make()\n        consume(owner)\n        break\n"
    after = "def __extracted_func_0():\n    for item in items: pass\n    owner=make()\n    consume(owner)\n    return owner\ndef f():\n    while True:\n        _towel_keep_owner=__extracted_func_0()\n        break\n"
    assert scope_changes(before, after) == {"f#0": ["gains local _towel_keep_owner"]}


def _comprehension_shadow() -> tuple[str, str]:
    return (
        (_FIXTURES / "scope_retained_comprehension_shadow_before.py.txt").read_text(),
        (_FIXTURES / "scope_retained_comprehension_shadow_after.py.txt").read_text(),
    )


def _shadow_observation(tmp_path: Path, source: str, label: str) -> str:
    path = tmp_path / (label + ".py")
    path.write_text(
        source
        + "\nfor function in (f1, f2):\n"
        + "    for arguments in ((0, [], {}, Box(0)), (1, [1, 2, 3], {'k': 1}, Box(1))):\n"
        + "        try:\n"
        + "            print('returned', function(*arguments))\n"
        + "        except Exception as error:\n"
        + "            print('raised', type(error).__name__, str(error))\n"
        + "print('state', LOG, G)\n"
    )
    ran = subprocess.run(
        [sys.executable, "-B", str(path)], capture_output=True, text=True, timeout=20
    )
    assert ran.returncode == 0 and not ran.stderr, ran.stderr
    return ran.stdout


def test_actual_nested_comprehension_shadow_has_complete_retained_correspondence(
    tmp_path: Path,
) -> None:
    before, after = _comprehension_shadow()
    assert scope_changes(before, after) == {}
    assert _shadow_observation(tmp_path, before, "before") == _shadow_observation(
        tmp_path, after, "after"
    )


@pytest.mark.parametrize(
    "expression",
    (
        "[(o + q) for q in b for o in range(q)]",
        "[(o + q) for q in b for o in range(o)]",
        "(lambda o: o + 1)(2)",
    ),
)
def test_nested_local_reads_and_later_iterables_keep_their_actual_binding(
    tmp_path: Path, expression: str
) -> None:
    before, after = (
        source.replace("[o + 1 for o in b]", expression) for source in _comprehension_shadow()
    )
    assert scope_changes(before, after) == {}
    assert _shadow_observation(tmp_path, before, "before") == _shadow_observation(
        tmp_path, after, "after"
    )


@pytest.mark.parametrize(
    "expression",
    (
        "[o + 1 for o in (o.v,)]",
        "[o + 1 for o in (lambda: (o.v,))()]",
        "[o + 1 for o in (lambda o=o.v: (o,))()]",
        "[(lambda: o.v)() for q in b]",
    ),
)
def test_real_outer_reads_in_iterables_defaults_and_lambdas_are_not_exempt(
    expression: str,
) -> None:
    before, after = (
        source.replace("[o + 1 for o in b]", expression) for source in _comprehension_shadow()
    )
    changes = scope_changes(before, after)
    assert changes["f1#0.inner#0"] == ["gains local _towel_keep_v1"]
    assert changes["f2#0.inner#0"] == ["gains local _towel_keep_v1"]


def test_captured_comprehension_target_uses_the_native_compiler_binding(
    tmp_path: Path,
) -> None:
    before, after = (
        source.replace("[o + 1 for o in b]", "[(lambda: o + 1)() for o in b]")
        for source in _comprehension_shadow()
    )
    changes = scope_changes(before, after)
    if sys.version_info >= (3, 12):
        assert changes["f1#0.inner#0"] == ["gains local _towel_keep_v1"]
        assert changes["f2#0.inner#0"] == ["gains local _towel_keep_v1"]
    else:
        assert changes == {}
        assert _shadow_observation(tmp_path, before, "before") == _shadow_observation(
            tmp_path, after, "after"
        )


def test_shadowed_binding_does_not_certify_changed_evaluation_order() -> None:
    before, after = _comprehension_shadow()
    module = ast.parse(after)
    helper = _helper(module)
    helper.body[0], helper.body[1] = helper.body[1], helper.body[0]
    changes = scope_changes(before, ast.unparse(module))
    assert changes["f1#0.inner#0"] == ["gains local _towel_keep_v1"]
    assert changes["f2#0.inner#0"] == ["gains local _towel_keep_v1"]


def test_uncompilable_standalone_nonlocal_helper_declines_correspondence() -> None:
    from tests.hostile_execution import _retained_correspondence

    before, after = (ast.parse(source) for source in _comprehension_shadow())
    original = _inner(before)
    rewritten = _inner(after)
    helper = _helper(after)
    original.body.insert(0, ast.Nonlocal(names=["outside"]))
    helper.body.insert(0, ast.Nonlocal(names=["outside"]))
    assignment = rewritten.body[0]
    assert isinstance(assignment, ast.Assign)
    assert (
        _retained_correspondence(
            original,
            rewritten,
            assignment,
            helper,
            frozenset({"v1"}),
            frozenset({"a", "b", "c", "k0", "o", "outside"}),
        )
        == {}
    )


@pytest.mark.parametrize("target", ("holder.value", "holder[0]"))
def test_comprehension_assignment_receivers_keep_their_enclosing_binding(target: str) -> None:
    moved = f"    owner = make()\n    [None for {target} in (1, 2)]\n    consume(owner)\n"
    before = "def f(holder):\n" + moved + "    report()\n"
    helper = (
        "def __extracted_func_0(supplied):\n"
        + moved.replace("holder", "supplied")
        + "    return owner\n"
    )
    after = helper + (
        "def f(holder):\n" "    _towel_keep_owner = __extracted_func_0(holder)\n" "    report()\n"
    )
    assert scope_changes(before, after) == {}
    changed = after.replace(target.replace("holder", "supplied"), "other.value")
    assert "gains local _towel_keep_owner" in scope_changes(before, changed)["f#0"]


@pytest.mark.parametrize("target", ("holder.value", "holder[key]"))
@pytest.mark.parametrize("name", ("holder", "key"))
def test_comprehension_assignment_reads_do_not_hide_new_locals(target: str, name: str) -> None:
    before = f"def f():\n    [None for {target} in (1, 2)]\n"
    after = before.replace("def f():\n", f"def f():\n    {name} = replacement\n")
    assert f"gains local {name}" in scope_changes(before, after)["f#0"]

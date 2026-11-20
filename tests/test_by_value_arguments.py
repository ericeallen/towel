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

"""Relaxed mode passes arguments by value and keeps a ``lambda:`` only where one is necessary.

The user-perspective audit of 1.949 (findings 1 and 9) quoted what its
reviewers would not merge: ``lambda: parse_user``, ``lambda: IsStr``,
``lambda: "Point"``, ``lambda: 'pybabel init …' + lang``. The owner's rule:
"relaxed mode should avoid eta expansion of arguments except in cases where
it is clearly necessary (if there even are such cases)." The rule as
implemented, its two assumptions and what it leaves to ``--strict`` are in
docs/DECISIONS.md, "Relaxed mode passes arguments by value".

The first half pins the audit's own material. The second is hostile: each
program is run before and after refactoring, in both modes, and the test
says which arguments must stay deferred and why.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
from pathlib import Path
from typing import List
from unittest.mock import call, patch

import pytest

from towel.api import UnificationRefactorEngine
from towel.unification.semantic_safety import _stable_names_bound_for, stable_module_names
from towel.unification.thunk_inlining import _evaluated_at_most_once
from tests.test_builtins_across_modules import (
    _passes_join,
    _patched_module_name_project,
    _refactor as _refactor_modules,
    _run as _run_check,
)
from tests.test_callback_parameters import (
    helper_calls,
    refactored,
    refactored_scenario,
    run_script,
)
from tests.test_class_object_annotations import (
    _written_annotations_of_the_class_guard,
    requires_mypy,
)
from tests.test_program_type_names import _class_and_list_helper_annotations
from towel.unification.argument_ownership import _supported_runtime

# The default mode is what this module pins; strict runs ask for it by name.
pytestmark = pytest.mark.relaxed_mode


def _passes_alpha_by_value(generated: str) -> bool:
    """Whether a helper call passes ``alpha`` itself, in any position (not ``lambda: alpha``)."""
    return re.search(r"_extracted_func_0\((?:[^()]*, )?alpha[,)]", generated) is not None


def _declined_without_lifetime_proofs(tmp_path: Path, source: str, generated: str) -> None:
    """Strict mode where frame release order is uncertified (CPython 3.14+).

    Each candidate below hands a caller's local to the helper, which strict
    mode declines there (``lifetime_order_uncertified``), so the program is
    left as it was and no lambda is written at all.
    """
    assert generated == source
    path = tmp_path / "uncertified.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False, strict=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert engine.analyze_file(str(path)) == []
    assert engine.declined_pairs.get("lifetime_order_uncertified", 0) > 0


# -- The audit's material ------------------------------------------------------


@pytest.mark.parametrize(
    "fixture, calls",
    [
        (
            "relaxed_scenarios/callee_user",
            ["__extracted_func_0(items, net_price)", "__extracted_func_0(items, gross_price)"],
        ),
        (
            "by_value/callee_parsers",
            ["__extracted_func_0(path, parse_user)", "__extracted_func_0(path, parse_product)"],
        ),
    ],
)
def test_a_module_level_function_is_passed_as_it_is(
    tmp_path: Path, fixture: str, calls: List[str]
) -> None:
    """Audit cases 06 and 07 (finding 9; engine map B): ``helper(parse_user, path)``, no lambda."""
    generated, same = refactored_scenario(tmp_path, fixture)
    assert helper_calls(generated) == calls
    assert "lambda" not in generated
    assert "_param_0(" in generated and "_param_0()" not in generated
    assert same


def test_strict_mode_still_defers_a_module_level_function(tmp_path: Path) -> None:
    """Strict mode is 1.949: a callee may rebind the name, so the helper looks it up where the block did."""
    generated, same = refactored_scenario(tmp_path, "by_value/callee_parsers", strict=True)
    if _supported_runtime():
        assert "lambda: parse_user" in generated and "lambda: parse_product" in generated
    else:
        # The blocks hand path to the helper, which strict mode declines here.
        assert "extracted_func" not in generated
    assert same


def test_a_differing_method_of_an_argument_is_passed_as_a_function_of_nothing(
    tmp_path: Path,
) -> None:
    """Audit case 21 (engine map D4): 1.949 declined it as ``forwarded_callee``.

    The lookup is the first thing the block does, but inside a ``try``: a
    property may raise what the handlers catch (review of lane ``byvalue``,
    defect 5), so the helper looks the method up where the block did. Never
    ``lambda *args, **kwargs``.
    """
    generated, same = refactored_scenario(tmp_path, "by_value/try_log_callee")
    assert "lambda: client.get_profile" in generated
    assert "lambda: client.get_preferences" in generated
    assert "response = _param_0()(user_id)" in generated
    assert "**kwargs" not in generated
    assert same


def test_strict_mode_still_declines_a_differing_method(tmp_path: Path) -> None:
    generated, _ = refactored_scenario(tmp_path, "by_value/try_log_callee", strict=True)
    assert "extracted_func" not in generated


MICROBLOG = """\
class os:
    @staticmethod
    def system(command):
        print("system", command)
        return 1 if "fail" in command else 0

    @staticmethod
    def remove(path):
        print("remove", path)


def init(lang):
    if os.system('pybabel extract -F babel.cfg -k _l -o messages.pot .'):
        raise RuntimeError('extract command failed')
    if os.system(
            'pybabel init -i messages.pot -d app/translations -l ' + lang):
        raise RuntimeError('init command failed')
    os.remove('messages.pot')


def update():
    if os.system('pybabel extract -F babel.cfg -k _l -o messages.pot .'):
        raise RuntimeError('extract command failed')
    if os.system('pybabel update -i messages.pot -d app/translations'):
        raise RuntimeError('update command failed')
    os.remove('messages.pot')


init("de")
update()
try:
    init("fail")
except RuntimeError as error:
    print("raised", error)
"""


@pytest.mark.parametrize("strict", [False, True])
def test_the_microblog_command_string_follows_a_call_and_stays_a_lambda(
    tmp_path: Path, strict: bool
) -> None:
    """The newcomer's real project (``microblog-no-extras.diff``): ``lambda: 'pybabel init …' + lang``.

    Review of lane ``byvalue``, defect 3: the string was passed by value
    because the block reads ``lang`` nowhere else. But the block runs the
    extract command first and raises if it fails; with ``init(None)``, by
    value raises ``TypeError`` at the call instead, before that command.
    The concatenation follows a call and a ``raise``, so it stays a lambda.
    """
    generated, same = refactored(
        tmp_path, MICROBLOG.replace('init("fail")', "init(None)"), strict=strict
    )
    assert "lambda: 'pybabel init -i messages.pot -d app/translations -l ' + lang" in generated
    assert same


CONCATENATION_IN_A_LOOP = """\
def first(lang):
    results = []
    for item in range(2):
        results.append("prefix-" + lang)
    print("collected", len(results))
    return results


def second(lang):
    results = []
    for item in range(2):
        results.append(lang)
    print("collected", len(results))
    return results


values = first("suffix")
print(values, second("x"))
print("same object", values[0] is values[1])
"""


@pytest.mark.parametrize("strict", [False, True])
def test_a_concatenation_in_a_loop_stays_a_lambda(tmp_path: Path, strict: bool) -> None:
    """Review of lane ``byvalue``, defect 3: by value, both iterations appended one string object.

    The loop makes a new string each time; ``values[0] is values[1]`` is
    false before and must stay so.
    """
    generated, same = refactored(tmp_path, CONCATENATION_IN_A_LOOP, strict=strict)
    if strict and not _supported_runtime():
        _declined_without_lifetime_proofs(tmp_path, CONCATENATION_IN_A_LOOP, generated)
    else:
        assert "results.append(__param_0())" in generated
    assert same


SHORT_CIRCUITED_CONCATENATION = """\
def check_tagged(enabled, label, x):
    print("checking")
    if enabled and x > 0 and ("tag:" + label) != "skip":
        return 1
    print("rejected")
    return 0


def check_titled(enabled, title, x):
    print("checking")
    if enabled and x > 0 and title != "skip":
        return 1
    print("rejected")
    return 0


print(check_tagged(False, None, 4), check_titled(False, None, 4))
print(check_tagged(True, "ok", 4), check_titled(True, "ok", 4))
"""


@pytest.mark.parametrize("strict", [False, True])
def test_a_short_circuited_concatenation_stays_a_lambda(tmp_path: Path, strict: bool) -> None:
    """Review of lane ``expressions``, defect 1: ``"tag:" + label`` under ``and`` was hoisted.

    With ``enabled`` false the block never builds the string; at the call,
    ``"tag:" + None`` raises ``TypeError``.
    """
    generated, same = refactored(tmp_path, SHORT_CIRCUITED_CONCATENATION, strict=strict)
    assert 'lambda: "tag:" + label' in generated
    assert same


CSVKIT = """\
import unittest


def load(kind):
    return {
        "features": [
            {"geometry": {"type": "Point", "coordinates": [0.0, 0.0]}},
            {"geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]}},
        ]
    }


class TestCSVJSON(unittest.TestCase):
    def test_geojson_types(self):
        geojson = load("types")
        self.assertEqual(len(geojson["features"]), 2)
        self.assertEqual(geojson["features"][0]["geometry"]["type"], "Point")
        self.assertEqual(geojson["features"][1]["geometry"]["type"], "LineString")

    def test_geojson_coordinates(self):
        geojson = load("coordinates")
        self.assertEqual(len(geojson["features"]), 2)
        self.assertEqual(geojson["features"][0]["geometry"]["coordinates"], [0.0, 0.0])
        self.assertEqual(
            geojson["features"][1]["geometry"]["coordinates"], [[0.0, 0.0], [1.0, 1.0]]
        )


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=0).result
    print(result.testsRun, len(result.failures), len(result.errors))
"""


def test_csvkits_literals_and_constant_lists_are_passed_by_value(tmp_path: Path) -> None:
    """Gemini's F3: ``lambda: "Point"`` beside ``lambda: [0.0, 0.0]`` in csvkit's tests.

    A display of constants makes a new object, cannot fail and runs
    nothing, so where the helper evaluates it at most once the call may
    make it instead.
    """
    generated, same = refactored(tmp_path, CSVKIT)
    calls = helper_calls(generated)
    assert len(calls) == 2
    assert "'Point', 'LineString'" in calls[0]
    assert "[0.0, 0.0], [[0.0, 0.0], [1.0, 1.0]]" in calls[1]
    assert "lambda" not in generated
    assert same


def test_strict_mode_keeps_csvkits_constant_lists_deferred(tmp_path: Path) -> None:
    """Strict mode is 1.949 for a display: what the block adds to it lives as long as before."""
    generated, same = refactored(tmp_path, CSVKIT, strict=True)
    assert "lambda: [0.0, 0.0]" in generated
    assert same


DISPLAY_THAT_A_CALLEE_FILLS = """\
class Token:
    def __del__(self):
        print("released")


def consume(values):
    if values is not None:
        values.append(Token())
    print("consumed")


def alpha():
    print("start")
    consume([0])
    print("after")
    print("end")
    return 1


def beta():
    print("start")
    consume(None)
    print("after")
    print("end")
    return 1


alpha()
beta()
"""


def test_strict_mode_keeps_a_display_a_callee_fills_deferred(tmp_path: Path) -> None:
    """Review of lane ``byvalue``, defect 1: strict mode released ``Token`` late.

    ``consume`` adds an object with a finalizer to the list. Built in the
    block, the list dies when ``consume`` returns and ``released`` prints
    before ``after``; held by a helper parameter, it would die when the
    helper returns. Strict mode keeps 1.949's ``lambda: [0]``.
    """
    generated, same = refactored(tmp_path, DISPLAY_THAT_A_CALLEE_FILLS, strict=True)
    assert "lambda: [0]" in generated
    assert same


def test_relaxed_mode_passes_a_display_a_callee_fills_by_value(tmp_path: Path) -> None:
    """The first assumption of relaxed mode, by its counterexample: ``released`` prints late."""
    generated, same = refactored(tmp_path, DISPLAY_THAT_A_CALLEE_FILLS)
    assert helper_calls(generated) == ["__extracted_func_0([0])", "__extracted_func_0(None)"]
    directory = tmp_path / "relaxed"
    before = run_script(directory / "original.py")[1].splitlines()
    after = run_script(directory / "changed.py")[1].splitlines()
    assert before[:4] == ["start", "consumed", "released", "after"]
    assert after[:4] == ["start", "consumed", "after", "end"]
    assert sorted(before) == sorted(after)
    assert not same


DIRTY_EQUALS = """\
import re


class IsStr:
    def __init__(self, regex=None):
        self.regex = regex

    def __eq__(self, other):
        return isinstance(other, str) and bool(re.fullmatch(self.regex, other))

    def __repr__(self):
        return f"IsStr(regex={self.regex!r})"


class IsBytes:
    def __init__(self, regex=None):
        self.regex = regex

    def __eq__(self, other):
        return isinstance(other, bytes) and bool(re.fullmatch(self.regex, other))

    def __repr__(self):
        return f"IsBytes(regex={self.regex!r})"


def test_regex_true():
    assert "whatever" == IsStr(regex="whatever")
    reg = IsStr(regex="wh.*er")
    assert "whatever" == reg
    assert "WHATEVER" != reg
    assert str(reg) == "IsStr(regex='wh.*er')"


def test_regex_bytes_true():
    assert b"whatever" == IsBytes(regex=b"whatever")
    reg = IsBytes(regex=b"wh.*er")
    assert b"whatever" == reg
    assert b"WHATEVER" != reg
    assert str(reg) == "IsBytes(regex=b'wh.*er')"


test_regex_true()
test_regex_bytes_true()
print("ok")
"""


def test_dirty_equals_classes_are_passed_as_they_are(tmp_path: Path) -> None:
    """Gemini's F3: ``__extracted_func_4(lambda: IsStr, 'wh.*er', …)`` in dirty-equals' tests."""
    generated, same = refactored(tmp_path, DIRTY_EQUALS)
    assert helper_calls(generated) == [
        "__extracted_func_0('whatever', IsStr, 'wh.*er', 'WHATEVER', \"IsStr(regex='wh.*er')\")",
        "__extracted_func_0(b'whatever', IsBytes, b'wh.*er', b'WHATEVER',"
        " \"IsBytes(regex=b'wh.*er')\")",
    ]
    assert "lambda" not in generated
    assert same


# -- Hostile cases: where a lambda is necessary --------------------------------

CALL_IN_A_LOOP = """\
import itertools

counter = itertools.count()


def tick():
    return next(counter)


def tock(scale):
    return next(counter) * scale


def run_ticks(items):
    seen = []
    for item in items:
        print("visit", item)
        seen.append((item, tick()))
    return seen


def run_tocks(items):
    seen = []
    for item in items:
        print("visit", item)
        seen.append((item, -tock(100)))
    return seen


print(run_ticks("ab"))
print(run_tocks("cd"))
print(run_ticks(""))
"""


@pytest.mark.parametrize("strict", [False, True])
def test_a_differing_call_in_a_loop_body_stays_a_lambda(tmp_path: Path, strict: bool) -> None:
    """The call runs once per item, after that item's ``print``, and not at all for no items."""
    generated, same = refactored(tmp_path, CALL_IN_A_LOOP, strict=strict)
    if strict and not _supported_runtime():
        _declined_without_lifetime_proofs(tmp_path, CALL_IN_A_LOOP, generated)
    else:
        assert "lambda: tick()" in generated and "lambda: -tock(100)" in generated
        assert "seen.append((item, _param_0()))" in generated
    assert same


GUARDED_ATTRIBUTE = """\
class Box:
    def __init__(self, width, depth):
        self.width = width
        self.depth = depth


def show(label, value):
    print(label, value)
    return value


def width_of(box, label):
    result = None
    if box:
        result = show(label, box.width)
    print("done", label)
    return result


def depth_of(box, label):
    result = None
    if box:
        result = show(label, box.depth)
    print("done", label)
    return result


print(width_of(Box(1, 2), "w"))
print(depth_of(Box(1, 2), "d"))
print(width_of(None, "w"))
print(depth_of(None, "d"))
"""


@pytest.mark.parametrize("strict", [False, True])
def test_a_guarded_attribute_read_stays_a_lambda(tmp_path: Path, strict: bool) -> None:
    """``if box: use(box.width)``: read at the call, ``None.width`` would raise where nothing did."""
    generated, same = refactored(tmp_path, GUARDED_ATTRIBUTE, strict=strict)
    if strict and not _supported_runtime():
        _declined_without_lifetime_proofs(tmp_path, GUARDED_ATTRIBUTE, generated)
    else:
        assert "result = show(label, _param_0())" in generated
    assert same
    if not strict:
        assert "lambda: box.width" in generated and "lambda: box.depth" in generated


def test_an_attribute_read_guarded_by_a_type_test_is_left_alone(tmp_path: Path) -> None:
    """``if box is not None``: the argument relies on the test (``narrowing_lost_at_call_site``)."""
    source = GUARDED_ATTRIBUTE.replace("if box:", "if box is not None:")
    generated, same = refactored(tmp_path, source)
    assert generated == source and same


ATTRIBUTE_AFTER_A_MUTATING_CALL = """\
class Account:
    def __init__(self):
        self.balance = 10
        self.pending = 5

    def settle(self):
        self.balance += self.pending
        self.pending = 0


def report(label, value):
    print(label, value)


def settled_balance(account):
    account.settle()
    report("after settling", account.balance)
    return account


def settled_pending(account):
    account.settle()
    report("after settling", account.pending)
    return account


settled_balance(Account())
settled_pending(Account())
"""


@pytest.mark.parametrize("strict", [False, True])
def test_an_attribute_read_after_a_call_stays_a_lambda(tmp_path: Path, strict: bool) -> None:
    """``account.settle()`` changes what ``account.balance`` gives; by value it would print 10, not 15."""
    generated, same = refactored(tmp_path, ATTRIBUTE_AFTER_A_MUTATING_CALL, strict=strict)
    assert 'report("after settling", _param_0())' in generated
    assert same
    if not strict:
        assert "lambda: account.balance" in generated


ATTRIBUTE_AFTER_PLAIN_STEPS = """\
class Stats:
    def __init__(self):
        self.total = 7
        self.count = 2


def report(label, value):
    print(label, value)
    return value


def show_total(stats, label):
    title = label
    shown = report(title, stats.total)
    print("done", title)
    return shown


def show_count(stats, label):
    title = label
    shown = report(title, stats.count)
    print("done", title)
    return shown


print(show_total(Stats(), "t"), show_count(Stats(), "c"))
"""


def test_an_attribute_read_before_any_call_is_passed_by_value(tmp_path: Path) -> None:
    """Evaluated once on every path, with only an assignment of a name before it."""
    generated, same = refactored(tmp_path, ATTRIBUTE_AFTER_PLAIN_STEPS)
    assert helper_calls(generated) == [
        "__extracted_func_0(label, stats.total)",
        "__extracted_func_0(label, stats.count)",
    ]
    assert same


def test_strict_mode_defers_the_attribute_read_before_any_call(tmp_path: Path) -> None:
    """Strict mode assumes nothing of an attribute read: a property may run code."""
    generated, same = refactored(tmp_path, ATTRIBUTE_AFTER_PLAIN_STEPS, strict=True)
    if _supported_runtime():
        assert "shown = report(title, _param_0())" in generated
    else:
        _declined_without_lifetime_proofs(tmp_path, ATTRIBUTE_AFTER_PLAIN_STEPS, generated)
    assert same


PROTOCOL_BEFORE_A_READ = """\
class Stats:
    def __init__(self):
        self.total = 7
        self.count = 2


def report(label, value):
    print(label, value)
    return value


def show_total(stats, label):
    title = TITLE
    shown = report(title, stats.total + 1)
    print("done", title)
    return shown


def show_count(stats, label):
    title = TITLE
    shown = report(title, stats.count + 2)
    print("done", title)
    return shown


class Label:
    def __init__(self, stats):
        self.stats = stats

    def __format__(self, spec):
        self.stats.total = 100
        return "label"

    def __hash__(self):
        self.stats.total = 100
        return 1

    def __repr__(self):
        return "label"


stats = Stats()
print(show_total(stats, Label(stats)))
"""


@pytest.mark.parametrize("title", ['f"{label}"', "{label: 0}"])
@pytest.mark.parametrize("strict", [False, True])
def test_a_read_is_not_moved_ahead_of_formatting_or_hashing(
    tmp_path: Path, strict: bool, title: str
) -> None:
    """Review of lane ``byvalue``, defect 4: ``__format__`` and ``__hash__`` may change what is read.

    Here they set ``stats.total`` to 100, so the block prints 101. Read at
    the call, before the label is formatted or hashed, it would print 8.
    """
    source = PROTOCOL_BEFORE_A_READ.replace("TITLE", title)
    generated, same = refactored(tmp_path, source, strict=strict)
    if strict and not _supported_runtime():
        _declined_without_lifetime_proofs(tmp_path, source, generated)
    else:
        assert re.search(r"report\(title, _param_\d+\(\)", generated), generated
    assert same


CALL_AS_AN_ARGUMENT = """\
class Database:
    def paginate(self, query, page):
        print("paginate", query, page)
        return [query, page]


db = Database()


def following_posts():
    print("following")
    return "following"


def index(page):
    posts = db.paginate(following_posts(), page)
    print("index", len(posts))
    return posts


def explore(page, query):
    posts = db.paginate(query, page)
    print("explore", len(posts))
    return posts


print(index(1), explore(2, "everything"))
"""


@pytest.mark.parametrize("lookup", ["paginate", "missing"])
def test_a_call_is_not_moved_ahead_of_the_lookup_of_its_own_callee(
    tmp_path: Path, lookup: str
) -> None:
    """microblog's ``db.paginate(current_user.following_posts(), …)`` against ``db.paginate(query, …)``.

    The lookup of ``db.paginate`` comes first and can fail. If it does, the
    block raises ``AttributeError`` without calling ``following_posts``; at
    the call, ``following_posts`` would run first (review of lane
    ``byvalue``, ruling 5).
    """
    source = CALL_AS_AN_ARGUMENT.replace("db.paginate(", f"db.{lookup}(").replace(
        'print(index(1), explore(2, "everything"))',
        'try:\n    print(index(1), explore(2, "everything"))\n'
        'except AttributeError as error:\n    print("AttributeError")',
    )
    generated, same = refactored(tmp_path, source)
    assert "lambda: following_posts()" in generated
    assert same


PLAIN_READ_AFTER_A_FAILING_STEP = """\
class Stats:
    def __init__(self):
        self.total = 7
        self.count = 2


def report(label, value):
    print(label, value)
    return value


def show_total(stats, label):
    title = label[0]
    shown = report(title, stats.total)
    print("done", title)
    return shown


def show_count(stats, label):
    title = label[0]
    shown = report(title, stats.count)
    print("done", title)
    return shown


print(show_total(Stats(), "t"), show_count(Stats(), "c"))
for function in (show_total, show_count):
    try:
        function(None, [])
    except Exception as error:
        print(type(error).__name__)
"""


@pytest.mark.parametrize("strict", [False, True])
def test_a_plain_read_is_not_moved_ahead_of_a_step_that_can_fail(
    tmp_path: Path, strict: bool
) -> None:
    """Review of lane ``byvalue``, defect 6: ``label[0]`` fails first, with ``IndexError``.

    Read at the call, ``stats.total`` on ``None`` raised ``AttributeError``
    instead. A plain value has no side effects; it can still fail.
    """
    generated, same = refactored(tmp_path, PLAIN_READ_AFTER_A_FAILING_STEP, strict=strict)
    if strict and not _supported_runtime():
        _declined_without_lifetime_proofs(tmp_path, PLAIN_READ_AFTER_A_FAILING_STEP, generated)
    else:
        assert re.search(r"report\(title, _param_\d+\(\)\)", generated), generated
    assert same


CALL_AFTER_AN_EARLIER_STATEMENT = """\
class Loud:
    def __init__(self, value):
        self.value = value

    def alpha(self):
        print("alpha", self.value)
        return self.value

    def beta(self):
        print("beta", self.value)
        return self.value * 2

    @property
    def prop(self):
        print("prop", self.value)
        return self.value


def first(p, o):
    base = FIRST
    y = o.alpha()
    z = base + y
    print("first", y, z)
    return z


def second(p, o):
    base = FIRST
    y = -o.beta()
    z = base + y
    print("second", y, z)
    return z


for function in (first, second):
    try:
        print(function(Loud(1), Loud(3)))
    except NameError as error:
        print("NameError", error)
"""


@pytest.mark.parametrize("first", ["p.prop", "NOT_DEFINED", "p.table['missing']"])
def test_a_call_is_not_moved_ahead_of_an_earlier_statement_that_can_fail(
    tmp_path: Path, first: str
) -> None:
    """``base = …`` raises, or prints, before ``o.alpha()`` runs; at the call, ``alpha`` would run first."""
    source = CALL_AFTER_AN_EARLIER_STATEMENT.replace("FIRST", first)
    generated, same = refactored(tmp_path, source)
    # The generated parameter is spelled __param_0 when it comes first and
    # _param_0 after an ordinary one (shape, for a project with no declared floor).
    assert "lambda: o.alpha()" in generated
    assert re.search(r"y = _{1,2}param_0\(\)", generated), generated
    assert same


POSSIBLY_UNBOUND_READ = """\
def first(flag, items):
    if flag:
        bonus = 10
    try:
        total = items[0] + bonus + items[1]
        print("total", total)
        print("done", total * 2)
        return total
    except IndexError:
        return "empty"


def second(flag, items, bonus):
    try:
        total = items[0] + bonus + items[1]
        print("total", total)
        print("done", total * 2)
        return total
    except IndexError:
        return "empty"


print(first(False, []), second(False, [], 3))
print(first(True, [1, 2]), second(True, [1, 2], 3))
"""


@pytest.mark.parametrize("strict", [False, True])
def test_a_read_of_a_possibly_unbound_local_stays_where_it_was(
    tmp_path: Path, strict: bool
) -> None:
    """Review of lane ``expressions``, defect 2: ``bonus`` was unwrapped and read at the call.

    ``items[0]`` raises ``IndexError``, which the block catches, before it
    reads ``bonus``; read at the call, the unbound ``bonus`` raised
    ``UnboundLocalError`` first.
    """
    generated, same = refactored(tmp_path, POSSIBLY_UNBOUND_READ, strict=strict)
    assert same


REBOUND_MODULE_FUNCTION = """\
import sys


def plain(text):
    return text


def loud(text):
    return text.upper()


def quiet(text):
    return text.lower()


def swap():
    module = sys.modules[__name__]
    module.plain = loud
    module.quiet = loud
    print("swapped")


def render_plain(text, hook):
    hook()
    print("rendering")
    return plain(text)


def render_quiet(text, hook):
    hook()
    print("rendering")
    return quiet(text)


print(render_plain("Hello", lambda: None))
print(render_quiet("Hello", lambda: None))
print(render_plain("Hello", swap))
print(render_quiet("Hello", swap))
"""


def test_strict_mode_reads_a_rebound_module_function_where_the_block_did(tmp_path: Path) -> None:
    """``hook()`` replaces ``plain`` before the block calls it; strict mode's thunk sees the new one."""
    generated, same = refactored(tmp_path, REBOUND_MODULE_FUNCTION, strict=True)
    assert "lambda: plain" in generated and "return _param_0()(text)" in generated
    assert same


def test_relaxed_mode_assumes_module_functions_are_not_rebound(tmp_path: Path) -> None:
    """The third assumption, stated by its counterexample.

    Relaxed mode passes ``plain`` itself. A program that replaces a
    module's function while a helper holding the old one runs then calls
    the old one: the third line printed is ``Hello`` where the original
    prints ``HELLO``. Such a program needs ``--strict``.
    """
    generated, same = refactored(tmp_path, REBOUND_MODULE_FUNCTION)
    assert helper_calls(generated) == [
        "__extracted_func_0(text, hook, plain)",
        "__extracted_func_0(text, hook, quiet)",
    ]
    assert not same
    directory = tmp_path / "relaxed"
    before = run_script(directory / "original.py")[1].splitlines()
    after = run_script(directory / "changed.py")[1].splitlines()
    assert before[5:7] == ["rendering", "HELLO"]
    assert after[5:7] == ["rendering", "Hello"]
    assert before[:5] == after[:5] and before[7:] == after[7:]


MODULE_CODE_RUN_EARLY = """\
def first(enabled):
    print("start")
    if enabled:
        print(alpha(1))
    print("end")
    return 0


def second(enabled):
    print("start")
    if enabled:
        print(beta(1))
    print("end")
    return 0


EARLY


def alpha(value):
    return value + 1


def beta(value):
    return value + 2


print(first(True), second(True))
"""


@pytest.mark.parametrize(
    "early",
    [
        "def trigger(value=first(False)):\n    pass",
        "def trigger(*, value=first(False)):\n    pass",
        "def run(function):\n    function(False)\n    return function\n\n\n"
        "@run\ndef trigger(enabled):\n    pass",
        "class Trigger:\n    value = first(False)",
        "class Base:\n    def __init_subclass__(cls):\n        first(False)\n\n\n"
        "class Trigger(Base):\n    pass",
    ],
)
@pytest.mark.parametrize("strict", [False, True])
def test_a_module_name_is_not_read_early_where_module_code_runs_the_block_first(
    tmp_path: Path, strict: bool, early: str
) -> None:
    """Review of lane ``byvalue``, defect 7: a default ran ``first`` before ``alpha`` existed.

    The block skips ``alpha(1)`` when ``enabled`` is false; passed by value,
    ``alpha`` was read at the call and raised ``NameError``. Defaults,
    decorators, class bodies and base classes all run code at definition.
    """
    source = MODULE_CODE_RUN_EARLY.replace("EARLY", early)
    generated, same = refactored(tmp_path, source, strict=strict)
    assert "(alpha)" not in generated and "(alpha," not in generated
    assert same


CIRCULAR_IMPORT = MODULE_CODE_RUN_EARLY.replace("EARLY", "import trigger")
CIRCULAR_TRIGGER = 'from case import first\nprint("during import", first(False))\n'


@pytest.mark.parametrize("strict", [False, True])
def test_an_import_may_call_back_into_the_module_before_its_later_names_exist(
    tmp_path: Path, strict: bool
) -> None:
    """Round-2 review of lane ``byvalue``, defect 1 (case N1): a circular import.

    ``import trigger`` imports ``case`` while ``case`` is still running, and
    calls ``first(False)`` before ``alpha`` is defined. Passed by value,
    ``alpha`` was read at the call and raised ``NameError``.
    """
    directories = {name: tmp_path / name for name in ("original", "changed")}
    for directory in directories.values():
        directory.mkdir()
        (directory / "case.py").write_text(CIRCULAR_IMPORT)
        (directory / "trigger.py").write_text(CIRCULAR_TRIGGER)
        (directory / "driver.py").write_text("import case\n")
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False, strict=strict)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_to_fixed_point(str(directories["changed"] / "case.py"))
    generated = (directories["changed"] / "case.py").read_text()
    assert "_extracted_func_0" in generated
    assert not _passes_alpha_by_value(generated), generated
    before = run_script(directories["original"] / "driver.py")
    assert before[0] == 0 and "during import 0" in before[1]
    assert run_script(directories["changed"] / "driver.py") == before


DEFINED_INSIDE = """\
def run_first(target):
    print("decorated", target.first(False))
    return target


def run_false(function):
    print("decorated", function(False))
    return function


OUTER


def alpha(value):
    return value + 1


def beta(value):
    return value + 2


print(RUNNERfirst(True), RUNNERsecond(True))
"""

DUPLICATES = """\
def first(enabled):
    print("start")
    if enabled:
        print(alpha(1))
    print("end")
    return 0


def second(enabled):
    print("start")
    if enabled:
        print(beta(1))
    print("end")
    return 0
"""


def _indented(text: str, depth: int) -> str:
    return "".join(
        ("    " * depth + line if line.strip() else line) for line in text.splitlines(True)
    )


@pytest.mark.parametrize(
    "outer, runner",
    [
        # The review's case N2: the class body calls the method it has just defined.
        (
            "class Runner:\n"
            + _indented(DUPLICATES, 1)
            + '\n    print("during class", first(False))',
            "Runner.",
        ),
        # A nested class, called from the rest of the enclosing class's body.
        (
            "class Outer:\n    class Runner:\n"
            + _indented(DUPLICATES, 2)
            + '\n    print("during outer", Runner.first(False))',
            "Outer.Runner.",
        ),
        # A nested class, called from its own body.
        (
            "class Outer:\n    class Runner:\n"
            + _indented(DUPLICATES, 2)
            + '\n        print("during inner", first(False))',
            "Outer.Runner.",
        ),
        # A decorator of the class calls the method as the class is made.
        ("@run_first\nclass Runner:\n" + _indented(DUPLICATES, 1), "Runner."),
        # A decorator of the function itself calls it as soon as it exists.
        # (``second`` comes first: a decorated definition after ``first`` would stop its scan.)
        (
            "@run_false\n" + DUPLICATES.split("\n\n\n")[1] + "\n\n" + DUPLICATES.split("\n\n\n")[0],
            "",
        ),
        # A module-level ``if``, whose body calls the function it defines.
        (
            "if True:\n" + _indented(DUPLICATES, 1) + '\n    print("during if", first(False))',
            "",
        ),
    ],
    ids=[
        "class-body",
        "nested-class-outer",
        "nested-class-inner",
        "class-decorator",
        "function-decorator",
        "module-if",
    ],
)
@pytest.mark.parametrize("strict", [False, True])
def test_a_module_name_is_not_read_early_where_the_enclosing_statement_runs_the_block(
    tmp_path: Path, strict: bool, outer: str, runner: str
) -> None:
    """Round-2 review of lane ``byvalue``, defect 2 (case N2): the rest of an enclosing statement.

    The proof that ``alpha`` exists whenever ``first`` can run scanned only
    the module statements after the whole statement that holds ``first``.
    Code later in that class body, an enclosing class, a class decorator or
    an ``if`` body called ``first(False)`` before ``alpha`` was defined.
    """
    source = DEFINED_INSIDE.replace("OUTER", outer).replace("RUNNER", runner)
    generated, same = refactored(tmp_path, source, strict=strict)
    assert "_extracted_func_0" in generated
    assert not _passes_alpha_by_value(generated), generated
    assert same


def test_a_method_of_a_class_that_runs_nothing_takes_a_later_module_function_by_value(
    tmp_path: Path,
) -> None:
    """The control for the test above: with nothing in the class to call it, ``alpha`` is passed."""
    source = DEFINED_INSIDE.replace("OUTER", "class Runner:\n" + _indented(DUPLICATES, 1))
    generated, same = refactored(tmp_path, source.replace("RUNNER", "Runner."))
    assert _passes_alpha_by_value(generated), generated
    assert same


LOOKUP_IN_A_TRY = """\
class Client:
    def __init__(self, broken=False):
        if not broken:
            self.get_profile = lambda user: {"data": ("profile", user)}
            self.get_preferences = lambda user: {"data": ("preferences", user)}


def fetch_profile(client, user_id):
    try:
        response = client.get_profile(user_id)
    except HANDLED:
        print("no such call", user_id)
        return None
    except ConnectionError as exc:
        print("failed", user_id, exc)
        return None
    return response["data"]


def fetch_preferences(client, user_id):
    try:
        response = client.get_preferences(user_id)
    except HANDLED:
        print("no such call", user_id)
        return None
    except ConnectionError as exc:
        print("failed", user_id, exc)
        return None
    return response["data"]


print(fetch_profile(Client(), 1), fetch_preferences(Client(), 2))
try:
    print(fetch_profile(Client(broken=True), 1), fetch_preferences(Client(broken=True), 2))
except AttributeError as error:
    print("raised", error)
"""


@pytest.mark.parametrize(
    "handled", ["TimeoutError", "(TimeoutError, OSError)", "AttributeError", "Exception"]
)
def test_a_lookup_never_leaves_its_try(tmp_path: Path, handled: str) -> None:
    """Raised at the call, what the lookup raises would escape the handler that caught it before."""
    generated, same = refactored(tmp_path, LOOKUP_IN_A_TRY.replace("HANDLED", handled))
    assert "lambda: client.get_profile" in generated
    assert "response = _param_0()(user_id)" in generated
    assert same


PROPERTY_RAISING_OSERROR = """\
class Client:
    @property
    def get_profile(self):
        raise OSError("unavailable")

    @property
    def get_preferences(self):
        raise OSError("unavailable")


def fetch_profile(client, user_id):
    try:
        response = client.get_profile(user_id)
    except OSError:
        print("no such call", user_id)
        return None
    except ConnectionError as exc:
        print("failed", user_id, exc)
        return None
    return response["data"]


def fetch_preferences(client, user_id):
    try:
        response = client.get_preferences(user_id)
    except OSError:
        print("no such call", user_id)
        return None
    except ConnectionError as exc:
        print("failed", user_id, exc)
        return None
    return response["data"]


try:
    print(fetch_profile(Client(), 7), fetch_preferences(Client(), 8))
except OSError:
    print("escaped OSError")
"""


def test_a_property_that_raises_what_a_handler_catches_stays_in_its_try(tmp_path: Path) -> None:
    """Review of lane ``byvalue``, defect 5: a property without side effects may raise ``OSError``.

    The block's ``except OSError`` handles it; read at the call, it escaped.
    Plain values have no side effects; nothing says which exceptions they raise.
    """
    generated, same = refactored(tmp_path, PROPERTY_RAISING_OSERROR)
    assert "lambda: client.get_profile" in generated
    assert same


GUARDED_CONCATENATION = """\
def run(command):
    print("run", command)
    return 0


def init(lang, log):
    log.append("start")
    status = None
    if lang:
        status = run("init -l " + lang)
    log.append("end")
    return status


def update(lang, log):
    log.append("start")
    status = None
    if lang:
        status = run("update")
    log.append("end")
    return status


print(init("de", []), update("de", []), init(None, []), update(None, []))
"""


def test_a_concatenation_the_block_guards_stays_a_lambda(tmp_path: Path) -> None:
    """``if lang: run("init -l " + lang)``: at the call, ``"init -l " + None`` raises."""
    generated, same = refactored(tmp_path, GUARDED_CONCATENATION)
    assert 'lambda: "init -l " + lang' in generated
    assert same


DISPLAY_IN_A_LOOP = """\
def blank_rows(count):
    rows = []
    for index in range(count):
        print("row", index)
        rows.append([0, 0])
    return rows


def empty_rows(count):
    rows = []
    for index in range(count):
        print("row", index)
        rows.append(None)
    return rows


grid = blank_rows(3)
grid[0][0] = 9
print(grid, empty_rows(2))
"""


@pytest.mark.parametrize("strict", [False, True])
def test_a_list_made_once_per_iteration_stays_a_lambda(tmp_path: Path, strict: bool) -> None:
    """By value, every row would be the same list and ``grid[0][0] = 9`` would change them all."""
    generated, same = refactored(tmp_path, DISPLAY_IN_A_LOOP, strict=strict)
    if strict and not _supported_runtime():
        _declined_without_lifetime_proofs(tmp_path, DISPLAY_IN_A_LOOP, generated)
    else:
        assert "lambda: [0, 0]" in generated and "rows.append(_param_0())" in generated
    assert same


ONE_KIND_FOR_ALL_SITES = """\
class Shape:
    kind = "LineString"


def record(log, value):
    log.append(value)


def point(shape, log):
    record(log, "begin")
    record(log, "Point")
    record(log, "end")
    return log


def line(shape, log):
    record(log, "begin")
    record(log, shape.kind)
    record(log, "end")
    return log


print(point(Shape(), []), line(Shape(), []))
"""


def test_a_parameter_has_one_kind_for_all_its_sites(tmp_path: Path) -> None:
    """``shape.kind`` follows a call, so it is deferred, and the literal at the other site with it."""
    generated, same = refactored(tmp_path, ONE_KIND_FOR_ALL_SITES)
    assert 'lambda: "Point"' in generated and "lambda: shape.kind" in generated
    assert same


# -- Relaxed companions of tests that pin strict mode's late lookups -------------


def test_relaxed_mode_passes_an_imported_name_itself_and_each_caller_passes_its_own(
    tmp_path: Path,
) -> None:
    """Relaxed mode hands over ``join`` as each caller's module binds it when it calls.

    A patch applied to one module before its function runs still reaches
    that function's call and no other (docs/DECISIONS.md, "Relaxed mode
    passes arguments by value").
    """
    before, after = _patched_module_name_project(tmp_path)
    applied, _ = _refactor_modules(after)
    assert applied > 0
    assert _passes_join(after, deferred=False)
    assert not _passes_join(after, deferred=True)
    assert _run_check(after, "check.py") == _run_check(before, "check.py")


@requires_mypy
def test_a_class_passed_by_value_reaches_isinstance_with_its_type(tmp_path: Path) -> None:
    """Relaxed mode passes the class itself; the parameter is the class object, not a constructor."""
    written = _written_annotations_of_the_class_guard(tmp_path)
    assert "type[Alpha] | type[Beta]" in written, written


@requires_mypy
def test_a_class_passed_by_value_shares_its_variable_with_the_list_end_to_end(
    tmp_path: Path,
) -> None:
    """Relaxed mode passes ``Row`` itself; ``self.rows`` follows a call and stays a thunk.

    One type variable still relates the class to the list it fills.
    """
    assert _class_and_list_helper_annotations(tmp_path) == [
        "Element",
        "type[_TowelT0]",
        "_typing.Callable[[], list[_TowelT0]]",
    ]


# -- The parts ------------------------------------------------------------------


def test_stable_module_names_are_definitions_and_imports_nothing_rebinds() -> None:
    module = ast.parse("""\
import json
import os.path
from decimal import Decimal as D

try:
    import tomllib
except ImportError:
    tomllib = None

LIMIT = 3


def keep():
    pass


def swapped():
    pass


def swapper():
    global swapped
    swapped = keep


class Kept:
    def method(self):
        pass


if LIMIT:
    def conditional():
        pass


def later():
    pass


del later
""")
    assert stable_module_names(module) == {"json", "os", "D", "keep", "swapper", "Kept"}


@pytest.mark.parametrize(
    "body, once",
    [
        ("use(p())", True),
        ("if c:\n        use(p())", True),
        ("for x in p():\n        use(x)", True),
        ("for x in xs:\n        pass\n    else:\n        use(p())", True),
        ("return [x for x in p()]", True),
        ("for x in xs:\n        use(p())", False),
        ("while p():\n        pass", False),
        ("return [p() for x in xs]", False),
        ("return [x for x in xs if p()]", False),
        ("return lambda: p()", False),
        ("def inner():\n        return p()\n    return inner", False),
    ],
)
def test_at_most_once_excludes_loops_comprehensions_and_deferred_bodies(
    body: str, once: bool
) -> None:
    helper = ast.parse(f"def h(p, c, xs):\n    {body}\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    assert _evaluated_at_most_once(helper, "p") is once


def test_by_value_checks_share_module_rebindings() -> None:
    """Checking each function can reuse the module's set of rebound builtin names."""
    source = "def padding(): pass\n"
    module = ast.parse(source + "".join(f"def f{i}(): pass\n" for i in range(100)))
    with patch.object(ast, "walk", wraps=ast.walk) as walks:
        for function in module.body[1:]:
            assert isinstance(function, ast.FunctionDef)
            assert "padding" in _stable_names_bound_for(module, function)
    assert walks.call_args_list.count(call(module)) <= 16

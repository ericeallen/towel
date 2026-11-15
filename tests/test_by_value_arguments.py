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
from pathlib import Path
from typing import List

import pytest

from towel.unification.semantic_safety import stable_module_names, unshadowed_names
from towel.unification.thunk_inlining import (
    UNRELATED_HANDLERS,
    _evaluated_at_most_once,
    _plain,
    _simple,
)
from tests.test_callback_parameters import (
    helper_calls,
    refactored,
    refactored_scenario,
    run_script,
)

# The default mode is what this module pins; strict runs ask for it by name.
pytestmark = pytest.mark.relaxed_mode


# -- The audit's material ------------------------------------------------------


@pytest.mark.parametrize(
    "fixture, calls",
    [
        (
            "relaxed_scenarios/callee_user",
            ["__extracted_func_0(net_price, items)", "__extracted_func_0(gross_price, items)"],
        ),
        (
            "by_value/callee_parsers",
            ["__extracted_func_0(parse_user, path)", "__extracted_func_0(parse_product, path)"],
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
    assert "__param_0(" in generated and "__param_0()" not in generated
    assert same


def test_strict_mode_still_defers_a_module_level_function(tmp_path: Path) -> None:
    """Strict mode is 1.949: a callee may rebind the name, so the helper looks it up where the block did."""
    generated, same = refactored_scenario(tmp_path, "by_value/callee_parsers", strict=True)
    assert "lambda: parse_user" in generated and "lambda: parse_product" in generated
    assert same


def test_a_differing_method_of_an_argument_is_passed_as_a_bound_method(tmp_path: Path) -> None:
    """Audit case 21 (engine map D4): 1.949 declined it as ``forwarded_callee``.

    The lookup is the first thing the block does, inside a ``try`` whose
    handlers (``TimeoutError``, ``ConnectionError``) cannot catch what a
    lookup raises, so the bound method is passed by value.
    """
    generated, same = refactored_scenario(tmp_path, "by_value/try_log_callee")
    assert "client.get_profile," in generated and "client.get_preferences," in generated
    assert "lambda" not in generated
    assert "response = __param_0(user_id)" in generated
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


def test_the_microblog_command_string_is_passed_by_value(tmp_path: Path) -> None:
    """The newcomer's real project (``microblog-no-extras.diff``): ``lambda: 'pybabel init …' + lang``.

    A concatenation with a string literal gives a value only from strings,
    and the block reads ``lang`` nowhere else.
    """
    generated, same = refactored(tmp_path, MICROBLOG)
    assert helper_calls(generated) == [
        "__extracted_func_0('pybabel init -i messages.pot -d app/translations -l ' + lang,"
        " 'init command failed')",
        "__extracted_func_0('pybabel update -i messages.pot -d app/translations',"
        " 'update command failed')",
    ]
    assert "lambda" not in generated
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


@pytest.mark.parametrize("strict", [False, True])
def test_csvkits_literals_and_constant_lists_are_passed_by_value(
    tmp_path: Path, strict: bool
) -> None:
    """Gemini's F3: ``lambda: "Point"`` beside ``lambda: [0.0, 0.0]`` in csvkit's tests.

    A display of constants makes a new object, cannot fail and runs
    nothing, so where the helper evaluates it at most once the call may
    make it instead. That holds in strict mode too.
    """
    generated, same = refactored(tmp_path, CSVKIT, strict=strict)
    calls = helper_calls(generated)
    assert len(calls) == 2
    assert "'Point', 'LineString'" in calls[0]
    assert "[0.0, 0.0], [[0.0, 0.0], [1.0, 1.0]]" in calls[1]
    assert "lambda" not in generated
    assert same


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
    assert "lambda: tick()" in generated and "lambda: -tock(100)" in generated
    assert "seen.append((item, __param_0()))" in generated
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
    assert "result = show(label, __param_0())" in generated
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
    assert "report('after settling', __param_0())" in generated
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
        "__extracted_func_0(stats.total, label)",
        "__extracted_func_0(stats.count, label)",
    ]
    assert same


def test_strict_mode_defers_the_attribute_read_before_any_call(tmp_path: Path) -> None:
    """Strict mode assumes nothing of an attribute read: a property may run code."""
    generated, same = refactored(tmp_path, ATTRIBUTE_AFTER_PLAIN_STEPS, strict=True)
    assert "shown = report(title, __param_0())" in generated
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


def test_a_call_is_passed_by_value_ahead_of_the_lookup_of_its_own_callee(tmp_path: Path) -> None:
    """microblog's ``db.paginate(current_user.following_posts(), …)`` against ``db.paginate(query, …)``.

    Before the call the helper has only read ``db``, which the module
    binds, and its attribute ``paginate``.
    """
    generated, same = refactored(tmp_path, CALL_AS_AN_ARGUMENT)
    assert helper_calls(generated) == [
        "__extracted_func_0(following_posts(), 'index', page)",
        "__extracted_func_0(query, 'explore', page)",
    ]
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
    assert "lambda: o.alpha()" in generated and "y = __param_0()" in generated
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
    assert "lambda: plain" in generated and "return __param_0()(text)" in generated
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
        "__extracted_func_0(plain, hook, text)",
        "__extracted_func_0(quiet, hook, text)",
    ]
    assert not same
    directory = tmp_path / "relaxed"
    before = run_script(directory / "original.py")[1].splitlines()
    after = run_script(directory / "changed.py")[1].splitlines()
    assert before[5:7] == ["rendering", "HELLO"]
    assert after[5:7] == ["rendering", "Hello"]
    assert before[:5] == after[:5] and before[7:] == after[7:]


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
    "handled, by_value",
    [
        ("TimeoutError", True),
        ("(TimeoutError, OSError)", True),
        ("AttributeError", False),
        ("Exception", False),
        ("RuntimeError", False),
        ("NotImplementedError", False),
    ],
)
def test_a_lookup_leaves_a_try_only_past_handlers_that_cannot_catch_it(
    tmp_path: Path, handled: str, by_value: bool
) -> None:
    """Raised at the call, an ``AttributeError`` would escape the handler that caught it before."""
    generated, same = refactored(tmp_path, LOOKUP_IN_A_TRY.replace("HANDLED", handled))
    assert same
    if by_value:
        assert "(client.get_profile, user_id)" in generated and "lambda" not in generated.replace(
            "lambda user", ""
        )
    else:
        assert "lambda: client.get_profile" in generated
        assert "response = __param_0()(user_id)" in generated


def test_a_handler_named_like_a_builtin_keeps_the_lambda(tmp_path: Path) -> None:
    """A module that binds ``TimeoutError`` itself may mean any class by it."""
    source = "class TimeoutError(AttributeError):\n    pass\n\n\n" + LOOKUP_IN_A_TRY.replace(
        "HANDLED", "TimeoutError"
    )
    generated, same = refactored(tmp_path, source)
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
    """``if lang: run("init -l " + lang)``: at the call, ``"init -l " + None`` raises.

    The block reads ``lang`` elsewhere, so something in it may be testing
    the operand; the microblog block, which does not, passes its string by
    value.
    """
    generated, same = refactored(tmp_path, GUARDED_CONCATENATION)
    assert "lambda: 'init -l ' + lang" in generated
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
    assert "lambda: [0, 0]" in generated and "rows.append(__param_0())" in generated
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
    assert "lambda: 'Point'" in generated and "lambda: shape.kind" in generated
    assert same


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


def test_a_builtin_name_means_the_builtin_only_where_no_code_binds_it() -> None:
    names = frozenset({"TimeoutError", "OSError"})
    assert unshadowed_names(ast.parse("def f(OSError):\n    pass\n"), names) == {"TimeoutError"}
    assert unshadowed_names(ast.parse("from compat import *\n"), names) == frozenset()
    assert unshadowed_names(ast.parse("x = 1\n"), names) == names


def test_unrelated_handlers_catch_nothing_a_plain_expression_raises() -> None:
    assert {"TimeoutError", "ConnectionError", "OSError", "FileNotFoundError"} <= UNRELATED_HANDLERS
    assert not UNRELATED_HANDLERS & {
        "Exception",
        "BaseException",
        "AttributeError",
        "KeyError",
        "LookupError",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
        "RuntimeError",
        "NotImplementedError",
        "ReferenceError",
        "MemoryError",
        "DeprecationWarning",
    }


def _expression(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body


@pytest.mark.parametrize(
    "text, simple",
    [
        ("'init -l ' + lang", True),
        ("prefix + 'x' + lang", True),
        ("b'x' + lang", True),
        ("-lang", True),
        ("('a' + lang, 1)", True),
        ("lang + other", False),  # lists concatenate too, and lists change
        ("'x' * lang", False),
        ("'%s' % lang", False),  # formats any object
        ("not lang", False),  # the truth of a container changes as it is filled
        ("'x' + lang.name", False),
        ("'x' + unknown", False),
        ("'x' + f(lang)", False),
    ],
)
def test_simple_computations_are_literal_concatenations_and_signs(text: str, simple: bool) -> None:
    assert _simple(_expression(text), frozenset({"lang", "prefix", "other"})) is simple


@pytest.mark.parametrize(
    "text, plain",
    [
        ("a.b.c[0] + d", True),
        ("f'{a.b}: {c}'", True),
        ("a if b else c", True),
        ("[a, {'k': b}]", True),
        ("lambda x: g(x)", True),  # made, not run
        ("a.b()", False),
        ("[*a]", False),
        ("[x for x in a]", False),
        ("{**a}", False),
        ("lambda x=g(): x", False),
        ("(y := a)", False),
    ],
)
def test_plain_expressions_call_nothing_and_iterate_nothing(text: str, plain: bool) -> None:
    assert _plain(_expression(text)) is plain


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

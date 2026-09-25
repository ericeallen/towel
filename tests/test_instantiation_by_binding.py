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

"""The instantiation check compares by binding, and refuses a substitution that captures.

It applied the hygienic renames to every identifier of the reduced helper,
so a template name the extractor left behind (a format spec's ``width``, a
loop target's ``box``) compared equal to the site's ``size`` or ``target``,
and at the template's own site the spelling matched while the helper read a
global where the block read its local. It substituted arguments under a
lambda that bound their names, so ``lambda order: order["total"]`` with the
argument ``order`` reduced to the block exactly. Each wrong helper below is
hand-made, so the check is tested without the extractor: with both fixed,
a future extractor bug of this class is caught before it is offered.
"""

from __future__ import annotations

import ast
import textwrap
from typing import Dict, FrozenSet, Mapping, NamedTuple, Optional

import pytest

from tests.test_helpers import function_def
from towel.unification.instantiation import instantiation_mismatch


def _parsed(source: str) -> list[ast.stmt]:
    return list(ast.parse(textwrap.dedent(source)).body)


class _Case(NamedTuple):
    helper: str
    call: str
    block: str
    site_function_names: FrozenSet[str]
    template_renames: Mapping[str, str] = {}
    block_renames: Mapping[str, str] = {}


def _mismatch(case: _Case) -> Optional[str]:
    return instantiation_mismatch(
        function_def(textwrap.dedent(case.helper)),
        ast.parse(case.call).body[0],
        _parsed(case.block),
        case.template_renames,
        case.block_renames,
        site_function_names=case.site_function_names,
        preamble_length=0,
        returns_variables=False,
    )


FSTRING_HELPER = "def h(__param_0, __param_1):\n    text = f'[{__param_0:>{width}}]'\n"
FOR_TARGET_HELPER = (
    "def h(__param_0, __param_1):\n    for box.v in __param_1:\n        print(__param_0.v)\n"
)
WITH_TARGET_HELPER = (
    "def h(__param_0, __param_1):\n    with __param_1 as box.v:\n        print(__param_0.v)\n"
)
LAMBDA_HELPER = (
    "def h(__param_0, __param_1):\n"
    "    best = max(__param_0, key=lambda order: __param_1['total'])\n"
)
# The hygienic renames the unifier records when the sites' names differ.
FSTRING_RENAMES = ({"width": "__c0", "value": "__c1"}, {"size": "__c0", "item": "__c1"})
FOR_RENAMES = ({"box": "__c0"}, {"target": "__c0"})
LAMBDA_RENAMES = ({"order": "__c0"}, {"quote": "__c0"})

WRONG: Dict[str, _Case] = {
    # P1-01: the template's name is left in a format spec. At the other
    # site the renaming ``size -> width`` once made it compare equal; at the
    # template's own site it is spelled alike but is a local there and the
    # helper's global.
    "format_spec_at_the_other_site": _Case(
        FSTRING_HELPER,
        "h(item, size)",
        "text = f'[{item:>{size}}]'",
        frozenset({"item", "size"}),
        *FSTRING_RENAMES,
    ),
    "format_spec_at_the_template_site": _Case(
        FSTRING_HELPER,
        "h(value, width)",
        "text = f'[{value:>{width}}]'",
        frozenset({"value", "width"}),
        FSTRING_RENAMES[0],
        FSTRING_RENAMES[0],
    ),
    # P1-02: the template's name is left in a loop or ``with`` target.
    "for_target_at_the_other_site": _Case(
        FOR_TARGET_HELPER,
        "h(target, values)",
        "for target.v in values:\n    print(target.v)",
        frozenset({"target", "values"}),
        *FOR_RENAMES,
    ),
    "for_target_at_the_template_site": _Case(
        FOR_TARGET_HELPER,
        "h(box, items)",
        "for box.v in items:\n    print(box.v)",
        frozenset({"box", "items"}),
        FOR_RENAMES[0],
        FOR_RENAMES[0],
    ),
    "with_target_at_the_template_site": _Case(
        WITH_TARGET_HELPER,
        "h(box, ctx)",
        "with ctx as box.v:\n    print(box.v)",
        frozenset({"box", "ctx"}),
    ),
    "comprehension_target_at_the_template_site": _Case(
        "def h(__param_0, items):\n    got = [__param_0.v for box.v in items]\n",
        "h(box, items)",
        "got = [box.v for box.v in items]",
        frozenset({"box", "items"}),
    ),
    # P1-03: an argument substituted under a lambda that binds its name.
    "lambda_capture_at_the_template_site": _Case(
        LAMBDA_HELPER,
        "h(orders, order)",
        "best = max(orders, key=lambda order: order['total'])",
        frozenset({"orders", "order"}),
        LAMBDA_RENAMES[0],
        LAMBDA_RENAMES[0],
    ),
    "lambda_capture_at_the_other_site": _Case(
        LAMBDA_HELPER,
        "h(quotes, quote)",
        "best = max(quotes, key=lambda quote: quote['total'])",
        frozenset({"quotes", "quote"}),
        *LAMBDA_RENAMES,
    ),
    "comprehension_capture": _Case(
        "def h(__param_0, rows):\n    got = [__param_0 for order in rows]\n",
        "h(order, rows)",
        "got = [order for order in rows]",
        frozenset({"order", "rows"}),
    ),
    "nested_function_capture": _Case(
        "def h(__param_0):\n    def get(order):\n        return __param_0\n",
        "h(order)",
        "def get(order):\n    return order",
        frozenset({"order"}),
    ),
    "helper_local_captures_an_argument": _Case(
        "def h(__param_0, xs):\n    for item in xs:\n        use(item, __param_0)\n",
        "h(item, xs)",
        "for x in xs:\n    use(x, item)",
        frozenset({"item", "xs", "x"}),
    ),
    # A thunk's argument is the helper's, placed under the thunk's own
    # comprehension that binds the same spelling.
    "thunk_argument_captured_inside_the_thunk": _Case(
        "def h(__param_0, k, r):\n    got = __param_0(k)\n",
        "h(lambda item: [item * k for k in r], k, r)",
        "got = [k * k for k in r]",
        frozenset({"k", "r"}),
    ),
    # A renamed binder matches only its own occurrences: a comprehension's
    # variable is not a free name spelled like it after the comprehension.
    "renamed_binder_is_not_a_free_namesake": _Case(
        "def h(xs):\n    got = [x for x in xs]\n    use(x)\n",
        "h(xs)",
        "got = [y for y in xs]\nuse(y)",
        frozenset({"xs", "y"}),
    ),
    # A name the helper reads free is its module's; the site's is its local.
    "free_name_the_site_binds_locally": _Case(
        "def h(rows):\n    use(rows, limit)\n",
        "h(rows)",
        "use(rows, limit)",
        frozenset({"rows", "limit"}),
    ),
}


@pytest.mark.parametrize("case", sorted(WRONG))
def test_r9sb_the_check_refuses_each_wrong_helper(case: str) -> None:
    assert _mismatch(WRONG[case]) is not None


RIGHT: Dict[str, _Case] = {
    "format_spec": _Case(
        "def h(__param_0, __param_1):\n    text = f'[{__param_0:>{__param_1}}]'\n",
        "h(item, size)",
        "text = f'[{item:>{size}}]'",
        frozenset({"item", "size"}),
        *FSTRING_RENAMES,
    ),
    "for_target": _Case(
        "def h(__param_0, __param_1):\n    for __param_0.v in __param_1:\n        print(__param_0.v)\n",
        "h(target, values)",
        "for target.v in values:\n    print(target.v)",
        frozenset({"target", "values"}),
        *FOR_RENAMES,
    ),
    "lambda_keeps_its_parameter": _Case(
        "def h(__param_0, __param_1):\n"
        "    best = max(__param_0, key=lambda order: order['total'])\n"
        "    print(__param_1['id'])\n",
        "h(quotes, quote)",
        "best = max(quotes, key=lambda quote: quote['total'])\nprint(quote['id'])",
        frozenset({"quotes", "quote"}),
        *LAMBDA_RENAMES,
    ),
    "free_name_read_from_the_module": _Case(
        "def h(rows):\n    use(rows, LIMIT)\n",
        "h(rows)",
        "use(rows, LIMIT)",
        frozenset({"rows"}),
    ),
    "thunk_argument_outside_the_thunks_scopes": _Case(
        "def h(__param_0, k, r):\n    got = __param_0(k)\n",
        "h(lambda item: [item * j for j in r], k, r)",
        "got = [k * j for j in r]",
        frozenset({"k", "r"}),
    ),
}


@pytest.mark.parametrize("case", sorted(RIGHT))
def test_r9sb_the_check_accepts_the_same_helpers_written_right(case: str) -> None:
    assert _mismatch(RIGHT[case]) is None

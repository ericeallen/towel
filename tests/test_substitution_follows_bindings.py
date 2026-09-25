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

"""Parameter substitution follows Python's bindings.

The round-4 audit found three ways the extractor wrote a helper that raised
``NameError`` or computed something else:

- a name in an f-string's format specification was not substituted
  (``f"[{value:>{width}}]"``, P1-01);
- the loads inside a ``for``, ``with`` or comprehension target were not
  (``for box.v in items``, P1-02);
- a lambda parameter spelled like a name that became a parameter was
  substituted inside the lambda (``key=lambda order: order["total"]``, P1-03).

The tables below pin what the substituter replaces at every binder spelling
and every expression position; ``tests/test_instantiation_by_binding.py``
pins, independently, that the check refuses a helper of each wrong shape.
"""

from __future__ import annotations

import ast
import re
import sys
import textwrap
from typing import Dict

import pytest

from towel.unification.extractor import HygienicExtractor, UnsupportedExtraction
from towel.unification.substitution import Substitution

PARAMETER = "__param_0"


def _parsed(source: str) -> list[ast.stmt]:
    return list(ast.parse(textwrap.dedent(source)).body)


def _substituted(source: str, name: str) -> str:
    """The helper body the extractor writes when block 0's ``name`` is ``__param_0``."""
    substitution = Substitution()
    substitution.add_mapping(0, ast.Name(id=name, ctx=ast.Load()), PARAMETER)
    helper, _ = HygienicExtractor().extract_function(
        template_block=_parsed(source),
        substitution=substitution,
        free_variables=set(),
        enclosing_names=set(),
        is_value_producing=False,
    )
    return ast.unparse(ast.Module(body=helper.body, type_ignores=[]))


def _expected(source: str) -> str:
    """``source`` with each ``P`` standing for the parameter, as ``ast.unparse`` writes it."""
    return ast.unparse(ast.parse(re.sub(r"\bP\b", PARAMETER, textwrap.dedent(source))))


# Each binder spelling of ``order`` against a block whose free ``order`` is
# the parameter: the binder's own occurrences stay, every other read is P.
BINDERS: Dict[str, tuple[str, str]] = {
    "lambda_positional": (
        "key = lambda order: order + 1\nuse(order, key)",
        "key = lambda order: order + 1\nuse(P, key)",
    ),
    "lambda_default": (
        "key = lambda x, order=order: order + x",
        "key = lambda x, order=P: order + x",
    ),
    "lambda_star_args": (
        "key = lambda *order: order\nuse(order)",
        "key = lambda *order: order\nuse(P)",
    ),
    "lambda_star_star_kw": (
        "key = lambda **order: order\nuse(order)",
        "key = lambda **order: order\nuse(P)",
    ),
    "lambda_keyword_only": (
        "key = lambda *, order: order\nuse(order)",
        "key = lambda *, order: order\nuse(P)",
    ),
    "lambda_keyword_only_default": ("key = lambda *, x=order: x", "key = lambda *, x=P: x"),
    "lambda_positional_only": (
        "key = lambda order, /: order\nuse(order)",
        "key = lambda order, /: order\nuse(P)",
    ),
    "lambda_reads_it_free": ("key = lambda x: order + x", "key = lambda x: P + x"),
    "lambda_inside_lambda": (
        "key = lambda x: (lambda order: order)(order)",
        "key = lambda x: (lambda order: order)(P)",
    ),
    "lambda_walrus": ("key = lambda: (order := 1) + order", "key = lambda: (order := 1) + order"),
    "comprehension_target": (
        "got = [order for order in rows]\nuse(order)",
        "got = [order for order in rows]\nuse(P)",
    ),
    "comprehension_first_iterable_outside": (
        "got = [order for order in order]",
        "got = [order for order in P]",
    ),
    "comprehension_later_iterable_inside": (
        "got = [x for order in rows for x in order]",
        "got = [x for order in rows for x in order]",
    ),
    "comprehension_condition_inside": (
        "got = [x for x, order in rows if order]",
        "got = [x for x, order in rows if order]",
    ),
    "dict_comprehension": (
        "got = {order: order for order in rows}\nuse(order)",
        "got = {order: order for order in rows}\nuse(P)",
    ),
    "set_comprehension_reads_it_free": (
        "got = {x + order for x in rows}",
        "got = {x + P for x in rows}",
    ),
    "generator": ("got = sum(order for order in rows)", "got = sum(order for order in rows)"),
    "walrus_in_comprehension": (
        "use(order)\ngot = [(order := x) for x in rows]\nuse(order)",
        "use(P)\ngot = [(order := x) for x in rows]\nuse(order)",
    ),
    "nested_def_parameter": (
        "def get(order):\n    return order\nuse(get(order))",
        "def get(order):\n    return order\nuse(get(P))",
    ),
    "nested_def_local": (
        "def get():\n    order = 1\n    return order\nuse(get)",
        "def get():\n    order = 1\n    return order\nuse(get)",
    ),
    "nested_def_reads_it_free": ("def get():\n    return order", "def get():\n    return P"),
    "nested_def_global": (
        "def get():\n    global order\n    return order\nuse(order)",
        "def get():\n    global order\n    return order\nuse(P)",
    ),
    "async_def_parameter": (
        "async def get(order):\n    return order\nuse(get, order)",
        "async def get(order):\n    return order\nuse(get, P)",
    ),
    "class_body_binding": (
        "class K:\n    order = 1\n    value = order\nuse(order)",
        "class K:\n    order = 1\n    value = order\nuse(P)",
    ),
    "class_body_reads_it_free": ("class K:\n    value = order", "class K:\n    value = P"),
    "method_looks_past_the_class": (
        "class K:\n    order = 1\n    def get(self):\n        return order",
        "class K:\n    order = 1\n    def get(self):\n        return P",
    ),
    "comprehension_looks_past_the_class": (
        "class K:\n    order = 1\n    got = [order for x in rows]",
        "class K:\n    order = 1\n    got = [P for x in rows]",
    ),
    "match_capture": (
        "match rows:\n    case [order]:\n        use(order)\n    case _:\n        use(order)",
        "match rows:\n    case [order]:\n        use(order)\n    case _:\n        use(P)",
    ),
    "match_capture_in_guard": (
        "match rows:\n    case [order] if order:\n        use(1)",
        "match rows:\n    case [order] if order:\n        use(1)",
    ),
    "match_star_and_rest": (
        "match rows:\n    case [*order]:\n        use(order)\n    case {**order}:\n        use(order)",
        "match rows:\n    case [*order]:\n        use(order)\n    case {**order}:\n        use(order)",
    ),
    "except_name": (
        "try:\n    use(order)\nexcept E as order:\n    use(order)\nexcept F:\n    use(order)",
        "try:\n    use(P)\nexcept E as order:\n    use(order)\nexcept F:\n    use(P)",
    ),
    "for_target": ("for order in rows:\n    use(order)", "for order in rows:\n    use(order)"),
    "starred_target": ("first, *order = rows\nuse(order)", "first, *order = rows\nuse(order)"),
    "with_target": ("with ctx as order:\n    use(order)", "with ctx as order:\n    use(order)"),
    "import_binding": ("import order\nuse(order)", "import order\nuse(order)"),
}


@pytest.mark.parametrize("case", sorted(BINDERS))
def test_r9sb_a_binder_keeps_its_own_occurrences(case: str) -> None:
    source, expected = BINDERS[case]
    assert _substituted(source, "order") == _expected(expected)


def test_r9sb_a_nested_scope_declaring_a_parameter_nonlocal_is_not_extracted() -> None:
    with pytest.raises(UnsupportedExtraction):
        _substituted("def get():\n    nonlocal order\n    order = 1\nuse(order)", "order")


# Each expression position that reads ``box``: every read there is P,
# including the loads inside a binding target, and the only unevaluated
# annotation (a function-body variable's) keeps its spelling.
POSITIONS: Dict[str, tuple[str, str]] = {
    "format_spec": ('text = f"[{v:>{box}}]"', 'text = f"[{v:>{P}}]"'),
    "nested_format_spec": ('text = f"[{v:>{w:{box}}}]"', 'text = f"[{v:>{w:{P}}}]"'),
    "two_fields_in_a_format_spec": ('text = f"[{v:{box}^{box}}]"', 'text = f"[{v:{P}^{P}}]"'),
    "value_conversion_and_spec": ('text = f"{box!r:>{box}}"', 'text = f"{P!r:>{P}}"'),
    "fstring_inside_a_field": ("text = f\"{f'{box}'}\"", "text = f\"{f'{P}'}\""),
    "for_attribute_target": (
        "for box.v in items:\n    use(box.v)",
        "for P.v in items:\n    use(P.v)",
    ),
    "for_subscript_target": ("for box[0] in items:\n    pass", "for P[0] in items:\n    pass"),
    "for_subscript_index": ("for rows[box] in items:\n    pass", "for rows[P] in items:\n    pass"),
    "for_tuple_target": ("for a, box.v in items:\n    use(a)", "for a, P.v in items:\n    use(a)"),
    "with_attribute_target": (
        "with ctx as box.v:\n    use(box.v)",
        "with ctx as P.v:\n    use(P.v)",
    ),
    "with_tuple_target": (
        "with ctx as (a, box[0]):\n    use(a)",
        "with ctx as (a, P[0]):\n    use(a)",
    ),
    "comprehension_attribute_target": (
        "got = [box.v for box.v in items]",
        "got = [P.v for P.v in items]",
    ),
    "comprehension_subscript_target": (
        "got = [1 for box[0] in items]",
        "got = [1 for P[0] in items]",
    ),
    "del_attribute": ("del box.v", "del P.v"),
    "del_subscript": ("del box[0], rows[box]", "del P[0], rows[P]"),
    "augmented_attribute": ("box.v += 1", "P.v += 1"),
    "augmented_subscript_index": ("rows[box] += 1", "rows[P] += 1"),
    "assigned_attribute": ("box.v = 1", "P.v = 1"),
    "starred_attribute_target": ("first, *box.v = rows", "first, *P.v = rows"),
    "annotated_attribute_target": ("box.v: int = 1", "P.v: int = 1"),
    "annotation_in_a_function_body": ("value: box = 1", "value: box = 1"),
    "annotation_in_a_class_body": ("class K:\n    value: box = 1", "class K:\n    value: P = 1"),
    "function_decorator": (
        "@box.register\ndef get():\n    return 1",
        "@P.register\ndef get():\n    return 1",
    ),
    "parameter_annotation_and_return": (
        "def get(v: box.T) -> box.T:\n    return v",
        "def get(v: P.T) -> P.T:\n    return v",
    ),
    "class_decorator_bases_and_keywords": (
        "@box\nclass K(box.Base, metaclass=box.Meta):\n    pass",
        "@P\nclass K(P.Base, metaclass=P.Meta):\n    pass",
    ),
    "lambda_default": ("f = lambda v=box: v", "f = lambda v=P: v"),
    "keyword_only_default": (
        "def get(*, v=box):\n    return v",
        "def get(*, v=P):\n    return v",
    ),
    "slice": ("part = rows[box:box + 1]", "part = rows[P:P + 1]"),
    "slice_in_a_target": ("rows[box:] = []", "rows[P:] = []"),
    "match_value_and_class": (
        "match rows:\n    case box.KIND:\n        pass\n    case box.Point(x=0):\n        pass",
        "match rows:\n    case P.KIND:\n        pass\n    case P.Point(x=0):\n        pass",
    ),
    "match_guard": (
        "match rows:\n    case [x] if box:\n        pass",
        "match rows:\n    case [x] if P:\n        pass",
    ),
    "walrus_value": ("if (n := box.size):\n    use(n)", "if (n := P.size):\n    use(n)"),
    "except_type": (
        "try:\n    pass\nexcept box.Error:\n    pass",
        "try:\n    pass\nexcept P.Error:\n    pass",
    ),
    "except_star_type": (
        "try:\n    pass\nexcept* box.Error as e:\n    use(e)",
        "try:\n    pass\nexcept* P.Error as e:\n    use(e)",
    ),
}


PEP_701_ONLY = frozenset({"nested_format_spec"})
"""A format spec nested two deep parses only from Python 3.12 (PEP 701)."""


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            case,
            marks=(
                [pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 701 f-string")]
                if case in PEP_701_ONLY
                else []
            ),
        )
        for case in sorted(POSITIONS)
    ],
)
def test_r9sb_every_evaluated_position_is_substituted(case: str) -> None:
    source, expected = POSITIONS[case]
    assert _substituted(source, "box") == _expected(expected)

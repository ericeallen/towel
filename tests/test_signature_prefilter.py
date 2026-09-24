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

from tests.test_helpers import parse_block
from towel.unification.block_signature import (
    extract_block_signature,
    quick_filter,
    IDENT_COUNT_TOLERANCE,
)


def test_quick_filter_rejects_stmt_count_mismatch():
    b1 = parse_block("a = 1\nb = 2\n")
    b2 = parse_block("a = 1\n")
    s1, s2 = extract_block_signature(b1), extract_block_signature(b2)
    assert quick_filter(s1, s2) is False


def test_quick_filter_rejects_first_last_mismatch():
    b1 = parse_block("a = f(x)\nreturn a\n")
    b2 = parse_block("if True:\n    a = f(x)\nelse:\n    a = f(x)\n")
    s1, s2 = extract_block_signature(b1), extract_block_signature(b2)
    assert quick_filter(s1, s2) is False


def test_quick_filter_within_name_and_call_tolerance_passes():
    # Create blocks with close counts of loads/stores/calls
    b1 = parse_block("""
foo = a + b
bar = foo + c
if cond:
    bar = bar + d
""")
    b2 = parse_block("""
foo = a + b
bar = foo + c
bar = bar + d
x = z
""")
    s1, s2 = extract_block_signature(b1), extract_block_signature(b2)
    # Ensure differences are within tolerance
    assert abs(s1.name_load_count - s2.name_load_count) <= IDENT_COUNT_TOLERANCE
    assert abs(s1.name_store_count - s2.name_store_count) <= IDENT_COUNT_TOLERANCE
    assert abs(s1.call_count - s2.call_count) <= IDENT_COUNT_TOLERANCE
    # Statement count differs -> should be rejected even though counts are within tolerance
    assert s1.stmt_count != s2.stmt_count
    assert quick_filter(s1, s2) is False


def test_extract_block_signature_skips_nested_defs():
    # Calls and names inside nested defs/classes should not contribute
    b_outer = parse_block("""
value = top(a)
""")
    b_nested = parse_block("""
value = top(a)

def inner():
    q = inner_call(b)
    return q
""")
    s_outer, s_nested = extract_block_signature(b_outer), extract_block_signature(b_nested)
    # Nested function statement remains in top-level sequence affecting stmt_count so filter rejects
    assert s_outer.name_load_count == s_nested.name_load_count
    assert s_outer.name_store_count == s_nested.name_store_count
    assert s_outer.call_count == s_nested.call_count
    assert s_outer.stmt_count != s_nested.stmt_count
    assert quick_filter(s_outer, s_nested) is False

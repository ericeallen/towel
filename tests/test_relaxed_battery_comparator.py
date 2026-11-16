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

"""Check the relaxed oracle independently of Towel's transformations."""

import pytest

from tests.test_hostile_battery import _events, _relaxed_events


@pytest.mark.parametrize(
    "changed",
    [
        "5 6 ['more', 'work', 'first', 'drop:first']\n",
        "999 888 ['first', 'work', 'more', 'drop:first']\n",
    ],
)
def test_review_oracle_gap_rejects_ordinary_changes(changed: str) -> None:
    """Review defect 2: ordinary call order and printed values remain observable."""
    original = "5 6 ['first', 'work', 'more', 'drop:first']\n"
    assert _events(original) != _events(changed)


def test_finalizers_can_move_without_changing_ordinary_observations() -> None:
    original = "5 6 ['first', 'work', 'more', 'drop:first', 'drop:second']\n"
    changed = "5 6 ['drop:second', 'first', 'drop:first', 'work', 'more']\n"
    assert _events(original) == _events(changed)


@pytest.mark.parametrize(
    "changed",
    [
        "5 6 ['first', 'work', 'more']\n",
        "5 6 ['first', 'work', 'more', 'drop:first', 'drop:first']\n",
        "5 6 ['first', 'work', 'more', 'drop:changed']\n",
        "5 6 ['first', 'work', 'more', 'drop:first', 'unexpected']\n",
    ],
)
def test_finalizer_presence_and_unlisted_effects_still_matter(changed: str) -> None:
    original = "5 6 ['first', 'work', 'more', 'drop:first']\n"
    assert _events(original) != _events(changed)


def test_context_exit_is_an_ordinary_effect() -> None:
    original = "['first', 'work', 'exit:first', 'drop:first']\n"
    reordered = "['first', 'exit:first', 'work', 'drop:first']\n"
    assert _events(original) != _events(reordered)


@pytest.mark.parametrize(
    ("case", "original", "changed"),
    [
        (
            "r1792_factory_finalizer_continuation",
            "1 (4, 'closed', 9) [4, 'closed', 9, 'closed']\n",
            "2 ('closed', 4, 'closed', 9) ['closed', 4, 'closed', 9]\n",
        ),
        (
            "r1792_factory_finalizer_order",
            "1 (4, 'z', 'a', 9) [4, 'z', 'a', 9, 'z', 'a']\n",
            "3 ('a', 'z', 4, 'a', 'z', 9) ['a', 'z', 4, 'a', 'z', 9]\n",
        ),
    ],
)
def test_factory_dependent_results_allow_only_finalizer_variation(
    case: str, original: str, changed: str
) -> None:
    assert _relaxed_events(case, original) == _relaxed_events(case, changed)


@pytest.mark.parametrize(
    "changed",
    [
        "999 (4, 'closed', 9) [4, 'closed', 9, 'closed']\n",
        "1 ('closed', 4, 9) ['closed', 4, 9, 'closed']\n",
        "1 (4, 'closed', 999) [4, 'closed', 9, 'closed']\n",
        "1 (4, 9) [4, 9, 'closed', 'closed']\n",
        "1 (4, 'closed', 9) [4, 'closed', 9]\n",
        "1 (4, 'closed', 9) [4, 9, 'closed', 'closed']\n",
    ],
)
def test_factory_results_are_validated_not_discarded(changed: str) -> None:
    with pytest.raises(AssertionError):
        _relaxed_events("r1792_factory_finalizer_continuation", changed)


def test_factory_ordinary_event_values_and_order_still_matter() -> None:
    case = "r1792_factory_finalizer_continuation"
    original = "1 (4, 'closed', 9) [4, 'closed', 9, 'closed']\n"
    reordered = "1 (9, 'closed', 4) [9, 'closed', 4, 'closed']\n"
    assert _relaxed_events(case, original) != _relaxed_events(case, reordered)

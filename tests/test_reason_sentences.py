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

"""Every reason Towel can leave code alone for has a plain sentence (audit finding 7).

A user whose code was left alone used to get internal codes such as
``owned_binding_frame_boundary``, and the profitability sentence followed
every list whatever its reasons. The table in ``towel.reason_sentences``
must cover every member of both vocabularies, so a new reason cannot ship
without its sentence, and the summary must attach each sentence to its own
reason only.
"""

from __future__ import annotations

import re

import pytest

from scripts import ecosystem_check
from towel.cli_reporting import reason_lines
from towel.reason_sentences import (
    PAIR_REASONS,
    PROPOSAL_REASONS,
    UNTYPEABLE_REASONS,
    explain,
    string_decline_reasons,
)
from towel.unification.exceptions import Untypeable
from towel.unification.fixed_point import RunReport
from towel.unification.models import RejectReason

_ALL = (
    [str(reason) for reason in RejectReason]
    + sorted(string_decline_reasons())
    + [str(reason) for reason in Untypeable]
)


def test_every_reject_reason_has_a_sentence() -> None:
    assert set(PAIR_REASONS) == set(RejectReason)


def test_every_decline_reason_has_a_sentence() -> None:
    assert set(PROPOSAL_REASONS) == string_decline_reasons()
    assert set(UNTYPEABLE_REASONS) == set(Untypeable)


@pytest.mark.parametrize("reason", _ALL)
def test_each_sentence_is_plain_and_complete(reason: str) -> None:
    """A sentence, not a code: it starts with a capital, ends with a stop, and names no code."""
    explanation = explain(reason)
    for text in filter(None, (explanation.sentence, explanation.remedy)):
        assert text[0].isupper() and text.endswith("."), text
        assert not any(str(code) in text for code in RejectReason), text
    assert not explanation.sentence.startswith("Left alone:"), reason


def test_a_named_subject_follows_the_sentence() -> None:
    explanation = explain("builtin_may_differ_by_module[len]")
    assert explanation.sentence.endswith("(Here: len.)")
    assert explanation.remedy == PAIR_REASONS[RejectReason.BUILTIN_MAY_DIFFER_BY_MODULE].remedy


def test_an_unknown_reason_is_still_reported() -> None:
    assert explain("reason_from_the_future").sentence == "Left alone: reason_from_the_future."


def test_each_reason_is_listed_once_most_frequent_first_with_its_own_sentence() -> None:
    """The profitability sentence used to follow every list; now it follows its own reason only."""
    report = RunReport(
        declined_pairs={
            "owned_binding_frame_boundary": 2,
            "unprofitable_helper": 1,
            "builtin_may_differ_by_module[len]": 3,
            "builtin_may_differ_by_module[print]": 1,
        },
        declined_proposals={"unprofitable helper": 2, "refused by the type checker": 1},
    )
    lines = reason_lines(report)
    counted = [line for line in lines if re.match(r"^ +\d+  ", line)]
    assert [int(line.split()[0]) for line in counted] == [4, 3, 2, 1]
    builtins, unprofitable, frame, refused = counted
    assert builtins.endswith("(Here: len, print.) [builtin_may_differ_by_module]")
    # Pairs and proposals declined for the same reason are one line.
    assert unprofitable.endswith("[unprofitable_helper, unprofitable helper]")
    assert "does not repay" in unprofitable
    assert "does not repay" not in "\n".join(line for line in lines if line != unprofitable)
    assert frame.endswith("[owned_binding_frame_boundary]")
    assert refused.endswith("[refused by the type checker]")
    remedy = lines[lines.index(builtins) + 1]
    assert "What you can do: Pass --parameterize-builtins" in remedy


def test_the_ecosystem_harness_reads_the_counts_it_needs() -> None:
    """The corpus harness reads two proposal counts from the run's summary."""
    report = RunReport(
        declined_proposals={
            "not verifiable: its file holds a name the type checker cannot type": 4,
            "not verifiable: the type checker does not look at the code it changes": 2,
        }
    )
    text = "\n".join(reason_lines(report))
    assert ecosystem_check._count_of(ecosystem_check.UNVERIFIABLE.search(text)) == 4
    assert ecosystem_check._count_of(ecosystem_check.UNLOOKED_DECLINED.search(text)) == 2

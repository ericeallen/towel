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

"""What resolving a name's typing forms costs, as a count of the recursion's calls.

A function-body alias of a name to itself, ``tok = tok.next_token``, sends
``origins`` back to that name at every level of its depth bound, once per
binding of the name. yapf binds ``tok`` so in dozens of functions, and one
analysis made 1.7 billion calls there before ``origins`` was kept per name
and depth. The count is what grows; this pins it, and the answer with it.
"""

from __future__ import annotations

from typing import List, Tuple

import pytest

from towel.unification import typing_forms
from towel.unification.typing_forms import _MAX_DEPTH, _module_facts

SELF_ALIASES = "\n".join(
    f"def walk_{index}(tok):\n    while tok:\n        tok = tok.next_token\n    return tok\n"
    for index in range(40)
)


def test_origins_of_a_name_aliased_to_itself_is_computed_once_per_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: List[Tuple[str, int]] = []
    compute = typing_forms._ModuleFacts._origins_of

    def counted(self: typing_forms._ModuleFacts, head: str, depth: int) -> object:
        calls.append((head, depth))
        return compute(self, head, depth)

    monkeypatch.setattr(typing_forms._ModuleFacts, "_origins_of", counted)
    facts = _module_facts(SELF_ALIASES + "\nimport typing\n")
    assert facts is not None
    origins, opaque = facts.origins("tok")
    assert calls == [("tok", depth) for depth in range(_MAX_DEPTH + 1)]
    assert (origins, opaque) == (frozenset(), False)
    assert facts.local_forms("tok") == (frozenset(), False)

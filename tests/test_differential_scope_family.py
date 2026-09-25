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

"""Generated blocks holding their function's only binding of a name keep their behaviour.

The family in :mod:`tests.differential.scope_grammar` crosses every binding
construct with every place code outside a block can read a name through its
function, and with what the name would otherwise be: the round-4 audit's
P1-04, which the main grammar cannot reach. Each seed is refactored as the
other differential tests refactor theirs and compared by the observer and by
the batteries' scope check. Before the fix, 13 of these 40 seeds changed
behaviour; ``just fuzz N SEED --family scope`` runs more.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.differential.runner import DEFAULT, run_case
from tests.differential.scope_grammar import generate_scope_case

SEEDS = range(40)


@pytest.mark.parametrize("seed", SEEDS, ids=lambda seed: f"seed{seed}")
def test_r9bd_generated_only_bindings_keep_their_behaviour(seed: int, tmp_path: Path) -> None:
    outcome = run_case(generate_scope_case(seed), DEFAULT, tmp_path)
    if outcome.status == "unsupported":
        pytest.skip(outcome.detail)
    assert outcome.status in ("unchanged", "equivalent"), outcome.report()


def test_r9bd_the_family_draws_every_binder_and_read() -> None:
    """The seeds the suite runs, and a longer run's, reach every construct the family has."""
    features = set().union(*(generate_scope_case(seed).features for seed in range(400)))
    for prefix, count in (("binder_", 14), ("read_", 10), ("outer_", 4), ("control_", 3)):
        assert len({feature for feature in features if feature.startswith(prefix)}) == count
    assert generate_scope_case(7) == generate_scope_case(7) != generate_scope_case(8)

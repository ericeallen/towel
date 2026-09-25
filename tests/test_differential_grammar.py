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

"""Generated near-duplicate blocks keep their behaviour when Towel refactors them.

Each seed draws one small project from the grammar in
:mod:`tests.differential.grammar`. Towel refactors a copy through its library
entry, as the hostile batteries run it, and the original and the copy are
observed in fresh interpreters and compared (:mod:`tests.differential`). A
failure names the seed in the test id and prints the generated source,
Towel's change and every difference, then the command that reruns it alone.

The seeds are fixed and were chosen by measurement on Python 3.13, where the
whole module runs in well under a minute:

- the default mode (``--no-types``) on seeds 0 to 299, less the two whose
  original program never finishes its probes. About 45% of these extract,
  at roughly 0.18 s each; the rest cost 0.02 s. Their outcomes, and Towel's
  very changes, are the same on 3.11, 3.12 and 3.13 and under any string
  hash seed;
- ``--cross-module`` on the two-module layouts among seeds 0 to 199, where
  the grammar puts sites in different modules;
- the typed mode, with mypy strict, on a handful of typed seeds, which cost
  about a second each;
- and, in each mode, the seeds beyond those ranges on which the default suite
  or a longer fuzz run (:mod:`tests.differential.fuzz`) has found a defect:
  the regression targets.

A seed that exposes a defect not yet fixed is an expected failure, strict,
with the id of the audit that reported it (``KNOWN_DEFECTS``): when its fix
lands the seed passes, the run reports XPASS as a failure, and its entry is
removed. The seed stays in ``REGRESSION_SEEDS``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from _pytest.mark.structures import ParameterSet
import pytest

from tests.differential.grammar import generate_case
from tests.differential.runner import CROSS_MODULE, DEFAULT, Mode, run_case
from tests.hostile_refactoring import with_known_defects

NEVER_FINISH = frozenset({32, 126})
"""Seeds whose original program loops forever: there is nothing to compare."""

DEFAULT_SEEDS: Tuple[int, ...] = tuple(seed for seed in range(300) if seed not in NEVER_FINISH)
CROSS_MODULE_SEEDS: Tuple[int, ...] = tuple(seed for seed in range(200) if seed % 5 in (3, 4))
"""The seeds the grammar lays out as two modules (``seed % 5`` is 3 or 4)."""
TYPED_SEEDS: Tuple[int, ...] = (10016, 10084, 10090, 10302)
"""Typed seeds whose project configures mypy strict (``seed % 4`` is 0 or 2), each extracting:
a method in three sites, a static method holding a nested ``def`` and a walrus, a match
inside a method reading ``self``, and a single file."""

TYPED = Mode(types=True)

REGRESSION_SEEDS: Dict[Mode, Tuple[int, ...]] = {
    DEFAULT: (30, 322, 417, 898, 907, 979, 1356, 1360, 1470, 1582, 1591, 1854, 1921, 1984)
    + (2267, 2319, 2746, 2881, 2882),
    CROSS_MODULE: (474, 624, 1209),
    TYPED: (10108, 10220),
}
"""Seeds on which a defect was found, run in the mode it was found in.

Beside the audit's own (417 and 898, and 474, 624 and 1209 across modules),
they come from a sweep of seeds 0 to 2999 in both modes, made when this
module was written (the audit's first 400 seeds had drawn other cases), and
from one of typed seeds 10000 to 10399. Each seed's defect, read from its
report, was one the round-3 audit reported, and each is fixed:

- P1-1, a for target rebinding a name bound before the block: 898, 1470,
  1854, 2882, and 474, 624 and 1209 across modules;
- P1-2, a block starting or ending inside a ';' line: 979, 1356, 1582,
  1921, 2267, and 10220 typed;
- P1-4, a public name added for a helper's annotation: 10108 typed;
- P1-5, a renamed binder in an UnboundLocalError: 1984;
- P1-7, a read before the block's own binding: 1591;
- P1-8, a later augmented assignment not counted as a read: 30, 322, 417,
  907, 1360, 2319, 2746 and 2881.
"""

KNOWN_DEFECTS: Dict[Mode, Dict[int, str]] = {DEFAULT: {}, CROSS_MODULE: {}, TYPED: {}}
"""Seeds whose defect is reported and not yet fixed, by mode, each with its reason from
``tests/audit_defects.py``. None is open."""


def _seeds(mode: Mode, seeds: Iterable[int]) -> List[ParameterSet]:
    """``seeds`` and the mode's regression seeds, each named ``seed<N>``, known defects expected."""
    return with_known_defects(
        [*seeds, *REGRESSION_SEEDS[mode]], KNOWN_DEFECTS[mode], lambda seed: f"seed{seed}"
    )


def _keeps_behaviour(seed: int, mode: Mode, workspace: Path) -> None:
    outcome = run_case(generate_case(seed, typed=mode.types), mode, workspace)
    if outcome.status == "unsupported":
        pytest.skip(outcome.detail)
    assert outcome.status in ("unchanged", "equivalent"), outcome.report()


@pytest.mark.parametrize("seed", _seeds(DEFAULT, DEFAULT_SEEDS))
def test_generated_duplicates_keep_their_behaviour(seed: int, tmp_path: Path) -> None:
    _keeps_behaviour(seed, DEFAULT, tmp_path)


@pytest.mark.parametrize("seed", _seeds(CROSS_MODULE, CROSS_MODULE_SEEDS))
def test_generated_duplicates_across_modules_keep_their_behaviour(
    seed: int, tmp_path: Path
) -> None:
    _keeps_behaviour(seed, CROSS_MODULE, tmp_path)


@pytest.mark.parametrize("seed", _seeds(TYPED, TYPED_SEEDS))
def test_generated_typed_duplicates_keep_their_behaviour(seed: int, tmp_path: Path) -> None:
    _keeps_behaviour(seed, TYPED, tmp_path)


AUDIT_CASE_DIGESTS = {
    (474, False): "8c06892f26d36f1e",
    (898, False): "b5351c065a85d302",
    (1209, False): "3b8567ee0d0e2fa2",
    (11094, True): "260a2161f73b6440",
}
"""The digests of the files the round-3 audit stored for gram_u0474, u0898, u1209 and t11094."""


def _digest(files: Iterable[Tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for path, text in sorted(files):
        digest.update(path.encode() + b"\0" + text.encode() + b"\0")
    return digest.hexdigest()[:16]


def test_the_generator_still_draws_the_audits_cases() -> None:
    """Seeds keep naming the audit's cases, as the fixtures and markers here cite them."""
    drawn = {key: _digest(generate_case(key[0], typed=key[1]).files) for key in AUDIT_CASE_DIGESTS}
    assert drawn == AUDIT_CASE_DIGESTS
    assert generate_case(898) == generate_case(898) != generate_case(899)

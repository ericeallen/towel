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
with the round-3 audit's id for it: when its fix lands the seed passes, the
run reports XPASS as a failure, and the marker is removed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Tuple

from _pytest.mark.structures import ParameterSet
import pytest

from tests.audit_defects import (
    P1_1_PREBOUND_REBINDING,
    P1_2_SEMICOLON_LINE,
    P1_4_ANNOTATION_IMPORT,
    P1_5_RENAMED_BINDER,
    P1_7_READ_BEFORE_BIND,
    P1_8_LATER_UPDATE,
)
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

KNOWN_DEFECTS: Dict[Mode, Dict[int, str]] = {
    DEFAULT: {
        30: P1_8_LATER_UPDATE,
        322: P1_8_LATER_UPDATE,
        417: P1_8_LATER_UPDATE,  # the audit's gram_u0417
        898: P1_1_PREBOUND_REBINDING,  # the audit's gram_u0898
        907: P1_8_LATER_UPDATE,
        979: P1_2_SEMICOLON_LINE,
        1356: P1_2_SEMICOLON_LINE,
        1360: P1_8_LATER_UPDATE,
        1470: P1_1_PREBOUND_REBINDING,
        1582: P1_2_SEMICOLON_LINE,
        1591: P1_7_READ_BEFORE_BIND,
        1854: P1_1_PREBOUND_REBINDING,
        1921: P1_2_SEMICOLON_LINE,
        1984: P1_5_RENAMED_BINDER,
        2267: P1_2_SEMICOLON_LINE,  # the block ends inside the line: its second statement is deleted
        2319: P1_8_LATER_UPDATE,
        2746: P1_8_LATER_UPDATE,
        2881: P1_8_LATER_UPDATE,
        2882: P1_1_PREBOUND_REBINDING,
    },
    CROSS_MODULE: {
        474: P1_1_PREBOUND_REBINDING,  # the audit's gram_u0474, gram_u0624 and gram_u1209
        624: P1_1_PREBOUND_REBINDING,
        1209: P1_1_PREBOUND_REBINDING,
    },
    Mode(types=True): {
        10108: P1_4_ANNOTATION_IMPORT,  # the helper's annotation adds a public Callable
        10220: P1_2_SEMICOLON_LINE,  # the deleted print breaks no type, so mypy accepts it
    },
}
"""Seeds on which a defect the round-3 audit reported is not yet fixed, by mode.

Beside the audit's own (417, 898, 474, 624 and 1209), the seeds come from a
sweep of seeds 0 to 2999 in both modes, made when this module was written
(the audit's first 400 seeds had drawn other cases), and 10108 and 10220 from
one of typed seeds 10000 to 10399. Each seed's defect was read from its report.
"""


def _seeds(seeds: Iterable[int], known: Mapping[int, str]) -> List[ParameterSet]:
    """``seeds``, then the known defects' seeds, each named ``seed<N>``."""
    return with_known_defects(seeds, known, lambda seed: f"seed{seed}")


def _keeps_behaviour(seed: int, mode: Mode, workspace: Path) -> None:
    outcome = run_case(generate_case(seed, typed=mode.types), mode, workspace)
    if outcome.status == "unsupported":
        pytest.skip(outcome.detail)
    assert outcome.status in ("unchanged", "equivalent"), outcome.report()


@pytest.mark.parametrize("seed", _seeds(DEFAULT_SEEDS, KNOWN_DEFECTS[DEFAULT]))
def test_generated_duplicates_keep_their_behaviour(seed: int, tmp_path: Path) -> None:
    _keeps_behaviour(seed, DEFAULT, tmp_path)


@pytest.mark.parametrize("seed", _seeds(CROSS_MODULE_SEEDS, KNOWN_DEFECTS[CROSS_MODULE]))
def test_generated_duplicates_across_modules_keep_their_behaviour(
    seed: int, tmp_path: Path
) -> None:
    _keeps_behaviour(seed, CROSS_MODULE, tmp_path)


@pytest.mark.parametrize("seed", _seeds(TYPED_SEEDS, KNOWN_DEFECTS[Mode(types=True)]))
def test_generated_typed_duplicates_keep_their_behaviour(seed: int, tmp_path: Path) -> None:
    _keeps_behaviour(seed, Mode(types=True), tmp_path)


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

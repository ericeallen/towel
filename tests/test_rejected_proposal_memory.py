"""A rejected proposal is heard once per whole-project analysis, not once per application.

Re-analyzing a rewritten file finds every proposal in it again, the rejected
ones included. On a capped Sphinx run 204 of 287 project checks retried four
proposals that were never accepted.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Mapping, Sequence

from towel.type_inference import (
    CheckResult,
    CheckSuccess,
    RevealKey,
    RevealRequest,
    Subtyping,
    TypeDiagnostic,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

POISON = "never acceptable"


# Each pair differs from every other in shape, so no extraction rewrites another pair.
_BODIES = {
    "doomed": (
        "    total = value + 5\n"
        "    doubled = total * 7\n"
        f"    print({POISON!r}, doubled)\n"
        "    return doubled - {index}\n"
    ),
    "listed": (
        "    items = [value, value]\n"
        "    items.append(len(items))\n"
        "    items.reverse()\n"
        "    return items[{index}]\n"
    ),
    "worded": (
        "    text = str(value).strip()\n"
        "    padded = text.rjust(12, '.')\n"
        "    upper = padded.upper()\n"
        "    return upper[{index}:]\n"
    ),
    "mapped": (
        "    table = {{'key': value}}\n"
        "    table.setdefault('other', [])\n"
        "    keys = sorted(table)\n"
        "    return keys, {index}\n"
    ),
}


def _module() -> str:
    return "\n".join(
        f"def {name}_{which}(value):\n" + body.format(index=index)
        for name, body in _BODIES.items()
        for index, which in enumerate(("first", "second"))
    )


class _RejectsThePoisonedHelper:
    """Reject a candidate whose extracted helper carries the marker.

    With ``relents_beside``, that helper becomes acceptable once the project
    holds so many other extracted helpers: a rejection the changed project lifts.
    """

    def __init__(self, relents_beside: int | None = None) -> None:
        self.hearings = 0
        self._relents_beside = relents_beside

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        helpers = [
            (path, POISON in ast.unparse(node))
            for path, source in sources.items()
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
        ]
        poisoned = [path for path, is_poisoned in helpers if is_poisoned]
        if not poisoned:
            return CheckSuccess()
        self.hearings += 1
        if self._relents_beside is not None and len(helpers) > self._relents_beside:
            return CheckSuccess()
        return CheckSuccess((TypeDiagnostic(poisoned[0], "the project rejects this helper"),))

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        return {}

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[tuple[str, str]]
    ) -> Sequence[Subtyping]:
        return [Subtyping.UNKNOWN for _ in pairs]

    def close(self) -> None:
        pass


def _applied_and_hearings(
    tmp_path: Path, *, directory: bool, relents_beside: int | None = None
) -> tuple[int, int, str]:
    source = tmp_path / "in"
    source.mkdir()
    (source / "program.py").write_text(_module())
    oracle = _RejectsThePoisonedHelper(relents_beside)
    engine = UnificationRefactorEngine(
        min_lines=3, reuse_existing_functions=False, type_oracle=oracle
    )
    if directory:
        results, reason = engine.refactor_directory_to_fixed_point(
            str(source), str(tmp_path / "out"), progress="none"
        )
        assert reason == "fixed_point"
        applied = sum(count for count, _ in results.values())
        final = (tmp_path / "out" / "program.py").read_text()
    else:
        final, applied, _ = engine.refactor_to_fixed_point(
            str(source / "program.py"), progress="none"
        )
    return applied, oracle.hearings, final


def _assert_heard_once_per_whole_analysis(applied: int, hearings: int, final: str) -> None:
    assert applied == 3, "each acceptable pair was extracted"
    assert final.count(POISON) == 2, "the rejected pair is still duplicated"
    # Once when first found, and once more after the project changed. Before
    # this memory it was also heard after every application: 1 + applied.
    assert hearings == 2, (hearings, applied)


def test_directory_run_hears_a_rejected_proposal_once_per_global_pass(tmp_path: Path) -> None:
    _assert_heard_once_per_whole_analysis(*_applied_and_hearings(tmp_path, directory=True))


def test_single_file_run_hears_a_rejected_proposal_again_only_at_quiescence(
    tmp_path: Path,
) -> None:
    _assert_heard_once_per_whole_analysis(*_applied_and_hearings(tmp_path, directory=False))


def _assert_applied_once_the_project_allows_it(applied: int, hearings: int, final: str) -> None:
    assert applied == 4, "the rejected pair was extracted at its second hearing"
    assert final.count(POISON) == 1
    assert hearings == 2, (hearings, applied)


def test_directory_run_applies_a_rejected_proposal_the_changed_project_accepts(
    tmp_path: Path,
) -> None:
    _assert_applied_once_the_project_allows_it(
        *_applied_and_hearings(tmp_path, directory=True, relents_beside=3)
    )


def test_single_file_run_applies_a_rejected_proposal_the_changed_file_accepts(
    tmp_path: Path,
) -> None:
    _assert_applied_once_the_project_allows_it(
        *_applied_and_hearings(tmp_path, directory=False, relents_beside=3)
    )

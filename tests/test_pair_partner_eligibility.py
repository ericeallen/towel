# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Pair-ineligible functions perform no block enumeration; eligible paths retain their budget."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import FrozenSet, Optional
from unittest.mock import patch

import pytest

from towel.diagnostics import Settings
from towel.unification.models import FunctionArtifact
from towel.unification.pipeline import analyze_scopes, collect_functions, parse_modules
from towel.unification.refactor_engine import UnificationRefactorEngine

BODY = "    a = data + 1\n    b = a * 2\n    return b\n"


def _functions(root: Path, modules: dict[str, tuple[str, ...]]) -> list[FunctionArtifact]:
    paths = []
    for module, names in modules.items():
        path = root / module
        path.write_text("\n".join(f"def {name}(data):\n{BODY}" for name in names))
        paths.append(str(path))
    return collect_functions(analyze_scopes(parse_modules(paths)))


@pytest.mark.parametrize(
    "modules, across, enumerated",
    [
        ({"a.py": ("first",)}, False, ()),
        ({"a.py": ("first",)}, True, ()),
        ({"a.py": ("first",), "b.py": ("second",)}, False, ()),
        ({"a.py": ("first",), "b.py": ("second",)}, True, ("first", "second")),
        (
            {"a.py": ("first", "second"), "b.py": ("lonely",)},
            False,
            ("first", "second"),
        ),
    ],
)
def test_only_functions_with_a_partner_enumerate_blocks(
    tmp_path: Path, modules: dict[str, tuple[str, ...]], across: bool, enumerated: tuple[str, ...]
) -> None:
    functions = _functions(tmp_path, modules)
    engine = UnificationRefactorEngine(
        min_lines=3,
        cross_module_helpers=across,
        settings=Settings.from_environ({"TOWEL_WORKERS": "1"}),
    )
    with patch.object(engine, "_signed_blocks", wraps=engine._signed_blocks) as signed:
        pairs = engine.find_block_pairs(functions, progress="none")
    assert tuple(call.args[0].name for call in signed.call_args_list) == enumerated
    assert bool(pairs) == bool(enumerated)
    if pairs:
        assert (pairs[0].function1_name, pairs[0].function2_name) == ("first", "second")


@pytest.mark.parametrize("changed_names", [(), ("b.py",), ("a.py",)])
def test_incremental_filtering_preserves_the_preexisting_budget_order(
    tmp_path: Path,
    changed_names: tuple[str, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The budget is global over structural partners, before changed-file
    # filtering. A changed module singleton must not stop an unchanged module
    # from contributing its original budget accounting and warning.
    functions = _functions(tmp_path, {"a.py": ("first", "second"), "b.py": ("lonely",)})
    engine = UnificationRefactorEngine(min_lines=1, max_candidate_pairs=1)
    changed: Optional[FrozenSet[str]] = frozenset(str(tmp_path / name) for name in changed_names)
    with patch.object(engine, "_signed_blocks", wraps=engine._signed_blocks) as signed:
        pairs = engine.find_block_pairs(functions, progress="none", changed_files=changed)
    assert Counter(call.args[0].name for call in signed.call_args_list) == Counter(
        {"first": 1, "second": 1}
    )
    # Ineligible singleton blocks used to inflate the warning's block count
    # (15 rather than 10 here), although they contribute zero possible pairs.
    assert (
        "8 candidate pairs exceed the budget of 1; leaving out 4 bucket(s) "
        "holding 10 similar blocks" in caplog.text
    )
    assert bool(pairs) == ("a.py" in changed_names)


def test_changed_borrower_still_pairs_with_unchanged_generated_helper(tmp_path: Path) -> None:
    functions = _functions(tmp_path, {"a.py": ("__extracted_func_0",), "b.py": ("borrower",)})
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with patch.object(engine, "_signed_blocks", wraps=engine._signed_blocks) as signed:
        pairs = engine.find_block_pairs(
            functions, progress="none", changed_files=frozenset({str(tmp_path / "b.py")})
        )
    assert len(pairs) == 1
    assert [call.args[0].name for call in signed.call_args_list] == [
        "__extracted_func_0",
        "borrower",
    ]


def test_directory_of_unrelated_singletons_does_not_enumerate_blocks(tmp_path: Path) -> None:
    _functions(tmp_path, {"a.py": ("first",), "b.py": ("second",)})
    engine = UnificationRefactorEngine(min_lines=3)
    with patch.object(engine, "_signed_blocks", wraps=engine._signed_blocks) as signed:
        assert engine.analyze_directory(str(tmp_path), progress="none") == []
    signed.assert_not_called()

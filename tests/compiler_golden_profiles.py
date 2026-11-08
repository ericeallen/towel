#!/usr/bin/env python3
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

"""Exact original compiler profiles for the reviewed ownership goldens."""

from inspect import CO_VARARGS, CO_VARKEYWORDS
from pathlib import Path
from types import CodeType
from typing import Mapping

# These inputs have parameter cells only when their comprehensions create
# separate code objects. The ownership guard then retains exactly the original
# four definitions; their other useful extractions remain required. Select
# reviewed goldens by original compiler facts, never by a version prefix.
PARAMETER_CELL_GOLDENS: dict[str, dict[str, frozenset[str]]] = {
    "edge_cases_stress_test.py": {
        "mixed_comprehensions_v1": frozenset({"mappers", "filters"}),
        "mixed_comprehensions_v2": frozenset({"mappers", "filters"}),
    },
    "nested_structures.py": {
        "chain_nested_operations_v1": frozenset({"processor"}),
        "chain_nested_operations_v2": frozenset({"processor"}),
    },
}

# These formerly compiler-dependent extractions now fail the default
# profitability check even with cell-free parameters: their generated
# signatures, calls and ownership transfers outweigh the shared statements.
# Pin original-body preservation separately instead of retaining redundant
# compiler goldens or weakening the exact cell-profile check above.
PROFITABILITY_RETAINED_DEFINITIONS: dict[str, tuple[str, ...]] = {
    "method_chains.py": ("query_database_v1", "query_database_v2"),
    "nested_structures.py": ("transform_with_comprehension_a", "transform_with_comprehension_b"),
    "syntactic_coverage_comprehensive.py": (
        "complex_expr_a",
        "complex_expr_b",
        "list_comp_a",
        "list_comp_b",
    ),
}

# Retained as historical fixtures, not selected by _compiler_baseline. The
# default policy now uses each file's common, cell-independent golden.
HISTORICAL_PARAMETER_CELL_GOLDENS = frozenset(
    {"method_chains.py", "syntactic_coverage_comprehensive.py"}
)


def _compiled_parameter_cells(source: str, names: set[str]) -> dict[str, frozenset[str]]:
    matches: dict[str, list[CodeType]] = {name: [] for name in names}

    def collect(code: CodeType) -> None:
        if code.co_name in matches:
            matches[code.co_name].append(code)
        for constant in code.co_consts:
            if isinstance(constant, CodeType):
                collect(constant)

    collect(compile(source, "<original-regression-input>", "exec", dont_inherit=True))
    assert all(len(found) == 1 for found in matches.values()), matches
    cells = {}
    for name, found in matches.items():
        code = found[0]
        # The reviewed definitions have neither variadic parameter kind.
        assert not code.co_flags & (CO_VARARGS | CO_VARKEYWORDS), name
        parameters = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
        cells[name] = frozenset(parameters).intersection(code.co_cellvars)
    return cells


def _uses_parameter_cell_golden(
    actual: Mapping[str, frozenset[str]], expected: Mapping[str, frozenset[str]]
) -> bool:
    assert actual.keys() == expected.keys(), "Unexpected original definition inventory"
    if actual == expected:
        return True
    assert not any(actual.values()), f"Unknown or mixed original parameter-cell profile: {actual}"
    return False


def _compiler_baseline(py_file: Path, expected_output: Path) -> Path:
    expected_cells = PARAMETER_CELL_GOLDENS.get(py_file.name)
    if expected_cells is not None:
        actual = _compiled_parameter_cells(py_file.read_text(), set(expected_cells))
        if _uses_parameter_cell_golden(actual, expected_cells):
            return expected_output / "parameter_cells" / py_file.name
    return expected_output / py_file.name


def require_cell_free_baseline_generation(test_examples: Path) -> None:
    """Refuse broad generation before it can overwrite another compiler's goldens."""
    for name in PARAMETER_CELL_GOLDENS:
        original = test_examples / name
        if (
            original.is_file()
            and _compiler_baseline(original, Path()).parent.name == "parameter_cells"
        ):
            raise RuntimeError(
                "This compiler requires reviewed parameter-cell goldens. Broad regeneration "
                "would overwrite the cell-free goldens; generate and validate only the affected "
                "outputs externally, then review their AST and behavior before adoption."
            )

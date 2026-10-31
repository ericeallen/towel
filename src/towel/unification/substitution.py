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

"""The result of anti-unifying blocks: the template's parameters and each block's arguments."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple
from weakref import WeakKeyDictionary

from ..canonical_ast import canonical_dump
from .ast_paths import Path, path_map

_STRUCTURAL_TEXT: "WeakKeyDictionary[ast.AST, str]" = WeakKeyDictionary()


def structural_text(node: ast.AST) -> str:
    """``canonical_dump(node)``, once per node.

    Rendered Python is not an AST identity: on Python 3.9 a bare
    FormattedValue and its enclosing JoinedStr unparse identically, so the
    dump is the key that preserves node kinds and structure. It spells an
    int too wide for decimal in hexadecimal, so no literal ends the run.
    The memo is weak and assumes a node's structure is fixed once it has
    been keyed: the blocks being unified are read-only, and the extractor
    queries each of its copied template nodes once, before substituting its
    children.
    """
    cached = _STRUCTURAL_TEXT.get(node)
    if cached is None:
        cached = canonical_dump(node)
        try:
            _STRUCTURAL_TEXT[node] = cached
        except TypeError:  # a node type that cannot be weakly referenced
            pass
    return cached


@dataclass(frozen=True)
class LiteralPool:
    """One immutable caller row of common compiler literals, selected by fixed indices.

    Each row retains original expression nodes, so structural memoization can
    freeze their source paths rather than a synthetic tuple that has no path.
    """

    parameter: str
    keys: Tuple[str, ...]
    rows: Tuple[Tuple[ast.expr, ...], ...]
    selectors: Tuple[Tuple[Tuple[Path, int], ...], ...] = ()

    def index_at(self, block_idx: int, path: Optional[Path]) -> Optional[int]:
        """The certified carrier slot at this occurrence, if any."""
        if path is None or block_idx >= len(self.selectors):
            return None
        return next(
            (index for selected, index in self.selectors[block_idx] if selected == path), None
        )


@dataclass
class Substitution:
    """
    Represents a substitution mapping from sub-expressions to parameter names.

    Ordinary expressions use structural keys; selected literal occurrences
    use field/index paths so equal values elsewhere remain untouched.
    """

    mappings: Dict[Tuple[int, str], str] = field(default_factory=dict)
    occurrence_mappings: Dict[Tuple[int, Path], str] = field(default_factory=dict)
    _source_paths: Tuple[Dict[ast.AST, Path], ...] = field(default=(), repr=False, compare=False)
    # Maps parameter names to the list of expressions they replace
    param_expressions: Dict[str, List[Tuple[int, ast.AST]]] = field(default_factory=dict)
    # Maps parameter names to list of bound variables they should take as function args
    # If a parameter is in this dict, it should be a function parameter
    function_params: Dict[str, List[str]] = field(default_factory=dict)
    # Optional hygienic renames captured during unification (one mapping per block)
    hygienic_renames: List[Dict[str, str]] = field(default_factory=list)
    # Parameters that, after substitution, are used in call position (as a callee)
    # within the extracted function body. These should be passed as thunks (lambdas)
    # that perform the call to avoid eager evaluation at the call site.
    params_used_as_callee: Set[str] = field(default_factory=set)
    # Thunk parameters the helper evaluates first, once and unconditionally;
    # their expressions are passed eagerly by the inlining pass.
    inlined_parameters: Set[str] = field(default_factory=set)

    # Optional: parameters introduced post-unification to promote literal arguments
    # of higher-order factory calls (e.g., make_validator(5)) into threaded parameters
    # of the extracted helper, even when those literals do not differ across blocks.
    #
    # Schema:
    #   promoted_literal_args[param_name][block_idx] = ast.AST (expression to pass)
    #
    # This allows call generation to supply the original literal (or expression)
    # per block for such promoted parameters. By default, this dict is empty and
    # has no effect on behavior until a promotion pass populates it.
    promoted_literal_args: Dict[str, Dict[int, ast.AST]] = field(default_factory=dict)
    aug_assign_mappings: Dict[str, Dict[int, str]] = field(default_factory=dict)
    literal_pool: Optional[LiteralPool] = None

    def add_mapping(
        self, block_idx: int, expr: ast.AST, param_name: str, bound_vars: Optional[List[str]] = None
    ) -> None:
        """
        Add a mapping from an expression to a parameter name.

        Args:
            block_idx: Block index
            expr: Expression being parameterized
            param_name: Name of the parameter
            bound_vars: List of bound variables the expression references (for function parameters)
        """
        key = (block_idx, structural_text(expr))
        self.mappings[key] = param_name

        if param_name not in self.param_expressions:
            self.param_expressions[param_name] = []
        self.param_expressions[param_name].append((block_idx, expr))

        # If this expression references bound variables, mark parameter as function
        if bound_vars:
            if param_name not in self.function_params:
                self.function_params[param_name] = bound_vars
            else:
                # Merge bound variables (should be same across all blocks)
                existing = set(self.function_params[param_name])
                new_vars = set(bound_vars)
                self.function_params[param_name] = sorted(existing | new_vars)

    def bind_source_paths(self, blocks: Sequence[Sequence[ast.AST]]) -> None:
        """Bind immutable source nodes without affecting copied-template lookups."""
        self._source_paths = tuple(path_map(block) for block in blocks)

    def source_path(self, block_idx: int, expr: ast.AST) -> Optional[Path]:
        """This source expression's occurrence; copied nodes have no source path."""
        return (
            self._source_paths[block_idx].get(expr) if block_idx < len(self._source_paths) else None
        )

    def add_occurrence_mapping(
        self,
        block_idx: int,
        path: Path,
        expr: ast.AST,
        param_name: str,
        bound_vars: Optional[List[str]] = None,
    ) -> None:
        """Map one selected occurrence, retaining one argument per caller."""
        key = (block_idx, path)
        if key in self.occurrence_mappings and self.occurrence_mappings[key] != param_name:
            raise ValueError("An occurrence already belongs to another parameter")
        self.occurrence_mappings[key] = param_name
        expressions = self.param_expressions.setdefault(param_name, [])
        if not any(index == block_idx for index, _ in expressions):
            expressions.append((block_idx, expr))
        if bound_vars:
            self.function_params[param_name] = list(bound_vars)

    def get_param_for_expr(
        self, block_idx: int, expr: ast.AST, *, path: Optional[Path] = None
    ) -> Optional[str]:
        """An exact occurrence first, then an explicitly recorded structural mapping."""
        located = path if path is not None else self.source_path(block_idx, expr)
        if located is not None:
            parameter = self.occurrence_mappings.get((block_idx, located))
            if parameter is not None:
                return parameter
        return self.mappings.get((block_idx, structural_text(expr)))

    def remove_parameter(self, param_name: str) -> None:
        """Forget ``param_name``: its expressions, the mappings to it, and its thunk variables."""
        if self.literal_pool is not None and self.literal_pool.parameter == param_name:
            self.literal_pool = None
        self.param_expressions.pop(param_name, None)
        self.function_params.pop(param_name, None)
        self.occurrence_mappings = {
            key: name for key, name in self.occurrence_mappings.items() if name != param_name
        }
        for key in [key for key, name in self.mappings.items() if name == param_name]:
            del self.mappings[key]

    def is_function_param(self, param_name: str) -> bool:
        """Check if a parameter should be a function parameter."""
        return param_name in self.function_params

    def get_function_param_vars(self, param_name: str) -> List[str]:
        """Get the bound variables a function parameter should take."""
        return self.function_params.get(param_name, [])

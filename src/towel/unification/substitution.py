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
from typing import Dict, List, Optional, Set, Tuple
from weakref import WeakKeyDictionary

from ..canonical_ast import canonical_dump

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


@dataclass
class Substitution:
    """
    Represents a substitution mapping from sub-expressions to parameter names.

    Each entry maps a (block_index, sub_expression) to a parameter name.
    """

    mappings: Dict[Tuple[int, str], str] = field(default_factory=dict)
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

    def get_param_for_expr(self, block_idx: int, expr: ast.AST) -> Optional[str]:
        """Get the parameter name for an expression."""
        return self.mappings.get((block_idx, structural_text(expr)))

    def remove_parameter(self, param_name: str) -> None:
        """Forget ``param_name``: its expressions, the mappings to it, and its thunk variables."""
        self.param_expressions.pop(param_name, None)
        self.function_params.pop(param_name, None)
        for key in [key for key, name in self.mappings.items() if name == param_name]:
            del self.mappings[key]

    def is_function_param(self, param_name: str) -> bool:
        """Check if a parameter should be a function parameter."""
        return param_name in self.function_params

    def get_function_param_vars(self, param_name: str) -> List[str]:
        """Get the bound variables a function parameter should take."""
        return self.function_params.get(param_name, [])

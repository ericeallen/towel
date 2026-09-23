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

"""The state and operations the unifier's mixins rely on.

``Unifier`` is assembled from the node-type unification core and mixins for
constant consistency, parameterization with alpha renaming, and literal
promotion, each in its own module. Every attribute and operation a mixin
reaches is declared here, so each module states its dependencies and mypy
checks the seams; the constructor assigns the attributes and the core or
another mixin implements each stub.
"""

from __future__ import annotations

import ast
from typing import Any, Dict, Hashable, Iterator, List, Optional, Set, Tuple, Sequence

ConstantIdentity = Tuple[type, Hashable]
"""A constant's type and value, as ``constant_consistency.constant_identity`` computes them."""


class UnifierState:
    """Attributes and operations shared across the unifier's mixins (declarations only)."""

    max_parameters: int
    """The most parameters a helper may take; a pair needing more is declined."""

    param_counter: int
    """The next ``__param_N`` index within one unification."""

    alpha_renamings: Dict[Tuple[int, str], str]
    """Per block, the canonical spelling of each alpha-renamed binder."""

    current_blocks: Optional[Sequence[Sequence[ast.AST]]]
    """The blocks being unified, for checks that need the whole block."""

    parameterize_constants: bool
    """Whether differing constants become parameters."""

    promote_equal_hof_literals: bool
    """Whether equal literal arguments of higher-order factory calls become parameters."""

    constant_positions: Dict[Tuple[int, ConstantIdentity], List[Tuple[object, ...]]]
    """Where each constant occurs in each block, by block and identity, as field paths."""

    _reserved_parameter_names: Set[str]
    """Identifiers the blocks mention; a fresh parameter must not alias one."""

    _pattern_depth: int
    """How many match patterns enclose the nodes being unified."""

    _pattern_parameters_allowed: bool
    """Whether a parameter may stand where the pattern being unified is (see ``Unifier._unify_pattern``)."""

    @staticmethod
    def _iter_child_fields(node: ast.AST) -> Iterator[Tuple[str, Any]]:
        """Provided by Unifier."""
        raise NotImplementedError

    def _fresh_parameter_name(self) -> str:
        """Provided by Parameterization."""
        raise NotImplementedError

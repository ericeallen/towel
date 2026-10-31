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

"""Spell otherwise unused caller bindings without changing their retained values.

A returned local with no binding or reference outside the extracted block is
kept solely for lifetime. Its new local name can express that fact to linters;
any outside read, rebind, deletion or nested free reference preserves its name.
This runs only after the original generated call passes semantic validation.
"""

from __future__ import annotations

import ast
import copy
from typing import Sequence

from .function_scope import code_names, function_names, identifiers
from .models import FunctionNode


def _targets(statement: ast.Assign) -> tuple[ast.Name, ...]:
    if len(statement.targets) != 1:
        return ()
    target = statement.targets[0]
    if isinstance(target, ast.Name):
        return (target,)
    if isinstance(target, ast.Tuple) and all(isinstance(item, ast.Name) for item in target.elts):
        return tuple(item for item in target.elts if isinstance(item, ast.Name))
    return ()


def spell_retained_bindings(
    function: FunctionNode, block: Sequence[ast.stmt], call: ast.stmt
) -> ast.stmt:
    """Clone and respell only unused returned locals; keep assignment and tuple order intact."""
    if not isinstance(call, ast.Assign):
        return call
    targets = _targets(call)
    if not targets:
        return call
    whole, part = function_names(function), code_names(block)
    outside = (whole.bound - part.bound) + (whole.references - part.references)
    eligible = {
        target.id
        for target in targets
        if target.id in part.local
        and target.id not in whole.declared_global | whole.declared_nonlocal
        and not outside[target.id]
    }
    if not eligible:
        return call
    reserved = set(identifiers((function, call)))
    rewritten = copy.deepcopy(call)
    for target in _targets(rewritten):
        if target.id not in eligible:
            continue
        stem = "_towel_keep_" + target.id
        name, index = stem, 0
        while name in reserved:
            index += 1
            name = f"{stem}_{index}"
        reserved.add(name)
        target.id = name
    return rewritten

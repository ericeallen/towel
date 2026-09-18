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


"""Enumerate the parameters of a Python ``ast.arguments`` block, and mint new ones.

Shared by the scope, binding, and assignment analyses, which each need the same
complete parameter set: positional-only, positional, keyword-only, ``*args``,
and ``**kwargs``. The minting rule for generated helper parameters lives here
too, so the unifier and the engine cannot drift apart on what a fresh
``__param_N`` is.
"""

import ast
from typing import Container, Iterator, Tuple

GENERATED_PARAMETER_PREFIX = "__param_"
"""The spelling of generated helper parameters; ``rename-helpers`` names them later."""


def parameter_nodes(args: ast.arguments) -> Iterator[ast.arg]:
    """Yield every ``ast.arg`` of an arguments block, in declaration order."""
    yield from args.posonlyargs
    yield from args.args
    yield from args.kwonlyargs
    if args.vararg:
        yield args.vararg
    if args.kwarg:
        yield args.kwarg


def parameter_names(args: ast.arguments) -> Iterator[str]:
    """Yield every parameter name of an arguments block, in declaration order."""
    for arg in parameter_nodes(args):
        yield arg.arg


def fresh_parameter_name(taken: Container[str], start: int = 0) -> Tuple[str, int]:
    """The first ``__param_N`` with ``N >= start`` not in ``taken``, and the next index.

    The unifier mints in order across one unification and passes its counter
    as ``start``; the engine mints against every name a block mentions and
    starts at zero. Both must agree on the spelling and the collision rule.
    """
    index = start
    while f"{GENERATED_PARAMETER_PREFIX}{index}" in taken:
        index += 1
    return f"{GENERATED_PARAMETER_PREFIX}{index}", index + 1

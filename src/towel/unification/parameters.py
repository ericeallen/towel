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


"""Enumerate the parameters of a Python ``ast.arguments`` block.

Shared by the scope, binding, and assignment analyses, which each need the same
complete parameter set: positional-only, positional, keyword-only, ``*args``,
and ``**kwargs``.
"""

import ast
from typing import Iterator


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

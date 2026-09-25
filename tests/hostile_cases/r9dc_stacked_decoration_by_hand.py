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

# Round-4 audit P1-06: a decorator applied by hand around another. traced
# recompiles the function from its source with a print after every
# assignment, as typeguard's @typechecked adds a check; register only records
# the function and returns it. parse_a = traced(register(parse_a)) instruments
# parse_a, and make_traced(level=1)(register(show_a)) does the same through a
# factory, so a block moved out of either into an untraced helper would no
# longer be traced.
import ast
import inspect
import textwrap

REGISTRY = []


def traced(fn):
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    tree.body[0].decorator_list = []

    class Trace(ast.NodeTransformer):
        def visit_Assign(self, node):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            return [node, *(ast.parse(f"print('  {n} =', {n})").body[0] for n in names)]

    tree = ast.fix_missing_locations(Trace().visit(tree))
    namespace = {}
    exec(compile(tree, inspect.getsourcefile(fn), "exec"), fn.__globals__, namespace)
    return namespace[fn.__name__]


def make_traced(level):
    return traced


def register(fn):
    REGISTRY.append(fn.__name__)
    return fn


def parse_a(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 3


def parse_b(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 5


def show_a(items):
    count = 0
    for item in items:
        count = count + item - 1
    shown = count * 4
    return shown + 3


def show_b(items):
    count = 0
    for item in items:
        count = count + item - 1
    shown = count * 4
    return shown + 9


parse_a = traced(register(parse_a))
show_a = make_traced(level=1)(register(show_a))


if __name__ == "__main__":
    print(parse_a([1, 5, 9]), parse_b([2, 4]), show_a([3, 4]), show_b([5]), REGISTRY)

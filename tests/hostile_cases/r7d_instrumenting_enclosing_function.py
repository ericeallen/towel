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

# The decorator is on the enclosing functions: it recompiles each from its
# source, nested functions included, with a print after every assignment. The
# blocks sit in the nested functions, and a helper shared by the two would
# live outside both decorated functions, untraced.
import ast
import inspect
import textwrap


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


@traced
def outer_a(items):
    def inner(values, scale):
        total = 0
        for value in values:
            total = total + value * scale
        result = total + 7
        return result * 3

    return inner(items, 2)


@traced
def outer_b(items):
    def inner(values, scale):
        total = 0
        for value in values:
            total = total + value * scale
        result = total + 7
        return result * 5

    return inner(items, 3)


if __name__ == "__main__":
    print(outer_a([1, 5, 9]), outer_b([2, 4]))

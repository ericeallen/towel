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

# The metaclass recompiles every method of the classes it builds from its
# source, with a print after every assignment: a block moved out of a method
# into a helper would no longer be traced.
import ast
import inspect
import textwrap


def recompile(fn):
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))

    class Trace(ast.NodeTransformer):
        def visit_Assign(self, node):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            return [node, *(ast.parse(f"print('  {n} =', {n})").body[0] for n in names)]

    tree = ast.fix_missing_locations(Trace().visit(tree))
    namespace = {}
    exec(compile(tree, inspect.getsourcefile(fn), "exec"), fn.__globals__, namespace)
    return namespace[fn.__name__]


class Traced(type):
    def __new__(mcs, name, bases, namespace):
        for key, value in list(namespace.items()):
            if inspect.isfunction(value):
                namespace[key] = recompile(value)
        return super().__new__(mcs, name, bases, namespace)


class Report(metaclass=Traced):
    def a(self, items):
        total = 0
        for item in items:
            total = total + item * 2
        result = total + 7
        return result * 3

    def b(self, items):
        total = 0
        for item in items:
            total = total + item * 2
        result = total + 7
        return result * 5


if __name__ == "__main__":
    report = Report()
    print(report.a([1, 5, 9]), report.b([2, 4]))

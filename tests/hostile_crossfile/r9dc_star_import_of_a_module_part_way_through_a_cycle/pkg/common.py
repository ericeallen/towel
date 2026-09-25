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

# common is imported first (run.py), and imports shapes before it binds
# __all__: shapes' star import then runs on a module part way through its
# import and binds every public name common has bound so far, the
# instrumenting staticmethod among them, which __all__ leaves out.
import ast
import builtins
import inspect
import textwrap


def staticmethod(fn):
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    tree.body[0].decorator_list = []

    class Trace(ast.NodeTransformer):
        def visit_Assign(self, node):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            return [node, *(ast.parse(f"print('  {n} =', {n})").body[0] for n in names)]

    tree = ast.fix_missing_locations(Trace().visit(tree))
    namespace = {}
    exec(compile(tree, inspect.getsourcefile(fn), "exec"), fn.__globals__, namespace)
    return builtins.staticmethod(namespace[fn.__name__])


from . import shapes  # noqa: E402

__all__ = ["scale", "shapes"]


def scale(v):
    return v * 3

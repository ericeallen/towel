# The decorator recompiles the function from its source with a print after
# every assignment, as typeguard's @typechecked adds a check: a block moved
# into an undecorated helper would no longer be traced.
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
def a(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 3


@traced
def b(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 5


if __name__ == "__main__":
    print(a([1, 5, 9]), b([2, 4]))

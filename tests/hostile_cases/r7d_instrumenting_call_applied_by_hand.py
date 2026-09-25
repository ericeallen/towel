# The recompiler is applied by hand, as numba's njit(kernel) often is: one
# kernel bare, the other through a factory. It recompiles each function from
# its source with a print after every assignment, so a block moved into an
# unapplied helper would no longer be traced.
import ast
import inspect
import textwrap


def traced(fn, label="trace"):
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))

    class Trace(ast.NodeTransformer):
        def visit_Assign(self, node):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            return [node, *(ast.parse(f"print('{label}', '{n} =', {n})").body[0] for n in names)]

    tree = ast.fix_missing_locations(Trace().visit(tree))
    namespace = {}
    exec(compile(tree, inspect.getsourcefile(fn), "exec"), fn.__globals__, namespace)
    return namespace[fn.__name__]


def traced_with(label):
    return lambda fn: traced(fn, label)


def a(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 3


def b(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 5


fast_a = traced(a)
fast_b = traced_with("b")(b)


if __name__ == "__main__":
    print(fast_a([1, 5, 9]), fast_b([2, 4]), a([1]), b([1]))

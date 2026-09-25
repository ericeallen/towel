# The class decorator recompiles every method from its source with a print
# after every assignment, as typeguard's @typechecked does on a class: a block
# moved out of a method into a helper would no longer be traced.
import ast
import inspect
import textwrap


def traced(cls):
    for name, member in list(vars(cls).items()):
        if not inspect.isfunction(member):
            continue
        tree = ast.parse(textwrap.dedent(inspect.getsource(member)))

        class Trace(ast.NodeTransformer):
            def visit_Assign(self, node):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                return [node, *(ast.parse(f"print('  {n} =', {n})").body[0] for n in names)]

        tree = ast.fix_missing_locations(Trace().visit(tree))
        namespace = {}
        exec(compile(tree, inspect.getsourcefile(cls), "exec"), member.__globals__, namespace)
        setattr(cls, name, namespace[name])
    return cls


@traced
class Report:
    def __init__(self, k):
        self.k = k

    def a(self, items):
        total = 0
        for item in items:
            total = total + item * self.k
        result = total + 7
        return result * 3

    def b(self, items):
        total = 0
        for item in items:
            total = total + item * self.k
        result = total + 7
        return result * 5


if __name__ == "__main__":
    report = Report(2)
    print(report.a([1, 5, 9]), report.b([2, 4]))

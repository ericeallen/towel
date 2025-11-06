import tempfile, textwrap, ast, sys
from pathlib import Path
from os.path import dirname
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.towel.unification.refactor_engine import UnificationRefactorEngine

code_global = textwrap.dedent(
    """
    G = 0
    def outer(a):
        def f1(x):
            global G
            t = x + a
            G = t
            return t
        def f2(x):
            global G
            t = x + a
            G = t
            return t
        return f1(1) + f2(2)
    """
)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    eng = UnificationRefactorEngine(max_parameters=5, min_lines=2, parameterize_constants=True)

    # First, nonlocal scenario (expect 0 proposals)
    code_nonlocal = textwrap.dedent(
        """
        def outer():
            x = 0
            def f1():
                nonlocal x
                t = x + 1
                u = t * 2
                return u
            def f2():
                nonlocal x
                t = x + 1
                u = t * 2
                return u
            return f1() + f2()
        """
    )
    p1 = td / "m1.py"
    p1.write_text(code_nonlocal, encoding="utf-8")
    props1 = eng.analyze_file(str(p1))
    print("nonlocal proposals:", len(props1))

    # Then, global scenario using same engine instance
    p2 = td / "m2.py"
    p2.write_text(code_global, encoding="utf-8")
    props = eng.analyze_file(str(p2))
    print(f"global proposals: {len(props)}")
    for i, pr in enumerate(props):
        out = eng.apply_refactoring(str(p2), pr)
        print(f"--- proposal {i} ---")
        print(out)
        mod = ast.parse(out)
        outers = [n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "outer"]
        if outers:
            inner_names = {n.name for n in outers[0].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            top_level = {n.name for n in mod.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            print("inner:", inner_names, "top:", top_level)

#!/usr/bin/env python3
"""
Debug a specific pair of functions in a file by forcing the engine to attempt
refactoring on the full body blocks, printing detailed validation output.

Usage:
  python scripts/debug_pair.py <file.py> <func1> <func2>
"""
import sys
import ast
from pathlib import Path
from typing import Optional, Tuple, List, Union, Sequence

from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer


def _body_without_docstring(body: Sequence[ast.stmt]) -> List[ast.stmt]:
    body_list = list(body)
    if not body_list:
        return []

    first_stmt = body_list[0]
    if (
        isinstance(first_stmt, ast.Expr)
        and isinstance(first_stmt.value, ast.Constant)
        and isinstance(first_stmt.value.value, str)
    ):
        return body_list[1:]

    return body_list


def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: python scripts/debug_pair.py <file.py> <func1> <func2>")
        sys.exit(2)

    file_path, f1, f2 = sys.argv[1], sys.argv[2], sys.argv[3]
    src = Path(file_path).read_text()
    tree = ast.parse(src)

    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    func1 = next((f for f in funcs if f.name == f1), None)
    func2 = next((f for f in funcs if f.name == f2), None)
    if not func1 or not func2:
        print("Functions not found.")
        sys.exit(3)

    analyzer = ScopeAnalyzer()
    scope = analyzer.analyze(tree)

    eng = UnificationRefactorEngine(max_parameters=5, min_lines=3)

    # Build a minimal all_functions tuple as expected by engine internal call
    all_functions = [
        (file_path, func1, src, analyzer, scope),
        (file_path, func2, src, analyzer, scope),
    ]

    # Extract full body blocks (skip docstring)
    def body_range(
        f: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> Optional[Tuple[Tuple[int, int], List[ast.stmt]]]:
        body = _body_without_docstring(f.body)
        if not body:
            return None
        start = body[0].lineno
        end = getattr(body[-1], "end_lineno", body[-1].lineno)
        return (start, end), body

    r1 = body_range(func1)
    r2 = body_range(func2)
    if not r1 or not r2:
        print("Empty bodies?")
        sys.exit(4)

    from towel.unification.refactor_engine import CodeBlockPair

    pair = CodeBlockPair(
        file_path=file_path,
        function1_name=f1,
        function2_name=f2,
        block1_range=r1[0],
        block2_range=r2[0],
        block1_nodes=r1[1],
        block2_nodes=r2[1],
        file_path2=file_path,
        scope_analyzer1=analyzer,
        scope_analyzer2=analyzer,
        root_scope1=scope,
        root_scope2=scope,
        source1=src,
        source2=src,
    )

    # Monkey-print debug by setting env var inside process
    import os

    os.environ["DEBUG_VALIDATION"] = "1"

    res = eng._try_refactor_pair_multi_file(pair, all_functions)
    if res:
        print("\nSUCCESS: Proposal generated:")
        print(res.description)
        try:
            import ast as _ast

            print("\nExtracted function preview:\n")
            print(_ast.unparse(res.extracted_function))
        except Exception as e:
            print(f"(Preview failed: {e})")
    else:
        print("\nNo proposal generated for full-body blocks.")


if __name__ == "__main__":
    main()

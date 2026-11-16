"""Equivalence driver for case `same_named_methods_semicolon_orphan`."""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

ROOT = Path(__file__).resolve().parent
ORIG_PATH = ROOT / "cases" / "same_named_methods_semicolon_orphan" / "case.py"
OUT_PATH = ROOT / "out" / "same_named_methods_semicolon_orphan" / "case.py"


def _load_module(path: Path, mod_name: str) -> ModuleType:
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path))
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_one(path: Path, mod_name: str, fn: Callable[[ModuleType], object]) -> tuple[str, object, str]:
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            module = _load_module(path, mod_name)
            value = fn(module)
        return ("RETURN", value, buf.getvalue())
    except BaseException as exc:
        return ("RAISE", (type(exc).__name__, str(exc)), buf.getvalue())


def main() -> int:
    if not ORIG_PATH.exists():
        print(f"ERROR: missing original {ORIG_PATH}")
        return 2
    if not OUT_PATH.exists():
        print(f"DEFECT CONFIRMED [same_named_methods_semicolon_orphan]: missing output {OUT_PATH} (towel dry crashed or produced no output)")
        return 1

    try:
        ast.parse(OUT_PATH.read_text(encoding="utf-8"), filename=str(OUT_PATH))
    except SyntaxError as exc:
        print(f"DEFECT CONFIRMED [same_named_methods_semicolon_orphan]: output failed ast.parse: {exc}")
        return 1

    calls: list[tuple[str, Callable[[ModuleType], object]]] = [
    ("First().compute(5)", lambda m: m.First().compute(5)),
    ("Second().compute(5)", lambda m: m.Second().compute(5)),
]

    mismatches = 0
    for label, call in calls:
        orig_outcome = _run_one(ORIG_PATH, "orig_same_named_methods_semicolon_orphan", call)
        out_outcome = _run_one(OUT_PATH, "out_same_named_methods_semicolon_orphan", call)
        if orig_outcome != out_outcome:
            mismatches += 1
            print(f"DEFECT CONFIRMED [same_named_methods_semicolon_orphan] on {label}:")
            print(f"  original  : {orig_outcome[0]} {orig_outcome[1]!r} stdout={orig_outcome[2]!r}")
            print(f"  refactored: {out_outcome[0]} {out_outcome[1]!r} stdout={out_outcome[2]!r}")
        else:
            print(f"MATCH [same_named_methods_semicolon_orphan] on {label}: {orig_outcome[0]} {orig_outcome[1]!r} stdout={orig_outcome[2]!r}")

    if mismatches:
        return 1
    print(f"OK [same_named_methods_semicolon_orphan]: all {len(calls)} test input(s) matched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

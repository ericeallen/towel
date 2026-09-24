"""How the hostile batteries execute a fixture before and after refactoring.

Both batteries compare the same observation of a program: its exit status,
everything it printed to stdout, and the last line of stderr, which names
the exception when it died. Earlier stderr lines hold the traceback, whose
file paths and line numbers legitimately differ after an extraction. The
program runs in an emptied environment so nothing on the developer's PATH
or in their shell variables can reach it.

Both also compare what every module shows the code that imports it
(:func:`module_faces`), since a program's own output is only the part of
its behaviour the fixture happens to print: a name an extraction adds or
rebinds reaches every consumer, and every module that star-imports it.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Sequence

import pytest

ISOLATED_ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PATH": ""}

_FACES = r"""
import builtins, importlib, json, sys, types

BUILTINS = {id(value): name for name, value in vars(builtins).items() if not name.startswith("_")}
SCALARS = (type(None), bool, int, float, complex, str, bytes)


def described(value):
    if id(value) in BUILTINS and getattr(builtins, BUILTINS[id(value)], None) is value:
        return "builtin " + BUILTINS[id(value)]
    if isinstance(value, types.ModuleType):
        return "module " + value.__name__
    if isinstance(value, type) or callable(value) and hasattr(value, "__qualname__"):
        kind = "class" if isinstance(value, type) else "function"
        return f"{kind} {getattr(value, '__module__', None)}.{value.__qualname__}"
    kind = type(value)
    text = f"{kind.__module__}.{kind.__qualname__}"
    if isinstance(value, SCALARS) or kind.__module__ in ("typing", "typing_extensions"):
        text += " " + repr(value)
    return text


faces = {}
for name in sys.argv[1:]:
    try:
        module = importlib.import_module(name)
    except BaseException as error:
        faces[name] = {"import": type(error).__name__}
        continue
    # dir() alone misses what a module's own __dir__ leaves out (packaging.ranges
    # answers with its __all__), so the namespace's own names count too.
    public = {
        attribute: described(getattr(module, attribute))
        for attribute in sorted(set(dir(module)) | set(vars(module)))
        if not attribute.startswith("_")
    }
    namespace = {}
    try:
        exec(f"from {name} import *", namespace)
        star = sorted(key for key in namespace if key != "__builtins__")
    except BaseException as error:
        star = ["<raises " + type(error).__name__ + ">"]
    faces[name] = {"public": public, "star": star}
sys.stdout.write("\n" + json.dumps(faces, sort_keys=True) + "\n")
"""


def module_faces(root: Path, modules: Sequence[str]) -> Mapping[str, object]:
    """What each of ``modules``, imported fresh from ``root``, shows its importers.

    For each module: its public names (``dir()`` and the module's own
    namespace, without leading underscores), each described as the builtin
    or module object it is, the
    class or function by qualified name, or any other value by its type, a
    scalar or ``typing`` object by its value too; and the names ``from
    module import *`` binds in a fresh namespace. A refactoring may add
    private names, its helpers among them; it may not add, drop or rebind a
    public one. Every module is imported in one fresh interpreter, in the
    order given, and one that fails to import is recorded by its exception.
    """
    completed = subprocess.run(
        [sys.executable, "-c", _FACES, *modules],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=ISOLATED_ENV,
    )
    lines = completed.stdout.strip().splitlines()
    assert completed.returncode == 0 and lines, completed.stdout + completed.stderr
    faces: Mapping[str, object] = json.loads(lines[-1])
    return faces


def observe(script: str, cwd: Path) -> tuple[int, str, list[str]]:
    """Run ``script`` (a path relative to ``cwd``) and return what the batteries compare."""
    completed = subprocess.run(
        [sys.executable, script],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=ISOLATED_ENV,
    )
    return completed.returncode, completed.stdout, completed.stderr.strip().splitlines()[-1:]


def parsed_or_skipped(path: Path) -> ast.Module:
    """``path`` parsed, or the calling test skipped where this Python lacks the fixture's syntax.

    A fixture may be written in syntax newer than the oldest supported Python:
    ``type Alias = ...`` and ``class Box[T]:`` need 3.12. Every test that reads
    the fixtures skips such a one there, alike.
    """
    try:
        return ast.parse(path.read_bytes(), filename=str(path))
    except SyntaxError:
        pytest.skip("the fixture is written in syntax this Python does not have")

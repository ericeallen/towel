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
rebinds reaches every consumer, and every module that star-imports it. And
they compare the scope of every name in every function the refactoring kept
(:func:`scope_changes`), by CPython's symbol table: a read that finds another
binding misbehaves only on the path that takes it, which a fixture may not
print.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import symtable
import sys
from typing import Dict, Iterator, List, Mapping, Sequence, Tuple, Union

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


NEWER_SYNTAX_SUFFIX = ".pynew"
"""The suffix of a fixture written in syntax some supported Python does not parse.

Towel refuses a program holding a file it cannot parse (``towel.program_files``),
and the suite refactors files inside this repository, so every ``.py`` file here
must parse on every supported Python. A fixture in newer syntax, ``type Alias =
...`` before 3.12 or a Python newer than any Towel supports, is stored under
this suffix instead, which no Python tool reads as source, and a battery gives
it back its ``.py`` name where it runs.
"""


def fixture_sources(directory: Path, *, recursive: bool = False) -> List[Path]:
    """Every Python fixture in ``directory``, sorted: its ``.py`` files and those in newer syntax."""
    found = directory.rglob if recursive else directory.glob
    return sorted([*found("*.py"), *found(f"*{NEWER_SYNTAX_SUFFIX}")])


def fixture_named(directory: Path, stem: str) -> Path:
    """The single-file fixture ``stem`` of ``directory``, whichever suffix it is stored under."""
    plain = directory / f"{stem}.py"
    return plain if plain.exists() else directory / f"{stem}{NEWER_SYNTAX_SUFFIX}"


def copy_fixture_tree(source: Path, destination: Path) -> None:
    """``shutil.copytree``, with each file in newer syntax copied under the ``.py`` name it runs under."""
    shutil.copytree(source, destination)
    for path in sorted(destination.rglob(f"*{NEWER_SYNTAX_SUFFIX}")):
        path.rename(path.with_suffix(".py"))


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


_GENERATED_HELPER_PREFIXES = ("__extracted_func", "_extracted_func")
"""How the names of the helpers a refactoring inserts begin (``is_generated_helper_name``)."""

_Key = Tuple[str, ...]


def _kind(table: symtable.SymbolTable) -> str:
    return str(getattr(table.get_type(), "value", table.get_type()))


def _is_definition(table: symtable.SymbolTable) -> bool:
    """Whether ``table`` is a ``def`` or ``class``, not a lambda, comprehension or annotation scope."""
    if _kind(table) == "class":
        return True
    if _kind(table) != "function" or table.get_name() == "lambda":
        return False
    assert isinstance(table, symtable.Function)
    return ".0" not in table.get_parameters()  # a comprehension's implicit iterator


def _function_tables(
    table: symtable.SymbolTable, path: _Key = ()
) -> Iterator[Tuple[_Key, symtable.SymbolTable]]:
    """Every ``def`` in ``table``, keyed by the definitions enclosing it and its place among namesakes.

    Lambdas, comprehensions and annotation scopes carry no name to match a
    table by, and a generated helper has no counterpart; they are left out,
    and what they read is judged through the function holding them.
    """
    seen: Dict[str, int] = {}
    for child in table.get_children():
        name = child.get_name()
        if not _is_definition(child) or name.startswith(_GENERATED_HELPER_PREFIXES):
            continue
        index = seen[name] = seen.get(name, -1) + 1
        key = path + (f"{name}#{index}",)
        if _kind(child) == "function":
            yield key, child
        yield from _function_tables(child, key)


def _definitions(body: Sequence[ast.stmt], path: _Key = ()) -> Iterator[Tuple[_Key, ast.AST]]:
    """The ``def`` and ``class`` statements under ``body``, keyed as ``_function_tables`` keys them."""
    seen: Dict[str, int] = {}
    pending: List[ast.AST] = list(reversed(body))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith(_GENERATED_HELPER_PREFIXES):
                continue
            index = seen[node.name] = seen.get(node.name, -1) + 1
            key = path + (f"{node.name}#{index}",)
            yield key, node
            yield from _definitions(node.body, key)
        elif not isinstance(node, ast.expr):  # a def stands only among statements
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


def _comprehension_targets(function: ast.AST) -> frozenset[str]:
    """The names the comprehensions of ``function``'s own code bind for themselves."""
    names: set[str] = set()
    pending: List[ast.AST] = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.comprehension):
            names.update(
                target.id for target in ast.walk(node.target) if isinstance(target, ast.Name)
            )
        pending.extend(ast.iter_child_nodes(node))
    return frozenset(names)


def _locals(table: symtable.SymbolTable) -> frozenset[str]:
    return frozenset(symbol.get_name() for symbol in table.get_symbols() if symbol.is_local())


def _reads_through(table: symtable.SymbolTable, name: str, *, nested: bool = False) -> bool:
    """Whether code of ``table``, or of a scope nested in it, looks ``name`` up outside itself.

    A nested scope that binds the name, or declares it ``global``, reads its
    own; a class body's own binding hides the name from its own code only,
    since the functions in it look past it.
    """
    if name in table.get_identifiers():
        symbol = table.lookup(name)
        own = symbol.is_local() or symbol.is_declared_global()
        if nested and own and _kind(table) != "class":
            return False
        if not own and (symbol.is_referenced() or symbol.is_free() or symbol.is_nonlocal()):
            return True
    return any(_reads_through(child, name, nested=True) for child in table.get_children())


def scope_changes(before: Union[bytes, str], after: Union[bytes, str]) -> Dict[str, List[str]]:
    """How ``after`` changes the scope of names in the functions ``before`` defines, per function.

    A function the refactoring kept may gain no local, since its call binds
    only names the block bound. It may lose a local only with every read of
    it: the code that used the name went into the helper. A local it loses
    and still reads, or that a scope nested in it still reads, now finds an
    enclosing function's variable, a module name or a builtin (the round-4
    audit's P1-04). Python 3.12 and later fold a list, set or dict
    comprehension into its function's symbol table, so the names a
    comprehension binds for itself are set aside, as the CPython-match test
    sets them aside. Empty when every kept function keeps its scopes.
    """
    tables: List[Dict[_Key, symtable.SymbolTable]] = []
    definitions: List[Dict[_Key, ast.AST]] = []
    for source in (before, after):
        text = source if isinstance(source, str) else importlib.util.decode_source(source)
        tables.append(dict(_function_tables(symtable.symtable(text, "<m>", "exec"))))
        definitions.append(dict(_definitions(ast.parse(text).body)))
    changes: Dict[str, List[str]] = {}
    for key, old in tables[0].items():
        new = tables[1].get(key)
        if new is None:
            continue
        set_aside = _comprehension_targets(definitions[0][key]) | _comprehension_targets(
            definitions[1][key]
        )
        old_locals, new_locals = _locals(old) - set_aside, _locals(new) - set_aside
        found = [
            f"gains local {name}"
            for name in sorted(new_locals - old_locals)
            if not name.startswith(_GENERATED_HELPER_PREFIXES)
        ]
        found += [
            f"loses local {name}, which it still reads"
            for name in sorted(old_locals - new_locals)
            if _reads_through(new, name)
        ]
        if found:
            changes[".".join(key)] = found
    return changes


class ScopeWatch:
    """A ``file_finisher`` that holds every change Towel renders to ``scope_changes``.

    Towel hands the finisher each file it would write, while the file on disk
    still holds the text the change was made from, so each change is judged
    against its own starting point: a later pass cannot hide a scope an
    earlier one changed by moving the reading code into a helper too. The
    text is returned as it came; ``found`` holds what each change did, by the
    order the changes came in.
    """

    def __init__(self) -> None:
        self.found: List[Tuple[str, Dict[str, List[str]]]] = []

    def __call__(self, path: str, text: str) -> str:
        try:
            ast.parse(text)
        except SyntaxError:
            return text  # declined as it is rendered
        changes = scope_changes(Path(path).read_bytes(), text)
        if changes:
            self.found.append((Path(path).name, changes))
        return text

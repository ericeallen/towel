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


def _spelled_names(tree: ast.AST) -> set[str]:
    """Lexical hygiene oracle independent of Towel's identifier collector."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            found.update(node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            found.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            found.add(node.rest)
        parameters: object = getattr(node, "type_params", ())
        if isinstance(parameters, list):
            for parameter in parameters:
                name: object = getattr(parameter, "name", None)
                if isinstance(name, str):
                    found.add(name)
    return found


def _ownership_box(old: ast.AST, new: ast.AST, original: ast.Module) -> frozenset[str]:
    """Recognize a complete, hygienic caller-side transfer, never a name prefix.

    The helper's behavior is checked by the execution battery. This scope
    oracle independently proves that the one gained local owns exactly all
    original parameters until the terminal call consumes its sole tuple.
    """
    if not isinstance(old, ast.FunctionDef) or not isinstance(new, ast.FunctionDef):
        return frozenset()
    if ast.dump(old.args) != ast.dump(new.args):
        return frozenset()
    parameters = [arg.arg for arg in old.args.posonlyargs + old.args.args + old.args.kwonlyargs]
    parameters += [arg.arg for arg in (old.args.vararg, old.args.kwarg) if arg is not None]
    if not parameters:
        return frozenset()
    body = new.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) < 3 or not isinstance(body[0], ast.Assign):
        return frozenset()
    assigned = body[0]
    if len(assigned.targets) != 1 or not isinstance(assigned.targets[0], ast.Name):
        return frozenset()
    box = assigned.targets[0].id
    if box in _spelled_names(original):
        return frozenset()
    expected = ast.List(
        elts=[
            ast.Tuple(
                elts=[ast.Name(id=n, ctx=ast.Load()) for n in reversed(parameters)], ctx=ast.Load()
            )
        ],
        ctx=ast.Load(),
    )
    if ast.dump(assigned.value) != ast.dump(expected):
        return frozenset()
    deleted: List[str] = []
    for statement in body[1:-1]:
        if not isinstance(statement, ast.Delete) or any(
            not isinstance(target, ast.Name) for target in statement.targets
        ):
            return frozenset()
        deleted.extend(target.id for target in statement.targets if isinstance(target, ast.Name))
    if deleted != parameters:
        return frozenset()
    last = body[-1]
    call = (
        last.value if isinstance(last, (ast.Return, ast.Expr, ast.Assign, ast.AnnAssign)) else None
    )
    if not isinstance(call, ast.Call) or not call.args or call.keywords:
        return frozenset()
    callee = (
        call.func.id
        if isinstance(call.func, ast.Name)
        else call.func.attr if isinstance(call.func, ast.Attribute) else ""
    )
    if not callee.startswith(_GENERATED_HELPER_PREFIXES):
        return frozenset()
    consumed = ast.Call(
        func=ast.Attribute(value=ast.Name(id=box, ctx=ast.Load()), attr="pop", ctx=ast.Load()),
        args=[],
        keywords=[],
    )
    if ast.dump(call.args[-1]) != ast.dump(consumed):
        return frozenset()
    for value in ast.walk(last):
        if isinstance(value, (ast.Lambda, ast.GeneratorExp)) and any(
            isinstance(n, ast.Name) and n.id == box for n in ast.walk(value)
        ):
            return frozenset()
    parents = {child: parent for parent in ast.walk(last) for child in ast.iter_child_nodes(parent)}
    pop = call.args[-1]
    assert isinstance(pop, ast.Call) and isinstance(pop.func, ast.Attribute)
    pop_name = pop.func.value
    for name in ast.walk(last):
        if not isinstance(name, ast.Name):
            continue
        if name.id in parameters:
            return frozenset()  # no original parameter read after its explicit deletion
        if name.id != box or name is pop_name:
            continue
        parent = parents.get(name)
        outer = parents.get(parent) if parent is not None else None
        if not (
            isinstance(name.ctx, ast.Load)
            and isinstance(parent, ast.Subscript)
            and parent.value is name
            and isinstance(parent.slice, ast.Constant)
            and parent.slice.value == 0
            and isinstance(outer, ast.Subscript)
            and outer.value is parent
            and isinstance(outer.slice, ast.Constant)
            and type(outer.slice.value) is int
            and 0 <= outer.slice.value < len(parameters)
        ):
            return frozenset()
    return frozenset((box,))


def _bind_pair(old: str, new: str, names: Dict[str, str]) -> bool:
    """Extend an injective binder correspondence, never equate unrelated names."""
    if old in names:
        return names[old] == new
    if new in names.values():
        return False
    names[old] = new
    return True


def _paired_bindings(
    old: object,
    new: object,
    names: Dict[str, str],
    old_locals: frozenset[str],
    literals: Mapping[str, ast.Constant],
) -> bool:
    """Register own-frame binders before reads, including reads preceding a store."""
    if isinstance(old, ast.Constant) and isinstance(new, ast.Name):
        provided = literals.get(new.id)
        return (
            isinstance(new.ctx, ast.Load)
            and provided is not None
            and type(old.value) is type(provided.value)
            and old.value == provided.value
        )
    if type(old) is not type(new):
        return False
    if isinstance(old, ast.Name) and isinstance(new, ast.Name):
        if isinstance(new.ctx, (ast.Store, ast.Del)) and new.id in literals:
            return False
        return not isinstance(old.ctx, ast.Store) or (
            old.id in old_locals and _bind_pair(old.id, new.id, names)
        )
    if isinstance(old, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return True  # child scope binders are registered in _paired_syntax
    if isinstance(old, ast.alias) and isinstance(new, ast.alias):
        left, right = old.asname or old.name.split(".")[0], new.asname or new.name.split(".")[0]
        return left in old_locals and _bind_pair(left, right, names)
    if isinstance(old, ast.ExceptHandler) and isinstance(new, ast.ExceptHandler):
        if (old.name is None) != (new.name is None):
            return False
        if (
            old.name is not None
            and new.name is not None
            and not (old.name in old_locals and _bind_pair(old.name, new.name, names))
        ):
            return False
    if isinstance(old, ast.AST) and isinstance(new, ast.AST):
        return all(
            _paired_bindings(getattr(old, field), getattr(new, field), names, old_locals, literals)
            for field in old._fields
        )
    if isinstance(old, list) and isinstance(new, list):
        return len(old) == len(new) and all(
            _paired_bindings(a, b, names, old_locals, literals) for a, b in zip(old, new)
        )
    return True  # scalar values are checked by the full syntax correspondence


def _paired_syntax(
    old: object,
    new: object,
    names: Dict[str, str],
    old_locals: frozenset[str],
    literals: Mapping[str, ast.Constant],
) -> bool:
    """Independent alpha correspondence for a bounded, lexical AST subset.

    Parameter defaults and first comprehension iterables run in their outer
    scope. Child binders shadow both sides of the enclosing correspondence.
    Unproved definitions, declarations and assignment expressions stay visible
    to the scope oracle rather than gaining an exemption.
    """
    if isinstance(old, ast.Constant) and isinstance(new, ast.Name):
        provided = literals.get(new.id)
        return (
            isinstance(new.ctx, ast.Load)
            and provided is not None
            and type(old.value) is type(provided.value)
            and old.value == provided.value
        )
    if type(old) is not type(new):
        return False
    if isinstance(old, ast.Name) and isinstance(new, ast.Name):
        if isinstance(new.ctx, (ast.Store, ast.Del)) and new.id in literals:
            return False
        if type(old.ctx) is not type(new.ctx):
            return False
        if isinstance(old.ctx, ast.Store):
            return old.id in old_locals and _bind_pair(old.id, new.id, names)
        if old.id in names:
            return names[old.id] == new.id
        return old.id == new.id and new.id not in names.values() and new.id not in literals
    if isinstance(old, ast.Lambda) and isinstance(new, ast.Lambda):
        left, right = old.args, new.args
        for kind in ("posonlyargs", "args", "kwonlyargs"):
            if len(getattr(left, kind)) != len(getattr(right, kind)):
                return False
        if (left.vararg is None) != (right.vararg is None) or (left.kwarg is None) != (
            right.kwarg is None
        ):
            return False
        if not _paired_syntax(left.defaults, right.defaults, names, old_locals, literals) or not (
            _paired_syntax(left.kw_defaults, right.kw_defaults, names, old_locals, literals)
        ):
            return False

        def parameters(args: ast.arguments) -> List[str]:
            return [arg.arg for arg in args.posonlyargs + args.args + args.kwonlyargs] + [
                arg.arg for arg in (args.vararg, args.kwarg) if arg is not None
            ]

        old_parameters, new_parameters = parameters(left), parameters(right)
        child = {key: value for key, value in names.items() if key not in old_parameters}
        child_literals = {
            key: value for key, value in literals.items() if key not in new_parameters
        }
        if not all(_bind_pair(a, b, child) for a, b in zip(old_parameters, new_parameters)):
            return False
        return _paired_syntax(
            old.body, new.body, child, old_locals | frozenset(old_parameters), child_literals
        )
    comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    if isinstance(old, comprehensions) and isinstance(new, comprehensions):
        if len(old.generators) != len(new.generators) or not old.generators:
            return False
        old_targets = {
            n.id for g in old.generators for n in ast.walk(g.target) if isinstance(n, ast.Name)
        }
        new_targets = {
            n.id for g in new.generators for n in ast.walk(g.target) if isinstance(n, ast.Name)
        }
        child = {key: value for key, value in names.items() if key not in old_targets}
        child_literals = {key: value for key, value in literals.items() if key not in new_targets}
        for first, second in zip(old.generators, new.generators):
            if first.is_async != second.is_async or not _paired_syntax(
                first.target,
                second.target,
                child,
                old_locals | frozenset(old_targets),
                child_literals,
            ):
                return False
        for index, (first, second) in enumerate(zip(old.generators, new.generators)):
            scope = names if index == 0 else child
            scope_literals = literals if index == 0 else child_literals
            if not _paired_syntax(
                first.iter, second.iter, scope, old_locals, scope_literals
            ) or not (
                _paired_syntax(
                    first.ifs,
                    second.ifs,
                    child,
                    old_locals | frozenset(old_targets),
                    child_literals,
                )
            ):
                return False
        if isinstance(old, ast.DictComp) and isinstance(new, ast.DictComp):
            return _paired_syntax(
                old.key, new.key, child, old_locals, child_literals
            ) and _paired_syntax(old.value, new.value, child, old_locals, child_literals)
        if isinstance(old, (ast.ListComp, ast.SetComp, ast.GeneratorExp)) and isinstance(
            new, (ast.ListComp, ast.SetComp, ast.GeneratorExp)
        ):
            return _paired_syntax(old.elt, new.elt, child, old_locals, child_literals)
        return False
    if isinstance(
        old,
        (
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.Global,
            ast.Nonlocal,
            ast.NamedExpr,
            ast.Yield,
            ast.YieldFrom,
            ast.Await,
        ),
    ):
        return False
    if isinstance(old, ast.AST) and isinstance(new, ast.AST):
        return all(
            _paired_syntax(getattr(old, field), getattr(new, field), names, old_locals, literals)
            for field in old._fields
        )
    if isinstance(old, list) and isinstance(new, list):
        return len(old) == len(new) and all(
            _paired_syntax(a, b, names, old_locals, literals) for a, b in zip(old, new)
        )
    return old == new


def _retained_correspondence(
    old: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    new: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    assignment: ast.Assign,
    helper: ast.FunctionDef,
    old_locals: frozenset[str],
    old_free: frozenset[str],
) -> Dict[str, str]:
    """Match the actual removed block and helper body, preserving all surrounding syntax."""
    call = assignment.value
    old_type_parameters: object = getattr(old, "type_params", None)
    helper_type_parameters: object = getattr(helper, "type_params", None)
    if (
        ast.dump(old.args) != ast.dump(new.args)
        or bool(old_type_parameters)
        or bool(helper_type_parameters)
        or isinstance(old, ast.AsyncFunctionDef)
    ):
        return {}
    if not isinstance(call, ast.Call) or call.keywords or assignment not in new.body:
        return {}
    args = helper.args
    parameters = args.posonlyargs + args.args
    if (
        helper.decorator_list
        or args.defaults
        or args.kw_defaults
        or args.vararg
        or args.kwarg
        or args.kwonlyargs
        or len(parameters) != len(call.args)
    ):
        return {}
    if not all(isinstance(argument, (ast.Name, ast.Constant)) for argument in call.args):
        return {}
    parameter_names = {parameter.arg for parameter in parameters}
    if any(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.id in parameter_names
        or isinstance(node, ast.arg)
        and node.arg in parameter_names
        or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in parameter_names
        or isinstance(node, ast.alias)
        and (node.asname or node.name.split(".")[0]) in parameter_names
        or isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar))
        and node.name in parameter_names
        or isinstance(node, ast.MatchMapping)
        and node.rest in parameter_names
        for statement in helper.body
        for node in ast.walk(statement)
    ):
        return {}  # readonly inputs; nested masking of an input is conservatively unproved
    index = new.body.index(assignment)
    moved = helper.body[:-1]
    if not moved or len(old.body) != len(new.body) + len(moved) - 1:
        return {}
    if ast.dump(ast.Module(body=old.body[:index], type_ignores=[])) != ast.dump(
        ast.Module(body=new.body[:index], type_ignores=[])
    ) or ast.dump(ast.Module(body=old.body[index + len(moved) :], type_ignores=[])) != ast.dump(
        ast.Module(body=new.body[index + 1 :], type_ignores=[])
    ):
        return {}
    names: Dict[str, str] = {}
    literals: Dict[str, ast.Constant] = {}
    for argument, parameter in zip(call.args, parameters):
        if isinstance(argument, ast.Constant):
            if type(argument.value) not in (
                str,
                bytes,
                int,
                float,
                complex,
                bool,
                type(None),
                type(Ellipsis),
            ):
                return {}
            literals[parameter.arg] = argument
        elif not isinstance(argument, ast.Name) or not _bind_pair(
            argument.id, parameter.arg, names
        ):
            return {}
    inputs = frozenset(names)
    bindable = (
        old_locals - inputs
    )  # reading caller locals/parameters is safe; rebinding them is not
    block = old.body[index : index + len(moved)]
    if (old_free - inputs) & _spelled_names(ast.Module(body=block, type_ignores=[])):
        return {}  # an unpassed enclosing binding cannot become a module lookup
    if not _paired_bindings(block, moved, names, bindable, literals) or not _paired_syntax(
        block, moved, names, bindable, literals
    ):
        return {}
    return {key: value for key, value in names.items() if key in bindable}


def _unique_helper_binding(helper: ast.FunctionDef, module: ast.Module) -> bool:
    """No second definition, import, argument or explicit write redirects this fresh helper."""
    helper_index = module.body.index(helper)
    for index, statement in enumerate(module.body):
        for node in ast.walk(statement):
            if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
                if node is not statement or index > helper_index:
                    return False  # conditional or later wildcard lookup is unproved
    for node in ast.walk(module):
        if node is helper:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == helper.name:
                return False
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if node.id == helper.name:
                return False
        elif isinstance(node, ast.arg) and node.arg == helper.name:
            return False
        elif isinstance(node, ast.alias):
            if (node.asname or node.name.split(".")[0]) == helper.name:
                return False
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            if node.name == helper.name:
                return False
        elif isinstance(node, ast.MatchMapping) and node.rest == helper.name:
            return False
    return True


def _retained_locals(
    old: ast.AST,
    new: ast.AST,
    original: ast.Module,
    rewritten: ast.Module,
    old_locals: frozenset[str],
    old_free: frozenset[str],
) -> frozenset[str]:
    """Recognize fresh, unread tuple slots that keep a moved original local alive.

    Each slot must return the original local from a generated helper. An
    arbitrary new binding, a wrong slot, or any further use is not exempt.
    """
    if not isinstance(old, (ast.FunctionDef, ast.AsyncFunctionDef)) or not isinstance(
        new, (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        return frozenset()
    original_names = _spelled_names(original)
    candidates = [
        node
        for node in rewritten.body
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith(_GENERATED_HELPER_PREFIXES)
        and node.name not in original_names
    ]
    helpers = {
        node.name: node
        for node in candidates
        if sum(candidate.name == node.name for candidate in candidates) == 1
        and _unique_helper_binding(node, rewritten)
    }
    accepted: set[str] = set()
    for assignment in new.body:
        if not (
            isinstance(assignment, ast.Assign)
            and len(assignment.targets) == 1
            and isinstance(assignment.targets[0], (ast.Name, ast.Tuple))
            and isinstance(assignment.value, ast.Call)
            and isinstance(assignment.value.func, ast.Name)
        ):
            continue
        helper = helpers.get(assignment.value.func.id)
        if helper is None or not helper.body or not isinstance(helper.body[-1], ast.Return):
            continue
        correspondence = _retained_correspondence(
            old, new, assignment, helper, old_locals, old_free
        )
        returned = helper.body[-1].value
        target_root = assignment.targets[0]
        targets = target_root.elts if isinstance(target_root, ast.Tuple) else [target_root]
        values = returned.elts if isinstance(returned, ast.Tuple) else [returned]
        if len(values) != len(targets):
            continue
        original_slots = {mapped: name for name, mapped in correspondence.items()}
        paired_slots = True
        for target, value in zip(targets, values):
            if not isinstance(target, ast.Name) or not isinstance(value, ast.Name):
                paired_slots = False
                break
            original_name = original_slots.get(value.id)
            if original_name is None:
                paired_slots = False
                break
            if target.id in original_names:
                if target.id != original_name:
                    paired_slots = False
                    break
            else:
                stem = "_towel_keep_" + original_name
                if target.id != stem and not (
                    target.id.startswith(stem + "_") and target.id[len(stem) + 1 :].isdigit()
                ):
                    paired_slots = False
                    break
        if not paired_slots:
            continue
        for target, value in zip(targets, values):
            assert isinstance(target, ast.Name) and isinstance(value, ast.Name)
            if target.id in original_names:
                continue
            original_name = original_slots[value.id]
            # Exactly one Store and no Load, nested capture, or string binder.
            occurrences = [
                node
                for node in ast.walk(new)
                if isinstance(node, ast.Name) and node.id == target.id
            ]
            if occurrences != [target]:
                continue
            if any(
                {original_name, target.id} & _spelled_names(statement)
                for statement in new.body
                if statement is not assignment
            ):
                continue
            accepted.add(target.id)
    return frozenset(accepted)


def scope_changes(before: Union[bytes, str], after: Union[bytes, str]) -> Dict[str, List[str]]:
    """How ``after`` changes the scope of names in the functions ``before`` defines, per function.

    A kept function may gain only a structurally certified ownership box or
    an unread slot retaining an original moved local. It may lose a local only with every read of
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
    trees: List[ast.Module] = []
    for source in (before, after):
        text = source if isinstance(source, str) else importlib.util.decode_source(source)
        tables.append(dict(_function_tables(symtable.symtable(text, "<m>", "exec"))))
        tree = ast.parse(text)
        trees.append(tree)
        definitions.append(dict(_definitions(tree.body)))
    changes: Dict[str, List[str]] = {}
    for key, old in tables[0].items():
        new = tables[1].get(key)
        if new is None:
            continue
        set_aside = _comprehension_targets(definitions[0][key]) | _comprehension_targets(
            definitions[1][key]
        )
        old_locals, new_locals = _locals(old) - set_aside, _locals(new) - set_aside
        handoff = _ownership_box(definitions[0][key], definitions[1][key], trees[0])
        retained = _retained_locals(
            definitions[0][key],
            definitions[1][key],
            trees[0],
            trees[1],
            old_locals,
            frozenset(s.get_name() for s in old.get_symbols() if s.is_free() or s.is_nonlocal()),
        )
        found = [
            f"gains local {name}"
            for name in sorted(new_locals - old_locals)
            if not name.startswith(_GENERATED_HELPER_PREFIXES) and name not in handoff | retained
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

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

"""Whether pytest rewrites the ``assert`` statements of a module, as the project configures it.

pytest rewrites the asserts of the modules its import hook claims, and a
rewritten assert that fails raises ``AssertionError`` with pytest's account of
the values in its message; a plain one carries only the message it was given.
Exception messages are preserved, so an assert may move to another module
only when both are rewritten alike. The rules are pytest 9.1.1's
(``_pytest/assertion/rewrite.py``, ``AssertionRewritingHook``): a module is
rewritten when it is a ``conftest.py``, a file given on the command line, a
file matching ``python_files`` (``test_*.py`` and ``*_test.py`` by default,
matched as ``fnmatch_ex`` does), or a module named, or inside a package
named, by ``pytest.register_assert_rewrite``, by ``pytest_plugins``, by
``-p``, or by an installed distribution's ``pytest11`` entry point; never
under ``--assert=plain``, and never when its docstring holds
``PYTEST_DONT_REWRITE``. The configuration is the first of pytest's files
found from the project's root upward (``_pytest/config/findpaths.py``,
``locate_config``).

What depends on how pytest is invoked is not known, and every question it
could change is answered None: a pytest configuration below the root, which
an invocation from there would use instead; ``-o``, ``-c`` or ``--rootdir``
in ``addopts``; a ``pytest11`` entry point of the project itself, which
rewrites its packages when installed; a ``register_assert_rewrite`` or
``pytest_plugins`` pytest may or may not run (anywhere but at the top of the
root's ``conftest.py``), for the modules it names; and a value that is not
a literal. ``PYTEST_ADDOPTS``, ``PYTEST_PLUGINS`` and paths given on the
command line are the invoker's, and are taken to be absent.
"""

from __future__ import annotations

import ast
import configparser
import fnmatch
import os
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import (
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from ..consumers import SKIPPED_DIRECTORIES
from ..project_layout import find_project_root
from ..source_text import read_source
from .bounded_cache import BoundedCache

_DEFAULT_PATTERNS = ("test_*.py", "*_test.py")
# In the order ``locate_config`` tries them in each directory.
_CONFIG_NAMES = (
    "pytest.toml",
    ".pytest.toml",
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "tox.ini",
    "setup.cfg",
)
_Value = Union[str, List[str]]


@dataclass(frozen=True)
class _Setup:
    """What decides, for one project, which of its modules pytest rewrites."""

    known: bool
    """False when what the invocation does cannot be told from the project."""
    rewriting: bool = True
    patterns: Tuple[str, ...] = _DEFAULT_PATTERNS
    initial_paths: FrozenSet[str] = frozenset()
    marked: FrozenSet[str] = frozenset()
    """Module names surely marked for rewriting, each with every module inside it."""
    uncertain: FrozenSet[str] = frozenset()
    """Module names a statement pytest may or may not run marks."""


_UNKNOWN = _Setup(known=False)
_SETUPS: BoundedCache[str, _Setup] = BoundedCache(32)


def rewrites_asserts(
    path: str, project_root: Callable[[Path], Path] = find_project_root
) -> Optional[bool]:
    """Whether pytest, run as the project configures it, rewrites the asserts of the module at ``path``.

    None when that cannot be told from the project (see the module docstring).
    ``project_root`` finds the project a resolved path belongs to; an engine
    passes its run's cache of them, since every pair asks.
    """
    root = os.path.realpath(project_root(Path(path).resolve()))
    setup = _SETUPS.get(root)
    if setup is None:
        setup = _SETUPS.put(root, _read_setup(Path(root)))
    if not setup.known:
        return None
    if not setup.rewriting:
        return False
    real = os.path.realpath(path)
    try:
        tree = ast.parse(read_source(real))
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return None
    docstring = ast.get_docstring(tree, clean=False)
    if docstring is not None and "PYTEST_DONT_REWRITE" in docstring:
        return False
    name = _module_name(Path(real))
    if any(_inside(name, marked) for marked in setup.uncertain):
        return None
    return (
        os.path.basename(real) == "conftest.py"
        or real in setup.initial_paths
        or any(_fnmatch_ex(pattern, real) for pattern in setup.patterns)
        or any(_inside(name, marked) for marked in setup.marked)
    )


def rewritten_alike(
    paths: Iterable[str], project_root: Callable[[Path], Path] = find_project_root
) -> bool:
    """Whether pytest rewrites the asserts of every module of ``paths`` alike, as far as is known."""
    statuses = {rewrites_asserts(path, project_root) for path in paths}
    return None not in statuses and len(statuses) == 1


def _inside(name: str, marked: str) -> bool:
    return name == marked or name.startswith(marked + ".")


def _fnmatch_ex(pattern: str, path: str) -> bool:
    """pytest's ``fnmatch_ex`` (``_pytest/pathlib.py``) on POSIX: a bare pattern matches the name."""
    if os.sep not in pattern:
        return fnmatch.fnmatch(PurePath(path).name, pattern)
    if os.path.isabs(path) and not os.path.isabs(pattern):
        pattern = f"*{os.sep}{pattern}"
    return fnmatch.fnmatch(path, pattern)


def _module_name(path: Path) -> str:
    """The name a module is imported by: its path from the first directory up with no ``__init__.py``."""
    parts = [path.stem] if path.name != "__init__.py" else []
    directory = path.parent
    while (directory / "__init__.py").is_file():
        parts.append(directory.name)
        directory = directory.parent
    return ".".join(reversed(parts))


# -- Reading the project -------------------------------------------------------


def _read_setup(root: Path) -> _Setup:
    """What decides rewriting in the project at ``root``; ``_UNKNOWN`` where the project cannot say."""
    if _declares_pytest_plugin(root):
        return _UNKNOWN
    located = _locate_config(root)
    if located is None:
        return _UNKNOWN
    config_dir, config = located
    if _configures_below(root):
        return _UNKNOWN
    options = _arguments(config.get("addopts", []))
    if options is None:
        return _UNKNOWN
    rewriting, plugins = _read_options(options)
    if plugins is None:
        return _UNKNOWN
    patterns = _arguments(config.get("python_files", list(_DEFAULT_PATTERNS)))
    testpaths = _arguments(config.get("testpaths", []))
    if patterns is None or testpaths is None or any("*" in entry for entry in testpaths):
        return _UNKNOWN
    marks = _plugin_marks(root, config_dir)
    if marks is None:
        return _UNKNOWN
    sure, uncertain = marks
    return _Setup(
        known=True,
        rewriting=rewriting,
        patterns=tuple(patterns),
        initial_paths=frozenset(
            os.path.realpath(config_dir / entry) for entry in testpaths if entry.endswith(".py")
        ),
        marked=frozenset(plugins) | sure,
        uncertain=uncertain,
    )


def _declares_pytest_plugin(root: Path) -> bool:
    """Whether the project's metadata may declare a ``pytest11`` entry point, whose packages pytest rewrites once installed."""
    for name in ("pyproject.toml", "setup.cfg", "setup.py"):
        try:
            if "pytest11" in (root / name).read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def _locate_config(root: Path) -> Optional[Tuple[Path, Mapping[str, _Value]]]:
    """The directory and values of the configuration pytest reads for ``root``; None if unreadable.

    The first directory from ``root`` upward holding one of ``_CONFIG_NAMES``
    with pytest configuration, as ``locate_config`` walks; failing that, the
    first ``pyproject.toml`` found, with none; failing that, ``root``.
    """
    first_pyproject: Optional[Path] = None
    for directory in (root, *root.parents):
        for name in _CONFIG_NAMES:
            path = directory / name
            if not path.is_file():
                continue
            if name == "pyproject.toml" and first_pyproject is None:
                first_pyproject = directory
            try:
                values = _config_values(path)
            except (OSError, UnicodeError, ValueError, configparser.Error, tomllib.TOMLDecodeError):
                return None
            if values is not None:
                return directory, values
    return (first_pyproject or root), {}


def _config_values(path: Path) -> Optional[Dict[str, _Value]]:
    """The pytest values ``path`` holds, as ``load_config_dict_from_file`` reads them; None if none."""
    if path.suffix == ".toml":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if path.name in {"pytest.toml", ".pytest.toml"}:
            return _toml_values(data.get("pytest", {}))
        table = data.get("tool", {}).get("pytest")
        if not isinstance(table, dict):
            return None
        native = {key: value for key, value in table.items() if key != "ini_options"}
        if native:
            return _toml_values(native)
        ini = table.get("ini_options")
        return None if ini is None else _toml_values(ini)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(path.read_text(encoding="utf-8"), source=str(path))
    section = "tool:pytest" if path.suffix == ".cfg" else "pytest"
    if parser.has_section(section):
        return dict(parser.items(section))
    return {} if path.name in {"pytest.ini", ".pytest.ini"} else None


def _toml_values(table: object) -> Dict[str, _Value]:
    if not isinstance(table, dict):
        raise ValueError("a pytest table that is not a table")
    return {
        str(key): ([str(item) for item in value] if isinstance(value, list) else str(value))
        for key, value in table.items()
    }


def _arguments(value: _Value) -> Optional[List[str]]:
    """An ``args``-typed value as pytest splits it: a list as given, a string by shell rules."""
    if isinstance(value, list):
        return value
    try:
        return shlex.split(value)
    except ValueError:
        return None


def _read_options(options: Sequence[str]) -> Tuple[bool, Optional[List[str]]]:
    """Whether ``addopts`` leaves rewriting on, and the plugins its ``-p`` loads; None if unknown."""
    rewriting = True
    plugins: List[str] = []
    tokens = list(options)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token in {"-o", "-c", "--override-ini", "--config-file", "--inifile", "--rootdir"} or (
            token.startswith(("-o", "--override-ini=", "--config-file=", "--rootdir="))
        ):
            return rewriting, None
        if token == "--assert" and index < len(tokens):
            rewriting = tokens[index] != "plain"
            index += 1
        elif token.startswith("--assert="):
            rewriting = token != "--assert=plain"
        elif token == "-p" and index < len(tokens):
            plugins.append(tokens[index].strip())
            index += 1
        elif token.startswith("-p"):
            plugins.append(token[2:].strip())
    return rewriting, [plugin for plugin in plugins if not plugin.startswith("no:")]


def _project_files(root: Path) -> Iterable[Path]:
    for parent, directories, files in os.walk(root, onerror=lambda _: None):
        directories[:] = sorted(name for name in directories if name not in SKIPPED_DIRECTORIES)
        for name in sorted(files):
            yield Path(parent, name)


def _configures_below(root: Path) -> bool:
    """Whether a directory below ``root`` holds pytest configuration an invocation there would read."""
    for path in _project_files(root):
        if path.parent == root or path.name not in _CONFIG_NAMES:
            continue
        try:
            if _config_values(path) is not None:
                return True
        except (OSError, UnicodeError, ValueError, configparser.Error, tomllib.TOMLDecodeError):
            return True
    return False


def _plugin_marks(root: Path, config_dir: Path) -> Optional[Tuple[FrozenSet[str], FrozenSet[str]]]:
    """The module names the project marks for rewriting: surely, and perhaps; None if unknown.

    A mark is sure only at the top level of the ``conftest.py`` beside the
    configuration, which pytest loads first: a ``pytest_plugins`` literal, or
    a ``register_assert_rewrite`` call statement with literal names.
    Anywhere else pytest may or may not run it.
    """
    sure: Set[str] = set()
    uncertain: Set[str] = set()
    root_conftest = os.path.realpath(config_dir / "conftest.py")
    for path in _project_files(root):
        if path.suffix != ".py":
            continue
        try:
            source = read_source(str(path))
        except (OSError, UnicodeError, ValueError):
            continue
        if "pytest_plugins" not in source and "register_assert_rewrite" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        found = _marks_in(tree)
        if found is None:
            return None
        certain, anywhere = found
        if os.path.realpath(path) == root_conftest:
            sure |= certain
            uncertain |= anywhere - certain
        else:
            uncertain |= anywhere
    return frozenset(sure), frozenset(uncertain)


def _marks_in(tree: ast.Module) -> Optional[Tuple[Set[str], Set[str]]]:
    """The names a module marks: at its top level, and anywhere; None when one is not a literal."""
    top: Set[str] = set()
    anywhere: Set[str] = set()
    top_level = {id(statement) for statement in tree.body}
    for node in ast.walk(tree):
        names: Optional[List[str]] = None
        statement_id: Optional[int] = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytest_plugins"
            for target in node.targets
        ):
            names = _literal_names(node.value)
            statement_id = id(node)
        elif isinstance(node, ast.Call) and _spelled(node.func).endswith("register_assert_rewrite"):
            literal = [
                argument.value
                for argument in node.args
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            ]
            names = literal if len(literal) == len(node.args) and not node.keywords else None
            statement_id = next(
                (
                    id(statement)
                    for statement in tree.body
                    if isinstance(statement, ast.Expr) and statement.value is node
                ),
                None,
            )
        else:
            continue
        if names is None:
            return None
        anywhere.update(names)
        if statement_id in top_level:
            top.update(names)
    return top, anywhere


def _literal_names(value: ast.expr) -> Optional[List[str]]:
    """A ``pytest_plugins`` value: a string of comma-separated names, or a sequence of strings."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return [name for name in value.value.split(",") if name]
    if isinstance(value, (ast.List, ast.Tuple)) and all(
        isinstance(item, ast.Constant) and isinstance(item.value, str) for item in value.elts
    ):
        return [str(item.value) for item in value.elts if isinstance(item, ast.Constant)]
    return None


def _spelled(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_spelled(node.value)}.{node.attr}"
    return ""

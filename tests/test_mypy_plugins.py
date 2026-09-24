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

"""A project's configured mypy plugins take part in every check, as in its own mypy run.

A plugin is a type rule: django-stubs, pydantic and SQLAlchemy each change
what an expression's type is. The worker used to drop them, so a project
whose types come from its plugins was judged by a mypy that would not run
there -- two arguments the project's mypy rejects were accepted -- and a
project whose own mypy cannot even start, because a plugin it names does not
load, was refactored with exit status 0. Plugins now load as the project's
mypy loads them; one that cannot load refuses the typed run before anything
is written, quoting mypy's own message.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import List

import pytest

pytest.importorskip("mypy")

from towel.type_inference import CheckFailure, CheckSuccess, MypyInferrer  # noqa: E402
from towel.unification.exceptions import RefactoringError  # noqa: E402
from towel.unification.refactor_engine import UnificationRefactorEngine  # noqa: E402
from tests.test_cli_integration import invoke  # noqa: E402

PLUGIN = """
from typing import Callable, Optional
from mypy.plugin import FunctionContext, Plugin
from mypy.types import Type


def _{result}(ctx: FunctionContext) -> Type:
    return ctx.api.named_generic_type("builtins.{result}", [])


class MagicPlugin(Plugin):
    def get_function_hook(self, fullname: str) -> Optional[Callable[[FunctionContext], Type]]:
        return _{result} if fullname == "alpha.magic.magic" else None


def plugin(version: str) -> type[Plugin]:
    return MagicPlugin
"""

MAGIC = "def magic(n: int) -> int:\n    return n\n"

SITE = """
from alpha.magic import magic


def {name}(items: list[int]) -> None:
    {preamble}
    print("start {name}")
    for item in items:
        print(value, item)
        print(item, value)
    print(value)
    print("done", {number})
"""


def _project(root: Path, plugins: str = '"myplugin.py"', result: str = "str") -> Path:
    """The release audit's k01 case: ``magic`` is ``int`` to mypy, ``str`` to its plugin."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        textwrap.dedent(f"""
            [project]
            name = "alpha"
            version = "0.1"
            requires-python = ">=3.10"

            [tool.mypy]
            strict = true
            plugins = [{plugins}]
            exclude = ["myplugin.py"]
            """).lstrip(),
        encoding="utf-8",
    )
    (root / "myplugin.py").write_text(PLUGIN.format(result=result), encoding="utf-8")
    package = root / "src" / "alpha"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "magic.py").write_text(MAGIC, encoding="utf-8")
    (package / "a.py").write_text(
        SITE.format(name="fa", preamble="value = magic(len(items))", number=1).lstrip(),
        encoding="utf-8",
    )
    (package / "b.py").write_text(
        SITE.format(
            name="fb",
            preamble='with open("/dev/null") as fh:\n        fh.read()\n        value = magic(7)',
            number=2,
        ).lstrip(),
        encoding="utf-8",
    )
    return root


def _fresh_mypy(root: Path) -> List[str]:
    """The project's own mypy, run as its developers would run it, plugins and all."""
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-incremental", "--cache-dir=/dev/null", "src"],
        cwd=root,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    return [line for line in completed.stdout.splitlines() if ": error:" in line]


def _consumer(root: Path) -> Path:
    path = root / "src" / "alpha" / "consumer.py"
    path.write_text("from alpha.magic import magic\n\nvalue: int = magic(1)\n", encoding="utf-8")
    return path


def test_a_check_sees_the_types_the_projects_plugin_gives(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")
    consumer = _consumer(root)
    expected = _fresh_mypy(root)
    assert any("consumer.py" in line for line in expected), expected
    checker = MypyInferrer()
    try:
        result = checker.check(str(consumer), consumer.read_text(encoding="utf-8"))
    finally:
        checker.close()
    assert isinstance(result, CheckSuccess), result
    assert [error.path for error in result.errors] == [str(consumer)], result.errors
    assert "incompatible types in assignment" in result.errors[0].message.lower()


def test_a_changed_plugin_is_what_the_next_check_uses(tmp_path: Path) -> None:
    """mypy's cache records each plugin's digest; a warm cache must not outlive the plugin.

    The module the plugin changes is read from disk and answered from the
    cache, not supplied, so only the cache's own plugin check can make the
    second build look at it again.
    """
    root = _project(tmp_path / "project", result="int")
    consumer = _consumer(root)
    magic = root / "src" / "alpha" / "magic.py"
    checker = MypyInferrer()
    try:
        first = checker.check(str(magic), magic.read_text(encoding="utf-8"))
        assert first == CheckSuccess(), first
        (root / "myplugin.py").write_text(PLUGIN.format(result="str"), encoding="utf-8")
        second = checker.check(str(magic), magic.read_text(encoding="utf-8"))
        assert isinstance(second, CheckSuccess), second
        assert [error.path for error in second.errors] == [str(consumer)], second
    finally:
        checker.close()


def test_loading_a_plugin_writes_nothing_into_the_project(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")
    consumer = _consumer(root)
    checker = MypyInferrer()
    try:
        checker.check(str(consumer), consumer.read_text(encoding="utf-8"))
    finally:
        checker.close()
    assert not (root / "__pycache__").exists(), "the plugin's bytecode stays out of the tree"


@pytest.mark.parametrize(
    "plugins, quoted",
    [
        ('"no_such_plugin_xyz"', 'Error importing plugin "no_such_plugin_xyz"'),
        ('"missing_plugin.py"', "Can't find plugin"),
    ],
)
def test_a_plugin_that_cannot_load_refuses_the_run_before_anything_is_written(
    tmp_path: Path, plugins: str, quoted: str
) -> None:
    root = _project(tmp_path / "project", plugins=plugins)
    before = {path: path.read_bytes() for path in root.rglob("*.py")}
    checker = MypyInferrer()
    try:
        result = checker.check(
            str(root / "src" / "alpha" / "a.py"),
            (root / "src" / "alpha" / "a.py").read_text(encoding="utf-8"),
        )
        assert isinstance(result, CheckFailure), result
        assert quoted in result.reason, result.reason
        assert "importable" in result.reason, result.reason
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=checker)
        with pytest.raises(RefactoringError, match=r"(?s)Original project type check.*--no-types"):
            engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
    finally:
        checker.close()
    assert {path: path.read_bytes() for path in root.rglob("*.py")} == before


def test_a_plugin_that_cannot_import_the_project_refuses_with_its_own_error(
    tmp_path: Path,
) -> None:
    """django-stubs imports the settings module; under -I the project is not on sys.path.

    mypy puts a ``.py`` plugin's directory on the path while importing it, so
    a flat layout would import; this project keeps its package under ``src``,
    as the project's own mypy run would also find.
    """
    root = _project(tmp_path / "project")
    (root / "myplugin.py").write_text(
        "import alpha.settings\n" + PLUGIN.format(result="str"), encoding="utf-8"
    )
    (root / "src" / "alpha" / "settings.py").write_text("DEBUG = True\n", encoding="utf-8")
    checker = MypyInferrer()
    try:
        result = checker.check(str(root / "src" / "alpha" / "magic.py"), MAGIC)
    finally:
        checker.close()
    assert isinstance(result, CheckFailure), result
    assert "No module named 'alpha'" in result.reason, result.reason


def test_a_refactoring_of_a_plugin_project_passes_the_projects_own_mypy(tmp_path: Path) -> None:
    """The audit's k01: the helper is typed as the plugin types it, so mypy accepts it."""
    root = _project(tmp_path / "project")
    assert _fresh_mypy(root) == []
    result = invoke(
        [
            "dry",
            str(root),
            str(root),
            "--no-interactive",
            "--no-format",
            "--cross-module",
            "--progress",
            "none",
        ]
    )
    assert result.status == 0, result
    assert "__extracted_func_0" in (root / "src" / "alpha" / "a.py").read_text(encoding="utf-8")
    assert _fresh_mypy(root) == []


def test_a_configured_interpreter_is_still_never_run(tmp_path: Path) -> None:
    """Plugins are type rules and load; ``python_executable`` is not and does not run."""
    marker = tmp_path / "executed"
    executable = tmp_path / "python"
    executable.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    executable.chmod(0o700)
    (tmp_path / "mypy.ini").write_text(
        f"[mypy]\npython_executable = {executable}\ndisallow_any_explicit = True\n"
    )
    path = tmp_path / "example.py"
    source = "from typing import Any\ndef f(x: Any) -> Any:\n    return x\n"
    path.write_text(source)
    checker = MypyInferrer()
    try:
        result = checker.check(str(path), source)
    finally:
        checker.close()
    assert isinstance(result, CheckSuccess)
    assert any('Explicit "Any"' in error.message for error in result.errors)
    assert not marker.exists()

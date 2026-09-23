"""Generated annotations evaluate on the oldest Python the project supports.

A module without ``from __future__ import annotations`` evaluates each
annotation when the function is defined. mypy spells an optional ``int`` as
``int | None`` and a list as ``list[int]``, and checks at the interpreter it
runs on, so a project declaring ``requires-python = ">=3.9"`` accepted
``v: int | None`` in a helper, and the adopted wheel raised ``TypeError`` at
import on Python 3.9 (the audit's k40). An annotation using syntax newer than
the project's oldest Python is now written as a string, which no interpreter
evaluates and every checker reads as the same type -- the same quotation that
already keeps annotations with calls in them from running.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess
import textwrap
from typing import Optional, Tuple

import pytest

from towel.unification.annotation_wiring import declared_oldest_python, python_lower_bound
from towel.unification.annotations import (
    OLDEST_PYTHON,
    evaluated_syntax,
    syntax_needs,
    written_for_python,
)
from tests.test_cli_integration import invoke


def _expression(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body


@pytest.mark.parametrize(
    "annotation, host, needs",
    [
        ("int", "", OLDEST_PYTHON),
        ("Optional[int]", "from typing import Optional\n", OLDEST_PYTHON),
        ("Callable[[int], str]", "from typing import Callable\n", OLDEST_PYTHON),
        ("list[int]", "", (3, 9)),
        ("type[C]", "class C: ...\n", (3, 9)),
        ("tuple[int, ...]", "", (3, 9)),
        ("Sequence[int]", "from collections.abc import Sequence\n", (3, 9)),
        ("collections.abc.Sequence[int]", "import collections.abc\n", (3, 9)),
        ("int | None", "", (3, 10)),
        ("dict[str, list[int]] | None", "", (3, 10)),
    ],
)
def test_what_each_annotation_needs(annotation: str, host: str, needs: Tuple[int, int]) -> None:
    assert syntax_needs(_expression(annotation), ast.parse(host)) == needs


def _helper(signature: str) -> ast.FunctionDef:
    node = ast.parse(f"def helper{signature}:\n    return None\n").body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def _signature(helper: ast.FunctionDef) -> str:
    return ast.unparse(helper).splitlines()[0]


def test_younger_syntax_is_written_as_a_string_for_an_older_python() -> None:
    helper = _helper("(v: int | None, w: list[int]) -> int | None")
    host = ast.parse("def other(x: int) -> int:\n    return x\n")
    written = written_for_python(helper, host, (3, 9))
    assert _signature(written) == "def helper(v: 'int | None', w: list[int]) -> 'int | None':"
    older = written_for_python(helper, host, (3, 8))
    assert _signature(older) == "def helper(v: 'int | None', w: 'list[int]') -> 'int | None':"
    assert _signature(helper) == "def helper(v: int | None, w: list[int]) -> int | None:"
    current = written_for_python(helper, host, (3, 10))
    assert _signature(current) == _signature(helper)


def test_a_module_that_postpones_annotations_keeps_them_bare() -> None:
    helper = _helper("(v: int | None) -> list[int]")
    host = ast.parse("from __future__ import annotations\n")
    assert _signature(written_for_python(helper, host, OLDEST_PYTHON)) == _signature(helper)


def test_what_the_module_already_evaluates_it_can_evaluate_again() -> None:
    """A module whose own signatures use ``int | None`` bare cannot import before 3.10."""
    host = ast.parse(textwrap.dedent("""
            from typing import Optional

            def mine(x: int | None) -> list[int]:
                return []

            class Kept:
                size: dict[str, int]

            if True:
                def conditional(x: tuple[int, ...] | None) -> None: ...
            """))
    assert evaluated_syntax(host) == (3, 10)
    assert evaluated_syntax(ast.parse("def f(x: list[int]) -> None: ...\n")) == (3, 9)
    assert evaluated_syntax(ast.parse("from __future__ import annotations\nx: int | None\n")) == (
        OLDEST_PYTHON
    )
    assert evaluated_syntax(ast.parse("if True:\n    def f(x: int | None): ...\n")) == (
        OLDEST_PYTHON
    ), "only what every import runs is evidence"


@pytest.mark.parametrize(
    "specifier, oldest",
    [
        (">=3.9", (3, 9)),
        (">= 3.9, <4", (3, 9)),
        ("~=3.8", (3, 8)),
        (">3.8", (3, 8)),
        ("==3.10.*", (3, 10)),
        ("!=3.9.0,>=3.8", (3, 8)),
        (">=3.8,>=3.10", (3, 10)),
        (">=3.9.2", (3, 9)),
        ("^3.9", (3, 9)),
        ("~3.9", (3, 9)),
        ("<4", None),
        ("", None),
        ("any", None),
    ],
)
def test_the_lower_bound_of_a_python_requirement(
    specifier: str, oldest: Optional[Tuple[int, int]]
) -> None:
    assert python_lower_bound(specifier) == oldest


def test_requires_python_is_read_before_the_checkers_versions(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\nrequires-python = ">=3.9"\n'
        '[tool.mypy]\npython_version = "3.11"\n[tool.pyright]\npythonVersion = "3.10"\n',
        encoding="utf-8",
    )
    assert declared_oldest_python(tmp_path / "m.py") == (3, 9)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mypy]\npython_version = "3.11"\n[tool.pyright]\npythonVersion = "3.10"\n',
        encoding="utf-8",
    )
    assert declared_oldest_python(tmp_path / "m.py") == (3, 10), "the older checker target"
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")
    (tmp_path / "setup.cfg").write_text("[options]\npython_requires = >=3.8\n", encoding="utf-8")
    assert declared_oldest_python(tmp_path / "m.py") == (3, 8)
    (tmp_path / "setup.cfg").unlink()
    assert declared_oldest_python(tmp_path / "m.py") is None


PYPROJECT = """
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "alpha"
version = "0.1"
requires-python = ">=3.9"

[tool.mypy]
strict = true
"""

A = """
from typing import Dict, List, Optional


def fa(items: List[int], table: Dict[str, int]) -> Optional[int]:
    v = table.get("k")
    w = [x for x in items]
    print("start a")
    for item in w:
        print(v, item)
        print(item, v)
    print(v)
    print("done", 1)
    return v
"""

B = """
from typing import Dict, List, Optional


def fb(items: List[int], table: Dict[str, int]) -> Optional[int]:
    with open("/dev/null") as fh:
        fh.read()
        v = table.get(fh.name)
        w = list(items)
    print("start bb")
    for item in w:
        print(v, item)
        print(item, v)
    print(v)
    print("done", 2)
    return v
"""


def _python_39() -> Optional[str]:
    """A Python 3.9 interpreter, from PATH or from uv's managed installs."""
    found = shutil.which("python3.9")
    if found is None and shutil.which("uv") is not None:
        located = subprocess.run(
            ["uv", "python", "find", "3.9"], capture_output=True, text=True, check=False
        )
        found = located.stdout.strip() or None if located.returncode == 0 else None
    return found


def test_a_project_declaring_python_39_imports_on_python_39_after_a_typed_run(
    tmp_path: Path,
) -> None:
    """The audit's k40, adopted and imported on the Python it declares."""
    pytest.importorskip("mypy")
    python = _python_39()
    if python is None:
        pytest.skip("no Python 3.9 interpreter found; `uv python install 3.9` provides one")
    root = tmp_path / "project"
    package = root / "src" / "alpha"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(PYPROJECT.lstrip(), encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "a.py").write_text(A.lstrip(), encoding="utf-8")
    (package / "b.py").write_text(B.lstrip(), encoding="utf-8")
    result = invoke(
        ["dry", str(root), str(root), "--no-interactive", "--cross-module", "--progress", "none"]
    )
    assert result.status == 0, result
    assert "__extracted_func_0" in (package / "a.py").read_text(encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    environment["PYTHONPATH"] = str(root / "src")
    imported = subprocess.run(
        [python, "-c", "import alpha.a, alpha.b; print(alpha.b.fb([1, -2], {}))"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.splitlines()[-1] == "None", imported.stdout

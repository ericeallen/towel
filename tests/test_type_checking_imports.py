"""An annotation may name a class the module cannot reach; the import says where it is.

A checker answers with a whole path. Written into a module that never imports
that submodule it is not a name at all, however plainly its head is bound: the
package object carries no such attribute. The import is stated under
``TYPE_CHECKING``, because the name is wanted only by an annotation and an
ordinary import would often close a cycle -- the extraction has just made the
other module import this one.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import List

import pytest

from towel.unification.annotations import (
    qualified_names_in_annotations,
    shorten_qualified_names,
)

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


def _helper(signature: str) -> ast.FunctionDef:
    parsed = ast.parse(f"def f({signature}) -> None: ...").body[0]
    assert isinstance(parsed, ast.FunctionDef)
    return parsed


def test_a_chain_is_taken_whole_and_its_prefixes_are_not_names() -> None:
    """``pkg.mod.Klass`` is one name; ``pkg`` and ``pkg.mod`` are not used as types."""
    assert qualified_names_in_annotations(_helper("a: pkg.mod.Klass")) == ["pkg.mod.Klass"]


def test_every_chain_in_a_compound_annotation_is_reported() -> None:
    found = qualified_names_in_annotations(_helper("a: x.A | y.B, b: list[z.C]"))
    assert found == ["x.A", "y.B", "z.C"]


def test_a_bare_name_is_not_a_chain() -> None:
    assert qualified_names_in_annotations(_helper("a: int, b: Klass")) == []


def test_shortening_rewrites_only_what_it_was_given() -> None:
    helper = _helper("a: x.A | y.B")
    shorten_qualified_names(helper, {"y.B": "B"})
    written = helper.args.args[0].annotation
    assert written is not None and ast.unparse(written) == "x.A | B"


def _package(root: Path) -> Path:
    """Two modules whose identical methods can only be shared through an import."""
    package = root / "pkg"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name, tag in (("alpha", "a"), ("beta", "b")):
        (package / f"{name}.py").write_text(
            textwrap.dedent(f"""
                from __future__ import annotations

                import pkg


                class {name.capitalize()}:
                    def __init__(self) -> None:
                        self.tag = "{tag}"

                    def render(self, width: int) -> str:
                        body = str(self.tag).strip()
                        padded = body.rjust(width, ".")
                        return padded.upper()
                """).lstrip(),
            encoding="utf-8",
        )
    return package


@requires_mypy
def test_a_cross_module_helper_names_the_other_class_and_still_type_checks(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(package),
            str(package),
            "--no-interactive",
            "--cross-module",
            "--progress",
            "none",
            "--min-lines",
            "3",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dropped a proposal" not in result.stdout + result.stderr

    host = (package / "alpha.py").read_text(encoding="utf-8")
    assert "if TYPE_CHECKING:" in host
    assert "from pkg.beta import Beta" in host
    helper = next(
        node
        for node in ast.walk(ast.parse(host))
        if isinstance(node, ast.FunctionDef) and "extracted" in node.name
    )
    receiver = helper.args.args[0].annotation
    assert receiver is not None and "Beta" in ast.unparse(receiver)

    checked = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(package)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=600,
    )
    assert "Success" in checked.stdout, checked.stdout + checked.stderr


@requires_mypy
def test_the_guarded_import_does_not_run(tmp_path: Path) -> None:
    """A cycle would be fatal at import time; under TYPE_CHECKING nothing executes."""
    package = _package(tmp_path)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(package),
            str(package),
            "--no-interactive",
            "--progress",
            "none",
            "--min-lines",
            "3",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=600,
        check=True,
    )
    ran = subprocess.run(
        [sys.executable, "-c", "from pkg.beta import Beta; print(Beta().render(6))"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip().endswith("B")


def _runtime_imports(source: str) -> set[str]:
    """The imports a module runs at its top level: every one not under ``TYPE_CHECKING``."""
    tree = ast.parse(source)
    guarded = {
        id(node)
        for statement in tree.body
        if isinstance(statement, ast.If)
        and isinstance(statement.test, ast.Name)
        and statement.test.id == "TYPE_CHECKING"
        for node in ast.walk(statement)
    }
    return {
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in guarded
    }


def _cross_module_project(root: Path) -> Path:
    """A host that needs another module's class in a signature, deferring nothing.

    Python 3.11 evaluates an annotation at definition time unless the module
    says otherwise, and the import that makes this name reachable is one only a
    checker reads.
    """
    (root / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "other.py").write_text(
        "class Thing:\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n",
        encoding="utf-8",
    )
    (package / "host.py").write_text(
        textwrap.dedent("""
            import pkg.other


            def make_a() -> 'pkg.other.Thing':
                return pkg.other.Thing('a')


            def make_b() -> 'pkg.other.Thing':
                return pkg.other.Thing('b')


            def first() -> str:
                t = make_a()
                s = str(t.name).strip()
                u = s.upper()
                return u + '!'


            def second() -> str:
                t = make_b()
                s = str(t.name).strip()
                u = s.upper()
                return u + '!'
            """).lstrip(),
        encoding="utf-8",
    )
    return package


@requires_mypy
def test_a_guarded_name_is_quoted_where_the_module_evaluates_its_annotations(
    tmp_path: Path,
) -> None:
    """Type-checking and importing are different questions and both must be answered.

    The helper stays in its module, so the run needs no ``--cross-module``;
    the import its annotation needs is one only a checker reads, so it is
    written anyway, spelled as the module spells its own package, and it is
    the only import the run adds besides ``TYPE_CHECKING`` itself.
    """
    package = _cross_module_project(tmp_path)
    before = _runtime_imports((package / "host.py").read_text(encoding="utf-8"))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(package),
            str(package),
            "--no-interactive",
            "--progress",
            "none",
            "--min-lines",
            "3",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    host = (package / "host.py").read_text(encoding="utf-8")
    assert "if TYPE_CHECKING:\n    from pkg.other import Thing\n" in host, host
    assert _runtime_imports(host) == before | {"from typing import TYPE_CHECKING"}
    helper = next(
        node
        for node in ast.walk(ast.parse(host))
        if isinstance(node, ast.FunctionDef) and "extracted" in node.name
    )
    written = helper.args.args[0].annotation
    assert isinstance(written, ast.Constant), f"the guarded name must be quoted: {host}"

    checked = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(package)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=600,
    )
    assert "Success" in checked.stdout, checked.stdout

    imported = subprocess.run(
        [sys.executable, "-c", "import pkg.host; print(pkg.host.first())"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.strip() == "A!"


def test_the_import_joins_a_guard_at_module_level_and_not_one_inside_a_function(
    tmp_path: Path,
) -> None:
    """A guard indented in a function body would put the name out of scope."""
    from towel.unification.refactor_engine import UnificationRefactorEngine

    engine = UnificationRefactorEngine()
    nested = [
        "import os\n",
        "from typing import TYPE_CHECKING\n",
        "\n",
        "def loader() -> None:\n",
        "    if TYPE_CHECKING:\n",
        "        import collections\n",
        "    return None\n",
    ]
    engine._ensure_type_checking_import(nested, "pkg.other", "Thing")
    written = "".join(nested)
    assert "        from pkg.other import Thing" not in written, written
    assert "if TYPE_CHECKING:\n    from pkg.other import Thing\n" in written, written

    at_top = [
        "from typing import TYPE_CHECKING\n",
        "\n",
        "if TYPE_CHECKING:\n",
        "    from pkg.first import One\n",
    ]
    engine._ensure_type_checking_import(at_top, "pkg.other", "Thing")
    joined = "".join(at_top)
    assert joined.count("if TYPE_CHECKING:") == 1, joined
    assert "    from pkg.other import Thing\n" in joined


def _joined_and_run(lines: List[str]) -> str:
    """Add the type-only import to ``lines``, then prove the module parses and never runs it."""
    from towel.unification.refactor_engine import UnificationRefactorEngine

    UnificationRefactorEngine()._ensure_type_checking_import(lines, "pkg.other", "Thing")
    written = "".join(lines)
    # pkg.other does not exist: the module raises if the import ever runs.
    exec(compile(written, "<module>", "exec"), {"__name__": "module_under_test"})
    return written


@pytest.mark.parametrize(
    "header",
    ["if TYPE_CHECKING:  # pragma: no cover\n", "if TYPE_CHECKING:  # noqa: SIM102\n"],
)
def test_a_guard_whose_line_carries_a_comment_is_joined_not_repeated(header: str) -> None:
    """packaging's guard reads ``if TYPE_CHECKING:  # pragma: no cover``; a second guard was written."""
    written = _joined_and_run(
        ["from typing import TYPE_CHECKING\n", "\n", header, "    import collections\n"]
    )
    assert written.count("if TYPE_CHECKING") == 1, written
    assert header + "    from pkg.other import Thing\n    import collections\n" in written, written


@pytest.mark.parametrize("indent", ["  ", "\t", "        "])
def test_the_import_takes_the_indentation_of_the_guards_body(indent: str) -> None:
    """A four-space line before a two-space body is an IndentationError."""
    written = _joined_and_run(
        [
            "from typing import TYPE_CHECKING\n",
            "if TYPE_CHECKING:\n",
            f"{indent}import collections\n",
            f"{indent}import os\n",
        ]
    )
    assert f"if TYPE_CHECKING:\n{indent}from pkg.other import Thing\n" in written, written
    assert written.count("if TYPE_CHECKING") == 1, written


def test_a_guard_through_the_typing_module_needs_no_import_of_the_name() -> None:
    written = _joined_and_run(["import typing\n", "if typing.TYPE_CHECKING:\n", "    import os\n"])
    assert "if typing.TYPE_CHECKING:\n    from pkg.other import Thing\n" in written, written
    assert "from typing import TYPE_CHECKING" not in written, written


def test_a_rebound_name_is_not_a_guard_to_join() -> None:
    """``TYPE_CHECKING = True`` makes the block run, so an import joined to it would too."""
    written = _joined_and_run(
        [
            "from typing import TYPE_CHECKING\n",
            "TYPE_CHECKING = True\n",
            "if TYPE_CHECKING:\n",
            "    import os\n",
        ]
    )
    assert "if TYPE_CHECKING:\n    import os\n" in written, written


def test_a_guard_whose_body_shares_its_line_gets_a_guard_beside_it() -> None:
    written = _joined_and_run(
        ["from typing import TYPE_CHECKING\n", "if TYPE_CHECKING: import os\n", "x = 1\n"]
    )
    assert "if TYPE_CHECKING:\n    from pkg.other import Thing\n" in written, written
    assert "if TYPE_CHECKING: import os\n" in written, written

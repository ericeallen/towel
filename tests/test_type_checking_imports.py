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

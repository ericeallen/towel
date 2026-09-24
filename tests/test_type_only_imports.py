"""A helper's type-only imports are the ones its own annotations use, and never break its import.

A name a checker spells in full, ``pkg.models.Item``, is shortened to ``Item``
and imported under ``TYPE_CHECKING``, where only a checker reads it. Two things
went wrong. The generic candidates were given the ordinary signature's imports,
not their own, so the helper written could import what it never names, or miss
what it does. And an annotation whose shortened name sat inside it,
``dict[pkg.models.Item, int]``, was left unquoted, so the interpreter evaluated
``Item`` when the helper was defined, where it does not exist: the output
failed at import with a ``NameError`` that no checker reports.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Set

from tests.typed_fixtures import CountingMypy, requires_mypy
from towel.unification.refactor_engine import UnificationRefactorEngine


def _project(tmp_path: Path, ops: str) -> Path:
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "models.py").write_text("class Item:\n    pass\n\n\nclass Box:\n    pass\n")
    path = package / "ops.py"
    path.write_text(textwrap.dedent(ops).lstrip())
    return path


def _apply(path: Path) -> str:
    oracle = CountingMypy()
    try:
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        (proposal,) = engine.analyze_file(str(path))
        return engine.apply_refactoring(str(path), proposal)
    finally:
        oracle.close()


def _type_only_imports(module: ast.Module) -> Set[str]:
    return {
        alias.asname or alias.name
        for node in module.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING"
        for statement in node.body
        if isinstance(statement, ast.ImportFrom)
        for alias in statement.names
    }


def _annotation_names(helper: ast.FunctionDef) -> Set[str]:
    names: Set[str] = set()
    for annotation in [a.annotation for a in helper.args.args] + [helper.returns]:
        if annotation is None:
            continue
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            annotation = ast.parse(annotation.value, mode="eval").body
        names |= {node.id for node in ast.walk(annotation) if isinstance(node, ast.Name)}
    return names


def _helper(module: ast.Module) -> ast.FunctionDef:
    (helper,) = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]
    return helper


@requires_mypy
def test_a_generic_helper_imports_what_its_own_annotations_name(tmp_path: Path) -> None:
    """The ordinary signature names ``Item`` and ``Box``; the generic one only ``Item``."""
    path = _project(
        tmp_path,
        """
        import pkg.models


        def store_box(
            pairs: dict[pkg.models.Item, pkg.models.Box],
            item: pkg.models.Item,
            value: pkg.models.Box,
        ) -> None:
            pairs[item] = value
            pairs.pop(item)


        def store_count(pairs: dict[pkg.models.Item, int], item: pkg.models.Item, value: int) -> None:
            pairs[item] = value
            pairs.pop(item)
        """,
    )
    module = ast.parse(_apply(path))
    helper = _helper(module)
    assert "_TowelT0" in _annotation_names(helper), ast.unparse(helper)
    imported = _type_only_imports(module)
    assert imported <= _annotation_names(helper), f"imported and never named: {imported}"
    assert "Box" not in imported


@requires_mypy
def test_a_shortened_name_inside_an_annotation_is_never_evaluated(tmp_path: Path) -> None:
    path = _project(
        tmp_path,
        """
        import pkg.models


        def store_box(
            pairs: dict[pkg.models.Item, pkg.models.Box],
            item: pkg.models.Item,
            value: pkg.models.Box,
        ) -> None:
            pairs[item] = value
            pairs.pop(item)


        def store_other(
            pairs: dict[pkg.models.Item, pkg.models.Box],
            item: pkg.models.Item,
            value: pkg.models.Box,
        ) -> None:
            pairs[item] = value
            pairs.pop(item)
        """,
    )
    written = _apply(path)
    module = ast.parse(written)
    imported = _type_only_imports(module)
    assert imported <= _annotation_names(_helper(module)), f"imported and never named: {imported}"
    path.write_text(written)
    ran = subprocess.run(
        # On a Python that defers annotations, reading them is what evaluates them.
        [
            sys.executable,
            "-c",
            "import pkg.ops; [vars(f) and f.__annotations__"
            " for f in vars(pkg.ops).values() if callable(f)]",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert ran.returncode == 0, ran.stderr

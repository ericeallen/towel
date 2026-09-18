"""Bare helper annotations are filled from a type inferrer when the sites declare types.

The copied annotations cover arguments that are annotated names or literals.
For an argument that is an expression, ``MypyInferrer`` reveals its type at
the start of the block in an in-memory copy of the module; the result is
written only when every site agrees and every name resolves where the helper
is defined. ``towel dry --no-types`` leaves helpers bare.
"""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest

from tests.test_cli_integration import invoke
from towel.type_inference import MypyInferrer, RevealRequest
from towel.unification.annotations import annotation_from_revealed
from towel.unification.refactor_engine import UnificationRefactorEngine

pytest.importorskip("mypy")


def _signature(source: str) -> str:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name:
            return ast.unparse(node).split("\n", 1)[0]
    raise AssertionError(f"no helper in:\n{source}")


@pytest.mark.parametrize(
    "revealed, expected",
    [
        ("builtins.int", "int"),
        ("int", "int"),
        ("list[str]", "list[str]"),
        ("str | None", "str | None"),
        ("Literal['x']?", "str"),
        ("Literal[3]? | None", "int | None"),
        ("tuple[int, str | None]", "tuple[int, str | None]"),
        ("Any", None),
        ("dict[str, Any]", None),
        ("def () -> int", None),
        ("<nothing>", None),
    ],
)
def test_revealed_spellings_become_annotations(revealed: str, expected: str | None) -> None:
    result = annotation_from_revealed(revealed, ast.parse(""), True)
    assert (ast.unparse(result) if result is not None else None) == expected


def test_dotted_names_reduce_to_what_the_host_binds() -> None:
    host = ast.parse("import typing\nfrom .other import Box\n")
    assert ast.unparse(annotation_from_revealed("pkg.other.Box", host, True)) == "Box"  # type: ignore[arg-type]
    assert ast.unparse(annotation_from_revealed("typing.Sequence[int]", host, True)) == "typing.Sequence[int]"  # type: ignore[arg-type]
    assert annotation_from_revealed("pkg.elsewhere.Thing", host, True) is None


def test_mypy_inferrer_reveals_types_at_the_probe_line(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = textwrap.dedent("""
        class Box:
            def __init__(self, value: int) -> None:
                self.value = value

        def f(box: Box, label: str) -> None:
            total = box.value * 2
            print(total, label.upper())
        """)
    module = package / "m.py"
    module.write_text(source)
    inferrer = MypyInferrer()
    revealed = inferrer(
        [RevealRequest(str(module), source, 7, "    ", ("box.value", "label.upper()", "box"))]
    )
    assert revealed[(str(module), 7, 0)] == "int"
    assert revealed[(str(module), 7, 1)] == "str"
    assert revealed[(str(module), 7, 2)].endswith("Box")


def test_engine_fills_expression_arguments_and_the_return_from_mypy(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            class Box:
                def __init__(self, value: int, name: str) -> None:
                    self.value = value
                    self.name = name

            def first(box: Box) -> str:
                scaled = box.value * 2
                label = box.name.upper()
                return label + str(scaled)

            def second(box: Box) -> str:
                scaled = box.value * 3
                label = box.name.upper()
                return label + str(scaled)
            """))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_inferrer=MypyInferrer()
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    result = engine.apply_refactoring(str(path), proposals[0])
    assert _signature(result) == "def __extracted_func_0(__param_0: int, box: 'Box') -> str:"
    exec(compile(result, "<inferred>", "exec"), {})


def test_dry_infers_by_default_and_not_with_no_types(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "m.py").write_text(textwrap.dedent("""
            def first(items: list[int]) -> int:
                total = sum(items) + 1
                print(total)
                return total * 2

            def second(items: list[int]) -> int:
                total = sum(items) + 1
                print(total)
                return total * 3
            """))
    typed = tmp_path / "typed"
    bare = tmp_path / "bare"
    common = ["--non-interactive", "--progress", "none", "--no-format"]
    assert invoke(["dry", str(source_dir), str(typed), *common]).status == 0
    assert invoke(["dry", str(source_dir), str(bare), *common, "--no-types"]).status == 0
    assert "(items: list[int]) -> int:" in (typed / "m.py").read_text()
    assert "(__param_0, items):" in (bare / "m.py").read_text()

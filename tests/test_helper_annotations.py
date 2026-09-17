"""Extracted helpers carry the annotations their call sites declare.

Nothing is inferred. A parameter is annotated when every site passes an
annotated, never-rebound parameter of its enclosing function or a literal of
one builtin type, and the sites agree. The return is annotated from the sites'
declared return type, from annotated locals the helper returns, or as ``None``
for a helper that returns nothing. Annotations are inserted unquoted only
where they cannot fail to resolve; otherwise as strings; across modules only
builtin names are used. An unannotated project stays unannotated.
"""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap

from towel.unification.refactor_engine import UnificationRefactorEngine


def _refactor(tmp_path: Path, code: str, **engine_options: object) -> str:
    """Extract from ``code``; reuse is off so a helper is always produced."""
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent(code))
    options = {"reuse_existing_functions": False, **engine_options}
    engine = UnificationRefactorEngine(min_lines=2, **options)  # type: ignore[arg-type]
    proposals = engine.analyze_file(str(path))
    assert proposals, "the fixture must produce a proposal"
    return engine.apply_refactoring(str(path), proposals[0])


def _helper(source: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name:
            return node
    raise AssertionError(f"no helper in:\n{source}")


def _signature(source: str) -> str:
    return ast.unparse(_helper(source)).split("\n", 1)[0]


BODY = """
            total = value * 2
            label = prefix + str(total)
            print(label)
"""


def test_parameters_and_none_return_come_from_agreeing_sites(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        f"""
        def first(value: int, prefix: str) -> None:{BODY}
        def second(value: int, prefix: str) -> None:{BODY}
        """,
    )
    assert _signature(result) == "def __extracted_func_0(prefix: str, value: int) -> None:"
    exec(compile(result, "<annotated>", "exec"), {})


def test_disagreeing_sites_leave_the_parameter_unannotated(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        f"""
        def first(value: int, prefix: str) -> None:{BODY}
        def second(value: float, prefix: str) -> None:{BODY}
        """,
    )
    assert _signature(result) == "def __extracted_func_0(prefix: str, value) -> None:"


def test_rebound_parameter_is_not_trusted(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        f"""
        def first(value: int, prefix: str) -> None:
            value = int(str(value)){BODY}
        def second(value: int, prefix: str) -> None:{BODY}
        """,
    )
    assert _signature(result) == "def __extracted_func_0(prefix: str, value) -> None:"


def test_literals_take_their_builtin_type_and_bool_is_not_int(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        """
        def first(items: list) -> None:
            limit = 10
            flag = True
            print(items[:limit], flag)

        def second(items: list) -> None:
            limit = 20
            flag = False
            print(items[:limit], flag)
        """,
    )
    assert (
        _signature(result)
        == "def __extracted_func_0(__param_0: int, __param_1: bool, items: list) -> None:"
    )


def test_declared_return_type_is_used_when_every_site_returns_the_call(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        """
        def first(value: int) -> str:
            total = value * 2
            text = str(total)
            return text.strip()

        def second(value: int) -> str:
            total = value * 2
            text = str(total)
            return text.strip()
        """,
    )
    assert _signature(result) == "def __extracted_func_0(value: int) -> str:"


def test_annotated_locals_give_the_return_type_of_returned_variables(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        """
        def first(value: int) -> str:
            total: int = value * 2
            label: str = str(total)
            print(label)
            return label.upper() + str(total)

        def second(value: int) -> str:
            total: int = value * 2
            label: str = str(total)
            print(label)
            return label.lower() + str(total)
        """,
    )
    header = _signature(result)
    assert header.startswith("def __extracted_func_0(value: int) -> tuple["), header
    assert header.endswith("-> tuple[int, str]:") or header.endswith("-> tuple[str, int]:")
    exec(compile(result, "<locals>", "exec"), {})


def test_unannotated_code_stays_unannotated(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        f"""
        def first(value, prefix):{BODY}
        def second(value, prefix):{BODY}
        """,
    )
    assert _signature(result) == "def __extracted_func_0(prefix, value):"


def test_annotation_can_be_switched_off(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        f"""
        def first(value: int, prefix: str) -> None:{BODY}
        def second(value: int, prefix: str) -> None:{BODY}
        """,
        annotate_helpers=False,
    )
    assert _signature(result) == "def __extracted_func_0(prefix, value):"


def test_class_defined_later_is_quoted_so_the_module_still_imports(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        """
        def first(box: Box) -> None:
            total = box.value * 2
            print(total, box)

        def second(box: Box) -> None:
            total = box.value * 2
            print(total, box)

        class Box:
            value = 1
        """,
    )
    assert _signature(result) == "def __extracted_func_0(box: 'Box') -> None:"
    exec(compile(result, "<quoted>", "exec"), {})


def test_deferred_annotations_are_copied_unquoted(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        """
        from __future__ import annotations

        def first(box: Box) -> None:
            total = box.value * 2
            print(total, box)

        def second(box: Box) -> None:
            total = box.value * 2
            print(total, box)

        class Box:
            value = 1
        """,
    )
    assert _signature(result) == "def __extracted_func_0(box: Box) -> None:"
    exec(compile(result, "<deferred>", "exec"), {})


def test_import_bound_names_are_copied_unquoted(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        """
        from typing import Optional

        def first(value: Optional[int]) -> None:
            total = (value or 0) * 2
            print(total, value)

        def second(value: Optional[int]) -> None:
            total = (value or 0) * 2
            print(total, value)
        """,
    )
    assert _signature(result) == "def __extracted_func_0(value: Optional[int]) -> None:"
    exec(compile(result, "<imported>", "exec"), {})


def test_cross_file_helper_keeps_only_builtin_annotations(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name, function in (("a.py", "fa"), ("b.py", "fb")):
        (package / name).write_text(textwrap.dedent(f"""
                from typing import Optional

                def {function}(value: int, label: Optional[str]) -> None:
                    total = value * 2
                    text = (label or "") + str(total)
                    print(text)
                    print(text.upper())
                """))
    engine = UnificationRefactorEngine(min_lines=2, reuse_existing_functions=False)
    proposals = engine.analyze_directory(str(package))
    assert proposals
    header = ast.unparse(proposals[0].extracted_function).split("\n", 1)[0]
    assert header == "def __extracted_func(label, value: int) -> None:"

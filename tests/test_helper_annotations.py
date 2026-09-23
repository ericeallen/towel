"""Extracted helpers carry the annotations their call sites declare.

Nothing is inferred. A parameter is annotated when every site passes an
annotated, never-rebound parameter of its enclosing function or a literal of
one builtin type, and the sites agree. The return is annotated from the sites'
declared return type, from annotated locals the helper returns, or as ``None``
for a helper that returns nothing. Annotations are inserted unquoted only
where they cannot fail to resolve; otherwise as strings; across modules only
builtin names are used. An unannotated project stays unannotated.

The fixture project declares Python 3.10, so a union written with ``|`` and a
subscripted builtin evaluate where the helper is defined; what an older or an
undeclared Python gets is test_annotations_for_the_oldest_python's subject.
"""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap
from typing import Unpack

from tests.test_helpers import EngineOptions
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.type_inference import Subtyping

YES, NO, UNKNOWN = Subtyping.YES, Subtyping.NO, Subtyping.UNKNOWN


def _refactor(tmp_path: Path, code: str, **engine_options: Unpack[EngineOptions]) -> str:
    """Extract from ``code``; reuse is off so a helper is always produced."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "m"\nrequires-python = ">=3.10"\n')
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent(code))
    options: EngineOptions = {"reuse_existing_functions": False, **engine_options}
    engine = UnificationRefactorEngine(min_lines=2, **options)
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


def test_disagreeing_sites_join_into_a_union(tmp_path: Path) -> None:
    # The parameter must accept every site's argument, so its annotation is
    # the least upper bound the sites spell: their union. Without a type
    # checker nothing is known about subtyping, so the union is not reduced
    # (with mypy, ``int | float`` becomes ``float``: see test_type_inference).
    result = _refactor(
        tmp_path,
        f"""
        def first(value: int, prefix: str) -> None:{BODY}
        def second(value: float, prefix: str) -> None:{BODY}
        """,
    )
    assert _signature(result) == "def __extracted_func_0(prefix: str, value: int | float) -> None:"


def test_unrelated_sites_join_into_a_union(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        f"""
        def first(value: int, prefix: str) -> None:{BODY}
        def second(value: str, prefix: str) -> None:{BODY}
        """,
    )
    assert _signature(result) == "def __extracted_func_0(prefix: str, value: int | str) -> None:"


def test_a_none_site_makes_the_parameter_optional(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        """
        def first(items: list, limit: int) -> None:
            print(items[:limit])
            print(len(items), limit)

        def second(items: list, limit: None) -> None:
            print(items[:limit])
            print(len(items), limit)
        """,
    )
    assert _signature(result) == "def __extracted_func_0(items: list, limit: int | None) -> None:"


def test_declared_return_types_need_a_checker_to_meet(tmp_path: Path) -> None:
    # The sites' declared return types bound the helper's value from above;
    # only a type checker can tell that ``int`` is under ``int | None``, so
    # without one the return is Any (with mypy: see test_type_inference).
    result = _refactor(
        tmp_path,
        """
        def first(value: int) -> int:
            total = value * 2
            text = str(total)
            return len(text.strip())

        def second(value: int) -> int | None:
            total = value * 2
            text = str(total)
            return len(text.strip())
        """,
    )
    assert _signature(result) == "def __extracted_func_0(value: int) -> Any:"


def test_unrelated_declared_return_types_leave_the_return_to_any(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        """
        def first(value: int) -> int:
            total = value * 2
            text = str(total)
            return len(text.strip())

        def second(value: int) -> str:
            total = value * 2
            text = str(total)
            return len(text.strip())
        """,
    )
    assert _signature(result) == "def __extracted_func_0(value: int) -> Any:"
    assert "from typing import Any" in result


def test_rebound_parameter_is_not_trusted(tmp_path: Path) -> None:
    result = _refactor(
        tmp_path,
        f"""
        def first(value: int, prefix: str) -> None:
            value = int(str(value)){BODY}
        def second(value: int, prefix: str) -> None:{BODY}
        """,
    )
    # A rebound parameter is not trusted; once the helper is annotated at
    # all, the bare parameter becomes ``Any`` so the signature is complete.
    assert _signature(result) == "def __extracted_func_0(prefix: str, value: Any) -> None:"


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


def test_class_defined_later_puts_the_helper_after_it_with_a_bare_name(tmp_path: Path) -> None:
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
    assert _signature(result) == "def __extracted_func_0(box: Box) -> None:"
    assert result.index("class Box") < result.index("def __extracted_func_0")
    exec(compile(result, "<bare>", "exec"), {})


def test_import_time_code_before_the_class_keeps_the_helper_early_and_quoted(
    tmp_path: Path,
) -> None:
    # ``configure()`` runs at import and may call the functions above it, so
    # the helper cannot move below it; the class name is a forward reference.
    result = _refactor(
        tmp_path,
        """
        def configure() -> None:
            pass

        configure()

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
    assert result.index("def __extracted_func_0") < result.index("configure()")
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
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, cross_module_helpers=True
    )
    proposals = engine.analyze_directory(str(package))
    assert proposals
    header = ast.unparse(proposals[0].extracted_function).split("\n", 1)[0]
    # ``Optional[str]`` needs typing; its members ``str | None`` are builtins.
    assert header == "def __extracted_func(label: str | None, value: int) -> None:"


def test_subscripted_annotations_are_quoted_unless_known_generic(tmp_path: Path) -> None:
    # ``memoryview`` is not subscriptable at runtime on every interpreter
    # (tornado failed to import); ``list[int]`` and ``Sequence[int]`` are.
    result = _refactor(
        tmp_path,
        """
        from typing import Sequence

        def first(view: memoryview[int], items: list[int], seq: Sequence[int]) -> None:
            total = len(view) + len(items)
            print(total, seq)

        def second(view: memoryview[int], items: list[int], seq: Sequence[int]) -> None:
            total = len(view) + len(items)
            print(total, seq)
        """,
    )
    assert (
        _signature(result)
        == "def __extracted_func_0(items: list[int], seq: Sequence[int], view: 'memoryview[int]') -> None:"
    )
    exec(compile(result, "<generic>", "exec"), {})


def test_a_union_of_forward_references_is_one_quoted_string(tmp_path: Path) -> None:
    # Two sites pass classes defined below the helper's position; the union
    # must not be ``'Left' | 'Right'``, which is a TypeError at definition.
    result = _refactor(
        tmp_path,
        """
        def configure() -> None:
            pass

        configure()

        def first(item: Left) -> None:
            total = item.value * 2
            print(total, item)

        def second(item: Right) -> None:
            total = item.value * 2
            print(total, item)

        class Left:
            value = 1

        class Right:
            value = 2
        """,
    )
    assert _signature(result) == "def __extracted_func_0(item: 'Left | Right') -> None:"
    exec(compile(result, "<union>", "exec"), {})


def test_inconsistent_subtype_verdicts_never_empty_a_union() -> None:
    # A checker that cannot judge some pairs can return verdicts no real
    # relation has: A under B, B under C, C under A, with the reverse
    # directions unknown. Absorbing on those would drop every member.
    from towel.unification.annotations import normalize_union

    parse = lambda text: ast.parse(text, mode="eval").body  # noqa: E731
    members = [parse("A"), parse("B"), parse("C")]
    cyclic = {("A", "B"): YES, ("B", "C"): YES, ("C", "A"): YES}

    def relation(pairs):
        return [cyclic.get((ast.unparse(n), ast.unparse(w)), UNKNOWN) for n, w in pairs]

    kept = normalize_union(members, relation)
    assert [ast.unparse(m) for m in kept] == ["A", "B", "C"]
    consistent = {("bool", "int"): YES, ("int", "bool"): NO}
    kept = normalize_union(
        [parse("bool"), parse("int")],
        lambda pairs: [consistent.get((ast.unparse(n), ast.unparse(w)), UNKNOWN) for n, w in pairs],
    )
    assert [ast.unparse(m) for m in kept] == ["int"]

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

from towel.type_inference import Subtyping  # noqa: E402

YES, NO, UNKNOWN = Subtyping.YES, Subtyping.NO, Subtyping.UNKNOWN


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
        ("dict[str, Any]", "dict[str, Any]"),
        ("list[Any]", "list[Any]"),
        ("def () -> int", "Callable[[], int]"),
        ("<nothing>", None),
    ],
)
def test_revealed_spellings_become_annotations(revealed: str, expected: str | None) -> None:
    result = annotation_from_revealed(revealed, ast.parse(""), True)
    assert (ast.unparse(result) if result is not None else None) == expected


def test_dotted_names_reduce_to_what_the_host_binds() -> None:
    host = ast.parse("import typing\nfrom .other import Box\n")
    box = annotation_from_revealed("pkg.other.Box", host, True)
    assert box is not None and ast.unparse(box) == "Box"
    sequence = annotation_from_revealed("typing.Sequence[int]", host, True)
    assert sequence is not None and ast.unparse(sequence) == "typing.Sequence[int]"
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
        min_lines=2, reuse_existing_functions=False, type_oracle=MypyInferrer()
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    result = engine.apply_refactoring(str(path), proposals[0])
    assert _signature(result) == "def __extracted_func_0(__param_0: int, box: Box) -> str:"
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
    common = ["--no-interactive", "--progress", "none", "--no-format"]
    assert invoke(["dry", str(source_dir), str(typed), *common]).status == 0
    assert invoke(["dry", str(source_dir), str(bare), *common, "--no-types"]).status == 0
    assert "(items: list[int]) -> int:" in (typed / "m.py").read_text()
    assert "(__param_0, items):" in (bare / "m.py").read_text()


def test_non_identifier_package_names_never_reach_an_annotation() -> None:
    host = ast.parse("class H2Stream: ...\n")
    assert annotation_from_revealed("h2-dbg.stream.H2Stream", host, True) is None
    stream = annotation_from_revealed("_towel_package.stream.H2Stream", host, True)
    assert stream is not None and ast.unparse(stream) == "'H2Stream'"


def test_inferrer_names_a_non_identifier_package_with_a_placeholder(tmp_path: Path) -> None:
    package = tmp_path / "cleaned-out"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = "class Box:\n    pass\n\ndef f(box: Box) -> None:\n    print(box)\n"
    module = package / "m.py"
    module.write_text(source)
    revealed = MypyInferrer()([RevealRequest(str(module), source, 5, "    ", ("box",))])
    assert revealed[(str(module), 5, 0)] == "_towel_package.m.Box"


def test_composite_any_is_written_and_typing_any_imported(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            import json

            def first(text: str) -> int:
                payload = json.loads(text)
                keys = sorted(payload)
                print(keys)
                return len(keys)

            def second(text: str) -> int:
                payload = json.loads(text)
                keys = sorted(payload)
                print(keys)
                return len(keys) * 2
            """))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=MypyInferrer()
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    result = engine.apply_refactoring(str(path), proposals[0])
    assert "from typing import Any" in result
    assert _signature(result) == "def __extracted_func_0(json: Any, text: str) -> list[Any]:"
    exec(compile(result, "<any>", "exec"), {})


def test_revealed_types_that_differ_join_into_a_union(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            class Box:
                def __init__(self) -> None:
                    self.count = 1
                    self.ratio = 2.5

            def first(box: Box) -> str:
                scaled = box.count * 2
                label = str(scaled).upper()
                return label.strip()

            def second(box: Box) -> str:
                scaled = box.ratio * 2
                label = str(scaled).upper()
                return label.strip()
            """))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=MypyInferrer()
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    result = engine.apply_refactoring(str(path), proposals[0])
    assert _signature(result) == "def __extracted_func_0(__param_0: float, box: Box) -> str:"


def test_subtype_oracle_judges_pairs_in_the_module_context(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = "from typing import Sequence\n\nclass Base: ...\nclass Box(Base): ...\n"
    module = package / "m.py"
    module.write_text(source)
    verdicts = MypyInferrer().is_subtype(
        str(module),
        source,
        [
            ("bool", "int"),
            ("int", "bool"),
            ("Box", "Base"),
            ("Base", "Box"),
            ("list[int]", "Sequence[int]"),
            ("'Box'", "Base"),
            ("None", "int | None"),
            ("int", "Unknown"),
        ],
    )
    assert verdicts == [YES, NO, YES, NO, YES, YES, YES, UNKNOWN]


def test_unions_are_normalized_by_the_oracle(tmp_path: Path) -> None:
    from towel.unification.annotations import normalize_union, oracle_subtypes

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = "from typing import Sequence\nclass Base: ...\nclass Box(Base): ...\n"
    module = package / "m.py"
    module.write_text(source)
    relation = oracle_subtypes(MypyInferrer(), str(module), source)
    parse = lambda text: ast.parse(text, mode="eval").body  # noqa: E731
    normalized = normalize_union(
        [parse(t) for t in ("Box", "bool", "Base", "int", "None", "list[int]", "Sequence[int]")],
        relation,
    )
    assert [ast.unparse(m) for m in normalized] == ["Base", "int", "Sequence[int]", "None"]


def test_revealed_return_is_written_when_it_satisfies_every_declaration(tmp_path: Path) -> None:
    # Sites declare ``-> int`` and ``-> object``; the helper returns an int,
    # which mypy confirms is a subtype of both, so ``int`` is written.
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            def first(value: int) -> int:
                total = value * 2
                text = str(total)
                return len(text.strip())

            def second(value: int) -> object:
                total = value * 2
                text = str(total)
                return len(text.strip())
            """))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=MypyInferrer()
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    result = engine.apply_refactoring(str(path), proposals[0])
    assert _signature(result) == "def __extracted_func_0(value: int) -> int:"


def test_declared_class_types_meet_through_the_oracle(tmp_path: Path) -> None:
    # ``Box`` is a subclass of ``Base``; only mypy knows, and the meet is Box.
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            class Base: ...
            class Box(Base): ...

            def make(flag: bool) -> Box:
                box = Box()
                print(flag, box)
                return box

            def build(flag: bool) -> Base:
                box = Box()
                print(flag, box)
                return box
            """))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=MypyInferrer()
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    result = engine.apply_refactoring(str(path), proposals[0])
    # ``Box`` the class is passed as a parameter, revealed as its constructor
    # signature; the helper is placed after the class, so the return is bare.
    assert (
        _signature(result) == "def __extracted_func_0(Box: Callable[[], Box], flag: bool) -> Box:"
    )


def test_declared_return_meets_through_the_checker(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            def first(value: int) -> int:
                total = value * 2
                text = str(total)
                return len(text.strip())

            def second(value: int) -> int | None:
                total = value * 2
                text = str(total)
                return len(text.strip())
            """))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=MypyInferrer()
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    result = engine.apply_refactoring(str(path), proposals[0])
    assert _signature(result) == "def __extracted_func_0(value: int) -> int:"


@pytest.mark.parametrize(
    "revealed, expected",
    [
        ("def () -> int", "Callable[[], int]"),
        ("def (x: int, y: str) -> bool", "Callable[[int, str], bool]"),
        ("def (*args: Any, **kwargs: Any) -> str", "Callable[..., str]"),
        ("def (x: int = ...) -> int", "Callable[..., int]"),
    ],
)
def test_callable_spellings(revealed: str, expected: str) -> None:
    result = annotation_from_revealed(revealed, ast.parse(""), True)
    assert result is not None and ast.unparse(result) == expected


class _Oracle:
    """A checker that reports one new error for any annotated helper."""

    def __init__(self) -> None:
        self.inner = MypyInferrer()

    def reveal(self, requests):
        return self.inner.reveal(requests)

    def is_subtype(self, file_path, source, pairs):
        return self.inner.is_subtype(file_path, source, pairs)

    def check(self, file_path, source):
        errors = list(self.inner.check(file_path, source))
        if "extracted_func" in source and ": Any" not in source and "-> Any" not in source:
            errors.append("Simulated: annotated helper does not type-check")
        return errors


def test_generated_code_that_fails_the_checker_degrades_to_any(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            def first(value: int) -> int:
                total = value * 2
                text = str(total)
                return len(text.strip())

            def second(value: int) -> int:
                total = value * 2
                text = str(total)
                return len(text.strip())
            """))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=_Oracle()
    )
    proposals = engine.analyze_file(str(path))
    result = engine.apply_refactoring(str(path), proposals[0])
    assert _signature(result) == "def __extracted_func_0(value: Any) -> Any:"
    assert "from typing import Any" in result


def test_thunk_arguments_get_callable_annotations(tmp_path: Path) -> None:
    # The differing expression sits under ``if``, so it is passed as a thunk;
    # mypy reveals the lambda as ``def () -> int``, spelled ``Callable[[], int]``.
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            def first(flag: bool, name: str) -> int:
                if flag:
                    return len(name)
                return 0

            def second(flag: bool, name: str) -> int:
                if flag:
                    return len(name) + 1
                return 0
            """))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=MypyInferrer()
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    result = engine.apply_refactoring(str(path), proposals[0])
    assert "Callable[[], int]" in _signature(result), _signature(result)
    assert "from typing import Callable" in result
    exec(compile(result, "<callable>", "exec"), {})


pytest.importorskip("pyright")


def _pyright_package(tmp_path: Path) -> tuple[Path, str]:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = textwrap.dedent("""
        from typing import Sequence

        class Base: ...
        class Box(Base): ...

        def f(box: Box, xs: Sequence[int], n: int) -> None:
            print(box, xs, n)
        """)
    module = package / "m.py"
    module.write_text(source)
    return module, source


def test_pyright_oracle_reveals_types(tmp_path: Path) -> None:
    from towel.type_inference import PyrightOracle

    module, source = _pyright_package(tmp_path)
    revealed = PyrightOracle().reveal(
        [RevealRequest(str(module), source, 8, "    ", ("box", "Box", "n * 2.5", "lambda: n"))]
    )
    assert revealed[(str(module), 8, 0)] == "Box"
    assert revealed[(str(module), 8, 1)] == "type[Box]"
    assert revealed[(str(module), 8, 2)] == "float"
    assert revealed[(str(module), 8, 3)] == "() -> int"
    assert not list(module.parent.glob("_towel_probe_*")), "probe files are removed"


def test_pyright_oracle_judges_subtypes_and_checks(tmp_path: Path) -> None:
    from towel.type_inference import PyrightOracle

    module, source = _pyright_package(tmp_path)
    oracle = PyrightOracle()
    assert oracle.is_subtype(
        str(module), source, [("bool", "int"), ("int", "bool"), ("Box", "Base"), ("int", "Unknown")]
    ) == [YES, NO, YES, UNKNOWN]
    assert oracle.check(str(module), source) == []
    assert any(
        "pyright" in message for message in oracle.check(str(module), source + "x: int = 'a'\n")
    )


def test_pyright_callable_spelling() -> None:
    result = annotation_from_revealed("(x: int) -> str", ast.parse(""), True)
    assert result is not None and ast.unparse(result) == "Callable[[int], str]"
    result = annotation_from_revealed("() -> int", ast.parse(""), True)
    assert result is not None and ast.unparse(result) == "Callable[[], int]"


def test_checker_follows_the_projects_configuration(tmp_path: Path) -> None:
    from towel.type_inference import (
        CombinedOracle,
        MypyInferrer,
        PyrightOracle,
        type_oracle_for_project,
    )

    (tmp_path / "pyproject.toml").write_text("[tool.pyright]\nstrict = []\n")
    choice = type_oracle_for_project(tmp_path / "m.py")
    oracle, note = choice.tool, choice.note
    assert isinstance(oracle, PyrightOracle) and note == "pyright"
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    choice = type_oracle_for_project(tmp_path / "m.py")
    oracle, note = choice.tool, choice.note
    assert isinstance(oracle, MypyInferrer) and note == "mypy"
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mypy]\nstrict = true\n[tool.pyright]\nstrict = []\n"
    )
    choice = type_oracle_for_project(tmp_path / "m.py")
    oracle, note = choice.tool, choice.note
    assert isinstance(oracle, CombinedOracle) and note.startswith("mypy for inference")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    choice = type_oracle_for_project(tmp_path / "m.py")
    oracle, note = choice.tool, choice.note
    assert isinstance(oracle, MypyInferrer)


def test_pyright_project_gets_pyright_types_end_to_end(tmp_path: Path) -> None:
    from towel.type_inference import PyrightOracle

    (tmp_path / "pyproject.toml").write_text("[tool.pyright]\nstrict = []\n")
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
        min_lines=2, reuse_existing_functions=False, type_oracle=PyrightOracle()
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    result = engine.apply_refactoring(str(path), proposals[0])
    assert _signature(result) == "def __extracted_func_0(__param_0: int, box: Box) -> str:"

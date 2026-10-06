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

"""Bare helper annotations are filled from a type inferrer when the sites declare types.

The copied annotations cover arguments that are annotated names or literals.
For an argument that is an expression, ``MypyInferrer`` reveals its type at
the start of the block in an in-memory copy of the module; the result is
written only when every site agrees and every name resolves where the helper
is defined. ``towel dry --no-types`` leaves helpers bare.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import re
from typing import Callable, Dict, cast
import textwrap

import pytest

from tests.test_cli_integration import invoke
from tests.test_helpers import original_argument_name
from towel.type_inference import (
    CheckFailure,
    CheckSuccess,
    MypyInferrer,
    RevealRequest,
    TypeDiagnostic,
)
from towel.unification.annotations import annotation_from_revealed
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

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
        ("list[str]", "'list[str]'"),
        ("str | None", "'str | None'"),
        ("Literal['x']?", "str"),
        ("Literal[3]? | None", "'int | None'"),
        ("tuple[int, str | None]", "'tuple[int, str | None]'"),
        ("Any", None),
        ("dict[str, Any]", "'dict[str, Any]'"),
        ("list[Any]", "'list[Any]'"),
        ("def () -> int", "'Callable[[], int]'"),
        ("<nothing>", None),
    ],
)
def test_revealed_spellings_become_annotations(revealed: str, expected: str | None) -> None:
    # Preserve each precise type and quote compounds: evaluating a generated
    # union, subscription or attribute can invoke code or precede its definition.
    result = annotation_from_revealed(revealed, ast.parse(""), True)
    assert (ast.unparse(result) if result is not None else None) == expected


def test_dotted_names_reduce_to_what_the_host_binds() -> None:
    host = ast.parse("import typing\nfrom .other import Box\n")
    box = annotation_from_revealed("pkg.other.Box", host, True)
    assert box is not None and ast.unparse(box) == "Box"
    sequence = annotation_from_revealed("typing.Sequence[int]", host, True)
    assert sequence is not None and ast.unparse(sequence) == "'typing.Sequence[int]'"
    # A whole path the host does not bind stays whole, for the caller to import
    # under TYPE_CHECKING or give up on.
    kept = annotation_from_revealed("pkg.elsewhere.Thing", host, True)
    assert isinstance(kept, ast.Constant) and kept.value == "pkg.elsewhere.Thing"


@requires_mypy
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
    assert revealed[(str(module), 7, 0)].removeprefix("builtins.") == "int"
    assert revealed[(str(module), 7, 1)].removeprefix("builtins.") == "str"
    assert revealed[(str(module), 7, 2)].endswith("Box")


@requires_mypy
def test_engine_fills_expression_arguments_and_the_return_from_mypy(
    tmp_path: Path,
) -> None:
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
    assert (
        _signature(result)
        == "def __extracted_func_0(__param_0: int, box: Box, _towel_owner: 'object') -> str:"
    )
    exec(compile(result, "<inferred>", "exec"), {})


@requires_mypy
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
    typed_tree = ast.parse((typed / "m.py").read_text())
    typed_helper = next(
        node
        for node in ast.walk(typed_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "__extracted_func_0"
    )
    assert [parameter.arg for parameter in typed_helper.args.posonlyargs] == ["__param_0"]
    assert (
        typed_helper.args.vararg,
        typed_helper.args.kwarg,
        typed_helper.args.kwonlyargs,
        typed_helper.args.defaults,
    ) == (None, None, [], [])
    multiplier_type = typed_helper.args.posonlyargs[0].annotation
    assert multiplier_type is not None and ast.unparse(multiplier_type) == "int"
    assert [parameter.arg for parameter in typed_helper.args.args] == [
        "items",
        "_towel_owner",
    ]
    owner_type = typed_helper.args.args[-1].annotation
    assert isinstance(owner_type, ast.Constant) and owner_type.value == "object"
    items_type = typed_helper.args.args[0].annotation
    assert isinstance(items_type, ast.Constant) and items_type.value == "list[int]"
    assert typed_helper.returns is not None and ast.unparse(typed_helper.returns) == "int"
    bare_tree = ast.parse((bare / "m.py").read_text())
    bare_helper = next(
        node
        for node in ast.walk(bare_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "__extracted_func_0"
    )
    parameters = bare_helper.args.posonlyargs + bare_helper.args.args
    assert (
        bare_helper.args.vararg,
        bare_helper.args.kwarg,
        bare_helper.args.kwonlyargs,
        bare_helper.args.defaults,
    ) == (None, None, [], [])
    assert [parameter.arg for parameter in bare_helper.args.posonlyargs] == ["__param_0"]
    assert [parameter.arg for parameter in bare_helper.args.args] == [
        "items",
        "_towel_owner",
    ]
    assert all(parameter.annotation is None for parameter in parameters)
    assert bare_helper.returns is None
    for tree, helper in ((typed_tree, typed_helper), (bare_tree, bare_helper)):
        callers = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in {"first", "second"}
        ]
        calls = []
        for function, multiplier in zip(callers, ("2", "3")):
            (call,) = [
                node
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == helper.name
            ]
            calls.append(call)
            assert ast.unparse(call.args[0]) == multiplier
            assert original_argument_name(function, call.args[1]) == "items"
            owner = call.args[-1]
            assert isinstance(owner, ast.Call) and isinstance(owner.func, ast.Attribute)
            assert owner.func.attr == "pop" and not owner.args and not owner.keywords
            assert isinstance(owner.func.value, ast.Name)
            box = owner.func.value.id
            (storage,) = [
                statement
                for statement in function.body
                if isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == box
                    for target in statement.targets
                )
            ]
            assert ast.dump(storage.value) == ast.dump(ast.parse("[(items,)]", mode="eval").body)
        assert all(
            not call.keywords and len(call.args) == len(helper.args.posonlyargs + helper.args.args)
            for call in calls
        )


def test_non_identifier_package_names_never_reach_an_annotation() -> None:
    host = ast.parse("class H2Stream: ...\n")
    assert annotation_from_revealed("h2-dbg.stream.H2Stream", host, True) is None
    stream = annotation_from_revealed("_towel_package.stream.H2Stream", host, True)
    assert stream is not None and ast.unparse(stream) == "'H2Stream'"


@requires_mypy
def test_inferrer_names_a_non_identifier_package_with_a_placeholder(
    tmp_path: Path,
) -> None:
    package = tmp_path / "cleaned-out"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = "class Box:\n    pass\n\ndef f(box: Box) -> None:\n    print(box)\n"
    module = package / "m.py"
    module.write_text(source)
    revealed = MypyInferrer()([RevealRequest(str(module), source, 5, "    ", ("box",))])
    assert revealed[(str(module), 5, 0)] == "_towel_package.m.Box"


@requires_mypy
def test_composite_any_is_written_and_typing_any_imported(tmp_path: Path) -> None:
    # Python 3.10 supports the inferred type syntax; generated compound
    # annotations must still avoid adding runtime evaluation.
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "m"\nrequires-python = ">=3.10"\n')
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            import json

            def first() -> int:
                payload = json.loads('{\"a\": 1}')
                keys = sorted(payload)
                print(keys)
                return len(keys)

            def second() -> int:
                payload = json.loads('{\"a\": 1}')
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
    # ``Any`` is reached through a private alias, so the module gains no public name.
    assert "import typing as _typing\n" in result and "from typing import Any" not in result
    # ``json`` is the module's import, read bare inside the helper, not a parameter.
    # Retaining the opaque payload must not erase the known type of keys.
    assert _signature(result) == (
        "def __extracted_func_0() -> 'tuple[_typing.Any, list[_typing.Any]]':"
    )
    exec(compile(result, "<any>", "exec"), {})


@requires_mypy
def test_revealed_types_that_differ_join_into_a_union(tmp_path: Path) -> None:
    """Normalize int/float to float without retaining an unread receiver.

    The approved unused-input construction leaves ``box`` at each call site.
    Its removal must not broaden the value parameter to Any or convert the
    actual int value to float: the two runtime string results still differ.
    """
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
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        proposals = engine.analyze_file(str(path))
        assert proposals
        result = engine.apply_refactoring(str(path), proposals[0])
        assert oracle.check(str(path), result) == CheckSuccess()
    finally:
        oracle.close()
    assert (
        _signature(result)
        == "def __extracted_func_0(__param_0: float, _towel_owner: 'object') -> str:"
    )
    for source in (path.read_text(), result):
        namespace: dict[str, object] = {}
        exec(
            compile(source + "\nobserved = first(Box()), second(Box())\n", str(path), "exec"),
            namespace,
        )
        assert namespace["observed"] == ("2", "5.0")


@requires_mypy
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


@requires_mypy
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
        [
            parse(t)
            for t in (
                "Box",
                "bool",
                "Base",
                "int",
                "None",
                "list[int]",
                "Sequence[int]",
            )
        ],
        relation,
    )
    assert [ast.unparse(m) for m in normalized] == [
        "Base",
        "int",
        "Sequence[int]",
        "None",
    ]


@requires_mypy
def test_revealed_return_is_written_when_it_satisfies_every_declaration(
    tmp_path: Path,
) -> None:
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
    assert (
        _signature(result) == "def __extracted_func_0(value: int, _towel_owner: 'object') -> int:"
    )


@requires_mypy
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
        _signature(result) == "def __extracted_func_0(flag: bool, _towel_owner: 'object') -> Box:"
    )


@requires_mypy
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
    assert (
        _signature(result) == "def __extracted_func_0(value: int, _towel_owner: 'object') -> int:"
    )


@pytest.mark.parametrize(
    "revealed, expected",
    [
        ("def () -> int", "'Callable[[], int]'"),
        ("def (x: int, y: str) -> bool", "'Callable[[int, str], bool]'"),
        ("def (*args: Any, **kwargs: Any) -> str", "'Callable[..., str]'"),
        ("def (x: int = ...) -> int", "'Callable[..., int]'"),
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
        return self.check_project({file_path: source})

    def check_project(self, sources, *, excluded_paths=()):
        result = self.inner.check_project(sources, excluded_paths=excluded_paths)
        if isinstance(result, CheckFailure):
            return result
        errors = list(result.errors)
        for file_path, source in sources.items():
            if "extracted_func" in source and not re.search(r"(:|->) (_typing\.)?Any\b", source):
                errors.append(
                    TypeDiagnostic(file_path, "Simulated: annotated helper does not type-check")
                )
        return CheckSuccess(tuple(errors))

    def close(self):
        self.inner.close()


@requires_mypy
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
    assert (
        _signature(result)
        == "def __extracted_func_0(value: _typing.Any, _towel_owner: 'object') -> _typing.Any:"
    )
    assert "import typing as _typing\n" in result and "from typing import Any" not in result


class _CountingOracle(_Oracle):
    """``_Oracle``, counting the checks of each distinct source text."""

    def __init__(self) -> None:
        super().__init__()
        self.checks: Dict[str, int] = {}

    def check_project(self, sources, *, excluded_paths=()):
        for source in sources.values():
            self.checks[source] = self.checks.get(source, 0) + 1
        return super().check_project(sources, excluded_paths=excluded_paths)


@requires_mypy
def test_each_original_is_checked_once_across_the_fallback_attempts(
    tmp_path: Path,
) -> None:
    """The annotated, all-``Any`` and bare attempts compare against one original."""
    path = tmp_path / "m.py"
    source = textwrap.dedent("""
            def first(value: int) -> int:
                total = value * 2
                text = str(total)
                return len(text.strip()) + 1

            def second(value: int) -> int:
                total = value * 2
                text = str(total)
                return len(text.strip()) + 1
            """)
    path.write_text(source)
    oracle = _CountingOracle()
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=oracle
    )
    proposals = engine.analyze_file(str(path))
    result = engine.apply_refactoring(str(path), proposals[0])
    assert "-> _typing.Any" in result, "the annotated attempt failed and the all-Any one was kept"
    assert oracle.checks[source] == 1


@requires_mypy
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
    assert "_typing.Callable[[], int]" in _signature(result), _signature(result)
    assert "import typing as _typing\n" in result and "from typing import Callable" not in result
    exec(compile(result, "<callable>", "exec"), {})


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


@requires_pyright
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


@requires_pyright
def test_pyright_oracle_judges_subtypes_and_checks(tmp_path: Path) -> None:
    from towel.type_inference import PyrightOracle

    module, source = _pyright_package(tmp_path)
    oracle = PyrightOracle()
    assert oracle.is_subtype(
        str(module),
        source,
        [("bool", "int"), ("int", "bool"), ("Box", "Base"), ("int", "Unknown")],
    ) == [YES, NO, YES, UNKNOWN]
    assert oracle.check(str(module), source) == CheckSuccess()
    result = oracle.check(str(module), source + "x: int = 'a'\n")
    assert isinstance(result, CheckSuccess)
    assert any("pyright" in error.message for error in result.errors)


def test_pyright_callable_spelling() -> None:
    result = annotation_from_revealed("(x: int) -> str", ast.parse(""), True)
    assert result is not None and ast.unparse(result) == "'Callable[[int], str]'"
    result = annotation_from_revealed("() -> int", ast.parse(""), True)
    assert result is not None and ast.unparse(result) == "'Callable[[], int]'"


@requires_mypy
@requires_pyright
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


@requires_pyright
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
    assert (
        _signature(result)
        == "def __extracted_func_0(__param_0: int, box: Box, _towel_owner: 'object') -> str:"
    )


@requires_mypy
def test_two_returned_variables_get_a_tuple_of_revealed_types(tmp_path: Path) -> None:
    """A helper returning two variables is annotated with the tuple of their revealed types."""
    # Python 3.10 supports the inferred type syntax; generated compound
    # annotations must still avoid adding runtime evaluation.
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "m"\nrequires-python = ">=3.10"\n')
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            class Box:
                def __init__(self) -> None:
                    self.count = 1
                    self.name = "n"

            def first() -> str:
                scaled = 2 * 2
                label = "n".upper()
                print(scaled)
                return label + str(scaled)

            def second() -> str:
                scaled = 2 * 2
                label = "n".upper()
                print(scaled)
                return label * scaled
            """))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=MypyInferrer()
    )
    proposals = engine.analyze_file(str(path))
    assert [p.description for p in proposals] == ["Extract common code from first and second"]
    result = engine.apply_refactoring(str(path), proposals[0])
    assert _signature(result) == "def __extracted_func_0() -> 'tuple[int, str]':"
    assert result.count("scaled, label = __extracted_func_0()") == 2
    for source in (path.read_text(), result):
        namespace: dict[str, object] = {}
        exec(compile(source, "<inferred>", "exec"), namespace)
        assert tuple(
            cast(Callable[[], str], namespace[name])() for name in ("first", "second")
        ) == ("N4", "NNNN")


@requires_pyright
def test_pyright_never_executes_a_project_package_that_shadows_the_standard_library(
    tmp_path: Path,
) -> None:
    """pyright runs from the module's directory; a ``locale`` package there must not run."""
    from towel.type_inference import PyrightOracle

    package = tmp_path / "proj"
    shadow = package / "locale"
    shadow.mkdir(parents=True)
    marker = tmp_path / "executed"
    (shadow / "__init__.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    module = package / "m.py"
    source = "def f(x: int) -> int:\n    return x + 1\n"
    module.write_text(source)
    messages = PyrightOracle().check(str(module), source)
    assert not marker.exists(), "the project's locale package was executed"
    assert isinstance(messages, CheckSuccess)
    assert not messages.errors


def test_original_box_owned_tuple_partial_stays_refused(tmp_path: Path) -> None:
    # Exact original partial: opaque helper locals would cross a frame while
    # the caller still owns its original parameter. Keep that refusal covered.
    source = textwrap.dedent(
        '\n            class Box:\n                def __init__(self) -> None:\n                    self.count = 1\n                    self.name = "n"\n\n            def first(box: Box) -> str:\n                scaled = box.count * 2\n                label = box.name.upper()\n                print(scaled)\n                return label + str(scaled)\n\n            def second(box: Box) -> str:\n                scaled = box.count * 2\n                label = box.name.upper()\n                print(scaled)\n                return label * scaled\n            '
    )
    path = tmp_path / "owned_partial.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(min_lines=2, annotate_helpers=False)
    assert engine.analyze_file(str(path)) == []
    assert "owned_binding_frame_boundary" in engine.declined_pairs
    assert path.read_text() == source

"""The generic rung sees the program's own types: a name is what the program's imports make it.

mypy names a type by its module's absolute name, ``packaging.version.Version``;
a module that imports it relatively, ``from .version import Version``, or
defines it, names it ``Version``. Resolution now gives both the same
identity from the import model's module names, so rows form and the type
variable machinery runs. Every case below is shaped like a corpus decline it
recovers (packaging D, I, K; rich R3, R4, R8; mistune M2, M3), with the
revealed texts mypy printed there.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import textwrap

import pytest

from towel.type_inference import CheckSuccess, MypyInferrer
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.type_bindings import (
    TypeResolver,
    TypeTerm,
    render_type,
    required_imports,
    spellable,
)
from towel.unification.type_generalization import generalize_signatures

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

MODULES = {
    "/project/src/packaging/ranges.py": "packaging.ranges",
    "/project/src/packaging/version.py": "packaging.version",
    "/project/src/packaging/direct_url.py": "packaging.direct_url",
    "/project/src/packaging/pylock.py": "packaging.pylock",
    "/project/src/packaging/dependency_groups.py": "packaging.dependency_groups",
    "/project/rich/text.py": "rich.text",
    "/project/rich/markup.py": "rich.markup",
    "/project/rich/tree.py": "rich.tree",
    "/project/rich/style.py": "rich.style",
    "/project/src/mistune/core.py": "mistune.core",
    "/project/src/mistune/helpers.py": "mistune.helpers",
}


def resolver(
    source: str,
    file: str,
    host: str | None = None,
    host_file: str | None = None,
    line: int | None = None,
) -> TypeResolver:
    source = textwrap.dedent(source)
    return TypeResolver(
        source,
        file,
        line or len(source.splitlines()),
        source if host is None else textwrap.dedent(host),
        file if host_file is None else host_file,
        module_names=MODULES.get,
    )


def rendered(term: TypeTerm | None) -> str:
    assert term is not None
    return ast.unparse(render_type(term))


def declared(instance: TypeResolver, annotation: str) -> TypeTerm | None:
    return instance.resolve(ast.parse(annotation, mode="eval").body)


def members(term: TypeTerm | None) -> set[str]:
    """A union's members as written, in no order: a union's order is not its meaning."""
    assert term is not None
    return {rendered(member) for member in term.children}


RANGES = """\
from .version import Version


def _decompose_dev0_gap(tail: list[Version] | None) -> list[Version] | None:
    pass
"""


def test_a_relative_import_is_the_checkers_absolute_name() -> None:
    """packaging D: ``list[packaging.version.Version]`` is the module's own ``list[Version]``."""
    ranges = resolver(RANGES, "/project/src/packaging/ranges.py")
    revealed = ranges.resolve_revealed("list[packaging.version.Version] | None")
    assert rendered(revealed) == "list[Version] | None"
    assert revealed == declared(ranges, "list[Version] | None")
    # Without the program's module names the relative import names a place, not a module.
    located = TypeResolver(
        RANGES, "/project/src/packaging/ranges.py", 5, RANGES, "/project/src/packaging/ranges.py"
    ).resolve_revealed("list[packaging.version.Version] | None")
    assert located is not None and not spellable(located)


def test_a_module_level_class_is_named_by_its_module() -> None:
    """mistune M3 and nox: ``mistune.core.BlockState`` is core.py's own ``BlockState``."""
    core = resolver(
        """\
        class BlockState:
            pass


        def find_line_end_at(state: BlockState) -> int:
            pass
        """,
        "/project/src/mistune/core.py",
    )
    revealed = core.resolve_revealed("mistune.core.BlockState")
    assert rendered(revealed) == "BlockState" and revealed == declared(core, "BlockState")


def test_a_class_another_module_imports_is_the_same_class() -> None:
    """The class ``version.py`` defines is the one ``ranges.py`` imports."""
    version = resolver(
        "class Version:\n    pass\n\ndef f(v: Version) -> None:\n    pass\n",
        "/project/src/packaging/version.py",
        host=RANGES,
        host_file="/project/src/packaging/ranges.py",
    )
    ranges = resolver(RANGES, "/project/src/packaging/ranges.py")
    assert declared(version, "Version") == declared(ranges, "Version")
    assert rendered(declared(version, "Version")) == "Version"


def test_a_name_imported_only_for_the_checker_is_a_binding() -> None:
    """rich R8: tree.py declares ``console: 'Console'``, imported under ``TYPE_CHECKING``."""
    tree = """\
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from .console import Console


    def render(console: "Console") -> None:
        pass
    """
    instance = resolver(tree, "/project/rich/tree.py")
    term = declared(instance, "'Console'")
    assert term is not None and term.name == "rich.console.Console" and rendered(term) == "Console"
    assert instance.resolve_revealed("rich.console.Console") == term
    rebound = tree.replace(
        "        from .console import Console\n",
        "        from .console import Console\n    else:\n        Console = None\n",
    )
    assert declared(resolver(rebound, "/project/rich/tree.py"), "'Console'") is None


def test_a_type_only_the_site_can_name_is_resolved_there() -> None:
    """rich R4: tree.py's ``Tree`` resolves at tree.py, but markup.py, the host, cannot write it."""
    term = resolver(
        "class Tree:\n    pass\n\ndef measure(stack: list[Tree]) -> None:\n    pass\n",
        "/project/rich/tree.py",
        host="from .text import Span\n",
        host_file="/project/rich/markup.py",
    ).resolve_revealed("builtins.list[typing.Iterator[rich.tree.Tree]]")
    assert term is not None and not spellable(term)
    iterator = term.children[1]
    assert iterator.children[0].name == "collections.abc.Iterator"
    assert iterator.children[1].name == "rich.tree.Tree"


def test_the_typing_names_a_signature_needs_are_imported_with_it() -> None:
    """packaging I: dependency_groups.py writes ``Callable`` without importing it."""
    instance = resolver(
        """\
        from .requirements import Requirement


        def _resolve(group: str) -> tuple[Requirement, ...]:
            pass
        """,
        "/project/src/packaging/dependency_groups.py",
    )
    term = instance.resolve_revealed(
        "def () -> dict[str, tuple[packaging.requirements.Requirement, ...]]"
    )
    assert rendered(term) == "Callable[[], dict[str, tuple[Requirement, ...]]]"
    assert term is not None and required_imports([term]) == (("typing", "Callable"),)


GET_REQUIRED = """\
from collections.abc import Mapping
from typing import Any, TypeVar

_T = TypeVar("_T")


class _DirectUrlRequiredKeyError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)


def _get(d: Mapping[str, Any], expected_type: type[_T], key: str) -> _T | None:
    pass


def _get_required(d: Mapping[str, Any], expected_type: type[_T], key: str) -> _T:
    pass
"""


@pytest.mark.parametrize(
    "revealed",
    [
        "def [_T] (d: typing.Mapping[str, Any], expected_type: type[_T], key: str) -> _T | None",
        "def [_T] (d: typing.Mapping[builtins.str, Any], expected_type: type[_T`-1],"
        " key: builtins.str) -> Union[_T`-1, None]",
    ],
)
def test_a_generic_callable_is_instantiated_at_the_sites_type_variable(revealed: str) -> None:
    """packaging K: ``_get`` passed into the helper shares ``_T`` with ``expected_type``.

    The first spelling is mypy 2.3's, the second 1.14's.
    """
    instance = resolver(GET_REQUIRED, "/project/src/packaging/direct_url.py")
    term = instance.resolve_revealed(revealed)
    assert rendered(term) == "Callable[[Mapping[str, Any], type[_T], str], _T | None]"
    assert term is not None
    expected_type = declared(instance, "type[_T]")
    assert term.children[1].children[1] == expected_type  # the very same _T


def test_nested_any_is_the_programs_own_type() -> None:
    """packaging K and mistune M2: ``Mapping[str, Any]`` and ``dict[str, Any] | None`` resolve."""
    instance = resolver(GET_REQUIRED, "/project/src/packaging/direct_url.py")
    assert rendered(instance.resolve_revealed("typing.Mapping[str, Any]")) == "Mapping[str, Any]"
    assert rendered(declared(instance, "Mapping[str, Any]")) == "Mapping[str, Any]"
    assert instance.resolve_revealed("Any") is None
    helpers = resolver(
        "from typing import Any, Dict, Tuple, Union\n\n"
        "def parse_link(src: str) -> Union[Tuple[Dict[str, Any], int], Tuple[None, None]]:\n"
        "    pass\n",
        "/project/src/mistune/helpers.py",
    )
    assert rendered(helpers.resolve_revealed("dict[str, Any] | None")) == "dict[str, Any] | None"
    assert members(declared(helpers, "Union[Tuple[Dict[str, Any], int], Tuple[None, None]]")) == {
        "tuple[dict[str, Any], int]",
        "tuple[None, None]",
    }


def test_a_function_returning_a_function_keeps_both_signatures() -> None:
    """packaging K: ``lambda: _DirectUrlRequiredKeyError`` reveals the class's constructor."""
    instance = resolver(GET_REQUIRED, "/project/src/packaging/direct_url.py")
    term = instance.resolve_revealed(
        "def () -> def (key: str) -> packaging.direct_url._DirectUrlRequiredKeyError"
    )
    assert rendered(term) == "Callable[[], Callable[[str], _DirectUrlRequiredKeyError]]"


TEXT = """\
from typing import NamedTuple, Union

from .style import Style


class Span(NamedTuple):
    start: int
    end: int
    style: Union[str, Style]


def highlight_words(words: list[str]) -> int:
    pass
"""


def test_a_named_tuple_and_its_constructor_read_as_the_class() -> None:
    """rich R3 under mypy 1.14: ``Span``, its constructor, and ``list.append`` of it."""
    instance = resolver(TEXT, "/project/rich/text.py")
    spelled = "tuple[builtins.int, builtins.int, Union[builtins.str, rich.style.Style], fallback=rich.text.Span]"
    assert rendered(instance.resolve_revealed(spelled)) == "Span"
    constructor = instance.resolve_revealed(
        "def (start: builtins.int, end: builtins.int, style: Union[builtins.str, rich.style.Style])"
        f" -> {spelled}"
    )
    assert rendered(constructor) == "Callable[[int, int, str | Style], Span]"
    append = instance.resolve_revealed(f"def ({spelled})")
    assert rendered(append) == "Callable[[Span], None]"


def test_a_callable_no_parameter_list_states_can_only_be_generalized_away() -> None:
    """rich R8: ``console.get_style`` has a keyword-only parameter with a default."""
    instance = resolver(
        "from .style import Style\n\ndef render(console: object) -> None:\n    pass\n",
        "/project/rich/tree.py",
        host="from .color import Color\n",
        host_file="/project/rich/style.py",
    )
    thunk = instance.resolve_revealed(
        "def () -> def (name: Union[builtins.str, rich.style.Style], *,"
        " default: Union[rich.style.Style, builtins.str, None] =) -> rich.style.Style"
    )
    assert thunk is not None and not spellable(thunk)
    other = resolver("from .color import Color\n", "/project/rich/style.py").resolve_revealed(
        "def () -> Union[rich.color.Color, None]"
    )
    assert other is not None
    (signature,) = generalize_signatures([(thunk, thunk), (other, other)], set())[:1]
    assert [rendered(term) for term in signature.types] == [
        "Callable[[], _TowelT0]",
        "Callable[[], _TowelT0]",
    ]


def test_union_members_line_up_by_structure_not_by_sorting() -> None:
    """``int | pkg.Foo`` and ``pkg.Foo | zlib.Bar`` share ``Foo``: only the rest differs."""
    host = "from pkg import Foo\nfrom zlib import Bar\n"
    instance = TypeResolver(host, "/project/m.py", 2, host, "/project/m.py")
    first = instance.resolve_revealed("builtins.int | pkg.Foo")
    second = instance.resolve_revealed("pkg.Foo | zlib.Bar")
    assert first is not None and second is not None
    (signature,) = generalize_signatures([(first, first), (second, second)], set())[:1]
    assert rendered(signature.types[0]) == "_TowelT0 | Foo"


def test_the_studys_signatures_come_out_of_the_rows() -> None:
    """packaging D and mistune M2: one variable over the element or the optional value."""
    ranges = resolver(RANGES, "/project/src/packaging/ranges.py")
    rows = [
        tuple(ranges.resolve_revealed(text) for text in (a, b, c))
        for a, b, c in (
            ("list[str] | None", "list[str]", "list[str] | None"),
            (
                "list[packaging.version.Version] | None",
                "list[packaging.version.Version]",
                "list[packaging.version.Version] | None",
            ),
        )
    ]
    assert all(term is not None for row in rows for term in row)
    first = generalize_signatures([tuple(t for t in row if t) for row in rows], set())[0]
    assert [rendered(t) for t in first.types] == [
        "list[_TowelT0] | None",
        "list[_TowelT0]",
        "list[_TowelT0] | None",
    ]
    helpers = resolver(
        "from typing import Any, Dict, Tuple, Union\n\n"
        "def parse_link(src: str) -> Union[Tuple[Dict[str, Any], int], Tuple[None, None]]:\n"
        "    pass\n",
        "/project/src/mistune/helpers.py",
    )
    mistune_rows = [
        (
            helpers.resolve_revealed(value),
            helpers.resolve_revealed("int | None"),
            declared(helpers, result),
        )
        for value, result in (
            ("str | None", "Union[Tuple[str, int], Tuple[None, None]]"),
            ("dict[str, Any] | None", "Union[Tuple[Dict[str, Any], int], Tuple[None, None]]"),
        )
    ]
    signature = generalize_signatures(
        [tuple(t for t in row if t is not None) for row in mistune_rows], set()
    )[0]
    assert [rendered(t) for t in signature.types[:2]] == ["_TowelT0 | None", "int | None"]
    assert members(signature.types[2]) == {"tuple[_TowelT0, int]", "tuple[None, None]"}


def test_a_constraint_the_host_cannot_write_leaves_its_variable_free() -> None:
    """rich R4: ``Iterator[Tree]`` may stand inside ``_TowelT0``, never in its declaration."""
    tree = resolver(
        "class Tree:\n    pass\n",
        "/project/rich/tree.py",
        host="from .text import Span\n",
        host_file="/project/rich/markup.py",
    )
    markup = resolver("from .text import Span\n", "/project/rich/markup.py")
    rows = [
        tuple(markup.resolve_revealed(text) for text in ("list[rich.text.Span]", "int", "int")),
        tuple(
            tree.resolve_revealed(text)
            for text in ("list[typing.Iterator[rich.tree.Tree]]", "str", "str")
        ),
    ]
    signatures = generalize_signatures(
        [tuple(t for t in row if t is not None) for row in rows], set(), constrainable=spellable
    )
    constrained = signatures[-1]
    domains = {parameter.name: parameter.constraints for parameter in constrained.parameters}
    assert domains["_TowelT0"] == ()
    assert [rendered(t) for t in domains["_TowelT1"]] == ["int", "str"]


PACKAGE = {
    "pyproject.toml": "[tool.mypy]\nstrict = true\n",
    "tests/test_shapes.py": "from shapes.version import Version\n",
    "shapes/__init__.py": "",
    "shapes/version.py": "class Version:\n    def __init__(self, text: str) -> None:\n        self.text = text\n",
}


def _project(tmp_path: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))


def _helper(source: str) -> ast.FunctionDef:
    return next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )


def _annotation(node: ast.expr | None) -> str:
    assert node is not None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node)


@requires_mypy
def test_a_relatively_imported_class_gets_a_type_variable_end_to_end(tmp_path: Path) -> None:
    """packaging D, in miniature: the rows form only when ``shapes.version.Version`` is ``Version``."""
    _project(
        tmp_path,
        {
            **PACKAGE,
            "shapes/ranges.py": """\
                from .version import Version


                def upper(upper_parts: list[str] | None, lower_parts: list[str]) -> list[str] | None:
                    if upper_parts is None:
                        return None
                    return lower_parts + upper_parts


                def tail(tail_parts: list[Version] | None, fragments: list[Version]) -> list[Version] | None:
                    if tail_parts is None:
                        return None
                    return fragments + tail_parts
                """,
        },
    )
    path = tmp_path / "shapes" / "ranges.py"
    original = path.read_text()
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        proposals = engine.analyze_file(str(path))
        assert proposals
        changed = engine.apply_refactoring(str(path), proposals[0])
        assert oracle.check(str(path), changed) == CheckSuccess(), changed
    finally:
        oracle.close()
    helper = _helper(changed)
    kinds = [_annotation(parameter.annotation) for parameter in helper.args.args]
    assert kinds == ["list[_TowelT0] | None", "list[_TowelT0]"], changed
    assert _annotation(helper.returns) == "list[_TowelT0] | None"
    assert path.read_text() == original


@requires_mypy
def test_the_callable_a_signature_writes_is_imported_end_to_end(tmp_path: Path) -> None:
    """packaging I, in miniature: the host imports no ``Callable``; the variant brings it."""
    _project(
        tmp_path,
        {
            **PACKAGE,
            "shapes/groups.py": """\
                from .version import Version


                class Resolver:
                    def __init__(self) -> None:
                        self._versions: dict[str, tuple[Version, ...]] = {}
                        self._names: dict[str, tuple[str, ...]] = {}

                    def versions(self, group: str, found: list[Version]) -> tuple[Version, ...]:
                        if not found:
                            return ()
                        self._versions[group] = tuple(found)
                        return self._versions[group]

                    def names(self, group: str, found: list[str]) -> tuple[str, ...]:
                        if not found:
                            return ()
                        self._names[group] = tuple(found)
                        return self._names[group]
                """,
        },
    )
    path = tmp_path / "shapes" / "groups.py"
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        proposals = engine.analyze_file(str(path))
        assert proposals
        changed = engine.apply_refactoring(str(path), proposals[0])
        assert oracle.check(str(path), changed) == CheckSuccess(), changed
    finally:
        oracle.close()
    assert "from typing import Callable" in changed, changed
    helper = _helper(changed)
    kinds = [
        _annotation(parameter.annotation)
        for parameter in helper.args.args
        if parameter.annotation is not None
    ]
    assert "Callable[[], dict[str, tuple[_TowelT0, ...]]]" in kinds, changed
    assert _annotation(helper.returns) == "tuple[_TowelT0, ...]"


@requires_mypy
def test_a_parameter_the_body_never_reads_is_object_end_to_end(tmp_path: Path) -> None:
    """mistune M3, in miniature: ``self`` reaches the helper unread, and its type is moot.

    The two sites' receivers are unrelated classes, which no variable the
    host could constrain would name; ``object`` accepts both. What remains
    is one variable over ``int`` and ``str``, whose ``+`` checks only when
    constrained to them.
    """
    _project(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nstrict = true\n",
            "lines.py": """\
                class BlockState:
                    def __init__(self, src: str) -> None:
                        self.src = src

                    def line_end(self, end: int) -> int:
                        if end < 0:
                            return len(self.src)
                        return end + 1


                class Renderer:
                    def __init__(self, escape: bool) -> None:
                        self.escape = escape

                    def block_html(self, html: str) -> str:
                        if self.escape:
                            return "<p>" + html.strip() + "</p>\\n"
                        return html + "\\n"
                """,
        },
    )
    path = tmp_path / "lines.py"
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        proposals = engine.analyze_file(str(path))
        assert proposals
        changed = engine.apply_refactoring(str(path), proposals[0])
        assert oracle.check(str(path), changed) == CheckSuccess(), changed
    finally:
        oracle.close()
    helper = _helper(changed)
    kinds = {parameter.arg: _annotation(parameter.annotation) for parameter in helper.args.args}
    assert kinds["self"] == "object" and "Any" not in changed, changed
    assert "_towel_typevar('_TowelT0', 'int', 'str')" in changed, changed
    namespace: dict[str, object] = {}
    exec(compile(changed, str(path), "exec"), namespace)

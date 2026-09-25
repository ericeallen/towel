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

"""A tree Towel builds compares as the tree the parser builds, on every supported Python.

Before 3.13 a constructor sets only the fields it is given, and ``ast.dump``
leaves out a field that is not set but writes one set empty, so a helper
Towel built never equalled its own rendering parsed back on 3.12. Each test
here builds a node by hand, parses the source it stands for, and checks
that a comparison Towel makes agrees; most failed on 3.12, and some on 3.11,
before every comparison went through ``canonical_dump``.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path
import sys
from typing import Callable, List, Sequence, Type, TypeVar

import pytest

from towel.canonical_ast import canonical_dump
from towel.type_inference import Subtyping
from towel.unification.annotations import _joined, _unknown_subtypes, normalize_union
from towel.unification.block_comments import HelperComments, weave_comments
from towel.unification.fixed_point import _RejectedProposals
from towel.unification.generic_annotations import _annotation_structure
from towel.unification.instantiation import instantiation_mismatch
from towel.unification.models import RefactoringProposal, Replacement, proposal_identity
from towel.unification.structural_memo import structural_id
from towel.unification.substitution import Substitution, structural_text

ROOT = Path(__file__).resolve().parent.parent

_Node = TypeVar("_Node", bound=ast.AST)


def _built(kind: Type[_Node], **fields: object) -> _Node:
    """``kind(**fields)``: a node given only ``fields``, as a 3.13 constructor accepts it.

    The 3.11 stubs require every list field; leaving some out is the point.
    """
    return kind(**fields)


def _load(name: str) -> ast.Name:
    return ast.Name(id=name, ctx=ast.Load())


def _expression(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def _statement(source: str) -> ast.stmt:
    return ast.parse(source).body[0]


def _helper_as_the_extractor_builds_it(
    parameters: Sequence[str], body: List[ast.stmt]
) -> ast.FunctionDef:
    """A helper with every field ``HygienicExtractor.extract_function`` gives, and no more.

    No ``type_params``: 3.11 has no such field, and before 3.13 the node
    then has no such attribute, where the parser sets an empty list.
    """
    helper = ast.FunctionDef(
        name="__extracted_func",
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=name) for name in parameters],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=body,
        decorator_list=[],
        returns=None,
    )
    return ast.fix_missing_locations(helper)


def _parsed_helper(source: str) -> ast.FunctionDef:
    parsed = _statement(source)
    assert isinstance(parsed, ast.FunctionDef)
    return parsed


# Each pair is a node built the way a 3.13 constructor allows, leaving out the
# lists, contexts and optional fields it fills in, and the source it stands for.
BUILT_AND_PARSED: List[tuple[str, Callable[[], ast.AST], Callable[[], ast.AST]]] = [
    (
        "helper without type_params",
        lambda: _helper_as_the_extractor_builds_it(
            ["x"], [ast.Return(value=ast.Name(id="x", ctx=ast.Load()))]
        ),
        lambda: _statement("def __extracted_func(x):\n    return x\n"),
    ),
    (
        "function without any list field",
        lambda: _built(
            ast.FunctionDef,
            name="f",
            args=_built(ast.arguments, args=[ast.arg(arg="x")]),
            body=[ast.Return(value=ast.Name(id="x"))],
        ),
        lambda: _statement("def f(x):\n    return x\n"),
    ),
    ("name without ctx", lambda: ast.Name(id="x"), lambda: _expression("x")),
    (
        "attribute without ctx",
        lambda: ast.Attribute(value=_load("a"), attr="b"),
        lambda: _expression("a.b"),
    ),
    (
        "subscript without ctx",
        lambda: ast.Subscript(value=_load("list"), slice=_load("int")),
        lambda: _expression("list[int]"),
    ),
    (
        "call without keywords",
        lambda: _built(ast.Call, func=_load("f"), args=[_load("x")]),
        lambda: _expression("f(x)"),
    ),
    (
        "class without bases, keywords, decorators or type_params",
        lambda: _built(ast.ClassDef, name="C", body=[ast.Pass()]),
        lambda: _statement("class C:\n    pass\n"),
    ),
    (
        "module without type_ignores",
        lambda: _built(ast.Module, body=[ast.Expr(value=ast.Constant(value=1))]),
        lambda: ast.parse("1\n"),
    ),
]


@pytest.mark.parametrize(
    "built, parsed",
    [(built, parsed) for _, built, parsed in BUILT_AND_PARSED],
    ids=[name for name, _, _ in BUILT_AND_PARSED],
)
def test_a_built_node_spells_as_the_parsed_node(
    built: Callable[[], ast.AST], parsed: Callable[[], ast.AST]
) -> None:
    assert canonical_dump(built()) == canonical_dump(parsed())


def test_the_spelling_is_the_same_on_every_version() -> None:
    tree = ast.parse("def f(x, *rest, key=None):\n    return g(x)[rest].attr\n")
    assert canonical_dump(tree) == (
        "Module(body=[FunctionDef(name='f', args=arguments(args=[arg(arg='x')], "
        "vararg=arg(arg='rest'), kwonlyargs=[arg(arg='key')], "
        "kw_defaults=[Constant(value=None)]), body=[Return(value=Attribute("
        "value=Subscript(value=Call(func=Name(id='g', ctx=Load()), "
        "args=[Name(id='x', ctx=Load())]), slice=Name(id='rest', ctx=Load()), "
        "ctx=Load()), attr='attr', ctx=Load()))])])"
    )


def _repository_trees() -> List[ast.AST]:
    trees: List[ast.AST] = []
    for directory in ("src/towel", "tests/hostile_cases"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            try:
                trees.append(ast.parse(path.read_bytes()))
            except SyntaxError:
                continue  # Written in syntax this Python does not have.
    return trees


@pytest.mark.skipif(sys.version_info < (3, 13), reason="the 3.13 dump is the reference")
def test_the_spelling_is_the_313_dump_of_every_node() -> None:
    compared = 0
    for tree in _repository_trees():
        for node in ast.walk(tree):
            try:
                expected = ast.dump(node)
            except ValueError:
                continue  # An int too wide for repr; see the test below.
            assert canonical_dump(node) == expected
            compared += 1
    assert compared > 100_000


@pytest.mark.skipif(sys.version_info < (3, 13), reason="3.13 constructors fill in fields")
@pytest.mark.parametrize(
    "built", [built for _, built, _ in BUILT_AND_PARSED], ids=[n for n, _, _ in BUILT_AND_PARSED]
)
def test_the_spelling_is_the_313_dump_of_every_built_node(built: Callable[[], ast.AST]) -> None:
    node = built()
    assert canonical_dump(node) == ast.dump(node)


@pytest.mark.parametrize(
    "first, second",
    [
        ("return", "return None"),
        ("x[1:]", "x[1:None]"),
        ("f(*a)", "f(a)"),
        ("{**a}", "{None: a}"),
        ("from . import x", "from .. import x"),
        ("def f():\n    pass", "async def f():\n    pass"),
        ("lambda: 0", "lambda *a: 0"),
        ("match x:\n    case None:\n        pass", "match x:\n    case _:\n        pass"),
        ("x = 1", "x == 1"),
    ],
)
def test_what_the_313_dump_distinguishes_stays_distinct(first: str, second: str) -> None:
    assert canonical_dump(ast.parse(first)) != canonical_dump(ast.parse(second))


def test_a_name_built_without_ctx_is_a_load_not_a_store() -> None:
    target = _statement("x = 1")
    assert isinstance(target, ast.Assign)
    assert canonical_dump(ast.Name(id="x")) != canonical_dump(target.targets[0])
    assert canonical_dump(ast.Name(id="x")) == canonical_dump(_expression("x"))


def _wide_int(extra_bits: int = 0) -> ast.stmt:
    digits = max(sys.get_int_max_str_digits(), sys.int_info.default_max_str_digits)
    return _statement(f"x = {(1 << (int(digits * 3.33) + 8 + extra_bits)) - 1:#x}")


def test_an_int_too_wide_for_repr_has_a_distinct_spelling() -> None:
    wide, wider = _wide_int(), _wide_int(4)
    assert canonical_dump(wide) == canonical_dump(copy.deepcopy(wide))
    assert canonical_dump(wide) != canonical_dump(wider)
    assert "int(0x" in canonical_dump(wide)
    assert canonical_dump(_statement("x = 1")) == (
        "Assign(targets=[Name(id='x', ctx=Store())], value=Constant(value=1))"
    )


# The comparisons Towel makes, each between a node it built and one it parsed.


def test_a_built_helper_renders_to_itself() -> None:
    body = [_statement("print(x)")]
    helper = _helper_as_the_extractor_builds_it(["x"], body)
    woven = weave_comments(helper, helper, HelperComments())
    assert woven.text == ast.unparse(helper)


def test_an_identical_built_annotation_is_a_subtype_without_a_checker() -> None:
    pairs = [
        (ast.Name(id="int"), _expression("int")),
        (ast.Subscript(value=_load("list"), slice=_load("int")), _expression("list[int]")),
    ]
    assert list(_unknown_subtypes(pairs)) == [Subtyping.YES, Subtyping.YES]


def test_a_union_of_a_built_and_a_parsed_spelling_has_one_member() -> None:
    members = [ast.Subscript(value=_load("list"), slice=_load("int")), _expression("list[int]")]
    assert [ast.unparse(m) for m in normalize_union(members, _unknown_subtypes)] == ["list[int]"]


def test_one_spelling_everywhere_is_kept_as_written() -> None:
    built = ast.Subscript(value=_load("Optional"), slice=_load("int"))
    joined = _joined([built, _expression("Optional[int]")], None, True, {"Optional"})
    assert joined is not None
    assert ast.unparse(joined) == "Optional[int]"


def test_a_built_helper_instantiates_to_the_parsed_block() -> None:
    # The call is built without ``keywords``; the argument replaces the parameter.
    call = _built(ast.Call, func=_load("print"), args=[_load("__param_0")])
    helper = _helper_as_the_extractor_builds_it(["__param_0"], [ast.Expr(value=call)])
    mismatch = instantiation_mismatch(
        helper,
        _statement("__extracted_func(value)"),
        [_statement("print(value)")],
        {},
        {},
        site_function_names=frozenset(),
        preamble_length=0,
        returns_variables=False,
    )
    assert mismatch is None


def test_a_built_body_annotation_has_the_parsed_structure() -> None:
    built = ast.Subscript(value=_load("dict"), slice=ast.Tuple(elts=[_load("str"), _load("int")]))
    assert _annotation_structure(built) == _annotation_structure(_expression("dict[str, int]"))
    assert _annotation_structure(built) == _annotation_structure(_expression("'dict[str, int]'"))


def test_structural_keys_of_a_built_and_a_parsed_expression_agree() -> None:
    built = _built(ast.Call, func=_load("f"), args=[ast.Attribute(value=_load("a"), attr="b")])
    parsed = _expression("f(a.b)")
    assert structural_text(built) == structural_text(parsed)
    statement = ast.Expr(value=copy.deepcopy(built))
    assert structural_id([statement]) == structural_id([_statement("f(a.b)")])
    substitution = Substitution()
    substitution.add_mapping(0, parsed, "__param_0")
    assert substitution.get_param_for_expr(0, built) == "__param_0"


def _proposal(helper: ast.FunctionDef) -> RefactoringProposal:
    return RefactoringProposal(
        file_path="m.py",
        extracted_function=helper,
        replacements=[Replacement(line_range=(2, 2), node=_statement("__extracted_func(x)"))],
        description="",
        parameters_count=1,
    )


def test_a_proposal_is_known_by_its_helper_however_it_was_built() -> None:
    built = _proposal(_helper_as_the_extractor_builds_it(["x"], [_statement("print(x)")]))
    parsed = _proposal(_parsed_helper("def __extracted_func(x):\n    print(x)\n"))
    assert proposal_identity(built) == proposal_identity(parsed)
    assert _RejectedProposals.identity(built) == _RejectedProposals.identity(parsed)


def _dump_references(tree: ast.AST) -> List[int]:
    lines: List[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "dump"
            and isinstance(node.value, ast.Name)
            and node.value.id == "ast"
        ):
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "ast":
            lines.extend(node.lineno for alias in node.names if alias.name in ("dump", "*"))
    return lines


def test_towel_compares_trees_only_through_the_canonical_spelling() -> None:
    """Every structural comparison, hash and key uses ``canonical_dump``; none uses ``ast.dump``."""
    source_root = ROOT / "src" / "towel"
    offenders = [
        f"{path.relative_to(ROOT)}:{line}"
        for path in sorted(source_root.rglob("*.py"))
        if path.name != "canonical_ast.py"
        for line in _dump_references(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == []

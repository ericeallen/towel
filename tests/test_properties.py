"""Property-based tests for the unifier, definite assignment, and instantiation.

Each property generates programs from a grammar small enough that the
expected answer is obvious, then checks the engine against it:

* **Alpha-variance.** Two copies of a block whose block-local binders are
  consistently renamed unify with a substitution that introduces no
  parameters. The grammar binds names by top-level assignment and by loop
  targets, because those are the binders the unifier alpha-renames; names
  first bound inside a nested body are deliberately not renamed by it.
* **Definite assignment.** ``definitely_bound_after`` agrees with a reference
  that enumerates every fall-through path abstractly. The grammar has
  ``return`` and ``raise`` (paths that never fall through), ``for`` and
  ``while`` (bodies that may run zero times), ``try`` with a handler, else and
  finally, and ``with`` (transparent to control flow, binding its ``as`` name
  on entry). It has no ``del`` and no ``except ... as name``, which the
  analysis over-approximates path-insensitively, so the reference stays a
  plain path enumeration.
* **Instantiation round trip.** For two blocks that differ only in one leaf
  expression, every proposal the engine emits passes the public instantiation
  check when re-derived from the proposal's own data and the original source.
  Every generated block contains a call, so the trivial-helper filter never
  leaves the property vacuous.

* **Idempotence.** A directory run's output is a fixed point: a fresh engine
  applies nothing to it and leaves its bytes alone.
* **Byte preservation.** A module written with CRLF newlines, with a UTF-8
  BOM, or under a latin-1 coding cookie comes back in the same convention.
* **Observational equivalence.** Every generated helper compiles, and both
  call sites compute what the originals computed, on a few inputs and with
  the same printed output.

All are deterministic (``derandomize=True``) and have no deadline, so they
cannot flake on a slow machine. The pure properties run more examples than
the ones that drive the engine; together the file stays well under twenty
seconds.
"""

from __future__ import annotations

import ast
import contextlib
import io
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple, Union

from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from tests.test_observational_equivalence import compare_function_behavior
from towel.source_text import decode_source, dominant_newline, source_encoding
from towel.unification.definite_assignment import definitely_bound_after
from towel.unification.instantiation import instantiation_mismatch
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.unifier import Unifier

BOUNDED = settings(max_examples=300, deadline=None, derandomize=True)
"""Pure properties: cheap enough for three hundred examples."""

ENGINE_BOUNDED = settings(
    max_examples=60,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
"""Properties that run the engine on a temporary project."""

# ---------------------------------------------------------------------------
# A tiny expression/statement grammar shared by the alpha-variance and
# instantiation properties. Binder slot ``k`` is the k-th block-local binder
# in order of first assignment: a top-level assignment may introduce the next
# slot or rebind an earlier one, and a nested assignment may only rebind a
# slot already introduced above it. ``render`` spells slots under a naming.


@dataclass(frozen=True)
class Const:
    value: int


@dataclass(frozen=True)
class Free:
    name: str


@dataclass(frozen=True)
class Slot:
    index: int


@dataclass(frozen=True)
class LoopVar:
    pass


@dataclass(frozen=True)
class Binary:
    operator: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Call:
    callee: str
    arguments: Tuple["Expr", ...]


Expr = Union[Const, Free, Slot, LoopVar, Binary, Call]


@dataclass(frozen=True)
class Assign:
    slot: int
    value: Expr


@dataclass(frozen=True)
class ExprStmt:
    value: Expr


@dataclass(frozen=True)
class If:
    condition: Expr
    body: Tuple["Stmt", ...]
    orelse: Tuple["Stmt", ...]


@dataclass(frozen=True)
class For:
    iterable: Expr
    body: Tuple["Stmt", ...]


Stmt = Union[Assign, ExprStmt, If, For]

FREE_NAMES = ("src", "limit")
CALLABLES = ("f", "g", "h", "print")
OPERATORS = ("+", "-", "*")


def expressions(bound: int, in_loop: bool) -> st.SearchStrategy[Expr]:
    leaves: List[st.SearchStrategy[Expr]] = [
        st.builds(Const, st.integers(0, 3)),
        st.builds(Free, st.sampled_from(FREE_NAMES)),
    ]
    if bound:
        leaves.append(st.builds(Slot, st.integers(0, bound - 1)))
    if in_loop:
        leaves.append(st.just(LoopVar()))

    def extend(children: st.SearchStrategy[Expr]) -> st.SearchStrategy[Expr]:
        return st.one_of(
            st.builds(Binary, st.sampled_from(OPERATORS), children, children),
            call_expressions(children),
        )

    return st.recursive(st.one_of(*leaves), extend, max_leaves=4)


def call_expressions(arguments: st.SearchStrategy[Expr]) -> st.SearchStrategy[Call]:
    return st.builds(
        Call,
        st.sampled_from(CALLABLES),
        st.lists(arguments, min_size=1, max_size=2).map(tuple),
    )


@st.composite
def nested_statements(draw: st.DrawFn, bound: int, in_loop: bool) -> Tuple[Stmt, ...]:
    statements: List[Stmt] = []
    for _ in range(draw(st.integers(1, 2))):
        if bound and draw(st.booleans()):
            slot = draw(st.integers(0, bound - 1))
            statements.append(Assign(slot, draw(expressions(bound, in_loop))))
        else:
            statements.append(ExprStmt(draw(call_expressions(expressions(bound, in_loop)))))
    return tuple(statements)


@st.composite
def blocks(draw: st.DrawFn, min_size: int = 2, max_size: int = 5) -> Tuple[Stmt, ...]:
    """A block whose binders are introduced by top-level assignments in order."""
    bound = 0
    statements: List[Stmt] = []
    for _ in range(draw(st.integers(min_size, max_size))):
        kind = draw(st.sampled_from(["assign", "assign", "expr", "if", "for"]))
        if kind == "assign":
            introduce = bound == 0 or draw(st.booleans())
            slot = bound if introduce else draw(st.integers(0, bound - 1))
            statements.append(Assign(slot, draw(expressions(bound, False))))
            bound += 1 if introduce else 0
        elif kind == "expr":
            statements.append(ExprStmt(draw(call_expressions(expressions(bound, False)))))
        elif kind == "if":
            condition = draw(expressions(bound, False))
            body = draw(nested_statements(bound, False))
            orelse = draw(nested_statements(bound, False)) if draw(st.booleans()) else ()
            statements.append(If(condition, body, orelse))
        else:
            iterable = draw(call_expressions(expressions(bound, False)))
            statements.append(For(iterable, draw(nested_statements(bound, True))))
    return tuple(statements)


@dataclass(frozen=True)
class Naming:
    """How one rendering spells the block-local binders."""

    slot_prefix: str
    loop_name: str

    def slot(self, index: int) -> str:
        return f"{self.slot_prefix}{index}"


def render_expression(expression: Expr, naming: Naming) -> str:
    if isinstance(expression, Const):
        return str(expression.value)
    if isinstance(expression, Free):
        return expression.name
    if isinstance(expression, Slot):
        return naming.slot(expression.index)
    if isinstance(expression, LoopVar):
        return naming.loop_name
    if isinstance(expression, Binary):
        left = render_expression(expression.left, naming)
        right = render_expression(expression.right, naming)
        return f"({left} {expression.operator} {right})"
    rendered = ", ".join(render_expression(a, naming) for a in expression.arguments)
    return f"{expression.callee}({rendered})"


def render_statements(statements: Sequence[Stmt], naming: Naming, indent: str = "") -> str:
    lines: List[str] = []
    inner = indent + "    "
    for statement in statements:
        if isinstance(statement, Assign):
            value = render_expression(statement.value, naming)
            lines.append(f"{indent}{naming.slot(statement.slot)} = {value}")
        elif isinstance(statement, ExprStmt):
            lines.append(f"{indent}{render_expression(statement.value, naming)}")
        elif isinstance(statement, If):
            lines.append(f"{indent}if {render_expression(statement.condition, naming)}:")
            lines.append(render_statements(statement.body, naming, inner))
            if statement.orelse:
                lines.append(f"{indent}else:")
                lines.append(render_statements(statement.orelse, naming, inner))
        else:
            iterable = render_expression(statement.iterable, naming)
            lines.append(f"{indent}for {naming.loop_name} in {iterable}:")
            lines.append(render_statements(statement.body, naming, inner))
    return "\n".join(lines)


def top_level_slots(statements: Sequence[Stmt]) -> List[int]:
    return sorted({s.slot for s in statements if isinstance(s, Assign)})


# ---------------------------------------------------------------------------
# (a) Alpha-variance of the unifier


@BOUNDED
@given(blocks())
def test_unifier_treats_consistently_renamed_binders_as_alpha_equivalent(
    block: Tuple[Stmt, ...],
) -> None:
    first = Naming("v", "i")
    second = Naming("w", "j")
    source_first = render_statements(block, first)
    source_second = render_statements(block, second)
    parsed_first: List[ast.stmt] = list(ast.parse(source_first).body)
    parsed_second: List[ast.stmt] = list(ast.parse(source_second).body)

    renames: List[Dict[str, str]] = [{}, {}]
    unifier = Unifier(max_parameters=5, parameterize_constants=True)
    substitution = unifier.unify_blocks([parsed_first, parsed_second], renames)

    assert substitution is not None, f"failed to unify:\n{source_first}\n---\n{source_second}"
    assert substitution.param_expressions == {}, substitution.param_expressions
    assert substitution.mappings == {}
    assert substitution.function_params == {}
    # Every top-level binder is renamed to the same canonical name in both
    # blocks, so the two spellings are one variable of the helper.
    for slot in top_level_slots(block):
        assert renames[0][first.slot(slot)] == renames[1][second.slot(slot)], (slot, renames)
    event("slots=%d" % len(top_level_slots(block)))


# ---------------------------------------------------------------------------
# (b) Definite assignment against a path-enumerating reference


@dataclass(frozen=True)
class Bind:
    name: str


@dataclass(frozen=True)
class Leave:
    """``return`` or ``raise``: the path does not fall through."""

    keyword: str


@dataclass(frozen=True)
class Branch:
    body: Tuple["Flow", ...]
    orelse: Tuple["Flow", ...]


@dataclass(frozen=True)
class Loop:
    keyword: str
    body: Tuple["Flow", ...]


@dataclass(frozen=True)
class Try:
    body: Tuple["Flow", ...]
    handler: Tuple["Flow", ...]
    orelse: Tuple["Flow", ...]
    final: Tuple["Flow", ...]


@dataclass(frozen=True)
class With:
    """``with ctx() as name:``: transparent to control flow; ``name`` is bound on entry."""

    name: Optional[str]
    body: Tuple["Flow", ...]


Flow = Union[Bind, Leave, Branch, Loop, Try, With]
FLOW_NAMES = ("a", "b", "c")


def flows() -> st.SearchStrategy[Tuple[Flow, ...]]:
    # Three bindings per exit keeps most programs falling through with something bound.
    leaf: st.SearchStrategy[Flow] = st.sampled_from(
        FLOW_NAMES + FLOW_NAMES + ("return", "raise")
    ).map(lambda word: Leave(word) if word in ("return", "raise") else Bind(word))

    def compound(children: st.SearchStrategy[Flow]) -> st.SearchStrategy[Flow]:
        body = st.lists(children, max_size=3).map(tuple)
        return st.one_of(
            st.builds(Branch, body, body),
            st.builds(Loop, st.sampled_from(["for", "while"]), body),
            st.builds(Try, body, body, body, body),
            st.builds(With, st.sampled_from((None,) + FLOW_NAMES), body),
        )

    return st.lists(st.recursive(leaf, compound, max_leaves=6), max_size=4).map(tuple)


def render_flow(statements: Sequence[Flow], indent: str = "") -> str:
    if not statements:
        return f"{indent}pass"
    lines: List[str] = []
    inner = indent + "    "
    for statement in statements:
        if isinstance(statement, Bind):
            lines.append(f"{indent}{statement.name} = value()")
        elif isinstance(statement, Leave):
            lines.append(f"{indent}{statement.keyword}")
        elif isinstance(statement, Branch):
            lines.append(f"{indent}if cond():")
            lines.append(render_flow(statement.body, inner))
            lines.append(f"{indent}else:")
            lines.append(render_flow(statement.orelse, inner))
        elif isinstance(statement, Loop):
            header = "for i in items()" if statement.keyword == "for" else "while cond()"
            lines.append(f"{indent}{header}:")
            lines.append(render_flow(statement.body, inner))
        elif isinstance(statement, With):
            target = f" as {statement.name}" if statement.name else ""
            lines.append(f"{indent}with ctx(){target}:")
            lines.append(render_flow(statement.body, inner))
        else:
            lines.append(f"{indent}try:")
            lines.append(render_flow(statement.body, inner))
            lines.append(f"{indent}except ValueError:")
            lines.append(render_flow(statement.handler, inner))
            if statement.orelse:
                lines.append(f"{indent}else:")
                lines.append(render_flow(statement.orelse, inner))
            if statement.final:
                lines.append(f"{indent}finally:")
                lines.append(render_flow(statement.final, inner))
    return "\n".join(lines)


def fall_through_paths(statements: Sequence[Flow], entry: FrozenSet[str]) -> List[FrozenSet[str]]:
    """Every set of names bound on a path that falls out of ``statements``."""
    current = [entry]
    for statement in statements:
        current = [after for before in current for after in step(statement, before)]
    return current


def step(statement: Flow, bound: FrozenSet[str]) -> List[FrozenSet[str]]:
    if isinstance(statement, Bind):
        return [bound | {statement.name}]
    if isinstance(statement, Leave):
        return []
    if isinstance(statement, Branch):
        return fall_through_paths(statement.body, bound) + fall_through_paths(
            statement.orelse, bound
        )
    if isinstance(statement, Loop):
        # The documented conservative rule: a loop may run zero times, so
        # its body binds nothing definitely and never ends the path.
        return [bound]
    if isinstance(statement, With):
        # Transparent: every path runs the body once, after binding the target.
        entry = bound | {statement.name} if statement.name else bound
        return fall_through_paths(statement.body, entry)
    normal = [
        after
        for after_body in fall_through_paths(statement.body, bound)
        for after in fall_through_paths(statement.orelse, after_body)
    ]
    handled = fall_through_paths(statement.handler, bound)
    return [
        after
        for before_final in normal + handled
        for after in fall_through_paths(statement.final, before_final)
    ]


def reference_definitely_bound_after(statements: Sequence[Flow]) -> Optional[FrozenSet[str]]:
    paths = fall_through_paths(statements, frozenset())
    if not paths:
        return None
    return frozenset.intersection(*paths)


@BOUNDED
@given(flows())
def test_definitely_bound_after_matches_path_enumeration(statements: Tuple[Flow, ...]) -> None:
    source = render_flow(statements)
    parsed = ast.parse(source).body
    expected = reference_definitely_bound_after(statements)
    actual = definitely_bound_after(parsed)
    if expected is None:
        assert actual is None, source
        event("no fall-through")
    else:
        assert actual == set(expected), source
        event("bound=%d" % len(expected))


# ---------------------------------------------------------------------------
# (c) Instantiation round trip through the engine
#
# A block is generated once, then one constant or free-name leaf (counted in
# rendering order) is replaced by ``alpha`` at the first site and ``beta`` at
# the second, so the two sites differ in exactly that leaf.

HOLE_LEAVES = (Free("alpha"), Free("beta"))


def count_leaves(expression: Expr) -> int:
    if isinstance(expression, (Const, Free)):
        return 1
    if isinstance(expression, Binary):
        return count_leaves(expression.left) + count_leaves(expression.right)
    if isinstance(expression, Call):
        return sum(count_leaves(a) for a in expression.arguments)
    return 0


def count_block_leaves(statements: Sequence[Stmt]) -> int:
    return sum(count_leaves(e) for e in block_expressions(statements))


def block_expressions(statements: Sequence[Stmt]) -> List[Expr]:
    found: List[Expr] = []
    for statement in statements:
        if isinstance(statement, (Assign, ExprStmt)):
            found.append(statement.value)
        elif isinstance(statement, If):
            found.append(statement.condition)
            found.extend(block_expressions(statement.body))
            found.extend(block_expressions(statement.orelse))
        else:
            found.append(statement.iterable)
            found.extend(block_expressions(statement.body))
    return found


def contains_call(statements: Sequence[Stmt]) -> bool:
    def has_call(expression: Expr) -> bool:
        if isinstance(expression, Call):
            return True
        if isinstance(expression, Binary):
            return has_call(expression.left) or has_call(expression.right)
        return False

    return any(has_call(e) for e in block_expressions(statements))


def replace_leaf(expression: Expr, index: int, leaf: Expr) -> Expr:
    """Replace the ``index``-th constant or free-name leaf; others are untouched."""
    if isinstance(expression, (Const, Free)):
        return leaf if index == 0 else expression
    if isinstance(expression, Binary):
        left_leaves = count_leaves(expression.left)
        return replace(
            expression,
            left=replace_leaf(expression.left, index, leaf),
            right=replace_leaf(expression.right, index - left_leaves, leaf),
        )
    if isinstance(expression, Call):
        arguments: List[Expr] = []
        offset = 0
        for argument in expression.arguments:
            arguments.append(replace_leaf(argument, index - offset, leaf))
            offset += count_leaves(argument)
        return replace(expression, arguments=tuple(arguments))
    return expression


def replace_block_leaf(statements: Sequence[Stmt], index: int, leaf: Expr) -> Tuple[Stmt, ...]:
    """Replace the ``index``-th leaf of the block, counted in rendering order."""
    updated: List[Stmt] = []
    offset = 0
    for statement in statements:
        if isinstance(statement, (Assign, ExprStmt)):
            updated.append(
                replace(statement, value=replace_leaf(statement.value, index - offset, leaf))
            )
        elif isinstance(statement, If):
            condition = replace_leaf(statement.condition, index - offset, leaf)
            offset_body = offset + count_leaves(statement.condition)
            body = replace_block_leaf(statement.body, index - offset_body, leaf)
            offset_orelse = offset_body + count_block_leaves(statement.body)
            orelse = replace_block_leaf(statement.orelse, index - offset_orelse, leaf)
            updated.append(If(condition, body, orelse))
        else:
            iterable = replace_leaf(statement.iterable, index - offset, leaf)
            offset_body = offset + count_leaves(statement.iterable)
            updated.append(
                For(iterable, replace_block_leaf(statement.body, index - offset_body, leaf))
            )
        offset += count_block_leaves([statement])
    return tuple(updated)


@st.composite
def leaf_variant_pairs(draw: st.DrawFn) -> Tuple[Tuple[Stmt, ...], Tuple[Stmt, ...]]:
    """Two blocks identical except for one constant or free-name leaf.

    A block of nothing but literal assignments is a trivial helper the engine
    declines, so a block without a call gets one appended.
    """
    block = draw(blocks(min_size=3, max_size=5))
    if not contains_call(block):
        block = block + (ExprStmt(Call("f", (Free("src"),))),)
    index = draw(st.integers(0, count_block_leaves(block) - 1))
    return (
        replace_block_leaf(block, index, HOLE_LEAVES[0]),
        replace_block_leaf(block, index, HOLE_LEAVES[1]),
    )


PARAMETERS = "items, src, limit, alpha, beta"


def render_module(first: Sequence[Stmt], second: Sequence[Stmt]) -> str:
    naming = Naming("v", "i")
    return (
        f"def site_one({PARAMETERS}):\n{render_statements(first, naming, '    ')}\n\n\n"
        f"def site_two({PARAMETERS}):\n{render_statements(second, naming, '    ')}\n"
    )


def statements_in_range(module: ast.Module, start: int, end: int) -> List[ast.stmt]:
    """The outermost statements whose lines lie within ``start``..``end``."""
    inside: List[ast.stmt] = []

    def visit(statements: Sequence[ast.stmt]) -> None:
        for statement in statements:
            if start <= statement.lineno and (statement.end_lineno or statement.lineno) <= end:
                inside.append(statement)
                continue
            for field in ("body", "orelse"):
                children = getattr(statement, field, None)
                if isinstance(children, list):
                    visit(children)

    visit(module.body)
    return inside


@ENGINE_BOUNDED
@given(leaf_variant_pairs())
def test_engine_proposals_pass_the_instantiation_check(
    pair: Tuple[Tuple[Stmt, ...], Tuple[Stmt, ...]],
) -> None:
    first, second = pair
    source = render_module(first, second)
    module = ast.parse(source)
    with tempfile.TemporaryDirectory(prefix="towel-property-") as directory:
        path = Path(directory) / "sites.py"
        path.write_text(source)
        engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=2, parameterize_constants=True
        )
        proposals = engine.analyze_files([str(path)], progress="none")

    checked = 0
    for proposal in proposals:
        if proposal.reused_function is not None:
            continue
        for replacement in proposal.replacements:
            start, end = replacement.line_range
            block = statements_in_range(module, start, end)
            assert block, (start, end, source)
            assert isinstance(replacement.node, ast.stmt)
            mismatch = instantiation_mismatch(
                proposal.extracted_function,
                replacement.node,
                block,
                {},
                {},
                preamble_length=0,
                returns_variables=bool(proposal.return_variables),
            )
            assert mismatch is None, (
                f"{mismatch}\nhelper:\n{ast.unparse(proposal.extracted_function)}\n"
                f"call: {ast.unparse(replacement.node)}\nsource:\n{source}"
            )
            checked += 1
    event("proposals=%d" % len(proposals))
    assert checked > 0, f"the engine proposed nothing for:\n{source}"


# ---------------------------------------------------------------------------
# (d) Idempotence: the output of a directory run is a fixed point


def _engine() -> UnificationRefactorEngine:
    return UnificationRefactorEngine(max_parameters=5, min_lines=2, parameterize_constants=True)


def _quiet_directory_run(engine: UnificationRefactorEngine, root: Path) -> Tuple[int, str]:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, reason = engine.refactor_directory_to_fixed_point(
            str(root), str(root), progress="none"
        )
    return sum(count for count, _ in results.values()), reason


@ENGINE_BOUNDED
@given(leaf_variant_pairs())
def test_a_directory_runs_output_is_a_fixed_point(
    pair: Tuple[Tuple[Stmt, ...], Tuple[Stmt, ...]],
) -> None:
    source = render_module(*pair)
    with tempfile.TemporaryDirectory(prefix="towel-property-") as directory:
        root = Path(directory) / "project"
        root.mkdir()
        module = root / "sites.py"
        module.write_text(source)
        applied, reason = _quiet_directory_run(_engine(), root)
        assert reason == "fixed_point"
        assert applied >= 1, f"the engine applied nothing to:\n{source}"
        output = module.read_bytes()
        compile(output, str(module), "exec")
        applied_again, reason_again = _quiet_directory_run(_engine(), root)
        assert (applied_again, reason_again) == (0, "fixed_point"), module.read_text()
        assert module.read_bytes() == output
    event("applied=%d" % applied)


# ---------------------------------------------------------------------------
# (e) Byte preservation: newline convention, BOM and coding cookie survive


def _encode_with_convention(source: str, convention: str) -> bytes:
    if convention == "crlf":
        return source.encode("utf-8").replace(b"\n", b"\r\n")
    if convention == "bom":
        return b"\xef\xbb\xbf" + source.encode("utf-8")
    assert convention == "latin-1"
    return ("# -*- coding: latin-1 -*-\n# caf\u00e9\n" + source).encode("latin-1")


@ENGINE_BOUNDED
@given(leaf_variant_pairs(), st.sampled_from(["crlf", "bom", "latin-1"]))
def test_refactoring_preserves_the_files_byte_convention(
    pair: Tuple[Tuple[Stmt, ...], Tuple[Stmt, ...]], convention: str
) -> None:
    original = _encode_with_convention(render_module(*pair), convention)
    with tempfile.TemporaryDirectory(prefix="towel-property-") as directory:
        path = Path(directory) / "sites.py"
        path.write_bytes(original)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            final, applied, _ = _engine().refactor_to_fixed_point(str(path), progress="none")
        assert applied >= 1, f"the engine applied nothing to:\n{decode_source(original)}"
        data = path.read_bytes()
    assert source_encoding(data) == source_encoding(original)
    assert dominant_newline(data) == dominant_newline(original)
    assert data.startswith(b"\xef\xbb\xbf") == original.startswith(b"\xef\xbb\xbf")
    if convention == "crlf":
        assert b"\n" not in data.replace(b"\r\n", b""), "no bare LF was introduced"
    if convention == "latin-1":
        assert b"# caf\xe9\n" in data, "the latin-1 byte and the cookie line survive"
    assert decode_source(data) == final
    compile(final, str(path), "exec")
    event(convention)


# ---------------------------------------------------------------------------
# (f) Observational equivalence of both call sites after refactoring
#
# The generated programs call ``f``, ``g``, ``h`` and ``print``. The prelude
# defines the first three over an int subclass that is also iterable, so
# every generated expression, loop and condition evaluates on both sides;
# ``print`` stays the builtin, so its output is part of what is compared.

PRELUDE = """\
class N(int):
    def __iter__(self):
        return iter(range(self % 4))


def f(*args):
    return N(sum(int(a) for a in args) + 1)


def g(*args):
    return N(sum(int(a) for a in args) * 2)


def h(*args):
    return N(len(args) + sum(int(a) for a in args))


"""

EQUIVALENCE_CASES: List[Tuple[Tuple[object, ...], Dict[str, object]]] = [
    ((), {"items": [1, 2, 3], "src": 2, "limit": 3, "alpha": 1, "beta": 4}),
    ((), {"items": [], "src": 0, "limit": 1, "alpha": 0, "beta": 0}),
    ((), {"items": [5], "src": 5, "limit": 2, "alpha": 3, "beta": 7}),
]


@ENGINE_BOUNDED
@given(leaf_variant_pairs())
def test_both_call_sites_behave_as_the_originals_after_refactoring(
    pair: Tuple[Tuple[Stmt, ...], Tuple[Stmt, ...]],
) -> None:
    source = PRELUDE + render_module(*pair)
    with tempfile.TemporaryDirectory(prefix="towel-property-") as directory:
        path = Path(directory) / "sites.py"
        path.write_text(source)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            final, applied, descriptions = _engine().refactor_to_fixed_point(
                str(path), progress="none"
            )
    assert applied >= 1, f"the engine applied nothing to:\n{source}"
    compile(final, str(path), "exec")
    for site in ("site_one", "site_two"):
        passed, differences = compare_function_behavior(source, final, site, EQUIVALENCE_CASES)
        assert passed, "\n".join([*descriptions, *differences, final])
    event("applied=%d" % applied)

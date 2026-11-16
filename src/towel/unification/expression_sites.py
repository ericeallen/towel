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

"""Expressions as candidates: one expression repeated inside statements that otherwise differ.

A candidate block is a run of whole statements, so a condition written once
in an ``if`` and once in a comprehension's ``if`` clause was never seen. An
expression candidate is a complete expression of a function body, at a place
where it is evaluated for its value, spanning at least ``--min-lines``
lines. Its helper is ``def helper(free variables): return E`` and each site
becomes ``helper(arguments)``, standing exactly where the expression stood,
so nothing outside the expression moves relative to anything else.

The engine sees such a candidate as the one-statement block ``return E``
(``standing_block``): a synthetic ``return`` whose value is the function's
own expression node, registered here with what the engine must know about
where the expression stands (``ExpressionSite``). The unifier, the argument
rules, the instantiation check and placement then run as they do for a
block of statements; the stages that ask where a block stands in its
function ask ``expression_site`` first.

Not candidates (docs/design/DECISIONS.md, "Expressions are candidates"):

- an expression holding ``yield``, ``await`` or an asynchronous
  comprehension, whose helper would be a generator or a coroutine;
- one holding an assignment expression, which binds a name of the site's
  function;
- one that loads ``super`` or ``__class__``, which need the method's class
  cell;
- a single call, whose helper would only forward (its arguments are still
  candidates);
- one made of constants alone, and one under seven syntax units, which the
  cost rule could never accept at two sites;
- an expression that is not a value where it stands, or is evaluated at
  another time: a call's callee, an assignment target, an annotation, part
  of an f-string, ``*x``, a slice, a pattern, a ``type`` statement, and
  anything inside a ``lambda`` or a nested definition (its body is that
  function's).
"""

from __future__ import annotations

import ast
import functools
import io
import keyword
import tokenize
from dataclasses import dataclass, replace
from typing import Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple
from weakref import WeakKeyDictionary

from .assignment_analyzer import stored_names
from .block_comments import DEFAULT_EXCLUSION, NodePath, SiteComments, site_comments
from .block_signature import BlockSignature, extract_block_signature
from .models import ExpressionPlace, FunctionNode
from .splicing import BlockColumns
from ..source_text import source_lines

MINIMUM_SYNTAX_UNITS = 7
"""Fewest syntax units an expression candidate holds.

The cost rule (``profitability.worthwhile_helper``) accepts a helper only
when the definition and its calls together are smaller than the code they
replace. With no argument at all a helper of ``return E`` and two calls
cost ``E + 6`` units against ``2 E + 2``, so nothing under seven units can
repay one.
"""

_NESTED_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_NEVER_CANDIDATES = (ast.Starred, ast.Slice, ast.Constant, ast.Name)
_TEXT_WITH_FIELDS = tuple(
    kind for kind in (ast.JoinedStr, getattr(ast, "TemplateStr", None)) if isinstance(kind, type)
)
_TYPE_ALIAS: Optional[type] = getattr(ast, "TypeAlias", None)


@dataclass(frozen=True)
class ExpressionSite:
    """Where an expression candidate stands in its function.

    ``anchor`` is the statement whose own code holds the expression, or the
    ``except`` clause or ``case`` that does: what is bound on every path to
    the anchor is bound where the expression is evaluated. A comprehension
    around the expression binds more: ``comprehension_names`` are the
    targets of the clauses that have run by then, bound there on every
    path, and ``unbound_names`` the targets of the clauses that have not:
    locals of the comprehension that a read there finds unassigned.
    ``tests_truth`` says the place uses only the truth of the value,
    and the compiler therefore turns ``and``, ``or``, ``not``, a conditional
    expression and a chained comparison written there into jumps that test
    each operand once (``truth_decides``). ``exact_truth`` says the block
    registered for this site is ``return True if E else False``, which
    compiles ``E`` to those same jumps. ``shares_parentheses`` marks a
    generator expression whose parentheses are its call's.
    """

    expression: ast.expr
    anchor: ast.AST
    comprehension_names: FrozenSet[str]
    tests_truth: bool
    exact_truth: bool = False
    shares_parentheses: bool = False
    unbound_names: FrozenSet[str] = frozenset()
    statement_line: int = 0
    comprehension_iterables: Tuple[Tuple[str, str], ...] = ()

    @property
    def first_line(self) -> int:
        return self.expression.lineno

    @property
    def place(self) -> ExpressionPlace:
        expression = self.expression
        return ExpressionPlace(
            (expression.lineno, expression.col_offset),
            (expression.end_lineno or expression.lineno, expression.end_col_offset or 0),
            self.exact_truth,
            self.statement_line,
            self.comprehension_iterables,
        )

    @property
    def last_line(self) -> int:
        return self.expression.end_lineno or self.expression.lineno


_SITES: "WeakKeyDictionary[ast.AST, ExpressionSite]" = WeakKeyDictionary()
"""The site of every standing block, for as long as the block lives.

Per process, and filled before evaluation forks, so each worker has the
sites of the pairs it judges.
"""


def expression_site(block: Sequence[ast.AST]) -> Optional[ExpressionSite]:
    """The site ``block`` stands for, when it is an expression candidate's block."""
    if len(block) != 1:
        return None
    return _SITES.get(block[0])


def is_expression_block(block: Sequence[ast.AST]) -> bool:
    """Whether ``block`` is an expression candidate's ``return E``, not statements of a function."""
    return expression_site(block) is not None


def truth_decides(expression: ast.expr) -> bool:
    """Whether a truth test of ``expression`` is compiled to jumps over its operands.

    ``if a and b:`` tests ``a`` once and, when it is true, ``b`` once.
    ``return a and b`` hands back one of the operands, and whoever tests
    that value tests it a second time. The compiler does the same for
    ``or``, ``not``, a conditional expression and a chained comparison; any
    other expression is evaluated to a value that is then tested once,
    wherever it stands.
    """
    if isinstance(expression, (ast.BoolOp, ast.IfExp)):
        return True
    if isinstance(expression, ast.UnaryOp):
        return isinstance(expression.op, ast.Not)
    return isinstance(expression, ast.Compare) and len(expression.ops) > 1


def standing_block(site: ExpressionSite) -> ast.Return:
    """The one-statement block that stands for ``site``: ``return E``, at the expression's position.

    The value is the function's own node, so every analysis that asks which
    of the function's nodes a block holds finds the expression's. With
    ``exact_truth`` the block is ``return True if E else False``.
    """
    expression = site.expression
    value: ast.expr = expression
    if site.exact_truth:
        value = ast.copy_location(
            ast.IfExp(
                test=expression,
                body=ast.copy_location(ast.Constant(value=True), expression),
                orelse=ast.copy_location(ast.Constant(value=False), expression),
            ),
            expression,
        )
    block = ast.copy_location(ast.Return(value=value), expression)
    _SITES[block] = site
    return block


def syntax_units(node: ast.AST) -> int:
    """The size of ``node`` in the cost rule's units (``profitability``)."""
    return sum(
        isinstance(inner, (ast.stmt, ast.expr, ast.arg, ast.keyword, ast.comprehension))
        for inner in ast.walk(node)
    )


def exclusion(expression: ast.expr) -> Optional[str]:
    """Why ``expression`` cannot be a candidate whatever its place, or None.

    The words are for the census of a debugging run, not for the user: an
    excluded expression is never paired, so nothing is reported about it.
    """
    constant = True
    for inner in ast.walk(expression):
        if isinstance(inner, (ast.Yield, ast.YieldFrom)):
            return "yield"
        if isinstance(inner, ast.Await) or (
            isinstance(inner, ast.comprehension) and inner.is_async
        ):
            return "await"
        if isinstance(inner, ast.NamedExpr):
            return "assignment expression"
        if isinstance(inner, ast.Name) and inner.id in {"super", "__class__"}:
            return "class cell"
        if isinstance(inner, (ast.Name, ast.Call, ast.Attribute, ast.Subscript)):
            constant = False
    if constant:
        return "constants only"
    if isinstance(expression, ast.Call):
        return "single call"
    if syntax_units(expression) < MINIMUM_SYNTAX_UNITS:
        return "too small"
    return None


ExpressionKey = Tuple[object, ...]


def expression_key(expression: ast.expr) -> ExpressionKey:
    """What two expressions must share at the root for the unifier to walk them in lockstep.

    The root's kind, and what of its layout no parameter can absorb: the
    operator, the number of operands, the argument and keyword layout, the
    clauses of a comprehension. A differing child is exactly what becomes a
    parameter, so nothing below the root is part of the key: a key over the
    children's kinds lost 81 of the 158 pairs that unify in rich.
    """
    kind = type(expression).__name__
    if isinstance(expression, ast.BoolOp):
        return (kind, type(expression.op).__name__, len(expression.values))
    if isinstance(expression, (ast.BinOp, ast.UnaryOp)):
        return (kind, type(expression.op).__name__)
    if isinstance(expression, ast.Compare):
        return (kind, tuple(type(op).__name__ for op in expression.ops))
    if isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
        return (kind, len(expression.elts))
    if isinstance(expression, ast.Dict):
        return (kind, len(expression.keys), tuple(key is None for key in expression.keys))
    if isinstance(expression, _COMPREHENSIONS):
        return (kind, tuple((len(g.ifs), bool(g.is_async)) for g in expression.generators))
    if isinstance(expression, ast.Lambda):
        arguments = expression.args
        return (
            kind,
            len(arguments.posonlyargs),
            len(arguments.args),
            len(arguments.kwonlyargs),
            arguments.vararg is not None,
            arguments.kwarg is not None,
        )
    return (kind,)


EXPRESSION_STATEMENT_KIND = "expression"
"""What stands for the statement kind in an expression candidate's signature.

No statement has this kind, so an expression candidate shares a bucket only
with expression candidates of the same ``expression_key``.
"""


def expression_signature(block: ast.Return) -> BlockSignature:
    """The signature expression candidates are bucketed and filtered by.

    The name and call counts of ``return E``, which pairing holds to the
    tolerances it holds statements to, and the expression's key in place of
    the statement kinds.
    """
    site = _SITES[block]
    return replace(
        extract_block_signature([block]),
        stmt_seq=(EXPRESSION_STATEMENT_KIND, repr(expression_key(site.expression))),
    )


def in_exact_truth_form(block: ast.Return) -> ast.Return:
    """The block of the same site that returns ``True if E else False``."""
    return standing_block(replace(_SITES[block], exact_truth=True))


@dataclass(frozen=True)
class _Place:
    """What the walk knows on reaching an expression."""

    anchor: ast.AST
    statement_line: int
    comprehension_names: FrozenSet[str] = frozenset()
    tests_truth: bool = False
    shares_parentheses: bool = False
    unbound_names: FrozenSet[str] = frozenset()
    comprehension_iterables: Tuple[Tuple[str, str], ...] = ()

    def within(self, *, tests_truth: bool = False, shares_parentheses: bool = False) -> "_Place":
        return replace(self, tests_truth=tests_truth, shares_parentheses=shares_parentheses)

    def entering(self, targets: FrozenSet[str]) -> "_Place":
        """Inside a comprehension whose clauses bind ``targets``, before any has run."""
        return replace(
            self,
            comprehension_names=self.comprehension_names - targets,
            unbound_names=self.unbound_names | targets,
            comprehension_iterables=tuple(
                (name, iterable)
                for name, iterable in self.comprehension_iterables
                if name not in targets
            ),
            tests_truth=False,
            shares_parentheses=False,
        )

    def binding(self, clause: ast.comprehension) -> "_Place":
        """After ``clause`` has bound its target."""
        names = frozenset(stored_names(clause.target))
        ranged = (
            ((clause.target.id, ast.unparse(clause.iter)),)
            if isinstance(clause.target, ast.Name) and not clause.is_async
            else ()
        )
        return replace(
            self,
            comprehension_names=self.comprehension_names | names,
            unbound_names=self.unbound_names - names,
            comprehension_iterables=self.comprehension_iterables + ranged,
            tests_truth=False,
            shares_parentheses=False,
        )


def expression_sites(function: FunctionNode, min_lines: int) -> List[ExpressionSite]:
    """Every expression candidate of ``function``, in source order.

    Nested definitions are functions of their own and are not entered.
    """
    found: List[ExpressionSite] = []
    for statement in function.body:
        found.extend(_in_statement(statement, min_lines))
    found.sort(
        key=lambda site: (
            site.expression.lineno,
            site.expression.col_offset,
            -(site.expression.end_lineno or 0),
            -(site.expression.end_col_offset or 0),
        )
    )
    return found


_TRUTH_TESTING_STATEMENTS = (ast.If, ast.While, ast.Assert)


def _in_statement(
    statement: ast.AST, min_lines: int, chain_line: Optional[int] = None
) -> Iterator[ExpressionSite]:
    """The candidates in ``statement``'s own code and in the statements it holds.

    ``chain_line`` is the first line of the ``if`` this statement is an
    ``elif`` of: no statement can be written between the two.
    """
    if isinstance(statement, _NESTED_DEFINITIONS):
        return
    if _TYPE_ALIAS is not None and isinstance(statement, _TYPE_ALIAS):
        return  # its value is evaluated when ``__value__`` is first read
    line = chain_line if chain_line is not None else getattr(statement, "lineno")
    place = _Place(statement, line)
    for field, value in ast.iter_fields(statement):
        if field == "annotation" or field == "type_params":
            continue
        for child in value if isinstance(value, list) else [value]:
            if isinstance(child, ast.stmt):
                is_elif = (
                    field == "orelse"
                    and isinstance(statement, ast.If)
                    and isinstance(child, ast.If)
                    and len(statement.orelse) == 1
                    and child.col_offset == statement.col_offset
                )
                yield from _in_statement(child, min_lines, line if is_elif else None)
            elif isinstance(child, ast.ExceptHandler):
                if child.type is not None:
                    yield from _in_expression(child.type, _Place(child, line), min_lines)
                for inner in child.body:
                    yield from _in_statement(inner, min_lines)
            elif isinstance(child, ast.match_case):
                # A pattern holds no expression evaluated for its value.
                if child.guard is not None:
                    yield from _in_expression(
                        child.guard, _Place(child, line, tests_truth=True), min_lines
                    )
                for inner in child.body:
                    yield from _in_statement(inner, min_lines)
            elif isinstance(child, ast.withitem):
                yield from _in_expression(child.context_expr, place, min_lines)
                if child.optional_vars is not None:
                    yield from _in_expression(child.optional_vars, place, min_lines)
            elif isinstance(child, ast.expr):
                tests_truth = field == "test" and isinstance(statement, _TRUTH_TESTING_STATEMENTS)
                yield from _in_expression(child, place.within(tests_truth=tests_truth), min_lines)


def _in_expression(
    expression: ast.expr, place: _Place, min_lines: int, *, is_value: bool = True
) -> Iterator[ExpressionSite]:
    """``expression`` if it is a candidate where it stands, and the candidates inside it."""
    if isinstance(expression, ast.Lambda) or isinstance(expression, _TEXT_WITH_FIELDS):
        # A lambda's body runs when it is called; an f-string's fields are
        # part of its text. The whole of either may still be a candidate.
        if is_value and _is_candidate(expression, min_lines):
            yield _site(expression, place)
        return
    if is_value and _is_candidate(expression, min_lines):
        yield _site(expression, place)
    plain = place.within()
    if isinstance(expression, ast.BoolOp):
        # Each operand of a truth-tested ``and`` or ``or`` is itself only tested.
        for operand in expression.values:
            yield from _in_expression(
                operand, place.within(tests_truth=place.tests_truth), min_lines
            )
    elif isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
        yield from _in_expression(
            expression.operand, place.within(tests_truth=place.tests_truth), min_lines
        )
    elif isinstance(expression, ast.IfExp):
        yield from _in_expression(expression.test, place.within(tests_truth=True), min_lines)
        for branch in (expression.body, expression.orelse):
            yield from _in_expression(
                branch, place.within(tests_truth=place.tests_truth), min_lines
            )
    elif isinstance(expression, _COMPREHENSIONS):
        yield from _in_comprehension(expression, plain, min_lines)
    elif isinstance(expression, ast.Call):
        # The callee is looked up, not evaluated for a value a helper could
        # return in its place without changing how a method is bound.
        yield from _in_expression(expression.func, plain, min_lines, is_value=False)
        sole = len(expression.args) == 1 and not expression.keywords
        for argument in expression.args:
            shared = (
                sole
                and isinstance(argument, ast.GeneratorExp)
                and (argument.end_lineno, argument.end_col_offset)
                == (expression.end_lineno, expression.end_col_offset)
            )
            yield from _in_expression(argument, place.within(shares_parentheses=shared), min_lines)
        for named in expression.keywords:
            yield from _in_expression(named.value, plain, min_lines)
    else:
        for child in ast.iter_child_nodes(expression):
            if isinstance(child, ast.expr):
                yield from _in_expression(child, plain, min_lines)


def _in_comprehension(
    comprehension: ast.expr, place: _Place, min_lines: int
) -> Iterator[ExpressionSite]:
    """The candidates in a comprehension's clauses and element, with the targets bound at each.

    The first iterable is evaluated where the comprehension stands. Each
    clause binds its target before its conditions run, and every later
    clause and the element run with all earlier targets bound. Every target
    is a local of the comprehension from its first clause on, so a later
    clause's target is unassigned there until that clause runs, whatever the
    function has bound under the same name.
    """
    generators: List[ast.comprehension] = getattr(comprehension, "generators")
    targets = [frozenset(stored_names(generator.target)) for generator in generators]
    current = place
    for index, generator in enumerate(generators):
        yield from _in_expression(generator.iter, current.within(), min_lines)
        if index == 0:
            current = current.entering(frozenset().union(*targets))
        # A target is stored, not evaluated; what it subscripts is.
        yield from _in_expression(generator.target, current.within(), min_lines, is_value=False)
        current = current.binding(generator)
        for condition in generator.ifs:
            yield from _in_expression(condition, current.within(tests_truth=True), min_lines)
    elements = (
        [getattr(comprehension, "key"), getattr(comprehension, "value")]
        if isinstance(comprehension, ast.DictComp)
        else [getattr(comprehension, "elt")]
    )
    for element in elements:
        if isinstance(element, ast.expr):
            yield from _in_expression(element, current.within(), min_lines)


def _is_candidate(expression: ast.expr, min_lines: int) -> bool:
    if isinstance(expression, _NEVER_CANDIDATES):
        return False
    if not isinstance(getattr(expression, "ctx", None) or ast.Load(), ast.Load):
        return False  # an assignment or deletion target
    if isinstance(expression, ast.Tuple) and any(
        isinstance(element, ast.Slice) for element in expression.elts
    ):
        return False  # ``a[1:2, i]``: a slice is written only inside a subscript
    lines = (expression.end_lineno or expression.lineno) - expression.lineno + 1
    return lines >= min_lines and exclusion(expression) is None


def _site(expression: ast.expr, place: _Place) -> ExpressionSite:
    return ExpressionSite(
        expression,
        place.anchor,
        place.comprehension_names,
        place.tests_truth,
        shares_parentheses=place.shares_parentheses,
        unbound_names=place.unbound_names,
        statement_line=place.statement_line,
        comprehension_iterables=place.comprehension_iterables,
    )


def census(function: FunctionNode, min_lines: int) -> Dict[str, int]:
    """How many expressions of ``function`` span ``min_lines`` lines, by what became of each.

    ``candidate`` for those enumerated, otherwise the ``exclusion``. For
    measurement and tests; the engine enumerates with ``expression_sites``.
    """
    counts: Dict[str, int] = {}
    candidates = {id(site.expression) for site in expression_sites(function, min_lines)}
    pending: List[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, _NESTED_DEFINITIONS):
            continue
        if isinstance(node, ast.expr) and not isinstance(node, _NEVER_CANDIDATES):
            lines = (node.end_lineno or node.lineno) - node.lineno + 1
            if lines >= min_lines:
                if id(node) in candidates:
                    reason = "candidate"
                else:
                    reason = exclusion(node) or "not a value where it stands"
                counts[reason] = counts.get(reason, 0) + 1
        pending.extend(ast.iter_child_nodes(node))
    return counts


# -- What the guards ask of a candidate -----------------------------------------


def comprehension_locals(block: Sequence[ast.AST]) -> FrozenSet[str]:
    """The names a comprehension around an expression candidate binds, assigned yet or not.

    Where the candidate stands these are the comprehension's variables,
    whatever its function or module binds under the same names. Empty for a
    block of statements, and for an expression outside any comprehension.
    """
    site = expression_site(block)
    return frozenset() if site is None else site.comprehension_names | site.unbound_names


# -- The call in the expression's place -----------------------------------------


def replacement_span(source: str, site: ExpressionSite) -> Tuple[Tuple[int, int], BlockColumns]:
    """The lines and columns of ``source`` that the call replacing ``site`` takes the place of.

    The expression's own text, and with it the parentheses that only group
    it, when nothing but line breaks stands between them and it: a call
    needs none, and ``if (`` a call ``):`` over three lines is not what
    anyone would write. Parentheses that hold a comment beside the
    expression stay, so the comment does. A generator expression written as
    a call's only argument uses the call's parentheses as its own, and
    those stay with the call.
    """
    expression = site.expression
    start = (expression.lineno, expression.col_offset)
    end = (expression.end_lineno or expression.lineno, expression.end_col_offset or 0)
    if site.shares_parentheses:
        start, end = (start[0], start[1] + 1), (end[0], end[1] - 1)
    else:
        start, end = _with_grouping_parentheses(source, start, end)
    return (start[0], end[0]), BlockColumns(start[1], end[1])


_Position = Tuple[int, int]
_LAYOUT = frozenset({tokenize.NL, tokenize.COMMENT})
_ENDS_A_VALUE = frozenset({")", "]", "}", "..."})
_VALUE_KEYWORDS = frozenset({"None", "True", "False"})


@functools.lru_cache(maxsize=8)
def _significant_tokens(source: str) -> Optional[Tuple[tokenize.TokenInfo, ...]]:
    """The tokens of ``source`` with byte columns, or None when it cannot be tokenized."""
    lines = source_lines(source)
    try:
        tokens = tuple(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return None

    def in_bytes(position: _Position) -> _Position:
        line, column = position
        if not 1 <= line <= len(lines) or lines[line - 1].isascii():
            return position
        return line, len(lines[line - 1][:column].encode("utf-8"))

    return tuple(
        token._replace(start=in_bytes(token.start), end=in_bytes(token.end)) for token in tokens
    )


def _with_grouping_parentheses(
    source: str, start: _Position, end: _Position
) -> Tuple[_Position, _Position]:
    """``start`` and ``end`` widened over parentheses that do nothing but group the expression.

    The token before the expression must be ``(`` and the one after it
    ``)``, with only line breaks between: a comment there would be lost
    with the parentheses. The ``(`` must open a group, not a call's or a
    definition's argument list: what precedes it is an operator, a keyword
    or the start of a statement, never a name, a literal or a closing
    bracket. Positions count bytes, as the syntax tree's do.
    """
    tokens = _significant_tokens(source)
    if tokens is None:
        return start, end
    first = next((index for index, token in enumerate(tokens) if token.start == start), None)
    if first is None or first == 0:
        return start, end
    opening = first - 1
    while opening > 0 and tokens[opening].type == tokenize.NL:
        opening -= 1
    if tokens[opening].type != tokenize.OP or tokens[opening].string != "(":
        return start, end
    closing = next((index for index, token in enumerate(tokens) if token.start >= end), None)
    while closing is not None and closing < len(tokens) and tokens[closing].type == tokenize.NL:
        closing += 1
    if (
        closing is None
        or closing >= len(tokens)
        or tokens[closing].type != tokenize.OP
        or tokens[closing].string != ")"
    ):
        return start, end
    before = opening - 1
    while before >= 0 and tokens[before].type in _LAYOUT:
        before -= 1
    if before >= 0 and not _opens_a_group_after(tokens[before]):
        return start, end
    return tokens[opening].start, tokens[closing].end


def _opens_a_group_after(token: tokenize.TokenInfo) -> bool:
    """Whether a ``(`` written after ``token`` groups an expression, and is not a call."""
    if token.type == tokenize.NAME:
        return keyword.iskeyword(token.string) and token.string not in _VALUE_KEYWORDS
    if token.type == tokenize.OP:
        return token.string not in _ENDS_A_VALUE
    return token.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING)


def as_expression_call(call: ast.stmt) -> ast.stmt:
    """The generated ``return helper(...)`` as the call alone, which is what an expression site gets.

    Kept as an expression statement so that every consumer of a call site
    finds a statement holding the call; ``Replacement.expression`` says its
    value is used where it stands.
    """
    if not isinstance(call, ast.Return) or call.value is None:
        raise ValueError("an expression candidate's helper returns its value")
    return ast.copy_location(ast.Expr(value=call.value), call)


def is_whole_body(place: ExpressionPlace, body: Sequence[ast.stmt]) -> bool:
    """Whether a function of this ``body`` does nothing but return the expression at ``place``.

    ``return E`` for a helper that returns the value, and ``return True if E
    else False`` for one in the exact truth form: the shapes this module
    gives its helpers, so that a later pass finds the helper it made.
    """
    if len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
        return False
    value = body[0].value
    if place.exact_truth:
        if not (
            isinstance(value, ast.IfExp)
            and isinstance(value.body, ast.Constant)
            and value.body.value is True
            and isinstance(value.orelse, ast.Constant)
            and value.orelse.value is False
        ):
            return False
        value = value.test
    return (value.lineno, value.col_offset) == place.start and (
        value.end_lineno,
        value.end_col_offset,
    ) == place.end


def comments_at(
    source: str,
    block: Sequence[ast.stmt],
    argument_lines: Iterable[int] = (),
    exclusion: str = DEFAULT_EXCLUSION,
) -> SiteComments:
    """``site_comments`` for any block, an expression candidate's included.

    A comment between the operands of an expression stood inside brackets:
    the grouping parentheses or the call around the expression, which stay
    at the site. In the helper it needs brackets again, so each such
    comment is recorded as grouped with the whole returned value, and the
    helper is written ``return (`` ... ``)`` around it.
    """
    comments = site_comments(source, block, argument_lines, exclusion)
    if not is_expression_block(block) or not comments.comments:
        return comments
    value = getattr(block[0], "value")
    whole = NodePath(0, type(block[0]).__name__, (("value", -1, type(value).__name__),))
    return replace(
        comments,
        comments=tuple(
            (
                comment
                if comment.anchor.group is not None
                else replace(comment, anchor=replace(comment.anchor, group=whole))
            )
            for comment in comments.comments
        ),
    )


def expression_at(source: str, place: ExpressionPlace) -> Optional[ast.expr]:
    """The expression of ``source`` standing at ``place``, parsed from its own text.

    None when the text there is not one expression, as when ``source`` is
    not the text the place was recorded in.
    """
    lines = source_lines(source)
    (first, start), (last, end) = place.start, place.end
    if not 1 <= first <= last <= len(lines):
        return None
    encoded = [line.encode("utf-8") for line in lines[first - 1 : last]]
    encoded[-1] = encoded[-1][:end]
    encoded[0] = encoded[0][start:]
    try:
        text = b"".join(encoded).decode("utf-8")
        return ast.parse("(" + text + "\n)", mode="eval").body
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return None

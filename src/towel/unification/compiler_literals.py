# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Literal object correlations that compiler pooling can make observable.

A literal-only operation may compile into one pooled object. Replacing its
children with parameters instead computes new objects at runtime. Separately
compiled modules also have separate literal pools; moving a retained literal
into a common helper can merge or split ordinary ``is`` correlations.
"""

from __future__ import annotations

import ast
import math
import sys
from typing import FrozenSet, Iterator, Optional, Sequence, Tuple
from weakref import WeakKeyDictionary

from .statement_facts import memoized_per_node
from .structural_memo import Path, path_map
from .substitution import structural_text

_LITERAL_INPUT: "WeakKeyDictionary[ast.AST, bool]" = WeakKeyDictionary()


def _literal_input(node: ast.AST) -> bool:
    """Pure classification once per immutable source node, weakly retained."""
    return memoized_per_node(_LITERAL_INPUT, node, _compute_literal_input)


def _compute_literal_input(node: ast.AST) -> bool:
    """A closed literal expression from a family CPython may constant-fold.

    Folding depends on interpreter limits and operand types. Treating every
    member of these families as potentially pooled is conservative; this
    predicate does not evaluate the expression or invoke the compiler.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id == "__debug__" and isinstance(getattr(node, "ctx", None), ast.Load)
    if isinstance(node, ast.Tuple):
        return isinstance(getattr(node, "ctx", None), ast.Load) and all(
            _literal_input(item) for item in node.elts
        )
    if isinstance(node, ast.UnaryOp):
        return _literal_input(node.operand)
    if isinstance(node, ast.BinOp):
        return _literal_input(node.left) and _literal_input(node.right)
    if isinstance(node, ast.Subscript):
        return (
            isinstance(getattr(node, "ctx", None), ast.Load)
            and not isinstance(node.slice, ast.Slice)
            and _literal_input(node.value)
            and _literal_input(node.slice)
        )
    return False


def is_pooled_expression(node: ast.AST) -> bool:
    """A closed expression whose children must stay in its original compiler pool."""
    return isinstance(node, (ast.Tuple, ast.UnaryOp, ast.BinOp, ast.Subscript)) and _literal_input(
        node
    )


def pooled_expression_positions(block: Sequence[ast.AST], expression: ast.AST) -> tuple[Path, ...]:
    """Every runtime occurrence of an exact structural root in a block.

    Global substitutions may replace every equal root. Passing the whole
    original expression preserves pooling only when these occurrence sets
    correspond at each participating site.
    """
    key = structural_text(expression)
    runtime_nodes = frozenset(_runtime_nodes(block))
    return tuple(
        sorted(
            path
            for node, path in path_map(block).items()
            if node in runtime_nodes and isinstance(node, ast.expr) and structural_text(node) == key
        )
    )


def _runtime_walk(
    block: Sequence[ast.AST], protected_roots: FrozenSet[ast.AST] = frozenset()
) -> Iterator[Tuple[ast.AST, bool]]:
    """Runtime nodes and whether a pooled ancestor protects their structure."""
    pending = [(node, False, False) for node in reversed(block)]
    while pending:
        node, variable_annotations_run, protected = pending.pop()
        yield node, protected
        children_protected = protected or node in protected_roots
        for field, value in ast.iter_fields(node):
            if (
                isinstance(node, ast.AnnAssign)
                and field == "annotation"
                and not variable_annotations_run
            ):
                continue
            nested_annotations_run = variable_annotations_run
            if field == "body":
                if isinstance(node, ast.ClassDef):
                    nested_annotations_run = True
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nested_annotations_run = False
            children = value if isinstance(value, list) else [value]
            pending.extend(
                (child, nested_annotations_run, children_protected)
                for child in reversed(children)
                if isinstance(child, ast.AST)
            )


def _runtime_nodes(block: Sequence[ast.AST]) -> Iterator[ast.AST]:
    """Function-body nodes, skipping unevaluated local variable annotations.

    A nested class's variable annotations can execute; a nested function's
    local annotations cannot. Headers remain conservative runtime inputs.
    """
    return (node for node, _ in _runtime_walk(block))


def pooled_literal_parts(block: Sequence[ast.AST]) -> FrozenSet[str]:
    """Structural keys substitution must not replace inside a pooled expression.

    Substitution is keyed by expression text, so a mapping created for a
    different occurrence of the same expression would replace this one too.
    Protect all matching descendant keys, not just these node identities.
    Entire expression roots may still be passed from the original caller.
    """
    roots: set[ast.AST] = set()
    membership: set[ast.AST] = set()
    for node in _runtime_nodes(block):
        if is_pooled_expression(node):
            roots.add(node)
        if isinstance(node, ast.Compare):
            # CPython's membership optimizer also folds literal lists/sets.
            for operator, comparator in zip(node.ops, node.comparators):
                if (
                    isinstance(operator, (ast.In, ast.NotIn))
                    and isinstance(comparator, (ast.List, ast.Set))
                    and all(_literal_input(item) for item in comparator.elts)
                ):
                    membership.add(comparator)
    return frozenset(
        structural_text(node)
        for node, protected in _runtime_walk(block, frozenset(roots | membership))
        if isinstance(node, ast.expr) and (protected or node in membership)
    )


def immutable_literal_value(node: ast.AST) -> bool:
    """Constants and literal tuples; no potentially failing closed computation."""
    if isinstance(node, ast.Constant):
        return type(node.value) in (
            type(None),
            bool,
            int,
            float,
            complex,
            str,
            bytes,
            type(Ellipsis),
        )
    return (
        isinstance(node, ast.Tuple)
        and isinstance(node.ctx, ast.Load)
        and all(immutable_literal_value(element) for element in node.elts)
    )


def literal_fragment_transfer_supported() -> bool:
    """Exact-string formatting/join identity on the reviewed CPython compilers."""
    return sys.implementation.name == "cpython" and (3, 9) <= sys.version_info[:2] <= (3, 13)


def common_literal_candidates(block: Sequence[ast.AST]) -> Iterator[ast.expr]:
    """Threadable literal roots, excluding grammar and definition metadata.

    Common exact-string f-string parts may travel in the literal carrier;
    their helper spelling uses no-spec exact-string formatting. Patterns,
    annotations and nested definitions retain their syntax. A same-key
    runtime literal elsewhere cannot bypass these pins.
    """
    runtime = frozenset(_runtime_nodes(block))
    paths = path_map(block)
    for node, (statement, steps) in paths.items():
        if (
            node not in runtime
            or not isinstance(node, ast.expr)
            or not immutable_literal_value(node)
        ):
            continue
        current = block[statement]
        metadata = False
        for field, index in steps:
            if isinstance(
                current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.pattern)
            ):
                metadata = True
                break
            if field == "annotation" or field == "returns":
                metadata = True
                break
            if (
                isinstance(current, ast.JoinedStr)
                and isinstance(node, ast.Constant)
                and field == "values"
                and index >= 0
                and isinstance(current.values[index], ast.Constant)
                and not (
                    index >= 0
                    and current.values[index] is node
                    and type(node.value) is str
                    and literal_fragment_transfer_supported()
                )
            ):
                metadata = True
                break
            if (
                isinstance(current, ast.FormattedValue)
                and field == "value"
                and isinstance(node, ast.Constant)
            ):
                metadata = True
                break
            value = getattr(current, field)
            current = value if index < 0 else value[index]
        if metadata:
            continue
        # A first expression string is a docstring, not a runtime literal input.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(steps) == 1 and steps[0] == ("value", -1) and statement == 0:
                continue
        if isinstance(node, ast.Constant) and is_global_literal(node.value):
            continue
        yield node


def is_global_literal(value: object) -> bool:
    """Objects shared by the reviewed CPython 3.9--3.13 literal constructors.

    Other implementations/versions use only language singletons. Compiler
    string interning covers ASCII letters, digits and underscores; Unicode
    construction also normalizes empty and single Latin1-character strings.
    Nonempty bytes are not exempt: long escaped tokens can bypass their cache.
    """
    if value is None or value is Ellipsis or type(value) is bool:
        return True
    if sys.implementation.name != "cpython" or not (3, 9) <= sys.version_info[:2] <= (3, 13):
        return False
    if isinstance(value, int) and type(value) is int:
        return -5 <= value <= 256
    if isinstance(value, str) and type(value) is str:
        return (
            not value
            or len(value) == 1
            and ord(value) < 256
            or all(
                "a" <= char <= "z" or "A" <= char <= "Z" or "0" <= char <= "9" or char == "_"
                for char in value
            )
        )
    return type(value) is bytes and value == b""


def _integer_literal_result(node: ast.AST) -> bool:
    """An integer result if this closed expression folds successfully."""
    if isinstance(node, ast.Constant):
        return type(node.value) in (int, bool)
    if isinstance(node, ast.Name):
        return node.id == "__debug__" and isinstance(getattr(node, "ctx", None), ast.Load)
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, ast.Not) or (
            isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert))
            and _integer_literal_result(node.operand)
        )
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Div):
            return False
        if isinstance(node.op, ast.Pow):
            return (
                _integer_literal_result(node.left)
                and isinstance(node.right, ast.Constant)
                and type(node.right.value) is int
                and node.right.value >= 0
            )
        return isinstance(
            node.op,
            (
                ast.Add,
                ast.Sub,
                ast.Mult,
                ast.FloorDiv,
                ast.Mod,
                ast.LShift,
                ast.RShift,
                ast.BitOr,
                ast.BitXor,
                ast.BitAnd,
            ),
        ) and all(_integer_literal_result(child) for child in (node.left, node.right))
    return False


def unstable_literal_pool(block: Sequence[ast.AST]) -> bool:
    """Closed arithmetic whose folded objects may include nonmerged NaNs.

    Finite floating inputs can overflow; integer true division and negative
    exponentiation can introduce floats without a floating source literal.
    Unknown closed subscript operands likewise have no integer-result proof.
    Preserve each original compiler site until its object correlations can
    be certified. Ordinary dynamic float('nan') calls are unaffected.
    """
    for node in _runtime_nodes(block):
        if isinstance(node, ast.Constant):
            value = node.value
            if type(value) is float and math.isnan(value):
                return True
            if type(value) is complex and (math.isnan(value.real) or math.isnan(value.imag)):
                return True
        if isinstance(node, ast.BinOp) and _literal_input(node):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and type(inner.value) in (float, complex):
                    return True
                if isinstance(inner, ast.Subscript):
                    return True
                if isinstance(inner, ast.BinOp) and (
                    isinstance(inner.op, ast.Div)
                    or isinstance(inner.op, ast.Pow)
                    and not _integer_literal_result(inner)
                ):
                    return True
    return False


_UNSAFE_BYTES: "WeakKeyDictionary[ast.AST, FrozenSet[int]]" = WeakKeyDictionary()


def _unsafe_byte_values(module: ast.AST) -> FrozenSet[int]:
    """Lexically nonglobal one-byte atoms anywhere in the original compiler pool.

    A long escaped token can bypass CPython's single-byte constructor cache.
    The compiler can then reuse that object for a same-valued short token;
    considering every original occurrence avoids a first-constant confound.
    A two-byte concatenation followed by a one-byte slice uses the reviewed
    cached constructor, without compiling or evaluating the user's program.
    """
    return frozenset(
        node.value[0]
        for node in ast.walk(module)
        if isinstance(node, ast.Constant)
        and type(node.value) is bytes
        and len(node.value) == 1
        and node.value is not (node.value + b"\0")[:1]
    )


def unsafe_byte_literal_moves(block: Sequence[ast.AST], module: Optional[ast.Module]) -> bool:
    """Whether rendering moved atoms can normalize an original nonglobal byte.

    Facts are immutable and weakly scoped to the original read-only source
    module; equal structural text from another parsed module is not authority.
    Unknown original compiler contexts remain conservative.
    """
    values: FrozenSet[int] = frozenset(
        node.value[0]
        for node in _runtime_nodes(block)
        if isinstance(node, ast.Constant) and type(node.value) is bytes and len(node.value) == 1
    )
    if not values:
        return False
    if (
        module is None
        or sys.implementation.name != "cpython"
        or not (3, 9) <= sys.version_info[:2] <= (3, 13)
    ):
        return True
    unsafe: FrozenSet[int] = memoized_per_node(_UNSAFE_BYTES, module, _unsafe_byte_values)
    return bool(values & unsafe)


def retained_literal_pool(
    block: Sequence[ast.AST], *, trusted_projection: Optional[Tuple[str, int]] = None
) -> bool:
    """Whether sharing this helper across compiler pools needs an identity proof.

    Language singletons and reviewed interpreter-global literal objects are
    exempt. Other values keep a separate compiler-pool identity boundary.
    """
    projected: set[ast.AST] = set()
    if trusted_projection is not None:
        parameter, width = trusted_projection
        for node in _runtime_nodes(block):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name)
                and node.value.id == parameter
                and isinstance(node.slice, ast.Constant)
                and type(node.slice.value) is int
                and 0 <= node.slice.value < width
            ):
                projected.add(node.slice)
    for node in _runtime_nodes(block):
        if isinstance(node, ast.Constant):
            if node not in projected and not is_global_literal(node.value):
                return True
        elif is_pooled_expression(node):
            # An aggregate or folded result can have its own identity
            # even when every leaf is a singleton, as in (True, None).
            return True
    return False

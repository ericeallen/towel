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
from typing import FrozenSet, Iterator, Sequence

from .substitution import structural_text


def _literal_input(node: ast.AST) -> bool:
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


def _runtime_nodes(block: Sequence[ast.AST]) -> Iterator[ast.AST]:
    """Function-body nodes, without annotations of local annotated assignments.

    A nested class's variable annotations can execute; a nested function's
    local annotations cannot. Headers and other nested-scope expressions
    remain conservative runtime inputs.
    """
    pending = [(node, False) for node in reversed(block)]
    while pending:
        node, variable_annotations_run = pending.pop()
        yield node
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
                (child, nested_annotations_run)
                for child in reversed(children)
                if isinstance(child, ast.AST)
            )


def pooled_literal_parts(block: Sequence[ast.AST]) -> FrozenSet[str]:
    """Structural keys substitution must not replace inside a pooled expression.

    Substitution is keyed by expression text, so a mapping created for a
    different occurrence of the same expression would replace this one too.
    Protect all matching descendant keys, not just these node identities.
    Entire expression roots may still be passed from the original caller.
    """
    protected: set[str] = set()
    for node in _runtime_nodes(block):
        if isinstance(node, (ast.Tuple, ast.UnaryOp, ast.BinOp, ast.Subscript)) and _literal_input(
            node
        ):
            protected.update(
                structural_text(inner)
                for inner in ast.walk(node)
                if inner is not node and isinstance(inner, ast.expr)
            )
        if isinstance(node, ast.Compare):
            # CPython folds literal lists/sets in membership tests into
            # immutable containers. Preserve their collision/probe order.
            for operator, comparator in zip(node.ops, node.comparators):
                if (
                    isinstance(operator, (ast.In, ast.NotIn))
                    and isinstance(comparator, (ast.List, ast.Set))
                    and all(_literal_input(item) for item in comparator.elts)
                ):
                    protected.update(
                        structural_text(inner)
                        for inner in ast.walk(comparator)
                        if isinstance(inner, ast.expr)
                    )
    return frozenset(protected)


def retained_literal_pool(block: Sequence[ast.AST]) -> bool:
    """Whether sharing this helper across compiler pools needs an identity proof.

    Only language singletons are exempt. This deliberately does not assume
    an interpreter's small-int cache or string interning behavior.
    """
    for node in _runtime_nodes(block):
        if isinstance(node, ast.Constant):
            if (
                node.value is not None
                and node.value is not Ellipsis
                and type(node.value) is not bool
            ):
                return True
        elif isinstance(
            node, (ast.Tuple, ast.UnaryOp, ast.BinOp, ast.Subscript)
        ) and _literal_input(node):
            # An aggregate or folded result can have its own identity
            # even when every leaf is a singleton, as in (True, None).
            return True
    return False

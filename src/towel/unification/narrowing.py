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

"""Extractions that would separate a narrowing test from code that depends on it.

A Python checker narrows a variable's type along a control-flow region: after
``if isinstance(other, Klass)``, the name ``other`` reads as a ``Klass`` for as
long as that branch holds, though it is declared ``object``. The narrowing
belongs to a region, not to the name, and extraction moves code out of regions.

Where the two sites differ, the differing expression is passed as a thunk and
so stays with the caller, while the test that narrowed it moves into the
helper. Each half is well typed where it was written and the pair is not::

    class ASTClass(ASTBase):
        def __eq__(self, other: object) -> bool:
            if not isinstance(other, ASTClass):
                return NotImplemented
            return self.name == other.name and self.final == other.final

becomes a helper holding the test and a call site holding ``lambda:
other.final``, where ``other`` is an ``object`` again. mypy reports
``"object" has no attribute "final"`` at the call site, so no signature on the
helper can repair it. Running the thunk is safe -- the helper calls it only
after the test passes -- but a checker cannot see that, and Towel's promise is
that a checked project still checks.

This module refuses such an extraction rather than proposing it and letting a
checker reject it later, which costs a whole-project check per candidate
signature and, on Sphinx, was seventy of two hundred and forty-one rejections.
The refusal is not a heuristic: both halves are Towel's own construction, so
the test and the expressions left behind are read directly off the proposal.

Java's pattern matching shows the alternative design. ``if (o instanceof
String s)`` binds a *new* name, so moving a use of ``s`` out of its scope is a
compile error about an unknown name -- loud, local and immediate. Narrowing an
existing name is silent under the same move: the name is still in scope and
still legal, only wider, and the complaint arrives somewhere else entirely.
"""

from __future__ import annotations

import ast
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set

from .models import Replacement

_NARROWING_CALLS = frozenset({"isinstance", "issubclass", "hasattr", "callable"})
"""Builtins whose result narrows their first argument in mypy and pyright."""


def _narrowed_by_test(test: ast.expr) -> Set[str]:
    """Names this test narrows for the code it guards."""
    narrowed: Set[str] = set()
    for node in ast.walk(test):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _NARROWING_CALLS and node.args:
                first = node.args[0]
                if isinstance(first, ast.Name):
                    narrowed.add(first.id)
        elif isinstance(node, ast.Compare):
            narrowed |= _narrowed_by_comparison(node)
    return narrowed


def _narrowed_by_comparison(node: ast.Compare) -> Set[str]:
    """Names a comparison narrows: ``x is None`` and ``type(x) is C``."""
    narrowed: Set[str] = set()
    subject: Optional[str] = None
    if isinstance(node.left, ast.Name):
        subject = node.left.id
        wants_none = True
    elif (
        isinstance(node.left, ast.Call)
        and isinstance(node.left.func, ast.Name)
        and node.left.func.id == "type"
        and len(node.left.args) == 1
        and isinstance(node.left.args[0], ast.Name)
    ):
        # ``type(x) is C`` narrows x to exactly C, which both checkers honour.
        subject = node.left.args[0].id
        wants_none = False
    if subject is None:
        return narrowed
    for operator, other in zip(node.ops, node.comparators):
        if not isinstance(operator, (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)):
            continue
        if not wants_none or (isinstance(other, ast.Constant) and other.value is None):
            narrowed.add(subject)
    return narrowed


def narrowed_names(body: Iterable[ast.stmt]) -> Set[str]:
    """Every name some test in ``body`` narrows for the code that test guards."""
    narrowed: Set[str] = set()
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, (ast.If, ast.While, ast.IfExp)):
                narrowed |= _narrowed_by_test(node.test)
            elif isinstance(node, ast.Assert):
                narrowed |= _narrowed_by_test(node.test)
            elif isinstance(node, ast.comprehension):
                for condition in node.ifs:
                    narrowed |= _narrowed_by_test(condition)
            elif isinstance(node, ast.Match) and isinstance(node.subject, ast.Name):
                # Every pattern but a bare capture narrows the subject.
                if any(
                    not isinstance(case.pattern, ast.MatchAs) or case.pattern.pattern is not None
                    for case in node.cases
                ):
                    narrowed.add(node.subject.id)
    return narrowed


def _parameters(helper: ast.FunctionDef) -> List[str]:
    """The parameters a call can fill, positionally then by name."""
    arguments = helper.args
    return [argument.arg for argument in (*arguments.posonlyargs, *arguments.args)]


def _nameable(helper: ast.FunctionDef) -> List[str]:
    """Every parameter a keyword can name, the keyword-only ones included."""
    arguments = helper.args
    return [argument.arg for argument in (*arguments.args, *arguments.kwonlyargs)]


def _call_in(node: ast.stmt, helper_name: str) -> Optional[ast.Call]:
    """The call to ``helper_name`` inside a replacement statement."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            called = inner.func
            if isinstance(called, ast.Name) and called.id == helper_name:
                return inner
            if isinstance(called, ast.Attribute) and called.attr == helper_name:
                return inner
    return None


def _arguments_by_parameter(
    call: ast.Call, parameters: Sequence[str], function: ast.FunctionDef
) -> Dict[str, ast.expr]:
    """Which expression each parameter receives, as far as position and keyword say.

    A receiver is bound by the call's own form rather than passed, and a
    starred argument makes the rest of the positions unknowable; both simply
    leave those parameters unmapped, which can only make the refusal narrower.
    """
    if any(isinstance(argument, ast.Starred) for argument in call.args):
        positional: Sequence[ast.expr] = ()
    else:
        positional = call.args
    # A bound method receives its receiver from the call's own form rather than
    # in the argument list, which shows as the call supplying one parameter
    # fewer than the helper declares. Counting the keywords as well as the
    # positions is what makes that true of a call that mixes the two.
    supplied = len(positional) + len(call.keywords)
    offset = 1 if supplied == len(parameters) - 1 else 0
    mapped = {
        parameters[index + offset]: argument
        for index, argument in enumerate(positional)
        if 0 <= index + offset < len(parameters)
    }
    nameable = _nameable(function)
    for keyword in call.keywords:
        if keyword.arg is not None and keyword.arg in nameable:
            mapped[keyword.arg] = keyword.value
    return mapped


def _bound_by(node: ast.expr) -> Set[str]:
    """Names this expression binds for its own body."""
    if isinstance(node, ast.Lambda):
        arguments = node.args
        named = {
            argument.arg
            for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
        }
        for extra in (arguments.vararg, arguments.kwarg):
            if extra is not None:
                named.add(extra.arg)
        return named
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        return {
            target.id
            for generator in node.generators
            for target in ast.walk(generator.target)
            if isinstance(target, ast.Name)
        }
    return set()


def _names_read(expression: ast.expr, shadowed: FrozenSet[str] = frozenset()) -> Set[str]:
    """Names ``expression`` reads from its enclosing scope, ignoring what it binds.

    A name a nested scope binds is hidden only inside that scope. Subtracting
    every such name from the whole expression would lose a genuine reading
    beside it, as in ``(other.final, lambda other: other)``.
    """
    if isinstance(expression, ast.Name):
        return set() if expression.id in shadowed else {expression.id}
    inner = shadowed | frozenset(_bound_by(expression))
    names: Set[str] = set()
    for child in ast.iter_child_nodes(expression):
        if isinstance(child, ast.expr):
            names |= _names_read(child, inner)
        else:
            names |= {
                node.id
                for node in ast.walk(child)
                if isinstance(node, ast.Name) and node.id not in inner
            }
    return names


def narrowing_lost_at_call_site(
    helper: ast.FunctionDef, replacements: Sequence[Replacement]
) -> Optional[str]:
    """Why this extraction separates a narrowing test from a use, or None if it does not.

    The helper's test narrows one of its parameters. A call site that passes a
    name for that parameter and also passes an expression reading the same name
    has left that expression outside the region the test governs, where it
    reads as the declared type again.
    """
    narrowed = narrowed_names(helper.body)
    if not narrowed:
        return None
    parameters = _parameters(helper)
    guarded = [parameter for parameter in parameters if parameter in narrowed]
    if not guarded:
        return None
    for replacement in replacements:
        call = _call_in(replacement.node, helper.name)
        if call is None:
            continue
        arguments = _arguments_by_parameter(call, parameters, helper)
        # What the call site calls each narrowed parameter.
        subjects = {
            argument.id
            for parameter in guarded
            if isinstance(argument := arguments.get(parameter), ast.Name)
        }
        if not subjects:
            continue
        for parameter, argument in arguments.items():
            if parameter in guarded or isinstance(argument, ast.Name):
                continue
            depends = _names_read(argument) & subjects
            if depends:
                subject = sorted(depends)[0]
                return f"{ast.unparse(argument)} reads {subject} outside the test that narrows it"
    return None

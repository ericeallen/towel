# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""A lower bound on parameters for windows of calls with literal arguments."""

from __future__ import annotations

import ast
from typing import NamedTuple, Sequence

from ..analysis_sources import ParserConfiguration, parsing_is_pure
from .static_positions import TYPING_FORM_NAMES, callee_spellings
from .typing_forms import ModuleText
from .constant_consistency import constant_identity
from .module_bindings import dotted_name
from .run_syntax import run_syntax
from .unifier_state import ConstantIdentity


class CheckedWindow(NamedTuple):
    statements: tuple[ast.stmt, ...]
    module: ModuleText | None
    parser: ParserConfiguration

    def reusable(self) -> bool:
        """Whether the completed typing lookups are safe to skip on a later refusal.

        Only a spelling ending in a typing form's name can follow an import
        and parse another source (TypingFormResolver). Otherwise all parses
        use this module's text under the recorded interpreter limits.
        """
        return self.module is None or (
            parsing_is_pure(self.module.source)
            and not any(
                name.rpartition(".")[2] in TYPING_FORM_NAMES
                for statement in self.statements
                for name in callee_spellings(statement)
            )
        )


class LiteralCall(NamedTuple):
    callee: str
    keywords: tuple[str | None, ...]
    arguments: tuple[ConstantIdentity, ...]


def _literal_call(statement: ast.AST) -> LiteralCall | None:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return None
    call = statement.value
    callee = dotted_name(call.func)
    if callee is None:
        return None
    arguments = []
    for argument in (*call.args, *(keyword.value for keyword in call.keywords)):
        if not isinstance(argument, ast.Constant):
            return None
        arguments.append(constant_identity(argument.value))
    return LiteralCall(callee, tuple(keyword.arg for keyword in call.keywords), tuple(arguments))


def exceeds_literal_parameter_limit(
    left: Sequence[ast.stmt], right: Sequence[ast.stmt], limit: int
) -> bool:
    """Prove failure from a prefix, stopping at the first unrecognized row.

    The unifier descends into each argument of these matching calls. Equal
    constants cost nothing. A differing pair costs a parameter, and only
    the same ordered pair of typed values can reuse it. A pin may refuse a
    difference earlier, but cannot make any such window unify.
    """
    if not left or len(left) != len(right) or not isinstance(left[0], ast.Expr):
        return False
    differences: set[tuple[ConstantIdentity, ConstantIdentity]] = set()
    facts = run_syntax.get()
    for first, second in zip(left, right):
        a = _literal_call(first) if facts is None else facts.literal_calls.get(first, _literal_call)
        b = (
            _literal_call(second)
            if facts is None
            else facts.literal_calls.get(second, _literal_call)
        )
        if (
            a is None
            or b is None
            or a.callee != b.callee
            or a.keywords != b.keywords
            or len(a.arguments) != len(b.arguments)
        ):
            return False
        for x, y in zip(a.arguments, b.arguments):
            if x != y:
                differences.add((x, y))
                if len(differences) > limit:
                    return True
    return False

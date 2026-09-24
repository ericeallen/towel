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

"""Propose generic signatures by sharing type differences across complete call rows.

A row contains all helper argument types followed by its required result type.
Anti-unification retains common constructors and maps each distinct column of
differences to one fresh binder. The output is a hypothesis for whole-project
checking: structural correlation alone does not establish that a helper body is
valid for every allowed instantiation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Sequence, Set, Tuple

from .type_bindings import (
    TypeKind,
    TypeParameter,
    TypeTerm,
    contains_type_parameter,
    type_parameter_identities,
)


@dataclass(frozen=True)
class NamedTypeParameter:
    """A fresh helper binder, with an independent bound or concrete constraints."""

    name: str
    bound: TypeTerm | None = None
    constraints: tuple[TypeTerm, ...] = ()


@dataclass(frozen=True)
class GenericSignature:
    """Immutable candidate argument/result types and their helper declarations."""

    types: tuple[TypeTerm, ...]
    parameters: tuple[NamedTypeParameter, ...]


@dataclass(frozen=True)
class _Hole:
    parameter: NamedTypeParameter
    concrete_alternatives: tuple[TypeTerm, ...]


class _UnsupportedGeneralization(Exception):
    """The proposed signature needs a domain or binder we cannot represent."""


def _domain(terms: tuple[TypeTerm, ...]) -> tuple[TypeTerm | None, tuple[TypeTerm, ...]]:
    parameters = tuple(term.parameter for term in terms if term.parameter is not None)
    if not parameters:
        return None, ()
    first = parameters[0]
    for parameter in parameters:
        if (
            parameter.bound != first.bound
            or frozenset(parameter.constraints) != frozenset(first.constraints)
            or (parameter.bound is not None and bool(parameter.constraints))
            or len(parameter.constraints) == 1
            or (parameter.bound is not None and contains_type_parameter(parameter.bound))
            or any(contains_type_parameter(constraint) for constraint in parameter.constraints)
        ):
            raise _UnsupportedGeneralization
    return first.bound, first.constraints


def _binder(parameter: NamedTypeParameter) -> TypeTerm:
    identity = f"towel.generated:{parameter.name}"
    return TypeTerm(
        TypeKind.ATOM,
        identity,
        parameter.name,
        parameter=TypeParameter(identity, parameter.bound, parameter.constraints),
    )


def _replace_binders(term: TypeTerm, parameters: dict[str, NamedTypeParameter]) -> TypeTerm:
    if term.parameter is not None and term.parameter.identity in parameters:
        return _binder(parameters[term.parameter.identity])
    return replace(
        term, children=tuple(_replace_binders(child, parameters) for child in term.children)
    )


def _shape(term: TypeTerm) -> Tuple[object, ...]:
    """What makes two members of a union counterparts: kind, constructor, and arity."""
    constructor = term.children[0].name if term.kind is TypeKind.APPLY and term.children else ""
    return term.kind, constructor, len(term.children)


_COUNTERPARTS: Tuple[Callable[[TypeTerm, TypeTerm], bool], ...] = (
    lambda member, other: member == other,
    lambda member, other: _shape(member) == _shape(other),
    lambda member, other: member.kind is other.kind,
    lambda member, other: True,
)
"""The relations matched in turn: the same type, the same structure, the same kind, any."""


def _counterparts(first: Sequence[TypeTerm], other: Sequence[TypeTerm]) -> List[TypeTerm]:
    """For each member of ``first``, the member of ``other`` it corresponds to."""
    chosen: Dict[int, int] = {}
    for related in _COUNTERPARTS:
        for index, member in enumerate(first):
            if index in chosen:
                continue
            match = next(
                (
                    position
                    for position, candidate in enumerate(other)
                    if position not in chosen.values() and related(member, candidate)
                ),
                None,
            )
            if match is not None:
                chosen[index] = match
    return [other[chosen[index]] for index in range(len(first))]


def aligned_children(terms: Sequence[TypeTerm]) -> Tuple[Tuple[TypeTerm, ...], ...] | None:
    """The children of same-shaped terms, as columns; ``None`` when the shapes differ.

    Positional structure lines up by position. A union's members have no
    position: its canonical order sorts by identity, which pairs ``int |
    pkg.Foo`` with ``pkg.Foo | zlib.Bar`` member by member into two
    unrelated columns. Members line up by what they are instead -- the same
    type, then the same structure (``list[str]`` with ``list[Version]``),
    then the same kind -- in the first union's order.
    """
    first = terms[0]
    if any(
        term.kind is not first.kind or len(term.children) != len(first.children) for term in terms
    ):
        return None
    if first.kind is not TypeKind.UNION:
        return tuple(zip(*(term.children for term in terms)))
    others = [_counterparts(first.children, term.children) for term in terms[1:]]
    return tuple(zip(first.children, *others))


def _can_merge_position(terms: tuple[TypeTerm, ...]) -> bool:
    """Keep literal metadata and callable argument-list syntax out of type holes."""
    first = terms[0]
    if any(term.kind is TypeKind.CONSTANT for term in terms):
        return all(term == first for term in terms)
    if any(term.kind in (TypeKind.LIST, TypeKind.TUPLE) for term in terms):
        return all(
            term.kind is first.kind and len(term.children) == len(first.children) for term in terms
        ) and all(_can_merge_position(column) for column in zip(*(term.children for term in terms)))
    return True


class _Builder:
    """Construction state is confined to one pure public call."""

    def __init__(self, reserved_names: Set[str], retained_parameters: frozenset[str]) -> None:
        self.reserved = set(reserved_names)
        self.retained_parameters = retained_parameters
        self.holes: dict[tuple[TypeTerm, ...], _Hole] = {}
        self.next_index = 0

    def _hole(self, terms: tuple[TypeTerm, ...]) -> TypeTerm:
        existing = self.holes.get(terms)
        if existing is not None:
            return _binder(existing.parameter)
        bound, constraints = _domain(terms)
        while (name := f"_TowelT{self.next_index}") in self.reserved:
            self.next_index += 1
        self.next_index += 1
        self.reserved.add(name)
        parameter = NamedTypeParameter(name, bound, constraints)
        # A whole list[T]/set[U] mismatch can be quantified as one ordinary
        # value type, but cannot become constraints containing source binders.
        concrete = (
            ()
            if any(contains_type_parameter(term) for term in terms)
            else tuple(dict.fromkeys(terms))
        )
        self.holes[terms] = _Hole(parameter, concrete)
        return _binder(parameter)

    def merge(self, terms: tuple[TypeTerm, ...]) -> TypeTerm:
        first = terms[0]
        if all(term == first for term in terms) and (
            type_parameter_identities(first) <= self.retained_parameters
        ):
            return first
        if any(term.parameter is not None for term in terms):
            return self._hole(terms)
        same_shape = all(
            term.kind is first.kind and len(term.children) == len(first.children) for term in terms
        )
        if same_shape and first.kind is TypeKind.APPLY:
            if not first.children or any(
                contains_type_parameter(term.children[0]) for term in terms
            ):
                raise _UnsupportedGeneralization
            if not all(term.children[0] == first.children[0] for term in terms):
                return self._hole(terms)
        elif not (same_shape and first.kind in (TypeKind.TUPLE, TypeKind.LIST, TypeKind.UNION)):
            return self._hole(terms)
        columns = aligned_children(terms)
        assert columns is not None  # same_shape
        if not all(_can_merge_position(column) for column in columns):
            if first.kind in (TypeKind.LIST, TypeKind.TUPLE):
                raise _UnsupportedGeneralization
            return self._hole(terms)
        return replace(first, children=tuple(self.merge(column) for column in columns))


def generalize_signatures(
    rows: Sequence[Sequence[TypeTerm]],
    reserved_names: Set[str],
    *,
    retained_parameters: frozenset[str] = frozenset(),
    constrainable: Callable[[TypeTerm], bool] = lambda term: True,
) -> tuple[GenericSignature, ...]:
    """Return at most two checkable candidates, without changing caller inputs.

    Fresh variables retain matching source bounds or constraints; incompatible
    source domains and dependent bounds are declined. Concrete differences are
    first unrestricted, then jointly constrained where there are two to four
    closed alternatives that are all ``constrainable`` (a constraint is
    written where the variable is declared, so each must be writable there;
    a variable whose alternatives are not stays unrestricted). We do not
    enumerate an exponential mix of constraint choices. Every generated binder
    must occur in an input, so a return-only promise cannot be manufactured
    from unrelated call sites. Parameters already bound by the helper's host
    class keep their identity and may occur only in the result: the implicit
    receiver supplies that class instantiation.
    """
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        return ()
    builder = _Builder(reserved_names, retained_parameters)
    try:
        types = tuple(builder.merge(tuple(column)) for column in zip(*rows))
    except _UnsupportedGeneralization:
        return ()
    parameters = tuple(hole.parameter for hole in builder.holes.values())
    if not parameters and not any(
        type_parameter_identities(term) & retained_parameters for term in types
    ):
        return ()
    input_binders = frozenset().union(*(type_parameter_identities(term) for term in types[:-1]))
    if any(f"towel.generated:{parameter.name}" not in input_binders for parameter in parameters):
        return ()
    first = GenericSignature(types, parameters)
    constrained = tuple(
        (
            replace(hole.parameter, constraints=hole.concrete_alternatives)
            if 2 <= len(hole.concrete_alternatives) <= 4
            and all(constrainable(term) for term in hole.concrete_alternatives)
            else hole.parameter
        )
        for hole in builder.holes.values()
    )
    if constrained == parameters:
        return (first,)
    substitutions = {f"towel.generated:{parameter.name}": parameter for parameter in constrained}
    second = GenericSignature(
        tuple(_replace_binders(term, substitutions) for term in types), constrained
    )
    return first, second

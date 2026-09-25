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

"""Type anti-unification preserves relationships and the domains of free binders."""

from towel.unification.type_bindings import TypeKind, TypeParameter, TypeTerm
from towel.unification.type_generalization import generalize_signatures


def atom(name: str, spelling: str = "") -> TypeTerm:
    return TypeTerm(TypeKind.ATOM, name, spelling or name)


def parameter(
    identity: str, bound: TypeTerm | None = None, constraints: tuple[TypeTerm, ...] = ()
) -> TypeTerm:
    return TypeTerm(
        TypeKind.ATOM, identity, "T", parameter=TypeParameter(identity, bound, constraints)
    )


def applied(name: str, *arguments: TypeTerm) -> TypeTerm:
    return TypeTerm(TypeKind.APPLY, children=(atom(name), *arguments))


INT, STR, FLOAT, BYTES = (atom(name) for name in ("int", "str", "float", "bytes"))


def test_concrete_correlated_columns_share_one_binder_and_two_candidates():
    candidates = generalize_signatures(((INT, INT, INT), (STR, STR, STR)), set())
    assert len(candidates) == 2
    first, constrained = candidates
    assert len(first.parameters) == 1
    assert first.types[0] == first.types[1] == first.types[2]
    assert first.parameters[0].bound is None and first.parameters[0].constraints == ()
    assert constrained.parameters[0].constraints == (INT, STR)
    assert constrained.types[0].parameter is not None
    assert constrained.types[0].parameter.constraints == (INT, STR)


def test_reversed_columns_are_not_falsely_correlated():
    first = generalize_signatures(
        ((INT, STR, applied("tuple", INT, STR)), (STR, INT, applied("tuple", STR, INT))),
        set(),
    )[0]
    assert len(first.parameters) == 2
    assert first.types[0] != first.types[1]
    assert first.types[2].children[1:] == first.types[:2]


def test_nested_constructor_positions_share_the_input_result_relationship():
    left, right = parameter("first:T"), parameter("second:T")
    rows = (
        (applied("dict", STR, applied("list", left)), left),
        (applied("dict", STR, applied("list", right)), right),
    )
    candidates = generalize_signatures(rows, set())
    assert len(candidates) == 1 and len(candidates[0].parameters) == 1
    first = candidates[0]
    assert first.types[0].children[2].children[1] == first.types[1]
    assert first.types[1] not in (left, right)


def test_identical_source_free_variable_is_still_lifted_to_fresh_binder():
    variable = parameter("caller:T")
    result = generalize_signatures(((variable, variable), (variable, variable)), {"_TowelT0"})
    assert len(result) == 1
    assert result[0].parameters[0].name != "_TowelT0"
    assert result[0].types[0] == result[0].types[1] != variable


def test_distinct_free_identities_with_same_spelling_are_not_merged():
    t1, t2, u1, u2 = (parameter(name) for name in ("a:T", "b:T", "a:U", "b:U"))
    result = generalize_signatures(
        ((t1, u1, applied("tuple", t1, u1)), (t2, u2, applied("tuple", t2, u2))), set()
    )[0]
    assert len(result.parameters) == 2
    assert result.types[0] != result.types[1]


def test_common_bound_is_preserved_without_constraint_promotion():
    bound = atom("collections.abc.Sized", "Sized")
    left, right = parameter("a:T", bound), parameter("b:T", bound)
    result = generalize_signatures(((left, left), (right, right)), set())
    assert len(result) == 1
    assert result[0].parameters[0].bound == bound
    assert result[0].parameters[0].constraints == ()


def test_common_constraints_are_preserved_even_if_declared_in_different_order():
    left = parameter("a:T", constraints=(STR, BYTES))
    right = parameter("b:T", constraints=(BYTES, STR))
    result = generalize_signatures(((left, left), (right, right)), set())
    assert len(result) == 1
    assert result[0].parameters[0].constraints == (STR, BYTES)


def test_free_variable_and_concrete_type_retain_existing_free_domain():
    free = parameter("a:T", bound=STR)
    result = generalize_signatures(((free, free), (STR, STR)), set())
    assert len(result) == 1
    assert result[0].parameters[0].bound == STR


def test_conflicting_free_domains_are_declined():
    left, right = parameter("a:T", STR), parameter("b:T", BYTES)
    assert generalize_signatures(((left, left), (right, right)), set()) == ()


def test_dependent_bound_or_constraint_cannot_escape_into_helper_declaration():
    outer = parameter("outer:T")
    for free in (
        parameter("inner:U", applied("list", outer)),
        parameter("inner:U", constraints=(outer, STR)),
    ):
        assert generalize_signatures(((free, free), (free, free)), set()) == ()


def test_different_constructors_generalize_whole_types_not_type_constructors():
    left, right = applied("list", INT), applied("set", STR)
    result = generalize_signatures(((left, left), (right, right)), set())
    assert len(result) == 2
    assert result[0].types[0].kind is TypeKind.ATOM
    assert result[0].types[0] == result[0].types[1]
    assert result[1].parameters[0].constraints == (left, right)


def test_different_constructors_containing_free_types_get_no_generic_constraints():
    left = applied("list", parameter("a:T"))
    right = applied("set", parameter("b:T"))
    result = generalize_signatures(((left, left), (right, right)), set())
    assert len(result) == 1
    assert result[0].parameters[0].constraints == ()


def test_literal_values_do_not_become_type_parameters_inside_literal():
    left = applied("Literal", TypeTerm(TypeKind.CONSTANT, "1", "1"))
    right = applied("Literal", TypeTerm(TypeKind.CONSTANT, "2", "2"))
    first = generalize_signatures(((left, left), (right, right)), set())[0]
    assert first.types[0].kind is TypeKind.ATOM
    assert first.types[0] == first.types[1]


def test_callable_argument_list_reuses_the_same_nested_input_binder():
    def callback(argument: TypeTerm) -> TypeTerm:
        return applied("Callable", TypeTerm(TypeKind.LIST, children=(argument,)), STR)

    first = generalize_signatures(
        ((callback(INT), INT, STR), (callback(FLOAT), FLOAT, STR)), set()
    )[0]
    assert len(first.parameters) == 1
    assert first.types[0].children[1].children[0] == first.types[1]


def test_different_callable_arities_abstract_the_callable_not_its_argument_list():
    left = applied("Callable", TypeTerm(TypeKind.LIST, children=(INT,)), STR)
    right = applied("Callable", TypeTerm(TypeKind.LIST, children=(INT, STR)), STR)
    first = generalize_signatures(((left, left), (right, right)), set())[0]
    assert first.types[0].kind is TypeKind.ATOM
    assert first.types[0] == first.types[1]


def test_return_only_variable_is_not_a_meaningful_generic_signature():
    assert generalize_signatures(((INT, STR), (INT, FLOAT)), set()) == ()
    variable = parameter("caller:T")
    assert generalize_signatures(((INT, variable), (INT, variable)), set()) == ()


def test_no_generalization_or_invalid_rows_produce_no_candidate():
    assert generalize_signatures((), set()) == ()
    assert generalize_signatures(((INT,), (STR,)), set()) == ()
    assert generalize_signatures(((INT, INT), (STR,)), set()) == ()
    assert generalize_signatures(((INT, INT), (INT, INT)), set()) == ()


def test_candidate_count_does_not_grow_exponentially_with_holes():
    rows = (
        (INT, STR, BYTES, applied("tuple", INT, STR, BYTES)),
        (STR, BYTES, INT, applied("tuple", STR, BYTES, INT)),
    )
    candidates = generalize_signatures(rows, set())
    assert len(candidates) == 2
    assert len(candidates[0].parameters) == 3
    assert all(len(parameter.constraints) == 2 for parameter in candidates[1].parameters)


def test_large_concrete_alternative_set_only_gets_unrestricted_candidate():
    rows = tuple((atom(f"Type{index}"), atom(f"Type{index}")) for index in range(5))
    result = generalize_signatures(rows, set())
    assert len(result) == 1


def test_import_alias_spelling_is_not_a_semantic_type_difference():
    first, second = atom("library.Value", "Value"), atom("library.Value", "Alias")
    assert generalize_signatures(((first, first), (second, second)), set()) == ()


def test_inputs_and_reserved_name_set_remain_unchanged_and_results_are_deterministic():
    rows = ((INT, INT), (STR, STR))
    reserved = {"_TowelT0", "_TowelT1"}
    first = generalize_signatures(rows, reserved)
    assert first == generalize_signatures(rows, reserved)
    assert reserved == {"_TowelT0", "_TowelT1"}
    assert rows == ((INT, INT), (STR, STR))
    assert first[0].parameters[0].name not in reserved


def test_host_class_parameter_stays_bound_while_method_parameters_are_freshened():
    host = parameter("class:Box:T")
    left, right = parameter("first:U"), parameter("second:V")
    result = generalize_signatures(
        (
            (applied("list", left), applied("tuple", host, left)),
            (applied("list", right), applied("tuple", host, right)),
        ),
        set(),
        retained_parameters=frozenset({"class:Box:T"}),
    )
    assert len(result) == 1 and len(result[0].parameters) == 1
    assert result[0].types[1].children[1] == host
    assert result[0].types[0].children[1] == result[0].types[1].children[2]


def test_implicit_receiver_can_supply_a_class_bound_result_without_new_parameters():
    host = parameter("class:Box:T")
    result = generalize_signatures(
        ((host,), (host,)), set(), retained_parameters=frozenset({"class:Box:T"})
    )
    assert len(result) == 1 and result[0].types == (host,) and result[0].parameters == ()
    assert generalize_signatures(((host,), (host,)), set()) == ()


def test_host_retention_does_not_capture_a_different_binder_with_the_same_spelling():
    host, source = parameter("class:Box:T"), parameter("method:T")
    result = generalize_signatures(
        ((source, applied("tuple", host, source)),) * 2,
        set(),
        retained_parameters=frozenset({"class:Box:T"}),
    )[0]
    assert result.types[1].children[1] == host
    assert result.types[0] not in {host, source}
    assert len(result.parameters) == 1

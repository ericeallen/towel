"""Binding identity, not annotation spelling, determines generic correspondence."""

import ast
from dataclasses import FrozenInstanceError
import sys
import textwrap

import pytest

from towel.unification.type_bindings import (
    TypeKind,
    TypeResolver,
    TypeTerm,
    render_type,
    required_imports,
    spellable,
    type_parameter_identities,
)


def resolver(
    source: str,
    host: str | None = None,
    file: str = "/project/site.py",
    line: int | None = None,
    host_class: str | None = None,
) -> TypeResolver:
    source = textwrap.dedent(source)
    host = source if host is None else textwrap.dedent(host)
    return TypeResolver(
        source,
        file,
        line or len(source.splitlines()),
        host,
        file if host == source else "/project/host.py",
        host_class=host_class,
    )


def resolve(source: str, annotation: str, host: str | None = None) -> TypeTerm | None:
    return resolver(source, host).resolve(ast.parse(annotation, mode="eval").body)


def rendered(term: TypeTerm | None) -> str:
    assert term is not None
    return ast.unparse(render_type(term))


def test_structured_builtins_keep_argument_and_return_relationships():
    term = resolve("def f():\n    pass", "dict[str, tuple[list[int], ...]]")
    assert term is not None and term.kind is TypeKind.APPLY
    assert rendered(term) == "dict[str, tuple[list[int], ...]]"
    assert term.children[0].name == "builtins.dict"


def test_aliases_have_equal_identity_and_host_specific_spelling():
    source = "from collections.abc import Callable as Function"
    left = resolve(source, "Function[[int, str], bool]", "import typing as t")
    right = resolve(source, "Function[[int, str], bool]", "from typing import Callable as C")
    assert left == right
    assert rendered(left) == "t.Callable[[int, str], bool]"
    assert rendered(right) == "C[[int, str], bool]"
    assert left is not None and left.children[1].kind is TypeKind.LIST


def test_legacy_collection_aliases_normalize_to_builtins():
    source = "from typing import List as L, Dict as D, Set as S"
    assert rendered(resolve(source, "D[str, L[S[int]]]", "")) == "dict[str, list[set[int]]]"


def test_a_typing_name_the_host_lacks_is_spelled_with_its_import():
    term = resolve("from typing import Sequence", "Sequence[int]", "")
    assert rendered(term) == "Sequence[int]"
    assert term is not None and required_imports([term]) == (("typing", "Sequence"),)
    # A host binding the name to something else cannot write the type at all.
    taken = resolve("from typing import Sequence", "Sequence[int]", "Sequence = list")
    assert taken is not None and not spellable(taken)


@pytest.mark.parametrize(
    "source",
    [
        "list = object()",
        "from other import *",
        "if condition:\n    list = object()",
        "def f(list):\n    pass",
    ],
)
def test_builtin_shadowing_at_the_site_declines(source):
    assert resolve(source, "list[int]", "") is None


def test_a_site_import_shadowing_a_builtin_names_the_import():
    term = resolve("from other import list", "list[int]", "")
    assert term is not None and term.children[0].name == "other.list" and not spellable(term)


@pytest.mark.parametrize("host", ["list = object()", "from other import list"])
def test_builtin_shadowed_in_the_host_is_spelled_by_its_typing_alias(host):
    term = resolve("", "list[int]", host)
    assert rendered(term) == "List[int]"
    assert term is not None and required_imports([term]) == (("typing", "List"),)


def test_a_host_star_import_leaves_even_builtins_unspellable():
    term = resolve("", "list[int]", "from other import *")
    assert term is not None and not spellable(term)


def test_shadowed_builtin_can_use_existing_host_builtin_alias():
    assert rendered(resolve("", "list[int]", "import builtins as b\nlist = 3")) == "b.list[int]"


def test_legacy_typevar_alias_bound_metadata_and_immutable_identity():
    source = "from typing_extensions import TypeVar as TV\nT = TV('T', bound=int)\ndef f(x: T):\n    pass"
    term = resolve(source, "list[T]", "")
    assert term is not None
    parameter = term.children[1].parameter
    assert parameter is not None
    assert parameter.bound == resolve("", "int")
    assert not parameter.constraints
    with pytest.raises(FrozenInstanceError):
        setattr(parameter, "identity", "changed")


def test_constraints_and_typevar_module_alias():
    source = "import typing as t\nT = t.TypeVar('T', str, bytes, covariant=True)"
    term = resolve(source, "T")
    assert term is not None and term.parameter is not None
    assert tuple(rendered(item) for item in term.parameter.constraints) == ("str", "bytes")


def test_same_module_typevar_shared_by_functions_retains_identity():
    source = "from typing import TypeVar\nT = TypeVar('T')\ndef f(x: T):\n    pass\ndef g(x: T):\n    pass"
    first = resolver(source, line=4).resolve(ast.Name(id="T"))
    second = resolver(source, line=6).resolve(ast.Name(id="T"))
    assert first is not None and first == second


def test_same_spelling_different_files_or_local_declarations_differ():
    source = "from typing import TypeVar\nT = TypeVar('T')"
    left = resolver(source, file="/a.py").resolve(ast.Name(id="T"))
    right = resolver(source, file="/b.py").resolve(ast.Name(id="T"))
    assert left is not None and right is not None and left != right
    local = "from typing import TypeVar\ndef f():\n    T = TypeVar('T')\n    pass\ndef g():\n    T = TypeVar('T')\n    pass"
    first = resolver(local, line=4).resolve(ast.Name(id="T"))
    second = resolver(local, line=7).resolve(ast.Name(id="T"))
    assert first is not None and second is not None and first != second


@pytest.mark.parametrize(
    "declaration",
    [
        "T = TypeVar('T', bound=list[U])",
        "T = TypeVar('T', U, int)",
        "T = TypeVar('T', bound=T)",
        "T = TypeVar('T', default=int)",
        "T = TypeVar('T', int)",
        "T = TypeVar('Different')",
        "T = TypeVar('T', bound=Any)",
        "T = TypeVar('T', bound=Foreign)",
        "T = ParamSpec('T')",
        "T = TypeVarTuple('T')",
    ],
)
def test_unsupported_parameter_declarations_decline(declaration):
    source = (
        "from typing import TypeVar, ParamSpec, TypeVarTuple, Any\nU = TypeVar('U')\n" + declaration
    )
    assert resolve(source, "T") is None


def test_typevar_rebinding_or_constructor_shadowing_declines():
    assert resolve("from typing import TypeVar\nT = TypeVar('T')\nT = int", "T") is None
    assert resolve("from typing import TypeVar\nTypeVar = factory\nT = TypeVar('T')", "T") is None


def test_attribute_and_nested_global_writes_invalidate_binding_proofs():
    source = "import typing as t\nt.Sequence = custom"
    assert resolve(source, "t.Sequence[int]") is None
    host = "def rebind():\n    global int\n    int = str"
    rebound = resolve("", "int", host)
    assert rebound is not None and not spellable(rebound)
    source = "from typing import TypeVar\ndef f():\n    T = TypeVar('T')\n    def rebind():\n        nonlocal T\n        T = int\n    pass"
    assert resolve(source, "T") is None


@pytest.mark.parametrize("annotation", ["Any", "Unknown", "Self", "ParamSpec", "TypeVarTuple"])
def test_dynamic_and_contextual_types_decline(annotation):
    assert resolve("from typing import Any, Self, ParamSpec, TypeVarTuple", annotation) is None


@pytest.mark.parametrize("annotation", ["list[Any]", "dict[str, Any]", "Callable[[Any], int]"])
def test_any_inside_a_type_is_the_programs_own(annotation):
    source = "from typing import Any, Callable"
    assert rendered(resolve(source, annotation)) == annotation


def test_nominal_imports_preserve_qualified_identity_across_aliases():
    source = "from domain.model import Value as V"
    host = "import domain.model as model"
    term = resolve(source, "list[V]", host)
    assert rendered(term) == "list[model.Value]"
    assert resolver(source, host).resolve_revealed("list[domain.model.Value]") == term
    elsewhere = resolve(source, "V", "from other.model import Value as V")
    assert elsewhere is not None and elsewhere.name == "domain.model.Value"
    assert not spellable(elsewhere)


def test_relative_imports_include_the_package_location():
    source = "from .model import Value"
    same = TypeResolver(source, "/project/site.py", 1, source, "/project/host.py")
    foreign = TypeResolver(source, "/project/site.py", 1, source, "/other/host.py")
    assert rendered(same.resolve(ast.Name(id="Value"))) == "Value"
    unreachable = foreign.resolve(ast.Name(id="Value"))
    assert unreachable is not None and not spellable(unreachable)


def test_same_module_class_can_be_forward_spelled_but_local_class_cannot():
    source = "def f(x: 'Value'):\n    pass\nclass Value:\n    pass"
    term = resolver(source, line=2).resolve(ast.Constant(value="Value"))
    assert rendered(term) == "Value"
    namesake = resolve(source, "Value", "class Value:\n    pass")
    assert namesake is not None and not spellable(namesake)
    local = "def f():\n    class Value:\n        pass\n    pass"
    assert resolve(local, "Value") is None


def test_union_optional_and_import_alias_order_are_canonical():
    left = resolve("from typing import Union, Optional", "Union[str, Optional[int]]")
    right = resolve("", "None | int | str | int")
    assert left == right
    assert left is not None and left.kind is TypeKind.UNION and len(left.children) == 3
    source = "from domain import A, B"
    first = resolve(source, "list[A] | list[B]", "from domain import A as X, B as Y")
    second = resolve(source, "list[B] | list[A]", "from domain import A as Y, B as X")
    assert first is not None and first == second


def test_literal_constants_are_preserved_without_evaluating_metadata():
    source = "from typing import Literal, Annotated"
    assert rendered(resolve(source, "Literal[-1, 'word', None]")) == "Literal[-1, 'word', None]"
    assert resolve(source, "Annotated[int, 'T']") is None


def test_oracle_typevar_decorations_are_checked_against_actual_scope():
    source = "from typing import TypeVar\nT = TypeVar('T')\ndef f(x: T):\n    pass"
    instance = resolver(source)
    expected = instance.resolve(ast.Name(id="T"))
    assert expected is not None
    assert instance.resolve_revealed("T`-1") == expected
    assert instance.resolve_revealed("T@f") == expected
    assert instance.resolve_revealed("T@other") is None
    assert instance.resolve_revealed("U`-1") is None
    assert rendered(instance.resolve_revealed("builtins.list[builtins.int]")) == "list[int]"


@pytest.mark.parametrize("text", ["def (value: T) -> T", "(T@f) -> T@f", "def (T`-1, /) -> T`-1"])
def test_revealed_fixed_callable_preserves_free_parameters(text):
    source = "from typing import TypeVar, Callable\nT = TypeVar('T')\ndef f():\n    pass"
    instance = resolver(source)
    term = instance.resolve_revealed(text)
    assert rendered(term) == "Callable[[T], T]"
    assert term is not None and term.children[1].children[0] == term.children[2]


@pytest.mark.parametrize(
    "text",
    [
        "def (*args: int) -> int",
        "def (*, value: int) -> int",
        "def (value: int = ...) -> int",
        "def (value: int =) -> int",
        "def [T <: int] (T) -> T",
        "Overload(def (x: int) -> int, def (x: str) -> str)",
    ],
)
def test_revealed_unsupported_callable_never_becomes_unrestricted_ellipsis(text):
    """What no parameter list states is only a spelling, for a type variable to stand for."""
    term = resolver("from typing import Callable").resolve_revealed(text)
    assert term is not None and term.kind is TypeKind.ATOM and not spellable(term)
    assert term == resolver("from typing import Callable").resolve_revealed(text)
    elsewhere = TypeResolver("", "/project/other.py", 1, "", "/project/other.py")
    assert elsewhere.resolve_revealed(text) != term  # Only the same file's spelling is the same.


@pytest.mark.parametrize(
    "text", ["def (int) -> Unknown", "def [T] (T) -> T", "def (value: int, [str) -> int"]
)
def test_revealed_callable_that_names_nothing_known_declines(text):
    assert resolver("from typing import Callable").resolve_revealed(text) is None


def test_revealed_callable_is_spelled_by_the_host_or_with_the_import_it_needs():
    fresh = resolver("").resolve_revealed("def (int) -> int")
    assert rendered(fresh) == "Callable[[int], int]"
    assert fresh is not None and required_imports([fresh]) == (("typing", "Callable"),)
    instance = resolver("", "from collections.abc import Callable as C")
    bound = instance.resolve_revealed("() -> int")
    assert rendered(bound) == "C[[], int]" and bound is not None and not required_imports([bound])


def test_revealed_literal_markers_preserve_strings_and_do_not_rewrite_unknown_markers():
    instance = resolver("from typing import Literal")
    # A declared literal is kept exactly, its text untouched by decoration stripping.
    assert rendered(instance.resolve_revealed("Literal['T@foreign']")) == "Literal['T@foreign']"
    assert rendered(instance.resolve_revealed("Literal['a,b:c]']")) == "Literal['a,b:c]']"
    declared = resolver("").resolve_revealed("Literal['word']")
    assert rendered(declared) == "Literal['word']"
    assert declared is not None and required_imports([declared]) == (("typing", "Literal"),)
    # One the checker inferred, marked ``?``, is the type its value belongs to.
    assert rendered(instance.resolve_revealed("Literal['T@foreign']?")) == "str"
    assert rendered(resolver("").resolve_revealed("Literal[3]?")) == "int"
    assert resolver("").resolve_revealed("Literal[Unknown]") is None
    assert resolver("").resolve_revealed("list[int]?") is None
    assert resolver("").resolve_revealed("str*") is None


def test_invalid_or_effectful_expressions_decline_without_execution():
    for annotation in ("factory()", "value[call()]", "int if flag else str", "lambda: int"):
        assert resolve("", annotation) is None
    assert resolver("broken syntax !").resolve(ast.Name(id="int")) is None
    assert resolver("").resolve_revealed("not valid syntax !") is None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 requires Python 3.12")
def test_pep695_function_and_class_binders_are_lexically_distinct():
    source = "class C[T: int]:\n    def f[U: (str, bytes)](self, value: T, other: U):\n        pass\ndef g[T](value: T):\n    pass"
    method = resolver(source, line=3)
    outer = method.resolve(ast.Name(id="T"))
    inner = method.resolve(ast.Name(id="U"))
    unrelated = resolver(source, line=5).resolve(ast.Name(id="T"))
    assert outer is not None and inner is not None and unrelated is not None
    assert outer != unrelated and outer.parameter is not None and inner.parameter is not None
    assert rendered(outer.parameter.bound) == "int"
    assert tuple(rendered(item) for item in inner.parameter.constraints) == ("str", "bytes")
    assert method.resolve_revealed("T@C") == outer
    assert method.resolve_revealed("T@f") is None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 requires Python 3.12")
@pytest.mark.parametrize(
    "parameters,annotation", [("T, U: list[T]", "U"), ("**P", "P"), ("*Ts", "Ts")]
)
def test_pep695_dependent_bounds_and_non_typevar_binders_decline(parameters, annotation):
    assert resolve(f"def f[{parameters}]():\n    pass", annotation) is None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 requires Python 3.12")
def test_pep695_body_rebinding_is_not_confused_with_the_type_binder():
    assert resolve("def f[T]():\n    T = int\n    pass", "T") is None


@pytest.mark.skipif(
    sys.version_info < (3, 13), reason="Type parameter defaults require Python 3.13"
)
def test_pep695_default_is_not_silently_discarded():
    assert resolve("def f[T = int]():\n    pass", "T") is None


@pytest.mark.parametrize(
    "base", ["Generic[T]", "G[T]", "Protocol[T]", "t.Generic[T]", "list[T]", "dict[str, T]"]
)
def test_class_parameters_include_explicit_and_proven_implicit_generic_bases(base):
    source = f"""\
from typing import Generic, Generic as G, Protocol, TypeVar
import typing as t
T = TypeVar("T")
U = TypeVar("U")
class Host({base}):
    def method(self, value: T, other: U) -> tuple[T, U]:
        return value, other
"""
    instance = resolver(source, host_class="Host")
    term = instance.resolve(ast.Name(id="T"))
    other = instance.resolve(ast.Name(id="U"))
    assert term is not None and other is not None
    assert instance.host_class_parameters == (term,)
    assert type_parameter_identities(term) == instance.host_class_parameter_identities
    assert type_parameter_identities(other).isdisjoint(instance.host_class_parameter_identities)
    compound = instance.resolve(ast.parse("tuple[T, U]", mode="eval").body)
    assert compound is not None
    assert type_parameter_identities(compound) == (
        type_parameter_identities(term) | type_parameter_identities(other)
    )


def test_generic_user_base_resolves_known_type_parameters():
    source = """\
from typing import Generic, TypeVar
T = TypeVar("T")
U = TypeVar("U")
class Base(Generic[T]):
    pass
class Host(Base[U]):
    def method(self, value: U) -> U:
        return value
"""
    instance = resolver(source, host_class="Host")
    term = instance.resolve(ast.Name(id="U"))
    assert term is not None and instance.host_class_parameters == (term,)


def test_shared_legacy_declaration_is_bound_separately_by_each_class():
    source = """\
from typing import Generic, TypeVar
T = TypeVar("T")
class First(Generic[T]):
    def method(self, value: T) -> T:
        return value
class Second(Generic[T]):
    def method(self, value: T) -> T:
        return value
def free(value: T) -> T:
    return value
"""
    first = resolver(source, host_class="First", line=5)
    second = resolver(source, host_class="Second", line=8)
    free = resolver(source, host_class="First", line=10)
    first_term = first.resolve(ast.Name(id="T"))
    second_term = second.resolve(ast.Name(id="T"))
    free_term = free.resolve(ast.Name(id="T"))
    assert first_term is not None and second_term is not None and free_term is not None
    assert len({first_term, second_term, free_term}) == 3
    assert first.host_class_parameter_identities.isdisjoint(second.host_class_parameter_identities)
    assert type_parameter_identities(free_term).isdisjoint(first.host_class_parameter_identities)


@pytest.mark.parametrize("decorator", ["", "    @classmethod\n", "    @staticmethod\n"])
def test_descriptor_kind_does_not_erase_lexical_class_parameters(decorator):
    source = 'from typing import Generic, TypeVar\nT = TypeVar("T")\nclass Host(Generic[T]):\n'
    source += decorator + "    def method(value: T) -> T:\n        return value\n"
    instance = resolver(source, host_class="Host")
    term = instance.resolve(ast.Name(id="T"))
    assert (
        term is not None
        and type_parameter_identities(term) == instance.host_class_parameter_identities
    )


def test_class_local_import_shadows_module_import_without_changing_identity():
    host = "from outer import Value\nclass Host:\n    from inner import Value\n    pass"
    local = resolver(host, host_class="Host").resolve(ast.Name(id="Value"))
    assert local is not None and local.name == "inner.Value" and rendered(local) == "Value"
    foreign = resolver("from outer import Value", host=host, host_class="Host")
    shadowed = foreign.resolve(ast.Name(id="Value"))
    assert shadowed is not None and shadowed.name == "outer.Value" and not spellable(shadowed)
    alias_host = "import outer as outside\n" + host
    alias = resolver("from outer import Value", host=alias_host, host_class="Host")
    assert rendered(alias.resolve(ast.Name(id="Value"))) == "outside.Value"


def test_class_shadowed_builtin_uses_only_an_existing_unshadowed_module_alias():
    host = "class Host:\n    int = str\n"
    shadowed = resolver("", host=host, host_class="Host").resolve(ast.Name(id="int"))
    assert shadowed is not None and not spellable(shadowed)
    alias = resolver("", host="import builtins as b\n" + host, host_class="Host")
    assert rendered(alias.resolve(ast.Name(id="int"))) == "b.int"
    # Supplying no class leaves the module helper's existing behavior intact.
    assert rendered(resolver("", host=host).resolve(ast.Name(id="int"))) == "int"


def test_fresh_parameter_domains_use_module_spelling_despite_class_shadowing():
    source = """\
from typing import TypeVar
from collections.abc import Sized as Bound
U = TypeVar("U", bound=Bound)
class Host:
    Bound = str
    def method(self, value: U) -> U:
        return value
"""
    instance = resolver(source, host_class="Host")
    term = instance.resolve(ast.Name(id="U"))
    assert term is not None and term.parameter is not None
    assert rendered(term.parameter.bound) == "Bound"
    assert term.parameter.bound is not None and term.parameter.bound.name == "collections.abc.Sized"
    assert instance.resolve(ast.Name(id="Bound")) is None


def test_class_only_bound_and_type_alias_decline_instead_of_escaping_to_module():
    """A fresh binder is declared at module scope, where a class-only name is not."""
    source = """\
from typing import TypeVar
class Host:
    from shapes import Sized as LocalBound
    U = TypeVar("U", bound=LocalBound)
    Alias = list[int]
    def method(self):
        pass
"""
    instance = resolver(source, host_class="Host")
    term = instance.resolve(ast.Name(id="U"))
    assert term is not None and term.parameter is not None and term.parameter.bound is not None
    assert term.parameter.bound.name == "shapes.Sized" and not spellable(term.parameter.bound)
    assert instance.resolve(ast.Name(id="Alias")) is None
    # A typing name is no escape: the module imports it afresh under its own name.
    typing_bound = resolver(source.replace("shapes", "collections.abc"), host_class="Host")
    typed = typing_bound.resolve(ast.Name(id="U"))
    assert typed is not None and typed.parameter is not None and typed.parameter.bound is not None
    assert rendered(typed.parameter.bound) == "Sized"
    assert required_imports([typed.parameter.bound]) == (("typing", "Sized"),)


@pytest.mark.parametrize(
    "source,host_class",
    [
        ("class Host: pass\nclass Host: pass", "Host"),
        ("class Other: pass", "Host"),
        ("class Outer:\n    class Host: pass", "Outer.Host"),
        ("class Host(factory()): pass", "Host"),
        ("class Host(Unresolved[T]): pass", "Host"),
        ("from typing import Generic\nfrom other import T\nclass Host(Generic[T]): pass", "Host"),
        ("from other import Base, T\nclass Host(Base[T]): pass", "Host"),
        (
            'from typing import Generic, TypeVar\nT = TypeVar("T")\nclass Host(Generic[T]):\n    T = int',
            "Host",
        ),
    ],
)
def test_unproven_class_hosts_or_binders_decline(source, host_class):
    instance = resolver(source, host_class=host_class)
    assert instance.resolve(ast.Name(id="int")) is None
    assert not instance.host_class_parameter_identities


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 requires Python 3.12")
def test_pep695_host_binder_is_retained_while_method_binder_remains_independent():
    source = "class Host[T]:\n    def method[U: int](self, value: T, other: U) -> tuple[T, U]:\n        return value, other"
    instance = resolver(source, host_class="Host")
    host = instance.resolve(ast.Name(id="T"))
    method = instance.resolve(ast.Name(id="U"))
    assert host is not None and method is not None and method.parameter is not None
    assert instance.host_class_parameters == (host,)
    assert type_parameter_identities(method).isdisjoint(instance.host_class_parameter_identities)
    assert rendered(method.parameter.bound) == "int"
    assert instance.resolve_revealed("T@Host") == host


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 requires Python 3.12")
def test_pep695_dependent_class_bound_or_class_local_method_bound_is_not_exported():
    dependent = "class Host[T, U: T]:\n    def method(self):\n        pass"
    assert resolver(dependent, host_class="Host").resolve(ast.Name(id="T")) is None
    local = "class Host:\n    from shapes import Sized as Local\n    def method[T: Local](self):\n        pass"
    term = resolver(local, host_class="Host").resolve(ast.Name(id="T"))
    assert term is not None and term.parameter is not None and term.parameter.bound is not None
    assert not spellable(term.parameter.bound)

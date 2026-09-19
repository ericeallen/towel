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


def test_required_typing_import_is_not_invented():
    assert resolve("from typing import Sequence", "Sequence[int]", "") is None


@pytest.mark.parametrize(
    "source,host",
    [
        ("list = object()", ""),
        ("", "list = object()"),
        ("from other import list", ""),
        ("from other import *", ""),
        ("", "from other import *"),
        ("if condition:\n    list = object()", ""),
        ("def f(list):\n    pass", ""),
    ],
)
def test_builtin_shadowing_declines(source, host):
    assert resolve(source, "list[int]", host) is None


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
    assert resolve("", "int", host) is None
    source = "from typing import TypeVar\ndef f():\n    T = TypeVar('T')\n    def rebind():\n        nonlocal T\n        T = int\n    pass"
    assert resolve(source, "T") is None


@pytest.mark.parametrize(
    "annotation", ["Any", "list[Any]", "Unknown", "Self", "ParamSpec", "TypeVarTuple"]
)
def test_dynamic_and_contextual_types_decline(annotation):
    assert resolve("from typing import Any, Self, ParamSpec, TypeVarTuple", annotation) is None


def test_nominal_imports_preserve_qualified_identity_across_aliases():
    source = "from domain.model import Value as V"
    host = "import domain.model as model"
    term = resolve(source, "list[V]", host)
    assert rendered(term) == "list[model.Value]"
    assert resolver(source, host).resolve_revealed("list[domain.model.Value]") == term
    assert resolve(source, "V", "from other.model import Value as V") is None


def test_relative_imports_include_the_package_location():
    source = "from .model import Value"
    same = TypeResolver(source, "/project/site.py", 1, source, "/project/host.py")
    foreign = TypeResolver(source, "/project/site.py", 1, source, "/other/host.py")
    assert rendered(same.resolve(ast.Name(id="Value"))) == "Value"
    assert foreign.resolve(ast.Name(id="Value")) is None


def test_same_module_class_can_be_forward_spelled_but_local_class_cannot():
    source = "def f(x: 'Value'):\n    pass\nclass Value:\n    pass"
    term = resolver(source, line=2).resolve(ast.Constant(value="Value"))
    assert rendered(term) == "Value"
    assert resolve(source, "Value", "class Value:\n    pass") is None
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
        "def (Any) -> int",
        "def (int) -> Unknown",
        "def [T] (T) -> T",
        "def (value: int, [str) -> int",
    ],
)
def test_revealed_unsupported_callable_never_becomes_unrestricted_ellipsis(text):
    assert resolver("from typing import Callable").resolve_revealed(text) is None


def test_revealed_callable_requires_existing_host_binding():
    assert resolver("").resolve_revealed("def (int) -> int") is None
    instance = resolver("", "from collections.abc import Callable as C")
    assert rendered(instance.resolve_revealed("() -> int")) == "C[[], int]"


def test_revealed_literal_markers_preserve_strings_and_do_not_rewrite_unknown_markers():
    instance = resolver("from typing import Literal")
    assert rendered(instance.resolve_revealed("Literal['T@foreign']?")) == "Literal['T@foreign']"
    assert rendered(instance.resolve_revealed("Literal['a,b:c]']?")) == "Literal['a,b:c]']"
    assert rendered(resolver("").resolve_revealed("Literal[3]?")) == "int"
    assert rendered(resolver("").resolve_revealed("Literal['word']")) == "str"
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
    assert foreign.resolve(ast.Name(id="Value")) is None
    alias_host = "import outer as outside\n" + host
    alias = resolver("from outer import Value", host=alias_host, host_class="Host")
    assert rendered(alias.resolve(ast.Name(id="Value"))) == "outside.Value"


def test_class_shadowed_builtin_uses_only_an_existing_unshadowed_module_alias():
    host = "class Host:\n    int = str\n"
    assert resolver("", host=host, host_class="Host").resolve(ast.Name(id="int")) is None
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
    source = """\
from typing import TypeVar
class Host:
    from collections.abc import Sized as LocalBound
    U = TypeVar("U", bound=LocalBound)
    Alias = list[int]
    def method(self):
        pass
"""
    instance = resolver(source, host_class="Host")
    assert instance.resolve(ast.Name(id="U")) is None
    assert instance.resolve(ast.Name(id="Alias")) is None


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
    local = "class Host:\n    from collections.abc import Sized as Local\n    def method[T: Local](self):\n        pass"
    assert resolver(local, host_class="Host").resolve(ast.Name(id="T")) is None

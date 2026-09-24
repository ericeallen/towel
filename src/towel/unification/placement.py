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

"""Whether a helper becomes a method of the class that holds both duplicates.

A helper is a method only when both blocks are methods of one unique
module-level class, every decorator on the source methods is known to
preserve the receiver, the receiver is the first parameter, and both methods
read an attribute of it; it is then class-private (``__extracted_func_0``), so
a class named only with underscores, which mangles nothing, takes none. No
other class ever takes a helper: methods of
sibling classes, of a parent and a child, or of classes in different modules
share a module-level helper that takes the receiver explicitly, as does
everything else (docs/DECISIONS.md, "A method helper lives in the class that
holds both duplicates"). This module also rewrites a rendered call into
method form and the helper signature to match.
"""

from __future__ import annotations

import ast

from pathlib import Path
from typing import (
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    cast,
)
from .models import (
    ClassInfo,
    ClassInsertionPlan,
    CodeBlockPair,
    FunctionNode,
    MethodInfo,
    MethodKind,
)
from .class_private import mangling_prefix
from .scope_analyzer import ScopeAnalyzer
from .module_bindings import ModuleBindings, dotted_name, global_bindings, import_origin
from .import_graph import ImportTimeCode, module_scope_statements
from .statement_facts import imported_binding_name
from .visitors import MethodCallRewriter, visit_as
from ..source_text import read_source

from .engine_state import EngineState

# Decorators that call the decorated function with the same receiver the
# source names. Anything else may rebind the first argument.
_RECEIVER_PRESERVING_DECORATORS = frozenset(
    {
        "property",
        "cached_property",
        "abstractmethod",
        "abstractproperty",
        "lru_cache",
        "cache",
        "contextmanager",
        "asynccontextmanager",
        "overload",
        "final",
        "override",
        "setter",
        "getter",
        "deleter",
    }
)


# Implicit classmethods and staticmethods that carry no decorator.
_IMPLICIT_RECEIVER_SPECIAL_METHODS = frozenset(
    {"__new__", "__init_subclass__", "__class_getitem__"}
)


def _dispatches_on(func: FunctionNode, implicit_param: str) -> bool:
    """Whether the method ever asks anything of its own receiver.

    Python binds no receiver when a method is reached through its class, so
    ``Formatter.as_dollars(None, 1.5)`` is an ordinary call with ``self`` set
    to ``None``, and it works for as long as the body never reads an attribute
    of ``self``. Code does this to reuse a method's logic without building an
    instance, most often in tests; a classmethod's function is reached the
    same way through ``__func__``.

    A helper reached as ``self.__extracted_func_0(...)`` would end that: the
    rewritten body demands a receiver the body it replaced did not, and the
    call raises ``AttributeError`` while every genuine instance goes on
    returning what it always did. A checker reports nothing, because the
    signature always said ``self`` was a ``Formatter``.

    So a method that never dispatches gets a helper that asks for no receiver
    at all, which is what the source method's own contract already was; see
    :meth:`HelperPlacement._choose_class_insertion` for where it goes. Reading
    an attribute is the condition, not mentioning the name: a body that merely
    passes ``self`` on still works with ``None``, and the helper still
    receives it as an ordinary argument.
    """
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == implicit_param
        for node in ast.walk(func)
    )


def _implicit_param_for(kind: MethodKind, first: MethodInfo, second: MethodInfo) -> Optional[str]:
    """The name the helper dispatches on, or ``None`` when it dispatches on nothing.

    A static helper has no receiver, whatever the methods it was taken from
    called theirs; carrying one of their names here would strip that argument
    from the helper and rewrite the call into method form again.
    """
    if kind == "staticmethod":
        return None
    return (
        first.implicit_param or second.implicit_param or ("self" if kind == "instance" else "cls")
    )


# What a receiver annotation may spell for the class, ``Self``, and a type variable.
_CLASS_OF = frozenset({"type", "Type", "typing.Type", "typing_extensions.Type"})
_SELF_TYPES = frozenset({"typing.Self", "typing_extensions.Self"})
_TYPE_VARIABLES = frozenset({"typing.TypeVar", "typing_extensions.TypeVar"})


def _admits_other_receivers(
    func: FunctionNode, kind: MethodKind, class_name: str, module: Optional[ast.Module]
) -> bool:
    """Whether the receiver's annotation declares receivers other than instances of the class.

    ``def m(self: HasV, n)`` declares that anything with a ``v`` may be the
    receiver, so ``Box.m(SimpleNamespace(v=10), 1)`` is a well-typed call, and a
    helper reached as ``self.__extracted_func_0(...)`` would raise
    ``AttributeError`` there. No annotation, the class itself (subscripted or
    not), ``Self``, or a type variable bound to the class declares instances
    alone; so does ``type[...]`` of one of them for a class method. Anything
    else is taken to admit others.
    """
    positional = [*func.args.posonlyargs, *func.args.args]
    if not positional or positional[0].annotation is None:
        return False
    annotation = _unquoted(positional[0].annotation)
    if kind == "classmethod":
        if not (
            isinstance(annotation, ast.Subscript) and dotted_name(annotation.value) in _CLASS_OF
        ):
            return True
        annotation = _unquoted(annotation.slice)
    return annotation is None or not _denotes_the_class(annotation, class_name, func, module, 0)


def _unquoted(annotation: ast.expr) -> Optional[ast.expr]:
    """The expression a string annotation spells, or the annotation itself; None if unparsable."""
    if not (isinstance(annotation, ast.Constant) and isinstance(annotation.value, str)):
        return annotation
    try:
        return ast.parse(annotation.value.strip(), mode="eval").body
    except SyntaxError:
        return None


def _denotes_the_class(
    annotation: ast.expr,
    class_name: str,
    func: FunctionNode,
    module: Optional[ast.Module],
    depth: int,
) -> bool:
    """Whether a receiver annotation names only the class: itself, ``Self``, or a bound type variable."""
    if depth > 4:
        return False
    if isinstance(annotation, ast.Subscript):
        # ``Box[T]`` is an instance of the class, whatever its type arguments.
        return dotted_name(annotation.value) == class_name
    spelled = dotted_name(annotation)
    if spelled is None or module is None:
        return spelled == class_name
    if spelled == class_name or _names_one_of(spelled, module, _SELF_TYPES):
        return True
    bound = _type_variable_bound(spelled, func, module)
    return bound is not None and _denotes_the_class(bound, class_name, func, module, depth + 1)


def _type_variable_bound(name: str, func: FunctionNode, module: ast.Module) -> Optional[ast.expr]:
    """The bound of ``name`` where it names a type variable: the function's own, or the module's.

    A type parameter of the function shadows the module's names. A module's
    type variable counts only when ``name = TypeVar(...)`` is the one way the
    module binds ``name``. The type-parameter nodes are inspected by
    attribute, since Python 3.11's AST has no ``ast.TypeVar``.
    """
    parameters: object = getattr(func, "type_params", ())
    for parameter in parameters if isinstance(parameters, list) else ():
        if getattr(parameter, "name", None) == name:
            bound: object = getattr(parameter, "bound", None)
            is_variable = type(parameter).__name__ == "TypeVar"
            return _unquoted(bound) if is_variable and isinstance(bound, ast.expr) else None
    statements = _module_scope_bindings(module, name)
    if len(statements) != 1 or not isinstance(statements[0], ast.Assign):
        return None
    call = statements[0].value
    if not (
        isinstance(call, ast.Call)
        and _names_one_of(dotted_name(call.func) or "", module, _TYPE_VARIABLES)
    ):
        return None
    bound = next((keyword.value for keyword in call.keywords if keyword.arg == "bound"), None)
    return None if bound is None else _unquoted(bound)


def _names_one_of(spelled: str, module: ast.Module, targets: FrozenSet[str]) -> bool:
    """Whether ``spelled`` names one of ``targets`` however the module runs.

    Every statement of the module's own scope that may bind its first name
    must be an absolute import that makes it one of them.
    """
    head, _, rest = spelled.partition(".")
    statements = _module_scope_bindings(module, head)
    return bool(statements) and all(
        (origin := _imported_origin(statement, head)) is not None
        and (f"{origin}.{rest}" if rest else origin) in targets
        for statement in statements
    )


def _imported_origin(statement: ast.stmt, name: str) -> Optional[str]:
    """What an absolute import statement binds ``name`` to, as a dotted name; None for anything else."""
    if not isinstance(statement, (ast.Import, ast.ImportFrom)):
        return None
    origins = {
        import_origin(statement, alias)
        for alias in statement.names
        if imported_binding_name(alias) == name
    }
    return origins.pop() if len(origins) == 1 else None


def _module_scope_bindings(module: ast.Module, name: str) -> List[ast.stmt]:
    """Every statement of the module's own scope that may bind ``name``, however conditionally.

    A star import may bind any name. A compound statement counts when
    anything within it stores the name, which can only over-count.
    """
    found: List[ast.stmt] = []
    for statement in module_scope_statements(module):
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            binds = statement.name == name
        elif isinstance(statement, (ast.Import, ast.ImportFrom)):
            binds = any(imported_binding_name(alias) in {name, None} for alias in statement.names)
        else:
            binds = any(
                isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store)
                for node in ast.walk(statement)
            )
        if binds:
            found.append(statement)
    return found


def _preserves_receiver(decorator: ast.expr) -> bool:
    """Whether a decorator is known to pass the receiver through unchanged."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id in _RECEIVER_PRESERVING_DECORATORS
    if isinstance(target, ast.Attribute):
        return target.attr in _RECEIVER_PRESERVING_DECORATORS
    return False


def _module_level_class(
    class_infos: Sequence[ClassInfo], file_path: str, name: str
) -> Optional[ClassInfo]:
    """The class ``name`` names in ``file_path`` when it names exactly one, at module level.

    A method is known only by its class's simple name, so a second class of
    that name anywhere in the file, nested ones included, leaves open which
    class statement holds it.
    """
    matches = [info for info in class_infos if info.file_path == file_path and info.name == name]
    return matches[0] if len(matches) == 1 and matches[0].qualname == name else None


def _assigned_through(function: FunctionNode, receiver: str) -> Dict[str, List[int]]:
    """The lines on which ``function``'s own scope assigns each attribute of ``receiver``."""
    lines: Dict[str, List[int]] = {}
    pending: List[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == receiver
        ):
            lines.setdefault(node.attr, []).append(node.lineno)
        pending.extend(ast.iter_child_nodes(node))
    return lines


def _class_body_names(owner: ast.ClassDef) -> Set[str]:
    """Names the class body itself binds or annotates: declarations no method can displace."""
    names: Set[str] = set()
    for statement in owner.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            names.add(statement.target.id)
        elif isinstance(statement, ast.Assign):
            names.update(t.id for t in statement.targets if isinstance(t, ast.Name))
    return names


def _instance_receiver(function: FunctionNode) -> Optional[str]:
    """The receiver an instance method assigns attributes through, or None for any other function."""
    if any(
        isinstance(decorator, ast.Name) and decorator.id in {"staticmethod", "classmethod"}
        for decorator in function.decorator_list
    ):
        return None
    positional = [*function.args.posonlyargs, *function.args.args]
    return positional[0].arg if positional else None


def _calls_helper(function: FunctionNode, helper_name: str) -> List[int]:
    """The lines of ``function`` that call ``helper_name`` as a method."""
    return sorted(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == helper_name
    )


def method_helper_position(
    source: str, class_name: str, helper: ast.FunctionDef, receiver: str
) -> Optional[int]:
    """Where a method helper goes so its class's attributes keep the declarations they had.

    mypy takes an attribute's type from the first assignment to it in the
    class, in the order the methods are written. A block that held that first
    assignment hands it to the helper, and the helper placed at the end of the
    class, as method helpers are, comes after every assignment the other
    methods still make: the first of those becomes the declaration.
    packaging's ``Tag`` is the case. ``__init__`` assigned ``_interpreter``
    from a ``str``; ``__setstate__`` still assigns it by unpacking a tuple of
    ``Any``; with the helper last, ``_interpreter`` was ``Any`` and every
    property returning it was refused under ``warn_return_any``.

    So when some attribute the helper assigns through its receiver had its
    first assignment in the block (no method before the first call assigns
    it) and another method after that call still assigns it, the helper goes
    right after the method holding the first call, where the block's
    assignment stood relative to the rest; or right before that method, when
    it is that method's own later assignment that must follow. An attribute
    the class body declares is fixed wherever the helper goes. None -- the end
    of the class -- when nothing needs the helper earlier, or when two
    attributes pull it both ways. ``source`` is the module with the calls
    already in place, ``receiver`` the helper's own receiver.

    The line returned is a 0-based index into the module's lines, before which
    the helper's lines are inserted.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    owners = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    if len(owners) != 1:
        return None
    owner = owners[0]
    wanted = set(_assigned_through(helper, receiver)) - _class_body_names(owner)
    if not wanted:
        return None
    members = [
        member
        for member in owner.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    first = next(
        (
            (index, lines[0])
            for index, member in enumerate(members)
            if (lines := _calls_helper(member, helper.name))
        ),
        None,
    )
    if first is None:
        return None
    first_index, first_call = first
    earlier: Set[str] = set()  # assigned by a method before the holder
    holder_before: Set[str] = set()  # assigned by the holder before its first call
    holder_after: Set[str] = set()  # assigned by the holder after that call
    later: Set[str] = set()  # assigned by a method after the holder
    for index, member in enumerate(members):
        own_receiver = _instance_receiver(member)
        if own_receiver is None:
            continue
        for attribute, lines in _assigned_through(member, own_receiver).items():
            if attribute not in wanted:
                continue
            if index < first_index:
                earlier.add(attribute)
            elif index > first_index:
                later.add(attribute)
            else:
                holder_before.update(attribute for line in lines if line < first_call)
                holder_after.update(attribute for line in lines if line > first_call)
    declared_first = wanted - earlier - holder_before  # the block held the first assignment
    if not declared_first & (holder_after | later):
        return None
    holder = members[first_index]
    if declared_first & holder_after:
        # The holder's own later assignment must follow the helper, so the
        # helper goes before the holder -- unless the holder assigns another
        # attribute first, whose declaration that would take away.
        if holder_before - earlier:
            return None
        return min([holder.lineno, *(d.lineno for d in holder.decorator_list)]) - 1
    return holder.end_lineno or holder.lineno


class _CallRenamer(ast.NodeTransformer):
    """Rename every call of one plain name to another, in place."""

    def __init__(self, old: str, new: str) -> None:
        self.old = old
        self.new = new

    def visit_Call(self, call: ast.Call) -> ast.AST:
        updated = cast(ast.Call, self.generic_visit(call))
        if isinstance(updated.func, ast.Name) and updated.func.id == self.old:
            updated.func.id = self.new
        return updated


class HelperPlacement(EngineState):
    """Helper Placement methods of the engine; see the module docstring."""

    @staticmethod
    def _function_contains_nonlocal(func: FunctionNode) -> bool:
        """Return True if the function body contains any nonlocal declarations."""

        for stmt in func.body:
            # Skip nested definitions; only consider nonlocal statements that belong
            # to the function itself. Nonlocals inside nested functions do not impact
            # whether the outer function may be safely extracted.
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for node in ast.walk(stmt):
                if isinstance(node, ast.Nonlocal):
                    return True
        return False

    def _declares_nonlocal(
        self, func: Optional[FunctionNode], scope_analyzer: Optional[ScopeAnalyzer]
    ) -> bool:
        """Return True if ``func`` declares any nonlocal variables of its own.

        Prefers the scope analyzer's precomputed nonlocal set for the function's
        scope; falls back to a direct body scan when no analyzer is available.
        """
        if func is None:
            return False
        if scope_analyzer:
            scope_id = scope_analyzer.node_scopes.get(func)
            if scope_id:
                return bool(scope_analyzer.nonlocal_vars.get(scope_id.scope_id, set()))
            return False
        return self._function_contains_nonlocal(func)

    @staticmethod
    def _decorator_name(decorator: ast.expr) -> Optional[str]:
        """Return the simple name for a decorator expression if it can be resolved."""

        if isinstance(decorator, ast.Name):
            return decorator.id
        if isinstance(decorator, ast.Attribute):
            return decorator.attr
        if isinstance(decorator, ast.Call):
            return HelperPlacement._decorator_name(decorator.func)
        return None

    @staticmethod
    def _has_decorator(function: ast.FunctionDef, name: str) -> bool:
        """Return True when the function already carries a decorator with the given name."""

        return any(HelperPlacement._decorator_name(dec) == name for dec in function.decorator_list)

    @staticmethod
    def _strip_decorator(function: ast.FunctionDef, name: str) -> None:
        """Remove any decorator whose resolved name matches ``name``."""

        function.decorator_list = [
            dec for dec in function.decorator_list if HelperPlacement._decorator_name(dec) != name
        ]

    @staticmethod
    def _ensure_leading_param(fn: ast.FunctionDef, param_name: str) -> None:
        """Ensure the positional-args list starts with ``param_name`` (preserving annotations)."""

        existing: Optional[ast.arg] = None
        remaining: List[ast.arg] = []
        for arg in fn.args.args:
            if arg.arg == param_name and existing is None:
                existing = arg
                continue
            if arg.arg == param_name:
                # Drop duplicate occurrences beyond the first
                continue
            remaining.append(arg)

        if existing is None:
            existing = ast.arg(arg=param_name)

        fn.args.args = [existing] + remaining

    @staticmethod
    def _retarget_helper_calls(node: ast.AST, original_name: str, final_name: str) -> ast.AST:
        """Rewrite the helper call sites to match a renamed extracted helper."""

        if original_name == final_name:
            return node
        return visit_as(_CallRenamer(original_name, final_name), node)

    def _prepare_extracted_method_signature(
        self,
        function: ast.FunctionDef,
        method_kind: MethodKind,
        implicit_param: Optional[str],
    ) -> None:
        """Normalize the extracted helper so it behaves like the requested method type."""

        if method_kind == "instance":
            name = implicit_param or "self"
            self._ensure_leading_param(function, name)
            # Strip any conflicting decorators that might have been synthesized earlier
            self._strip_decorator(function, "staticmethod")
            self._strip_decorator(function, "classmethod")
        elif method_kind == "classmethod":
            name = implicit_param or "cls"
            self._ensure_leading_param(function, name)
            self._strip_decorator(function, "staticmethod")
            if not self._has_decorator(function, "classmethod"):
                function.decorator_list.insert(0, ast.Name(id="classmethod", ctx=ast.Load()))
        elif method_kind == "staticmethod":
            self._strip_decorator(function, "classmethod")
            if not self._has_decorator(function, "staticmethod"):
                function.decorator_list.insert(0, ast.Name(id="staticmethod", ctx=ast.Load()))
        else:
            raise ValueError(f"Unsupported method kind: {method_kind}")

    def _rewrite_call_for_method(
        self,
        node: ast.AST,
        original_name: str,
        new_name: str,
        method_kind: Optional[MethodKind],
        implicit_param: Optional[str],
        class_name: Optional[str],
        receiver_parameter_index: Optional[int] = None,
        helper_parameter_count: Optional[int] = None,
    ) -> ast.AST:
        """Rewrite calls to the extracted helper so they use method dispatch semantics."""

        if method_kind is None:
            return node

        def remove_receiver(args: List[ast.expr], implicit_name: str) -> List[ast.expr]:
            if receiver_parameter_index is None:
                # Legacy manually constructed proposals may include a leading
                # receiver outside the helper signature. Its extra arity makes
                # it distinguishable from a receiver used as an ordinary operand.
                if (
                    helper_parameter_count is not None
                    and len(args) == helper_parameter_count + 1
                    and isinstance(args[0], ast.Name)
                    and args[0].id == implicit_name
                ):
                    return args[1:]
                return args
            if receiver_parameter_index >= len(args):
                return args
            receiver = args[receiver_parameter_index]
            if not isinstance(receiver, ast.Name) or receiver.id != implicit_name:
                raise ValueError("Helper receiver argument does not match method dispatch")
            return args[:receiver_parameter_index] + args[receiver_parameter_index + 1 :]

        rewriter = MethodCallRewriter(
            remove_receiver,
            (
                self._drop_implicit_keyword
                if receiver_parameter_index is not None
                else lambda keywords, _: keywords
            ),
            original_name=original_name,
            new_name=new_name,
            method_kind=method_kind,
            implicit_name=implicit_param,
            class_name=class_name,
        )
        return visit_as(rewriter, node)

    @staticmethod
    def _drop_implicit_keyword(
        keywords: List[ast.keyword], implicit_name: str
    ) -> List[ast.keyword]:
        """Drop the first keyword argument whose name matches ``implicit_name``."""

        result: List[ast.keyword] = []
        dropped = False
        for kw in keywords:
            if not dropped and kw.arg == implicit_name:
                dropped = True
                continue
            result.append(kw)
        return result

    @staticmethod
    def _method_class(
        func: Optional[FunctionNode],
        class_name: Optional[str],
        analyzer: Optional[ScopeAnalyzer],
    ) -> Optional[str]:
        """The class ``func`` is a method of, or None when it is merely nested in one."""
        if func is None or class_name is None:
            return None
        if analyzer is not None and func in analyzer.node_scopes and not analyzer.is_method(func):
            return None
        return class_name

    def _get_method_context(
        self,
        func: Optional[FunctionNode],
        class_name: Optional[str],
        module: Optional[ast.Module] = None,
    ) -> MethodInfo:
        """Return method metadata for ``func`` when it is defined inside ``class_name``.

        ``module`` is the module ``func`` is defined in, where a type variable
        or ``Self`` its receiver is annotated with is looked up.
        """

        if func is None or class_name is None:
            return MethodInfo(kind=None, implicit_param=None)

        kind: Optional[MethodKind] = None
        receiver_known = func.name not in _IMPLICIT_RECEIVER_SPECIAL_METHODS
        for decorator in func.decorator_list:
            name = self._decorator_name(decorator)
            if name == "staticmethod":
                kind = "staticmethod"
                break
            if name == "classmethod":
                kind = "classmethod"
                break
            if not _preserves_receiver(decorator):
                # A descriptor such as a lazy class property may pass the
                # class where the source spells ``self`` or ``cls``. The helper
                # must then receive that object explicitly, not by dispatch.
                receiver_known = False

        if kind is None:
            kind = "instance"

        implicit_param = None
        if kind in {"instance", "classmethod"}:
            positional = [*func.args.posonlyargs, *func.args.args]
            if positional:
                implicit_param = positional[0].arg
            else:
                # Nothing to dispatch on: a parameterless function in a class
                # body is a helper called while the body runs (pygments'
                # ``gen_rubystrings_rules()``), not a method.
                implicit_param = "self" if kind == "instance" else "cls"
                receiver_known = False
            # A function in a class body whose first parameter is not ``self``
            # is often a plain helper called while the class body runs
            # (pygments' ``fstring_rules(ttype)``); dispatching on that
            # parameter would call a method on an arbitrary object.
            if kind == "instance" and implicit_param != "self":
                receiver_known = False
            # A receiver annotated to admit other objects is one the helper
            # must take as an argument, as for an unknown receiver.
            if _admits_other_receivers(func, kind, class_name, module):
                receiver_known = False
            if implicit_param is not None and not _dispatches_on(func, implicit_param):
                # The method never asks anything of its receiver, so the helper
                # must not either. See :func:`_dispatches_on`.
                kind, implicit_param = "staticmethod", None

        return MethodInfo(kind=kind, implicit_param=implicit_param, receiver_known=receiver_known)

    @staticmethod
    def _module_bindings(file_path: str, sources: Mapping[str, str]) -> Optional[ModuleBindings]:
        """What ``file_path`` binds at its top level, read from ``sources`` or from disk.

        The pair under evaluation carries the text its own modules were
        parsed from, which is the text the class index describes; any other
        module is read from the file.
        """
        source = sources.get(file_path)
        if source is None:
            try:
                source = read_source(file_path)
            except (OSError, UnicodeError, ValueError):
                return None
        return global_bindings(source)

    def _can_host(self, info: ClassInfo, sources: Mapping[str, str]) -> bool:
        """Whether a helper placed in ``info``'s body stays a plain member of the class.

        See :meth:`ModuleBindings.refuses_helper` for what rules a class out.
        """
        table = self._module_bindings(info.file_path, sources)
        return table is not None and table.refuses_helper(info.qualname) is None

    def _hosts_method_helpers(self, info: ClassInfo, sources: Mapping[str, str]) -> bool:
        """Whether a helper placed in the class's body stays what its methods reach.

        A metaclass that wraps every callable of the namespace, or a base whose
        ``__init_subclass__`` registers them, would wrap or register the helper
        too, and a ``__getattribute__`` on the class's method resolution order
        intercepts ``self.__extracted_func_0`` itself: a forwarding proxy looks
        it up on another object (:meth:`ImportTimeCode.hosts_method_helpers`).
        The helper then stays at module level and takes the receiver as an
        argument.
        """
        source = sources.get(info.file_path)
        try:
            code = ImportTimeCode(
                source if source is not None else read_source(info.file_path),
                path=Path(info.file_path),
                cache=self.import_graph,
            )
        except (OSError, UnicodeError, SyntaxError, ValueError):
            return False
        return code.hosts_method_helpers(info.qualname)

    def _choose_class_insertion(
        self,
        pair: CodeBlockPair,
        method_info1: MethodInfo,
        method_info2: MethodInfo,
        class_infos: List[ClassInfo],
    ) -> Optional[ClassInsertionPlan]:
        """The class whose method the helper becomes, or None for a module-level helper.

        Only the class whose methods hold both duplicates can take it. A
        method added to any other class, such as a base the two classes
        share, is inherited by every class deriving from it, including ones
        outside the project that Towel cannot see; the choice of base may not
        be unique; and the class would gain a member where it held none of
        the code. Blocks shared by sibling classes, by a parent and a child,
        or by classes in different modules get the module-level helper that
        takes the receiver as an argument, and whether it belongs in a class
        is left to whoever reviews the change (docs/DECISIONS.md, "Towel does
        not change externally visible class design").
        """
        if not (method_info1.receiver_known and method_info2.receiver_known):
            return None
        class_name = pair.class1_name
        if class_name is None or pair.is_cross_file or pair.class2_name != class_name:
            return None
        host = _module_level_class(class_infos, pair.file_path, class_name)
        if host is None:
            return None
        k1, k2 = method_info1.kind, method_info2.kind
        # A block with no method context (a function nested inside a method, or
        # one outside any class) has no receiver to dispatch on; its helper
        # stays at module level even when the block lexically sits in a class.
        if k1 is None or k2 is None:
            return None
        if "staticmethod" in (k1, k2):
            # A helper that dispatches on nothing is a module-level function.
            # As a static method it had to be reached through something, and
            # nothing a method can spell is sure to be its class: the class's
            # name may be a parameter, deleted, rebound by a ``global``,
            # mangled (``class __C``), bound to whatever a decorator returned,
            # or not bound yet while the class body calls the method; a
            # metaclass sees the lookup; and ``__class__``, which is always the
            # class, is a name mypy does not know. A module-level helper is
            # defined before the first class, and its call is a plain name
            # lookup the method's own names cannot shadow. When one method
            # dispatches and the other does not, the static form still serves
            # both, since a block that uses the receiver takes it as an
            # ordinary argument.
            return None
        if k1 != k2:
            return None
        if mangling_prefix(class_name) is None:
            # The helper is class-private, ``__extracted_func_0``, so that no
            # subclass can reach it; a class named only with underscores
            # (``class __``) mangles nothing, and there it would be an
            # ordinary member any subclass may override.
            return None
        sources = {pair.file_path: pair.source1}
        if not self._can_host(host, sources) or not self._hosts_method_helpers(host, sources):
            return None
        return ClassInsertionPlan(
            class_name=class_name,
            file_path=pair.file_path,
            method_kind=k1,
            implicit_param=_implicit_param_for(k1, method_info1, method_info2),
        )

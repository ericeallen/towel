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

"""Whether a helper becomes a method, and of which class.

A helper is a method only when both blocks are methods of one unique
module-level class, or of classes with a unique module-level common
ancestor, every decorator on the source methods is known to preserve the
receiver, the receiver is the first parameter, and both methods read an
attribute of it. Base classes are resolved
the way the referencing module resolves them at the point the class
statement runs, through the module's own bindings and unconditional
imports, never by name across the project. Everything else gets a
module-level helper that takes the receiver explicitly. This module also
rewrites a rendered call into method form and the helper signature to
match.
"""

from __future__ import annotations

import ast

from collections import deque
from typing import (
    Callable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
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
from .scope_analyzer import ScopeAnalyzer
from .module_bindings import ModuleBindings, global_bindings
from .import_graph import (
    imported_definition_sites,
    relative_import_levels,
    relative_imports_resolve_alike,
)
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

    A helper reached as ``self._extracted_func_0(...)`` would end that: the
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


def _preserves_receiver(decorator: ast.expr) -> bool:
    """Whether a decorator is known to pass the receiver through unchanged."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id in _RECEIVER_PRESERVING_DECORATORS
    if isinstance(target, ast.Attribute):
        return target.attr in _RECEIVER_PRESERVING_DECORATORS
    return False


def _unique_module_level_class(class_infos: Sequence[ClassInfo], file_path: str, name: str) -> bool:
    """Whether ``name`` names exactly one class in ``file_path`` and it is module-level."""
    matches = [info for info in class_infos if info.file_path == file_path and info.name == name]
    return len(matches) == 1 and matches[0].qualname == name


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
        self, func: Optional[FunctionNode], class_name: Optional[str]
    ) -> MethodInfo:
        """Return method metadata for ``func`` when it is defined inside ``class_name``."""

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
            if implicit_param is not None and not _dispatches_on(func, implicit_param):
                # The method never asks anything of its receiver, so the helper
                # must not either. See :func:`_dispatches_on`.
                kind, implicit_param = "staticmethod", None

        return MethodInfo(kind=kind, implicit_param=implicit_param, receiver_known=receiver_known)

    @staticmethod
    def _class_info_key(info: ClassInfo) -> Tuple[str, str]:
        """Return a stable identifier for a class definition."""

        return (info.file_path, info.qualname)

    def _find_class_info_by_name(
        self, class_infos: List[ClassInfo], file_path: str, class_name: str
    ) -> Optional[ClassInfo]:
        """Locate class metadata using its defining file and simple name."""

        matches = [
            info for info in class_infos if info.file_path == file_path and info.name == class_name
        ]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        # Prefer the innermost definition (longest qualname) when duplicates exist.
        matches.sort(key=lambda info: info.qualname.count("."), reverse=True)
        return matches[0]

    @staticmethod
    def _module_bindings(file_path: str, sources: Mapping[str, str]) -> Optional[ModuleBindings]:
        """What ``file_path`` binds at its top level, read from ``sources`` or from disk.

        The pair under evaluation carries the text its own modules were
        parsed from, which is the text the class index describes; a module
        reached only by walking a class hierarchy is read from the file,
        the way an imported base class already is.
        """
        source = sources.get(file_path)
        if source is None:
            try:
                source = read_source(file_path)
            except (OSError, UnicodeError, ValueError):
                return None
        return global_bindings(source)

    def _find_class_info_for_base(
        self,
        class_infos: List[ClassInfo],
        base_name: str,
        *,
        referencing_file: str,
        referencing_qualname: str,
        sources: Mapping[str, str],
    ) -> Optional[ClassInfo]:
        """Resolve a base-class reference where the class statement making it runs.

        A module binds its globals as it runs, so what a base name denotes
        belongs to a position, not to the module as a whole: an ordinary
        ``Base = object`` between two subclasses leaves the second
        inheriting ``object`` while the first still inherits the class the
        name held before. The binding the name has at the referencing class
        statement therefore decides the answer, and it must be either a
        ``class`` statement of this module (a base written as
        ``Outer.Inner`` is that class's qualname) or one of the module's own
        unconditional imports, in whose module the class is then looked up.
        A project may define several classes with one name (oauthlib has a
        ``BaseEndpoint`` per protocol), so a name is never matched across
        the project, and a reference whose binding cannot be established, or
        that resolves to no single class, contributes no ancestor.
        """
        table = self._module_bindings(referencing_file, sources)
        if table is None:
            return None
        order = table.class_orders.get(referencing_qualname)
        if order is None:
            return None
        binding = table.in_effect(base_name.split(".")[0], order)
        if binding is None:
            return None
        if binding.class_qualname is not None:
            root = binding.class_qualname
            if base_name != root and not base_name.startswith(f"{root}."):
                return None
            local = [
                info
                for info in class_infos
                if info.file_path == referencing_file and info.qualname == base_name
            ]
            found = local[0] if len(local) == 1 else None
        elif not binding.is_import:
            return None
        else:
            sites = imported_definition_sites(referencing_file, base_name, self.import_graph)
            if not sites:
                return None
            matches = [
                info
                for info in class_infos
                if (self.import_graph.resolve(info.file_path), info.qualname) in sites
            ]
            found = matches[0] if len(matches) == 1 else None
        if found is None or self._decorated_out_of_reach(found, sources):
            return None
        return found

    def _decorated_out_of_reach(self, info: ClassInfo, sources: Mapping[str, str]) -> bool:
        """Whether a decorator may have bound the class's name to something other than it.

        ``@register class Base:`` binds ``Base`` to whatever ``register``
        returns, which a subclass then inherits from; only decorators known to
        keep the class and its namespace let the class statement stand for it.
        """
        if "." in info.qualname:
            return False  # A nested class is found through its module-level owner.
        table = self._module_bindings(info.file_path, sources)
        return table is None or not table.keeps_namespace(info.qualname)

    def _can_host(self, info: ClassInfo, sources: Mapping[str, str]) -> bool:
        """Whether a helper placed in ``info``'s body stays a plain member of the class.

        See :meth:`ModuleBindings.refuses_helper` for what rules a class out.
        """
        table = self._module_bindings(info.file_path, sources)
        return table is not None and table.refuses_helper(info.qualname) is None

    def _ancestor_depths(
        self,
        class_info: ClassInfo,
        class_infos: List[ClassInfo],
        sources: Mapping[str, str],
    ) -> List[Tuple[ClassInfo, int]]:
        """Each ancestor with how many base-class steps away it is, nearest first."""
        found: List[Tuple[ClassInfo, int]] = []
        # The starting class is seeded: bases are resolved by name through
        # imports, so a chain can appear to return to where it began, and it
        # would otherwise be recorded as its own ancestor at depth two.
        visited: Set[Tuple[str, str]] = {self._class_info_key(class_info)}
        queue: deque[Tuple[ClassInfo, int]] = deque([(class_info, 0)])

        while queue:
            current, depth = queue.popleft()
            for base_name in current.bases:
                base_info = self._find_class_info_for_base(
                    class_infos,
                    base_name,
                    referencing_file=current.file_path,
                    referencing_qualname=current.qualname,
                    sources=sources,
                )
                if base_info is None:
                    continue
                key = self._class_info_key(base_info)
                if key in visited:
                    continue
                visited.add(key)
                found.append((base_info, depth + 1))
                queue.append((base_info, depth + 1))

        found.sort(key=lambda item: item[1])
        return found

    def _find_common_ancestor(
        self,
        class1: Tuple[str, str],
        class2: Tuple[str, str],
        class_infos: List[ClassInfo],
        sources: Mapping[str, str],
        admits: Callable[[ClassInfo], bool] = lambda info: True,
    ) -> Optional[ClassInfo]:
        """The shared ancestor nearest to both classes, or None when they share none.

        Several classes can be ancestors of both. Taking the first found from
        one side made the answer depend on which class the pair happened to
        present first: two classes whose common ancestors are ``Mid`` and its
        own base ``Root`` were given ``Mid`` in one order and ``Root`` in the
        other. Runtime dispatch is indifferent, since every common ancestor is
        on both classes' method resolution orders, but the choice decides what
        the helper's receiver is, and a nearer ancestor states a narrower type
        that exposes more of what the body may use. So the candidates are
        ranked by their greatest distance from either class, then by their
        total distance, then by where they are defined, which is an order both
        sides agree on. An ancestor ``admits`` refuses is passed over like one
        that cannot host, and a farther one may still serve.
        """
        file1, name1 = class1
        file2, name2 = class2

        info1 = self._find_class_info_by_name(class_infos, file1, name1)
        info2 = self._find_class_info_by_name(class_infos, file2, name2)
        if info1 is None or info2 is None:
            return None

        key1 = self._class_info_key(info1)
        key2 = self._class_info_key(info2)
        reachable1 = {
            self._class_info_key(info): depth
            for info, depth in [(info1, 0), *self._ancestor_depths(info1, class_infos, sources)]
        }
        shared: List[Tuple[int, int, Tuple[str, str], ClassInfo]] = []
        for info, depth2 in [(info2, 0), *self._ancestor_depths(info2, class_infos, sources)]:
            key = self._class_info_key(info)
            depth1 = reachable1.get(key)
            if depth1 is None or (key == key1 and key == key2):
                continue  # Identical class; handled elsewhere.
            if not self._can_host(info, sources) or not admits(info):
                # A farther ancestor is on both method resolution orders too.
                continue
            shared.append((max(depth1, depth2), depth1 + depth2, key, info))
        if not shared:
            return None
        return min(shared, key=lambda candidate: candidate[:3])[3]

    def _choose_class_insertion(
        self,
        pair: CodeBlockPair,
        method_info1: MethodInfo,
        method_info2: MethodInfo,
        class_infos: List[ClassInfo],
    ) -> Optional[ClassInsertionPlan]:
        """Determine whether the helper should be inserted into a class context."""
        # Both blocks must sit in methods of the same kind.
        if not (method_info1.receiver_known and method_info2.receiver_known):
            return None
        if pair.class1_name is not None and not _unique_module_level_class(
            class_infos, pair.file_path, pair.class1_name
        ):
            return None
        if pair.class2_name is not None and not _unique_module_level_class(
            class_infos, pair.file_path2, pair.class2_name
        ):
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
        if pair.class1_name is None or pair.class2_name is None:
            return None

        file1 = pair.file_path
        file2 = pair.file_path2

        # At this point we know effective_kind is valid because we've already validated k1/k2
        effective_kind: MethodKind = k1
        sources = {file1: pair.source1, file2: pair.source2}
        if file1 == file2 and pair.class1_name == pair.class2_name:
            own = self._find_class_info_by_name(class_infos, file1, pair.class1_name)
            if own is None or not self._can_host(own, sources):
                return None
            implicit_param = _implicit_param_for(effective_kind, method_info1, method_info2)
            return ClassInsertionPlan(
                class_name=pair.class1_name,
                file_path=file1,
                method_kind=effective_kind,
                implicit_param=implicit_param,
            )

        # The modules the pair was parsed from answer for their own bindings
        # without being read again, and answer for the text the class index
        # describes rather than for whatever is on disk now. A method helper
        # runs the template's relative imports in the ancestor's module, so
        # an ancestor in another package would import other modules.
        levels = relative_import_levels(pair.block1_nodes)
        ancestor = self._find_common_ancestor(
            (file1, pair.class1_name),
            (file2, pair.class2_name),
            class_infos,
            sources,
            admits=lambda info: relative_imports_resolve_alike(
                (info.file_path, file1, file2), levels
            ),
        )
        if ancestor is None or not _unique_module_level_class(
            class_infos, ancestor.file_path, ancestor.name
        ):
            return None

        implicit_param = _implicit_param_for(effective_kind, method_info1, method_info2)

        return ClassInsertionPlan(
            class_name=ancestor.name,
            file_path=ancestor.file_path,
            method_kind=effective_kind,
            implicit_param=implicit_param,
        )

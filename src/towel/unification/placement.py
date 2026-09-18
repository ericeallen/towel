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
receiver, and the receiver is the first parameter. Base classes are resolved
the way the referencing module resolves them, through its own unconditional
imports, never by name across the project. Everything else gets a
module-level helper that takes the receiver explicitly. This module also
rewrites a rendered call into method form and the helper signature to
match.
"""

from __future__ import annotations

import ast

from collections import deque
from pathlib import Path
from typing import List, Literal, Optional, Sequence, Set, Tuple, cast
from .models import ClassInfo, ClassInsertionPlan, CodeBlockPair, FunctionNode, MethodInfo
from .scope_analyzer import ScopeAnalyzer
from .semantic_safety import imported_definition_sites
from .visitors import MethodCallRewriter

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
    def _has_decorator(fn: ast.FunctionDef, name: str) -> bool:
        """Return True when the function already carries a decorator with the given name."""

        return any(HelperPlacement._decorator_name(dec) == name for dec in fn.decorator_list)

    @staticmethod
    def _strip_decorator(fn: ast.FunctionDef, name: str) -> None:
        """Remove any decorator whose resolved name matches ``name``."""

        fn.decorator_list = [
            dec for dec in fn.decorator_list if HelperPlacement._decorator_name(dec) != name
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

        class _CallRenamer(ast.NodeTransformer):
            def __init__(self, old: str, new: str) -> None:
                self.old = old
                self.new = new

            def visit_Call(self, call: ast.Call) -> ast.AST:
                updated = cast(ast.Call, self.generic_visit(call))
                if isinstance(updated.func, ast.Name) and updated.func.id == self.old:
                    updated.func.id = self.new
                return updated

        return cast(ast.AST, _CallRenamer(original_name, final_name).visit(node))

    def _prepare_extracted_method_signature(
        self,
        fn: ast.FunctionDef,
        method_kind: Literal["instance", "classmethod", "staticmethod"],
        implicit_param: Optional[str],
    ) -> None:
        """Normalize the extracted helper so it behaves like the requested method type."""

        if method_kind == "instance":
            name = implicit_param or "self"
            self._ensure_leading_param(fn, name)
            # Strip any conflicting decorators that might have been synthesized earlier
            self._strip_decorator(fn, "staticmethod")
            self._strip_decorator(fn, "classmethod")
        elif method_kind == "classmethod":
            name = implicit_param or "cls"
            self._ensure_leading_param(fn, name)
            self._strip_decorator(fn, "staticmethod")
            if not self._has_decorator(fn, "classmethod"):
                fn.decorator_list.insert(0, ast.Name(id="classmethod", ctx=ast.Load()))
        elif method_kind == "staticmethod":
            self._strip_decorator(fn, "classmethod")
            if not self._has_decorator(fn, "staticmethod"):
                fn.decorator_list.insert(0, ast.Name(id="staticmethod", ctx=ast.Load()))
        else:
            raise ValueError(f"Unsupported method kind: {method_kind}")

    def _rewrite_call_for_method(
        self,
        node: ast.AST,
        original_name: str,
        new_name: str,
        method_kind: Optional[Literal["instance", "classmethod", "staticmethod"]],
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
        return cast(ast.AST, rewriter.visit(node))

    @staticmethod
    def _drop_implicit_positional(args: List[ast.expr], implicit_name: str) -> List[ast.expr]:
        """Drop the first positional argument matching ``implicit_name`` if present."""

        result: List[ast.expr] = []
        dropped = False
        for arg in args:
            if not dropped and isinstance(arg, ast.Name) and arg.id == implicit_name:
                dropped = True
                continue
            result.append(arg)
        return result

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

        kind: Optional[Literal["instance", "classmethod", "staticmethod"]] = None
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

        return MethodInfo(kind=kind, implicit_param=implicit_param, receiver_known=receiver_known)

    @staticmethod
    def _resolve_base_name(expr: ast.expr) -> Optional[str]:
        """Resolve a base-class expression into its dotted name when feasible."""

        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            parts: List[str] = []
            current: ast.expr = expr
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                return ".".join(reversed(parts))
        return None

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

    def _find_class_info_for_base(
        self,
        class_infos: List[ClassInfo],
        base_name: str,
        *,
        referencing_file: str,
    ) -> Optional[ClassInfo]:
        """Resolve a base-class reference as the referencing module would.

        A base written as ``Outer.Inner`` is the class of that qualname in
        the same module; otherwise the name must be bound by one of the
        module's own imports, and the class is looked up in the module that
        import denotes. A project may define several classes with one name
        (oauthlib has a ``BaseEndpoint`` per protocol), so a name is never
        matched across the project, and a reference that resolves to no
        single class contributes no ancestor.
        """
        local = [
            info
            for info in class_infos
            if info.file_path == referencing_file and info.qualname == base_name
        ]
        if local:
            return local[0] if len(local) == 1 else None
        sites = imported_definition_sites(referencing_file, base_name, self.import_graph)
        if not sites:
            return None
        matches = [
            info for info in class_infos if (Path(info.file_path).resolve(), info.qualname) in sites
        ]
        return matches[0] if len(matches) == 1 else None

    def _collect_class_ancestors(
        self, class_info: ClassInfo, class_infos: List[ClassInfo]
    ) -> List[ClassInfo]:
        """Return ancestors starting from the nearest base class."""

        ancestors: List[Tuple[int, ClassInfo]] = []
        visited: Set[Tuple[str, str]] = set()
        queue: deque[Tuple[ClassInfo, int]] = deque([(class_info, 0)])

        while queue:
            current, depth = queue.popleft()
            for base_name in current.bases:
                base_info = self._find_class_info_for_base(
                    class_infos, base_name, referencing_file=current.file_path
                )
                if base_info is None:
                    continue
                key = self._class_info_key(base_info)
                if key in visited:
                    continue
                visited.add(key)
                ancestors.append((depth + 1, base_info))
                queue.append((base_info, depth + 1))

        ancestors.sort(key=lambda item: item[0])
        return [info for _depth, info in ancestors]

    def _find_common_ancestor(
        self,
        class1: Tuple[str, str],
        class2: Tuple[str, str],
        class_infos: List[ClassInfo],
    ) -> Optional[ClassInfo]:
        """Return the nearest shared ancestor class for two class definitions."""

        file1, name1 = class1
        file2, name2 = class2

        info1 = self._find_class_info_by_name(class_infos, file1, name1)
        info2 = self._find_class_info_by_name(class_infos, file2, name2)
        if info1 is None or info2 is None:
            return None

        key1 = self._class_info_key(info1)
        key2 = self._class_info_key(info2)

        chain1 = [info1] + self._collect_class_ancestors(info1, class_infos)
        chain2 = [info2] + self._collect_class_ancestors(info2, class_infos)
        lookup2 = {self._class_info_key(info): info for info in chain2}

        for info in chain1:
            key = self._class_info_key(info)
            if key in lookup2:
                if key == key1 and key == key2:
                    # Identical class; handled elsewhere.
                    continue
                return lookup2[key]
        return None

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
            class_infos, pair.file_path2 or pair.file_path, pair.class2_name
        ):
            return None
        k1, k2 = method_info1.kind, method_info2.kind
        # A block with no method context (a function nested inside a method, or
        # one outside any class) has no receiver to dispatch on; its helper
        # stays at module level even when the block lexically sits in a class.
        if k1 is None or k2 is None or k1 != k2:
            return None
        if pair.class1_name is None or pair.class2_name is None:
            return None

        file1 = pair.file_path
        file2 = pair.file_path2 or pair.file_path

        # At this point we know effective_kind is valid because we've already validated k1/k2
        effective_kind: Literal["instance", "classmethod", "staticmethod"] = k1
        if file1 == file2 and pair.class1_name == pair.class2_name:
            implicit_param = method_info1.implicit_param or method_info2.implicit_param
            if effective_kind == "instance" and not implicit_param:
                implicit_param = "self"
            if effective_kind == "classmethod" and not implicit_param:
                implicit_param = "cls"
            return ClassInsertionPlan(
                class_name=pair.class1_name,
                file_path=file1,
                method_kind=effective_kind,
                implicit_param=implicit_param,
            )

        ancestor = self._find_common_ancestor(
            (file1, pair.class1_name),
            (file2, pair.class2_name),
            class_infos,
        )
        if ancestor is None or not _unique_module_level_class(
            class_infos, ancestor.file_path, ancestor.name
        ):
            return None

        implicit_param = method_info1.implicit_param or method_info2.implicit_param
        if effective_kind == "instance" and not implicit_param:
            implicit_param = "self"
        if effective_kind == "classmethod" and not implicit_param:
            implicit_param = "cls"

        return ClassInsertionPlan(
            class_name=ancestor.name,
            file_path=ancestor.file_path,
            method_kind=effective_kind,
            implicit_param=implicit_param,
        )

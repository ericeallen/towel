"""Conservative guards for extractions that change execution context."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Set, Union

from .binding_detector import BindingDetector
from .project_layout import ProjectLayout
from .scope_analyzer import ScopeAnalyzer, pattern_capture_names

if TYPE_CHECKING:
    from .unifier import Substitution


def uses_class_private_names(nodes: Iterable[ast.AST]) -> bool:
    """Whether moving these nodes to a different class changes name mangling."""
    for statement in nodes:
        for node in ast.walk(statement):
            name = (
                node.id
                if isinstance(node, ast.Name)
                else (node.attr if isinstance(node, ast.Attribute) else "")
            )
            if name.startswith("__") and not name.endswith("__"):
                return True
    return False


def nested_bindings_escape(
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef], nodes: Iterable[ast.AST]
) -> bool:
    """Reject nested extraction whose local writes are observable outside it.

    The engine's return-variable analysis covers top-level statement slices.
    A loop/branch body needs control-flow liveness, including reads on the next
    loop iteration. Until that analysis exists, require its bindings to remain
    entirely inside the extracted block. Attribute/subscript mutations do not
    rebind their base objects and are not counted as local writes.
    """
    block = tuple(nodes)
    if all(node in function.body for node in block):
        return False
    extracted = {child for statement in block for child in ast.walk(statement)}
    detector = BindingDetector()
    for statement in block:
        detector.visit(statement)
    bound = {binding.name for binding in detector.bindings}
    # Deletion changes the original local binding too, and is not a binding
    # construct reported by BindingDetector. Store contexts also conservatively
    # include declarations that do not initialize a value.
    bound.update(
        node.id
        for node in extracted
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    )
    if not bound:
        return False
    for node in ast.walk(function):
        if node in extracted:
            continue
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Load, ast.Del))
            and node.id in bound
        ):
            return True
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in bound
        ):
            return True
    return False


def snapshots_rebound_external_names(
    analyzer: ScopeAnalyzer,
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    nodes: Iterable[ast.AST],
) -> bool:
    """Reject snapshots of external bindings with visible rebinding hazards.

    Explicit global/nonlocal declarations identify bindings another function can
    update during the extraction. Namespace reflection makes that identification
    unreliable, so its presence conservatively disqualifies external snapshots.
    Opaque mutation originating outside the analyzed module is not modeled.
    """
    scope = analyzer.node_scopes.get(function)
    root = analyzer.root_scope
    if scope is None or root is None:
        return True
    hazards = analyzer.external_binding_hazards
    if hazards is None:
        return True
    rebound = hazards.rebound
    unresolved = hazards.unresolved_nonlocal
    reflective = hazards.reflective
    for statement in nodes:
        for node in ast.walk(statement):
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            binding = scope.lookup(node.id)
            if binding is None:
                if unresolved or reflective or (root.scope_id, node.id) in rebound:
                    return True
                continue
            # A local captured by a nested function is a mutable cell too.
            # Passing it to the helper snapshots it before that function runs.
            if (binding.scope_id, node.id) in rebound:
                return True
            if binding.scope_id == scope.scope_id:
                continue
            if unresolved or reflective:
                return True
    return False


def has_comprehension_assignment(nodes: Iterable[ast.AST]) -> bool:
    """Whether a comprehension writes a binding in its containing function.

    This requires return/liveness analysis across the comprehension boundary;
    until that is modeled, such a comprehension must remain in its caller.
    """
    return any(
        isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
        and any(isinstance(child, ast.NamedExpr) for child in ast.walk(node))
        for statement in nodes
        for node in ast.walk(statement)
    )


def requires_original_frame(nodes: Iterable[ast.AST]) -> bool:
    """Reject suspension and operations that inspect the original call frame.

    Generator delegation needs a separate transformation preserving send/throw
    and return values. Moving frame inspection into a helper is not equivalent.
    Unknown shadowing of these call names is deliberately treated conservatively.
    """
    block = tuple(nodes)
    if has_external_loop_control(block) or has_comprehension_assignment(block):
        return True
    for statement in block:
        for node in ast.walk(statement):
            if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await, ast.AsyncFor, ast.AsyncWith)):
                return True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"locals", "globals", "eval", "exec"}:
                    return True
                if node.func.id in {"vars", "super"} and not node.args and not node.keywords:
                    return True
    return False


def has_external_loop_control(nodes: Iterable[ast.AST]) -> bool:
    """Whether break/continue targets a loop outside the extraction boundary."""

    class LoopControlVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0
            self.external = False

        def visit_Break(self, node: ast.Break) -> None:
            if self.depth == 0:
                self.external = True

        def visit_Continue(self, node: ast.Continue) -> None:
            if self.depth == 0:
                self.external = True

        def _visit_loop(self, node: Union[ast.For, ast.AsyncFor, ast.While]) -> None:
            self.depth += 1
            for statement in node.body:
                self.visit(statement)
            self.depth -= 1
            # A loop's else-suite is outside that loop's break/continue scope.
            for statement in node.orelse:
                self.visit(statement)

        visit_For = _visit_loop
        visit_AsyncFor = _visit_loop
        visit_While = _visit_loop

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            pass

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            pass

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            pass

    visitor = LoopControlVisitor()
    for statement in nodes:
        visitor.visit(statement)
    return visitor.external


def would_create_import_cycle(canonical_file: str, replacement_files: Set[str]) -> bool:
    """Check whether adding imports of the helper closes a local import cycle.

    Follow static imports through local modules, including modules without any
    candidate functions and package initializers. Imports inside functions are
    included conservatively. Dynamic imports cannot be resolved statically.
    """
    canonical = Path(canonical_file).resolve()
    targets = {Path(path).resolve() for path in replacement_files} - {canonical}
    if not targets:
        return False
    common_root = Path(os.path.commonpath([str(path.parent) for path in targets | {canonical}]))
    roots = set(ProjectLayout.discover(canonical).source_roots) | {common_root}

    def module_files(base: Path, components: Iterable[str]) -> Set[Path]:
        result: Set[Path] = set()
        cursor = base
        for component in components:
            cursor = cursor / component
            initializer = cursor / "__init__.py"
            if initializer.is_file():
                result.add(initializer.resolve())
        module = cursor.with_suffix(".py")
        if module.is_file():
            result.add(module.resolve())
        return result

    pending = [canonical]
    visited: Set[Path] = set()
    while pending:
        current = pending.pop()
        if current in targets:
            return True
        if current in visited:
            continue
        visited.add(current)
        try:
            tree = ast.parse(current.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            # If an import cannot be inspected, do not claim that it is safe.
            return True
        dependencies: Set[Path] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for root in roots:
                        dependencies.update(module_files(root, alias.name.split(".")))
            elif isinstance(node, ast.ImportFrom):
                bases = roots
                if node.level:
                    base = current.parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    bases = {base}
                components = node.module.split(".") if node.module else []
                for base in bases:
                    dependencies.update(module_files(base, components))
                    for alias in node.names:
                        if alias.name != "*":
                            dependencies.update(module_files(base, [*components, alias.name]))
        pending.extend(dependencies - visited)
    return False


def bound_names(nodes: Iterable[ast.AST]) -> Set[str]:
    """Every name a node binds or unbinds, including inside nested scopes.

    This over-approximates scope: a nested function's local counts too. The
    guards below use it to decide whether a nested scope and the extracted
    block could share a binding, where over-approximation only rejects.
    """
    names: Set[str] = set()
    for statement in nodes:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.match_case):
                names.update(pattern_capture_names(node.pattern))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.asname or alias.name.split(".")[0])
    return names


def _deleted_names(nodes: Iterable[ast.AST]) -> Set[str]:
    """Names a block unbinds: explicit ``del`` and implicit except-clause cleanup."""
    names: Set[str] = set()
    for statement in nodes:
        for node in ast.walk(statement):
            if isinstance(node, ast.Delete):
                for target in node.targets:
                    names.update(
                        child.id
                        for child in ast.walk(target)
                        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Del)
                    )
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
    return names


def unbinds_external_name(
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    nodes: Iterable[ast.AST],
    bound_before: Set[str],
) -> bool:
    """Whether the block deletes a binding that outlives it.

    Deleting a helper parameter leaves the caller's variable bound; the
    original raised ``UnboundLocalError`` on the next read. ``except ... as e``
    deletes ``e`` when the handler exits, so it is a deletion too. Global and
    nonlocal names are rejected because the helper holds no such declaration.
    """
    deleted = _deleted_names(nodes)
    if not deleted:
        return False
    declared: Set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
    return bool(deleted & (bound_before | declared))


_NESTED_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.GeneratorExp)


def _loaded_names(node: ast.AST) -> Set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def nested_scopes_cross_block_boundary(
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef], nodes: Iterable[ast.AST]
) -> bool:
    """Reject extraction when a closure and the block share a mutable binding.

    A nested function, lambda or generator reads its free names when it runs,
    not when it is defined. If it is defined outside the block and the block
    rebinds one of those names, the helper rebinds its own local instead of the
    caller's cell. If it is defined inside the block and the caller rebinds one
    of its free names after the block, the helper's parameter snapshot goes
    stale. Both cases are rejected; reads of names bound only before a
    top-level block remain eligible.
    """
    block = tuple(nodes)
    extracted = {child for statement in block for child in ast.walk(statement)}
    written_in_block = bound_names(block)
    outside_scopes = [
        node
        for node in ast.walk(function)
        if isinstance(node, _NESTED_SCOPE_TYPES) and node is not function and node not in extracted
    ]
    if written_in_block and any(
        _loaded_names(scope) & written_in_block for scope in outside_scopes
    ):
        return True
    inside_scopes = [node for node in extracted if isinstance(node, _NESTED_SCOPE_TYPES)]
    if not inside_scopes:
        return False
    captured: Set[str] = set()
    for scope in inside_scopes:
        captured.update(_loaded_names(scope))
    captured -= written_in_block
    if not captured:
        return False
    top_level = all(node in function.body for node in block)
    block_end = max(getattr(node, "end_lineno", 0) or 0 for node in block)
    for statement in function.body:
        if statement in extracted:
            continue
        if top_level and (getattr(statement, "lineno", 0) or 0) <= block_end:
            continue
        if bound_names([statement]) & captured:
            return True
    return False


def is_eagerly_evaluable(expression: ast.AST) -> bool:
    """Whether hoisting this expression to the call site is unobservable.

    A parameter argument runs once, before the block, even when the block would
    have evaluated it later, repeatedly, conditionally, or not at all. Only
    expressions with no effects, no failure modes, and no fresh identity may
    move that way: local names, literals, and tuples of those. Attribute
    access can run a property, subscripts and operators can call arbitrary
    methods, calls are effects by definition, and mutable displays allocate.
    """
    if isinstance(expression, (ast.Constant, ast.Name)):
        return True
    # A tuple of such values is immutable, so one evaluation is as good as
    # many. List, set and dict displays create a fresh mutable object each
    # time they run; hoisting one out of a loop would alias every iteration.
    if isinstance(expression, ast.Tuple):
        return all(is_eagerly_evaluable(element) for element in expression.elts)
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.operand, ast.Constant):
        return isinstance(expression.op, (ast.USub, ast.UAdd, ast.Invert, ast.Not))
    return False


def has_impure_eager_parameters(substitution: "Substitution") -> bool:
    """Whether any eagerly passed parameter argument may be observable when hoisted.

    Lambda-lifted parameters and forwarded callees are evaluated inside the
    helper at the original position, so any expression is acceptable there.
    Call this after extraction, which is when callee parameters are known.
    """
    deferred = set(substitution.function_params) | set(substitution.params_used_as_callee)
    return any(
        not is_eagerly_evaluable(expression)
        for name, expressions in substitution.param_expressions.items()
        if name not in deferred
        for _, expression in expressions
    )


def defer_impure_parameters(
    substitution: "Substitution", template_block: Iterable[ast.AST]
) -> None:
    """Turn parameters whose arguments cannot be hoisted into zero-argument thunks.

    The helper then evaluates ``__param_n()`` at the original position, as often
    and as conditionally as the block did. Parameters already lambda-lifted keep
    their arguments; parameters used only as callees are forwarded lazily by
    the extractor and need no thunk.
    """
    callees: Set[str] = set()
    for statement in template_block:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call):
                parameter = substitution.get_param_for_expr(0, node.func)
                if parameter is not None:
                    callees.add(parameter)
    for name, expressions in substitution.param_expressions.items():
        if name in substitution.function_params or name in callees:
            continue
        if any(not is_eagerly_evaluable(expression) for _, expression in expressions):
            substitution.function_params[name] = []


def moves_scope_declaration(
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef], nodes: Iterable[ast.AST]
) -> bool:
    """Whether the block carries a ``global``/``nonlocal`` declaration the caller still needs.

    A declaration inside the block moves into the helper with it. Any remaining
    use of that name in the caller then silently becomes a local access. Only
    declarations at the block's own scope count; a nested function's own
    declarations move with that function.
    """
    block = tuple(nodes)
    extracted = {child for statement in block for child in ast.walk(statement)}
    declared: Set[str] = set()
    for statement in block:
        for node in _walk_own_scope(statement):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                declared.update(node.names)
    if not declared:
        return False
    for node in ast.walk(function):
        if node in extracted or node is function:
            continue
        if isinstance(node, ast.Name) and node.id in declared:
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and declared & set(node.names):
            return True
    return False


def _walk_own_scope(node: ast.AST) -> Iterable[ast.AST]:
    """Yield nodes of ``node`` without entering nested function or class scopes."""
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(current))

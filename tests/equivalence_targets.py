"""Resolve behavioral test targets from original replacement locations."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Mapping

from towel.unification.models import RefactoringProposal


def affected_functions(
    proposal: RefactoringProposal, sources: Mapping[Path, str]
) -> dict[Path, tuple[str, ...]]:
    """Select every replaced top-level entry point, including methods and closure factories."""
    trees = {path.resolve(): ast.parse(source) for path, source in sources.items()}
    selected: dict[Path, list[str]] = {}
    for replacement in proposal.replacements:
        path = Path(replacement.file_path or proposal.file_path).resolve()
        if path not in trees:
            raise ValueError(f"No original source supplied for replacement in {path}")
        tree = trees[path]
        start, end = replacement.line_range

        def contains(node: ast.AST) -> bool:
            return isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ) and node.lineno <= start <= end <= (node.end_lineno or node.lineno)

        owners = [node for node in tree.body if contains(node)]
        if len(owners) != 1:
            raise ValueError(f"{path}:{start}-{end}: no unique callable owner")
        owner = owners[0]
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # A coroutine function is run to completion by the harness.
            name = owner.name
        else:
            assert isinstance(owner, ast.ClassDef)
            methods = [node for node in owner.body if contains(node)]
            if len(methods) != 1 or not isinstance(methods[0], ast.FunctionDef):
                raise ValueError(f"{path}:{start}-{end}: unsupported class/async replacement")
            name = f"{owner.name}.{methods[0].name}"
        names = selected.setdefault(path, [])
        if name not in names:
            names.append(name)
    if not selected:
        raise ValueError("No replacement functions identified; equivalence was not tested")
    return {path: tuple(names) for path, names in selected.items()}


Callable = ast.FunctionDef | ast.AsyncFunctionDef
"""A definition the harness can invoke: a coroutine function is awaited to completion."""


def invocation_definitions(
    source: str, target: str
) -> tuple[str, str, Callable, ast.FunctionDef | None]:
    """Return adapter source, callable name, method signature and optional constructor."""
    tree = ast.parse(source)
    if "." not in target:
        matches = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target
        ]
        if len(matches) != 1:
            raise ValueError(f"No unique top-level function {target}")
        return "", target, matches[0], None

    class_name, method_name = target.split(".", 1)
    classes = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    if len(classes) != 1:
        raise ValueError(f"No unique top-level class {class_name}")
    cls = classes[0]
    if cls.keywords or cls.decorator_list:
        raise ValueError(f"{target}: decorated or metaclass construction is unsupported")
    methods = [
        node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name
    ]
    if len(methods) != 1:
        raise ValueError(f"No unique method {target}")
    method = methods[0]
    # A subclass is constructed like any class as long as this module declares
    # its constructor; an inherited one would have an unknown signature.
    if cls.bases and not any(
        isinstance(node, ast.FunctionDef) and node.name == "__init__" for node in cls.body
    ):
        raise ValueError(f"{target}: a subclass with an inherited constructor is unsupported")
    decorators = [
        node.id if isinstance(node, ast.Name) else "unsupported" for node in method.decorator_list
    ]
    if decorators not in ([], ["classmethod"], ["staticmethod"]):
        raise ValueError(f"{target}: unsupported method decorators")
    constructor = next(
        (
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        ),
        None,
    )
    if any(isinstance(node, ast.FunctionDef) and node.name == "__new__" for node in cls.body):
        raise ValueError(f"{target}: custom __new__ is unsupported")
    if any(isinstance(node, ast.Name) and node.id == "__slots__" for node in ast.walk(cls)):
        raise ValueError(f"{target}: slotted instance observation is unsupported")
    for definition in (method, constructor):
        if definition is not None and (
            definition.args.posonlyargs
            or definition.args.kwonlyargs
            or definition.args.vararg
            or definition.args.kwarg
        ):
            raise ValueError(f"{target}: exotic constructor/method signatures are unsupported")

    adapter_name = "__towel_audit_invoke"
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names.update(
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    )
    while adapter_name in names or adapter_name + "_invoked" in names:
        adapter_name += "_"
    if decorators:
        constructor = None
        body = f"    return {class_name}.{method_name}(*method_args)\n"
    else:
        body = f"    instance = {class_name}(*constructor_args)\n"
        body += (
            "    result = None\n"
            if method_name == "__init__"
            else f"    result = instance.{method_name}(*method_args)\n"
        )
        body += "    return result, vars(instance)\n"
    invocation_flag = adapter_name + "_invoked"
    if decorators or method_name == "__init__":
        body = f"    {invocation_flag} = True\n" + body
    else:
        body = body.replace(
            f"    result = instance.{method_name}",
            f"    {invocation_flag} = True\n    result = instance.{method_name}",
        )
    adapter = (
        f"\ndef {adapter_name}(constructor_args, method_args):\n"
        f"    global {invocation_flag}\n    {invocation_flag} = False\n" + body
    )
    return adapter, adapter_name, method, constructor

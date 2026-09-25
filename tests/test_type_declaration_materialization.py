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

"""A generated generic signature and its declarations form one proposal."""

from __future__ import annotations

import ast
from dataclasses import replace
import os
from pathlib import Path

import pytest

from towel.changes import apply_changes
from towel.cli import _apply_rename_mappings, _find_extracted_helpers, helper_inventory
from towel.renaming import plan_renames
from towel.unification.exceptions import RefactoringError
from towel.unification.models import RefactoringProposal, Replacement, ReusedFunction
from towel.unification.refactor_engine import UnificationRefactorEngine


def _proposal(
    path: Path,
    declarations: str,
    *,
    parameter_name: str = "_T",
    source_function: ast.FunctionDef | None = None,
) -> RefactoringProposal:
    source = ast.parse(path.read_text())
    function = source_function or next(
        node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "first"
    )
    statement = function.body[0]
    helper = ast.parse(
        f"def __extracted_func(value: {parameter_name}) -> {parameter_name}:\n    return value\n"
    ).body[0]
    assert isinstance(helper, ast.FunctionDef)
    return RefactoringProposal(
        file_path=str(path),
        extracted_function=helper,
        replacements=[
            Replacement(
                (statement.lineno, statement.end_lineno or statement.lineno),
                ast.parse("return __extracted_func(value)").body[0],
            )
        ],
        description="Share the identity computation",
        parameters_count=1,
        helper_type_declarations=tuple(ast.parse(declarations).body),
    )


def _engine() -> UnificationRefactorEngine:
    return UnificationRefactorEngine(
        annotate_helpers=False, reuse_existing_functions=False, cross_module_helpers=True
    )


@pytest.mark.parametrize(
    "definition,constraint,expression",
    [
        ("class Value:\n    pass\n", "Value", "Value()"),
        ("from decimal import Decimal\n", "Decimal", "Decimal('2.5')"),
    ],
)
def test_type_declarations_follow_runtime_dependencies_and_preserve_source_bindings(
    tmp_path: Path, definition: str, constraint: str, expression: str
) -> None:
    path = tmp_path / "module.py"
    original = (
        '"""Module docstring."""\n'
        "from __future__ import annotations\n\n"
        "TypeVar = 'existing binding'\n\n"
        "def first(value):\n    return value\n\n" + definition
    )
    path.write_text(original)
    proposal = _proposal(
        path,
        "from typing import TypeVar as _GenericTypeVar\n"
        f"_T = _GenericTypeVar('_T', {constraint}, int)\n",
    )
    original_helper = ast.dump(proposal.extracted_function)
    original_declarations = tuple(ast.dump(node) for node in proposal.helper_type_declarations)
    engine = _engine()
    rendered = engine.apply_refactoring(str(path), proposal)
    assert engine.apply_refactoring(str(path), proposal) == rendered
    assert rendered.index(definition.rstrip()) < rendered.index("_T = _GenericTypeVar")
    assert rendered.index("_T = _GenericTypeVar") < rendered.index("def __extracted_func_0")
    assert ast.dump(proposal.extracted_function) == original_helper
    assert (
        tuple(ast.dump(node) for node in proposal.helper_type_declarations) == original_declarations
    )
    assert path.read_text() == original
    plan = engine.plan_refactoring(proposal)
    assert len(plan.changes) == 1 and path.read_text() == original
    apply_changes(plan)
    assert path.read_text() == rendered
    exec(
        compile(
            rendered
            + f"\nvalue = {expression}\nassert first(value) is value\n"
            + "assert TypeVar == 'existing binding'\n"
            + "assert __doc__ == 'Module docstring.'\n",
            str(path),
            "exec",
        ),
        {},
    )


def test_quoted_type_bounds_do_not_require_eager_project_bindings(tmp_path: Path) -> None:
    path = tmp_path / "module.py"
    path.write_text(
        "def first(value):\n    return value\n\n"
        "assert first(7) == 7\n\n"
        "class Later:\n    pass\n"
    )
    proposal = _proposal(
        path,
        "from typing import TypeVar as _GenericTypeVar\n"
        "_T = _GenericTypeVar('_T', bound='Later')\n",
    )
    rendered = _engine().apply_refactoring(str(path), proposal)
    assert rendered.index("_T = _GenericTypeVar") < rendered.index("def first")
    exec(compile(rendered, str(path), "exec"), {})


@pytest.mark.parametrize(
    "definition",
    [
        "assert first(7) == 7\nclass Later:\n    pass\n",
        "if False:\n    class Later:\n        pass\n",
    ],
)
def test_unavailable_eager_type_binding_refuses_without_writing(
    tmp_path: Path, definition: str
) -> None:
    path = tmp_path / "module.py"
    original = "def first(value):\n    return value\n\n" + definition
    path.write_text(original)
    proposal = _proposal(
        path,
        "from typing import TypeVar as _GenericTypeVar\n"
        "_T = _GenericTypeVar('_T', bound=Later)\n",
    )
    with pytest.raises(RefactoringError, match="unavailable binding"):
        _engine().plan_refactoring(proposal)
    assert path.read_text() == original


@pytest.mark.parametrize("placement", ["function", "reuse"])
def test_type_declarations_require_a_fresh_module_or_class_helper(
    tmp_path: Path, placement: str
) -> None:
    path = tmp_path / "module.py"
    original = "def first(value):\n    return value\n"
    path.write_text(original)
    proposal = _proposal(path, "from typing import TypeVar\n_T = TypeVar('_T')\n")
    if placement == "function":
        proposal = replace(proposal, insert_into_function="first")
    else:
        proposal = replace(proposal, reused_function=ReusedFunction("first", str(path), (1, 2)))
    with pytest.raises(RefactoringError, match="fresh module or class"):
        _engine().plan_refactoring(proposal)
    assert path.read_text() == original


def _host_proposal(path: Path, declarations: str) -> RefactoringProposal:
    """``_proposal``'s helper as a static method of ``Host``, called from ``Host.first``.

    A method helper is class-private, so its call must be written in its
    class's own body for the compiler to spell it as the class stores it.
    """
    host = next(
        node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.ClassDef) and node.name == "Host"
    )
    first = next(node for node in host.body if isinstance(node, ast.FunctionDef))
    statement = first.body[0]
    proposal = _proposal(path, declarations, source_function=first)
    return replace(
        proposal,
        insert_into_class="Host",
        method_kind="staticmethod",
        replacements=[
            Replacement(
                (statement.lineno, statement.end_lineno or statement.lineno),
                ast.parse("return __extracted_func(value)").body[0],
                class_name="Host",
                method_kind="staticmethod",
            )
        ],
    )


@pytest.mark.parametrize("bound_before_host", [True, False])
def test_method_declarations_respect_module_dependencies_and_host_decorators(
    tmp_path: Path, bound_before_host: bool
) -> None:
    path = tmp_path / "module.py"
    bound = "class Bound:\n    pass\n\n"
    source = '"""Example."""\nfrom __future__ import annotations\n\n' "def decorate(cls):\n    return cls\n\n" + (
        bound if bound_before_host else ""
    ) + "@decorate\nclass Host:\n    @staticmethod\n    def first(value):\n        return value\n\n" + (
        "" if bound_before_host else bound
    )
    path.write_text(source)
    proposal = _host_proposal(
        path, "from typing import TypeVar as _TV\n_T = _TV('_T', bound=Bound)\n"
    )
    engine = _engine()
    if bound_before_host:
        rendered = engine.apply_refactoring(str(path), proposal)
        assert rendered.index("class Bound:") < rendered.index("_T =") < rendered.index("@decorate")
        assert "@decorate\nclass Host:" in rendered
        assert "return Host.__extracted_func_0(value)" in rendered
        namespace: dict[str, object] = {}
        exec(
            compile(
                rendered + "\nvalue = Bound()\nassert Host.first(value) is value\n",
                str(path),
                "exec",
            ),
            namespace,
        )
        assert namespace["__doc__"] == "Example."
    else:
        # The decorator runs at import before Bound exists, which refuses the
        # declaration before its position relative to the host is considered.
        with pytest.raises(RefactoringError, match="binding after its host|unavailable binding"):
            engine.apply_refactoring(str(path), proposal)
        assert engine.change_log == ()
    assert path.read_text() == source


def test_a_method_helper_called_outside_its_class_is_refused(tmp_path: Path) -> None:
    """``Host.__extracted_func_0`` written outside ``Host`` names no attribute ``Host`` has.

    The compiler rewrites the private name only inside the class's own body,
    where the helper is stored as ``_Host__extracted_func_0``; a proposal that
    calls it from anywhere else would write code raising ``AttributeError``.
    """
    path = tmp_path / "module.py"
    original = "def first(value):\n    return value\n\n\nclass Host:\n    pass\n"
    path.write_text(original)
    proposal = _proposal(path, "")
    proposal = replace(
        proposal,
        insert_into_class="Host",
        method_kind="staticmethod",
        replacements=[
            replace(item, class_name="Host", method_kind="staticmethod")
            for item in proposal.replacements
        ],
    )
    with pytest.raises(RefactoringError, match="class-private"):
        _engine().apply_refactoring(str(path), proposal)
    assert path.read_text() == original


def test_helper_name_allocation_avoids_its_own_type_declarations(tmp_path: Path) -> None:
    path = tmp_path / "module.py"
    path.write_text("def first(value):\n    return value\n")
    proposal = _proposal(
        path,
        "from typing import TypeVar as _GenericTypeVar\n"
        "__extracted_func_0 = _GenericTypeVar('__extracted_func_0')\n",
        parameter_name="__extracted_func_0",
    )
    rendered = _engine().apply_refactoring(str(path), proposal)
    assert "def __extracted_func_1" in rendered
    assert "return __extracted_func_1(value)" in rendered
    exec(compile(rendered + "\nassert first(7) == 7\n", str(path), "exec"), {})


@pytest.mark.parametrize("quoted", [False, True])
def test_generic_helper_inventory_and_rename_keep_type_variable_bindings(
    tmp_path: Path, quoted: bool
) -> None:
    path = tmp_path / "module.py"
    path.write_text("def first(value):\n    return value\n")
    proposal = _proposal(
        path,
        "from typing import TypeVar as _GenericTypeVar\n" "_T = _GenericTypeVar('_T', int, str)\n",
    )
    if quoted:
        proposal.extracted_function.args.args[0].annotation = ast.Constant(value="_T")
        proposal.extracted_function.returns = ast.Constant(value="_T")
    apply_changes(_engine().plan_refactoring(proposal))
    inventory = helper_inventory(tmp_path, _find_extracted_helpers(tmp_path, None, None))
    assert len(inventory["helpers"]) == 1
    record = inventory["helpers"][0]
    assert record["renameable"] and record["scope"] == "module"
    assert "_T" in record["source"]
    assert record["parameters"][0]["name"] == "value"
    assert len(record["calls"]) == 1
    declaration_before = ast.dump(ast.parse(path.read_text()).body[1])
    changes = _apply_rename_mappings(
        tmp_path,
        {
            record["rename_key"]: "preserve",
            record["parameters"][0]["rename_key"]: "item",
        },
        dry_run=False,
        quiet=True,
    )
    assert changes > 0
    renamed = path.read_text()
    assert "def preserve(item:" in renamed and "return preserve(value)" in renamed
    assert ast.dump(ast.parse(renamed).body[1]) == declaration_before
    exec(
        compile(
            renamed
            + "\nfrom typing import get_type_hints\n"
            + "assert get_type_hints(preserve) == {'item': _T, 'return': _T}\n"
            + "assert first(7) == 7 and first('ok') == 'ok'\n",
            str(path),
            "exec",
        ),
        {},
    )


@pytest.mark.parametrize("target", ["_T", "_GenericTypeVar"])
def test_helper_rename_cannot_capture_its_type_declarations(tmp_path: Path, target: str) -> None:
    path = tmp_path / "module.py"
    path.write_text("def first(value):\n    return value\n")
    proposal = _proposal(
        path,
        "from typing import TypeVar as _GenericTypeVar\n" "_T = _GenericTypeVar('_T', int, str)\n",
    )
    apply_changes(_engine().plan_refactoring(proposal))
    original = path.read_bytes()
    with pytest.raises(ValueError):
        plan_renames(tmp_path, [("__extracted_func_0", target, None)])
    assert path.read_bytes() == original


def test_cross_file_rollback_removes_declarations_imports_and_calls_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, caller = tmp_path / "a.py", tmp_path / "b.py"
    original = "def first(value):\n    return value\n"
    # The caller imports the host, so the program's own imports show it can.
    borrowing = "import a\n\n\n" + original
    host.write_text(original)
    caller.write_text(borrowing)
    # Private bindings only: a public ``TypeVar`` would be refused before any file is planned.
    proposal = _proposal(
        host, "from typing import TypeVar as _GenericTypeVar\n_T = _GenericTypeVar('_T')\n"
    )
    site = proposal.replacements[0]
    shifted = (site.line_range[0] + 3, site.line_range[1] + 3)  # below the caller's import
    proposal = replace(
        proposal, replacements=[replace(site, file_path=str(caller), line_range=shifted)]
    )
    plan = _engine().plan_refactoring(proposal)
    assert len(plan.changes) == 2
    assert b"TypeVar" in plan.changes[0].after
    assert b"from a import __extracted_func_0" in plan.changes[1].after
    original_replace = os.replace
    failed = False

    def fail_second_file(source: str | Path, destination: str | Path) -> None:
        nonlocal failed
        if Path(destination) == caller and not failed:
            failed = True
            raise OSError("simulated second file write failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_file)
    with pytest.raises(OSError, match="simulated second file"):
        apply_changes(plan)
    assert failed
    assert host.read_text() == original and caller.read_text() == borrowing
    assert not list(tmp_path.glob(".towel-transaction-*"))

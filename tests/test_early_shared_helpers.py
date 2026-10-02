# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""A shared helper exists before ordinary imports can reenter its partial host."""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import subprocess
import sys

import pytest

from towel.source_text import source_lines
from towel.unification.exceptions import RefactoringError
from towel.unification.insertion import early_helper_line
from towel.unification.materialize import _early_module_helper
from towel.unification.models import RefactoringProposal, Replacement, ReusedFunction
from towel.unification.refactor_engine import UnificationRefactorEngine

_BODY = """def {name}(seed):
    total = seed + 1
    result = total * 2
    added = result + 3
    return added * {factor}
"""


def _run(root: Path, search_paths: tuple[Path, ...] = ()) -> str:
    paths = [str(root), *(str(path) for path in search_paths)]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            f"import sys; sys.path[:0] = {paths!r}; import run",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _refactor(package: Path) -> int:
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(package), str(package), progress="none"
        )
    return sum(count for count, _ in results.values())


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("call_during_import", [False, True])
def test_shared_helper_exists_in_a_partial_host(
    tmp_path: Path, external: bool, call_during_import: bool
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="partial-host"\nversion="0"\n')
    (package / "__init__.py").write_text("")
    reentry = "import importlib\nborrower = importlib.import_module('pkg.b')\n"
    if call_during_import:
        reentry += "print(borrower.second(1))\n"
    if external:
        (tmp_path / "support.py").write_text(reentry)
        prefix = "import support\n"
    else:
        prefix = reentry
    header = '#!/usr/bin/env python3\n# coding: utf-8\n"""Host documentation."""\n'
    header += "from __future__ import annotations\n"
    (package / "a.py").write_text(header + prefix + _BODY.format(name="first", factor=5))
    # A direct module import proves the host is present; a package member
    # import could instead bind an unrelated existing attribute.
    (package / "b.py").write_text("import pkg.a\n" + _BODY.format(name="second", factor=7))
    (tmp_path / "run.py").write_text("from pkg import a, b\nprint(a.first(2), b.second(3))\n")
    expected = ("49\n" if call_during_import else "") + "45 77\n"
    assert _run(tmp_path) == expected
    assert _refactor(package) > 0
    assert _run(tmp_path) == expected
    host = (package / "a.py").read_text()
    assert host.startswith(header)
    tree = ast.parse(host)
    assert ast.get_docstring(tree) == "Host documentation."
    helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    first_import = next(node for node in tree.body if isinstance(node, ast.Import))
    assert helper.name.startswith("__extracted_func") and helper.lineno < first_import.lineno
    assert _refactor(package) == 0
    assert _run(tmp_path) == expected


def test_previous_helper_body_can_run_before_later_imports(tmp_path: Path) -> None:
    """A helper-shaped definition is still a real boundary on subsequent passes."""
    package = tmp_path / "pkg"
    package.mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="existing-helper"\nversion="0"\n')
    (package / "__init__.py").write_text("")
    (package / "a.py").write_text(
        _BODY.format(name="__extracted_func_9", factor=5)
        + "\nprint(__extracted_func_9(2))\nfrom pkg import b\n"
    )
    (package / "b.py").write_text(_BODY.format(name="second", factor=7))
    (tmp_path / "run.py").write_text("from pkg import a, b\nprint(b.second(3))\n")
    expected = "45\n77\n"
    assert _run(tmp_path) == expected
    assert _refactor(package) == 0
    assert _run(tmp_path) == expected


def test_early_helper_keeps_existing_names_and_star_imports(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="private-helper"\nversion="0"\n')
    (package / "__init__.py").write_text("")
    (package / "a.py").write_text(
        "__extracted_func_0 = 111\n__all__ = ['first', '__extracted_func_0']\n"
        + _BODY.format(name="first", factor=5)
    )
    (package / "b.py").write_text("import pkg.a\n" + _BODY.format(name="second", factor=7))
    (tmp_path / "run.py").write_text(
        "from pkg.a import *\nfrom pkg import b\n"
        "print(first(2), b.second(3), __extracted_func_0)\n"
    )
    assert _run(tmp_path) == "45 77 111\n"
    assert _refactor(package) > 0
    assert _run(tmp_path) == "45 77 111\n"
    assert "def __extracted_func_1(" in (package / "a.py").read_text()


@pytest.mark.parametrize("inactive", [False, True])
@pytest.mark.parametrize("placement", ["host", "borrower-late", "borrower-leading"])
def test_external_star_import_cannot_overwrite_an_early_helper(
    tmp_path: Path, inactive: bool, placement: str
) -> None:
    project, vendor = tmp_path / "project", tmp_path / "vendor"
    package = project / "pkg"
    package.mkdir(parents=True)
    vendor.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="star-host"\nversion="0"\n')
    (package / "__init__.py").write_text("")
    (vendor / "wildcard.py").write_text(
        "__all__ = ['__extracted_func_0']\n" "def __extracted_func_0(*args):\n    return 999\n"
    )
    importing = (
        "if 0 > 1:\n    from wildcard import *\n" if inactive else "from wildcard import *\n"
    )
    (package / "a.py").write_text(
        (importing if placement == "host" else "") + _BODY.format(name="first", factor=5)
    )
    (package / "b.py").write_text(
        (importing if placement == "borrower-leading" else "")
        + "import pkg.a\n"
        + _BODY.format(name="second", factor=7)
        + (importing if placement == "borrower-late" else "")
    )
    (project / "run.py").write_text("from pkg import a, b\nprint(a.first(2), b.second(3))\n")
    assert _run(project, (vendor,)) == "45 77\n"
    count = _refactor(package)
    assert _run(project, (vendor,)) == "45 77\n"
    assert (count > 0) is (inactive or placement == "borrower-leading")


def _function(source: str) -> ast.FunctionDef:
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    return function


def test_early_helper_annotations_do_not_evaluate_unavailable_bindings() -> None:
    helper = _function(
        "def __extracted_func(value: Missing, /, *args: Other, extra: More, **kwargs: Last)"
        " -> None:\n    return None\n"
    )
    original = ast.dump(helper)
    early = _early_module_helper(helper)
    source = ast.unparse(early)
    exec(source, {})
    assert ast.dump(helper) == original
    assert (
        "'Missing'" in source and "'Other'" in source and "'More'" in source and "'Last'" in source
    )
    assert early.returns is not None and ast.unparse(early.returns) == "None"


@pytest.mark.parametrize(
    "source",
    [
        "@decorate\ndef __extracted_func(value):\n    return value\n",
        "def __extracted_func(value=load()):\n    return value\n",
        "def __extracted_func(*, value=load()):\n    return value\n",
    ],
)
def test_eager_helper_definitions_are_refused(source: str) -> None:
    with pytest.raises(RefactoringError, match="must be inert"):
        _early_module_helper(_function(source))


@pytest.mark.skipif(sys.version_info < (3, 12), reason="native type parameters require Python 3.12")
def test_native_type_parameter_scope_needs_its_own_availability_proof() -> None:
    with pytest.raises(RefactoringError, match="must be inert"):
        _early_module_helper(
            _function("def __extracted_func[T: Missing](value: T) -> T:\n    return value\n")
        )


@pytest.mark.parametrize(
    "source",
    [
        '"""Doc."""; run()\ndef f():\n    pass\n',
        "from __future__ import annotations; run()\ndef f():\n    pass\n",
    ],
)
def test_header_line_shared_with_effects_is_refused(source: str) -> None:
    with pytest.raises(RefactoringError, match="header's line"):
        early_helper_line(source_lines(source), ast.parse(source))


def test_relative_future_module_is_an_ordinary_import() -> None:
    source = "from .__future__ import annotations\ndef f():\n    pass\n"
    assert early_helper_line(source_lines(source), ast.parse(source)) == 0


def test_runtime_type_declaration_reads_cannot_delay_a_shared_helper(tmp_path: Path) -> None:
    host, borrower = tmp_path / "a.py", tmp_path / "b.py"
    proposal = RefactoringProposal(
        file_path=str(host),
        extracted_function=_function("def __extracted_func(value: T) -> T:\n    return T(value)\n"),
        replacements=[Replacement((1, 1), ast.Pass(), file_path=str(borrower))],
        description="A shared generic helper",
        parameters_count=1,
        helper_type_declarations=tuple(ast.parse("T = make_type()\n").body),
    )
    lines = ["import dependency\n"]
    with pytest.raises(RefactoringError, match="require type declarations"):
        UnificationRefactorEngine()._insert_helper_at_module_level(proposal, lines)
    assert lines == ["import dependency\n"]


def test_generic_helper_can_run_before_its_signature_declarations(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="generic-reentry"\nversion="0"\n')
    (package / "__init__.py").write_text("")
    host, borrower = package / "a.py", package / "b.py"
    host.write_text(
        "import importlib\nborrower = importlib.import_module('pkg.b')\n"
        "print(borrower.second(2))\ndef first(value):\n    return value\n"
    )
    borrower.write_text("from pkg import a\ndef second(value):\n    return value\n")
    (tmp_path / "run.py").write_text("from pkg import a, b\nprint(a.first(3), b.second(4))\n")
    expected = "2\n3 4\n"
    assert _run(tmp_path) == expected
    helper = _function("def __extracted_func(value: _T) -> _T:\n    return value\n")
    replacements: list[Replacement] = []
    for path in (host, borrower):
        function = next(
            node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef)
        )
        statement = function.body[0]
        replacements.append(
            Replacement(
                (statement.lineno, statement.end_lineno or statement.lineno),
                ast.parse("return __extracted_func(value)").body[0],
                file_path=str(path),
            )
        )
    proposal = RefactoringProposal(
        file_path=str(host),
        extracted_function=helper,
        replacements=replacements,
        description="A generic shared identity",
        parameters_count=1,
        helper_type_declarations=tuple(
            ast.parse(
                "from typing import TypeVar as _GenericTypeVar\n_T = _GenericTypeVar('_T')\n"
            ).body
        ),
    )
    engine = UnificationRefactorEngine(annotate_helpers=False, cross_module_helpers=True)
    modified = engine._materialize_once(proposal)
    for filename, source in modified.items():
        Path(filename).write_text(source)
    rendered = host.read_text()
    assert rendered.index("def __extracted_func_0") < rendered.index("import importlib")
    assert rendered.index("print(borrower.second(2))") < rendered.index("_T = _GenericTypeVar")
    assert _run(tmp_path) == expected


@pytest.mark.parametrize(
    "prefix, available",
    [
        ("", True),
        ("from __future__ import annotations\n", True),
        ("def earlier():\n    return 1\n", True),
        ("BASE = 1\n", True),
        ("import dependency\n", False),
        ("run()\n", False),
        ("def earlier(value=run()):\n    return value\n", False),
    ],
)
def test_reused_helpers_need_an_already_inert_prefix(
    tmp_path: Path, prefix: str, available: bool
) -> None:
    host, borrower = tmp_path / "a.py", tmp_path / "b.py"
    host.write_text(prefix + "def __extracted_func_0(value):\n    return value\n")
    helper = next(
        node
        for node in ast.parse(host.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "__extracted_func_0"
    )
    proposal = RefactoringProposal(
        file_path=str(host),
        extracted_function=helper,
        replacements=[Replacement((1, 1), ast.Pass(), file_path=str(borrower))],
        description="Reuse a shared helper",
        parameters_count=1,
        reused_function=ReusedFunction(
            helper.name, str(host), (helper.lineno, helper.end_lineno or helper.lineno)
        ),
    )
    engine = UnificationRefactorEngine()
    if available:
        engine._require_early_reused_helper(proposal)
    else:
        with pytest.raises(RefactoringError, match="unavailable before reentrant imports"):
            engine._materialize_once(proposal)


def test_partial_host_cannot_supply_an_existing_late_helper(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="late-reuse"\nversion="0"\n')
    (package / "__init__.py").write_text("")
    host, borrower = package / "a.py", package / "b.py"
    host.write_text(
        "import importlib\nimportlib.import_module('pkg.b')\n"
        + _BODY.format(name="__extracted_func_0", factor=5)
    )
    borrower.write_text("from pkg import a\n" + _BODY.format(name="second", factor=5))
    (tmp_path / "run.py").write_text(
        "from pkg import a, b\nprint(a.__extracted_func_0(2), b.second(3))\n"
    )
    assert _run(tmp_path) == "45 55\n"
    helper = next(
        node for node in ast.parse(host.read_text()).body if isinstance(node, ast.FunctionDef)
    )
    function = next(
        node for node in ast.parse(borrower.read_text()).body if isinstance(node, ast.FunctionDef)
    )
    proposal = RefactoringProposal(
        file_path=str(host),
        extracted_function=helper,
        replacements=[
            Replacement(
                (function.body[0].lineno, function.end_lineno or function.lineno),
                ast.parse("return __extracted_func_0(seed)").body[0],
                file_path=str(borrower),
            )
        ],
        description="Reuse an existing late helper",
        parameters_count=1,
        reused_function=ReusedFunction(
            helper.name, str(host), (helper.lineno, helper.end_lineno or helper.lineno)
        ),
    )
    with pytest.raises(RefactoringError, match="unavailable before reentrant imports"):
        UnificationRefactorEngine(cross_module_helpers=True)._materialize_once(proposal)
    assert _run(tmp_path) == "45 55\n"


def test_alias_import_keeps_the_original_first_definition_boundary(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="early-alias"\nversion="0"\n')
    (package / "__init__.py").write_text("")
    host, borrower = package / "a.py", package / "b.py"
    host.write_text(_BODY.format(name="__extracted_func_1", factor=5) + "BASE = 1\n")
    borrower.write_text(
        _BODY.format(name="__extracted_func_0", factor=5)
        + "from pkg.a import BASE\nprint(__extracted_func_0(2))\n"
    )
    (tmp_path / "run.py").write_text("from pkg import b\n")
    assert _run(tmp_path) == "45\n"
    helper = _function(host.read_text())
    old = _function(borrower.read_text())
    proposal = RefactoringProposal(
        file_path=str(host),
        extracted_function=helper,
        replacements=[
            Replacement(
                (old.lineno, old.end_lineno or old.lineno),
                ast.parse("__extracted_func_0 = __extracted_func_1").body[0],
                file_path=str(borrower),
            )
        ],
        description="Alias an early generated helper",
        parameters_count=1,
        reused_function=ReusedFunction(
            helper.name, str(host), (helper.lineno, helper.end_lineno or helper.lineno)
        ),
    )
    engine = UnificationRefactorEngine(cross_module_helpers=True)
    origin = tmp_path / "original"
    (origin / "pkg").mkdir(parents=True)
    (origin / "pyproject.toml").write_text((tmp_path / "pyproject.toml").read_text())
    (origin / "pkg/__init__.py").write_text("")
    for original in (host, borrower):
        (origin / "pkg" / original.name).write_text(
            original.read_text()
            .replace("__extracted_func_0", "user")
            .replace("__extracted_func_1", "provider")
        )
    engine._output_origin = (origin, tmp_path)
    engine.import_graph.begin_run(origin, tmp_path)
    modified = engine._materialize_once(proposal)
    for filename, source in modified.items():
        Path(filename).write_text(source)
    rendered = borrower.read_text()
    assert rendered.index("import __extracted_func_1") < rendered.index("__extracted_func_0 =")
    assert _run(tmp_path) == "45\n"


@pytest.mark.parametrize("class_host", [False, True])
def test_cross_module_helpers_cannot_bypass_availability_with_a_nested_host(
    tmp_path: Path, class_host: bool
) -> None:
    proposal = RefactoringProposal(
        file_path=str(tmp_path / "a.py"),
        extracted_function=_function("def helper(value):\n    return value\n"),
        replacements=[Replacement((1, 1), ast.Pass(), file_path=str(tmp_path / "b.py"))],
        description="An invalid cross-module nested helper",
        parameters_count=1,
        insert_into_class="Box" if class_host else None,
        insert_into_function=None if class_host else "outer",
    )
    with pytest.raises(RefactoringError, match="available at module scope"):
        UnificationRefactorEngine()._materialize_once(proposal)

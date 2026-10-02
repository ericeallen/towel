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

"""Generated module aliases share a safe home without forwarding or changing input bindings."""

from __future__ import annotations

import ast
import copy
from pathlib import Path
import subprocess
import sys
from typing import TypedDict

import pytest

from towel.unification.exceptions import RefactoringError
from towel.unification.function_index import FunctionIndex
from towel.unification.models import RefactoringProposal, Replacement
from towel.unification.pipeline import analyze_scopes, collect_functions, parse_modules
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.type_inference import CheckSuccess, MypyInferrer, TypeOracle
from tests.test_generated_helper_reuse import BODY
from tests.typed_fixtures import requires_mypy


class _AliasOptions(TypedDict, total=False):
    signature: str
    decorator: str
    same_name: bool
    original_name: bool
    same_module: bool
    different_body: bool
    annotation_mismatch: bool


def _staged_pair(
    root: Path,
    *,
    signature: str = "(n)",
    decorator: str = "",
    same_name: bool = False,
    original_name: bool = False,
    same_module: bool = False,
    different_body: bool = False,
    annotation_mismatch: bool = False,
    oracle: TypeOracle | None = None,
) -> tuple[UnificationRefactorEngine, RefactoringProposal, FunctionIndex]:
    origin, stage = root / "origin", root / "stage"
    names = ("__extracted_func_0", "__extracted_func_0" if same_name else "__extracted_func_1")
    for folder in (origin, stage):
        (folder / "pkg").mkdir(parents=True)
        (folder / "pyproject.toml").write_text(
            "[project]\nname='test'\nversion='0'\n"
            + ("[tool.mypy]\nstrict=true\n" if oracle is not None else "")
        )
        (folder / "pkg/__init__.py").write_text("")
    for filename, name in zip(("a.py", "b.py"), names):
        prelude = "from pkg.b import BASE\n" if filename == "a.py" else "BASE = 1\n"
        body = (
            BODY.replace("return acc", "return acc + 1")
            if different_body and filename == "a.py"
            else BODY
        )
        header = (
            signature + " -> 'str'" if annotation_mismatch and filename == "a.py" else signature
        )
        original = f'def {name if original_name else "user"}{signature}:\n{BODY}'
        (origin / "pkg" / filename).write_text(prelude + original)
        (stage / "pkg" / filename).write_text(prelude + decorator + f"def {name}{header}:\n{body}")
    if same_module:
        (stage / "pkg/b.py").write_text(
            "BASE = 1\n" + "".join(f"def {name}{signature}:\n{BODY}" for name in names)
        )
        paths = [str(stage / "pkg/b.py")]
    else:
        paths = [str(stage / "pkg/a.py"), str(stage / "pkg/b.py")]
    functions = FunctionIndex.build(collect_functions(analyze_scopes(parse_modules(paths))))
    helpers = sorted(functions.functions, key=lambda item: (item.file_path, item.node.lineno))
    target = helpers[-1]
    assert isinstance(target.node, ast.FunctionDef)
    new = copy.deepcopy(target.node)
    new.name = "__extracted_func"
    replacements = [
        Replacement(
            (site.node.body[0].lineno, site.node.body[-1].end_lineno or site.node.body[-1].lineno),
            ast.parse("return __extracted_func(n)").body[0],
            file_path=site.file_path,
        )
        for site in helpers
    ]
    proposal = RefactoringProposal(target.file_path, new, replacements, "staged pair", 1)
    engine = UnificationRefactorEngine(
        min_lines=3, cross_module_helpers=True, annotate_helpers=False, type_oracle=oracle
    )
    engine._output_origin = (origin, stage)
    engine.import_graph.begin_run(origin, stage)
    return engine, proposal, functions


@pytest.mark.parametrize(
    "options, accepted",
    [
        ({}, True),
        ({"signature": "(n: 'int') -> 'int'"}, True),
        ({"signature": "(n: annotation()) -> int"}, False),
        ({"signature": "(n=default())"}, False),
        ({"decorator": "@decorate\n"}, False),
        ({"same_name": True}, False),
        ({"original_name": True}, False),
        ({"same_module": True}, False),
        ({"different_body": True}, False),
        ({"annotation_mismatch": True}, False),
    ],
    ids=[
        "plain",
        "inert-annotations",
        "evaluated-annotations",
        "default",
        "decorator",
        "same-name",
        "input-functions",
        "same-module",
        "different-body",
        "different-signature",
    ],
)
def test_aliasing_requires_distinct_proved_generated_module_bindings(
    tmp_path: Path, options: _AliasOptions, accepted: bool
) -> None:
    engine, proposal, functions = _staged_pair(tmp_path, **options)
    before = ast.dump(proposal.extracted_function), [
        ast.dump(r.node) for r in proposal.replacements
    ]
    reused = engine._reusing_generated_helper(proposal, functions)
    assert (reused is not None) is accepted
    assert before == (
        ast.dump(proposal.extracted_function),
        [ast.dump(r.node) for r in proposal.replacements],
    )
    if reused is not None:
        (alias,) = reused.replacements
        assert alias.file_path is not None
        assert ast.unparse(alias.node) == "__extracted_func_0 = __extracted_func_1"
        rendered = engine._materialize_once(reused)
        assert "__extracted_func_0 = __extracted_func_1" in rendered[alias.file_path]
        assert "def __extracted_func_0" not in rendered[alias.file_path]


@pytest.mark.parametrize("corruption", ["late", "wrong-module", "shadowed"])
def test_rendered_alias_must_read_the_verified_import(tmp_path: Path, corruption: str) -> None:
    engine, proposal, functions = _staged_pair(tmp_path)
    reused = engine._reusing_generated_helper(proposal, functions)
    assert reused is not None and reused.reused_function is not None
    rendered = engine._materialize_once(reused)
    path = reused.replacements[0].file_path
    assert path is not None
    lines = rendered[path].splitlines(keepends=True)
    imported = next(
        line for line in lines if line.startswith("from ") and "__extracted_func_1" in line
    )
    if corruption == "late":
        rendered[path] = rendered[path].replace(imported, "") + imported
    elif corruption == "wrong-module":
        rendered[path] = rendered[path].replace(
            imported, "from missing import __extracted_func_1\n"
        )
    else:
        rendered[path] = rendered[path].replace(
            imported, imported + "__extracted_func_1 = object\n"
        )
    with pytest.raises(RefactoringError, match="does not read the proved helper"):
        engine._verify_reused_function_calls(rendered, reused.reused_function, reused)


@pytest.mark.parametrize("name", ["ordinary", "__extracted_func_0"])
def test_cross_module_input_functions_keep_independent_bindings(tmp_path: Path, name: str) -> None:
    origin, output = tmp_path / "origin", tmp_path / "output"
    package = origin / "pkg"
    package.mkdir(parents=True)
    (origin / "pyproject.toml").write_text("[project]\nname='test'\nversion='0'\n")
    (package / "__init__.py").write_text("")
    (package / "a.py").write_text(f"def {name}(n):\n{BODY}")
    # Load the real host module so this reaches the independent-binding
    # guarantee, rather than the package-member ambiguity refusal.
    (package / "b.py").write_text("import pkg.a\ndef other(n):\n" + BODY)
    program = (
        "from pkg import a,b\n"
        f'print(a.{name}(3),b.other(4))\na.{name}=lambda n: "patched"\nprint(b.other(5))\n'
    )
    before = subprocess.run(
        [sys.executable, "-B", "-c", program],
        cwd=origin,
        capture_output=True,
        text=True,
        check=True,
    )
    engine = UnificationRefactorEngine(
        min_lines=3, cross_module_helpers=True, annotate_helpers=False
    )
    results, reason = engine.refactor_directory_to_fixed_point(
        str(origin), str(output), progress="none"
    )
    assert results and reason == "fixed_point"
    after = subprocess.run(
        [sys.executable, "-B", "-c", program],
        cwd=output,
        capture_output=True,
        text=True,
        check=True,
    )
    assert (before.stdout, before.stderr) == (after.stdout, after.stderr)
    assert f"def {name}(" in (output / "pkg/a.py").read_text()
    assert "def other(" in (output / "pkg/b.py").read_text()


@requires_mypy
def test_equivalent_helper_aliases_pass_the_complete_project_check(tmp_path: Path) -> None:
    """Continue a typed stage with inert signatures; check every generated consumer."""
    oracle = MypyInferrer()
    try:
        engine, proposal, functions = _staged_pair(
            tmp_path, signature="(n: 'int') -> 'int'", oracle=oracle
        )
        stage = tmp_path / "stage"
        for index, name in enumerate(("a.py", "b.py")):
            path = stage / "pkg" / name
            path.write_text(
                path.read_text()
                + f"\ndef call(n: int) -> int:\n    return __extracted_func_{index}(n)\n"
            )
        program = "from pkg import a,b\nprint(a.call(4),b.call(5))\n"
        before = subprocess.run(
            [sys.executable, "-B", "-c", program],
            cwd=stage,
            capture_output=True,
            text=True,
            check=True,
        )
        reused = engine._reusing_generated_helper(proposal, functions)
        assert reused is not None and isinstance(reused.replacements[0].node, ast.Assign)
        sources = engine.apply_refactoring_multi_file(reused)
        assert all("Any" not in source for source in sources.values())
        checked = oracle.check_project(sources)
        assert isinstance(checked, CheckSuccess) and not checked.errors, checked
        for file_path, source in sources.items():
            Path(file_path).write_text(source)
        after = subprocess.run(
            [sys.executable, "-B", "-c", program],
            cwd=stage,
            capture_output=True,
            text=True,
            check=True,
        )
        assert (before.stdout, before.stderr) == (after.stdout, after.stderr)
    finally:
        oracle.close()

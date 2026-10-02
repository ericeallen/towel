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

"""An earlier pass's helper can serve new callers without acquiring a forwarding layer.

The round-four four-module repro and its eight-module extension originally
ended with three and seven helpers in chains. Independent first-pass batches
now share one equivalent definition through aliases, preserving their existing
consumers without redirecting any function already named in the input.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
from pathlib import Path
import subprocess
import sys

import pytest

from towel.type_inference import CheckSuccess, MypyInferrer
from towel.unification.function_index import FunctionIndex
from towel.unification.models import RefactoringProposal, Replacement, is_generated_helper_name
from towel.unification.pipeline import analyze_scopes, collect_functions, parse_modules
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.typed_fixtures import requires_mypy

BODY = (
    "    acc = n * 2\n    acc = acc - 11\n    msg = 'value %d' % acc\n"
    "    msg = msg.strip()\n    return acc\n"
)
"""A whole body with no module lookup to introduce an unrelated caller thunk."""


def _package(root: Path, count: int, *, typed: bool = False, third_float: bool = False) -> Path:
    package = root / "pkg"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'pkg'\nversion = '0'\n"
        + ("[tool.mypy]\nstrict = true\n" if typed else "")
    )
    (package / "__init__.py").write_text("")
    for name in "abcdefgh"[:count]:
        annotation = "float" if third_float and name == "c" else "int"
        signature = f"(n: {annotation}) -> {annotation}:" if typed else "(n):"
        (package / f"{name}.py").write_text(
            ("import pkg.a\n\n" if name != "a" else "") + f"def {name}_one{signature}\n" + BODY
        )
    return package


def _runtime(root: Path, count: int, *, half: bool = False) -> str:
    """Separate interpreters exercise package imports, output, results and exception messages."""
    names = "abcdefgh"[:count]
    program = f"from pkg import {', '.join(names)}\n"
    for index, name in enumerate(names):
        program += f"print({name}.{name}_one({index + .5 if half else index}))\n"
        program += (
            f"try:\n    {name}.{name}_one(None)\n"
            "except Exception as error:\n    print(type(error).__name__, str(error))\n"
        )
    run = subprocess.run(
        [sys.executable, "-B", "-c", program],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def _helpers(package: Path) -> list[ast.FunctionDef]:
    return [
        node
        for path in sorted(package.glob("*.py"))
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
    ]


@pytest.mark.parametrize("count, expected_helpers", [(3, 1), (4, 1), (8, 1)])
def test_cross_module_batches_keep_real_helpers_without_forwarding_chains(
    tmp_path: Path, count: int, expected_helpers: int
) -> None:
    root, output = tmp_path / "input", tmp_path / "output"
    package = _package(root, count)
    before = {path.name: path.read_bytes() for path in package.iterdir()}
    expected = _runtime(root, count)
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    results, reason = engine.refactor_directory_to_fixed_point(
        str(root), str(output), progress="none"
    )
    assert results and reason == "fixed_point"
    helpers = _helpers(output / "pkg")
    assert len(helpers) == expected_helpers
    for helper in helpers:
        assert len(helper.body) == 5, ast.unparse(helper)
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and is_generated_helper_name(node.func.id)
            for node in ast.walk(helper)
        ), ast.unparse(helper)
    if count == 3:
        assert any(
            "in place of its restatement" in desc for _, descs in results.values() for desc in descs
        )
    assert _runtime(output, count) == expected
    assert {path.name: path.read_bytes() for path in package.iterdir()} == before


@requires_mypy
@pytest.mark.parametrize("third_float", [False, True])
def test_reuse_keeps_existing_signature_and_requires_project_type_acceptance(
    tmp_path: Path, third_float: bool
) -> None:
    """An int helper is reused for int callers; float callers keep their body, without Any fallback."""
    root, output = tmp_path / "input", tmp_path / "output"
    _package(root, 3, typed=True, third_float=third_float)
    expected = _runtime(root, 3, half=third_float)
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(
            min_lines=3,
            cross_module_helpers=True,
            annotate_helpers=True,
            type_oracle=oracle,
        )
        results, reason = engine.refactor_directory_to_fixed_point(
            str(root), str(output), progress="none"
        )
        assert results and reason == "fixed_point"
        (helper,) = _helpers(output / "pkg")
        assert helper.returns is not None and "int" in ast.unparse(helper.returns)
        assert "Any" not in ast.unparse(helper)
        reused = any(
            "in place of its restatement" in desc for _, descs in results.values() for desc in descs
        )
        assert reused is not third_float
        if third_float:
            assert "acc = n * 2" in (output / "pkg" / "c.py").read_text()
            assert engine.run_report.declined_proposals
        checked = oracle.check_project(
            {str(path): path.read_text() for path in (output / "pkg").glob("*.py")}
        )
        assert isinstance(checked, CheckSuccess) and not checked.errors, checked
    finally:
        oracle.close()
    assert _runtime(output, 3, half=third_float) == expected


@pytest.mark.parametrize("name", ["existing", "__extracted_func_0"])
def test_names_already_in_input_keep_independent_monkeypatch_behavior(
    tmp_path: Path, name: str
) -> None:
    """A reserved-looking spelling is not provenance: both input functions retain independent lookup."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='test'\nversion='0'\n")
    source = root / "module.py"
    source.write_text(f"def {name}(n):\n{BODY}\ndef other(n):\n{BODY}")
    engine = UnificationRefactorEngine(min_lines=3)
    final, count, _ = engine.refactor_to_fixed_point(str(source), progress="none")
    assert count == 1
    namespace: dict[str, object] = {}
    exec(final, namespace)
    other = namespace["other"]
    assert callable(other)
    namespace[name] = lambda n: "patched"
    assert other(2) == -7


@pytest.mark.parametrize("name", ["existing", "__extracted_func_0"])
def test_input_name_does_not_hide_a_whole_body_matching_another_prefix(
    tmp_path: Path, name: str
) -> None:
    """Audit5: provenance, not helper spelling, governs the forwarder quality guard.

    The input function's whole computation also occurs before another
    function's residual print. Both functions can share a new helper while
    retaining independent lookup; requiring both sites to be whole bodies
    incorrectly hid this valid extraction only for the reserved-looking name.
    """
    from tests.test_scope_guard_boundaries import _extract

    original = f"""events = []
def {name}(n):
    value = n + 3
    result = value * 2
    events.append(('same', result))
    return result
def second(n):
    value = n + 3
    result = value * 2
    events.append(('same', result))
    print('after block', result)
    return result
first = {name}
print(first(1), second(2), events)
{name} = lambda n: 'patched'
print(second(3), events)
"""
    final, count = _extract(tmp_path, original)
    assert count == 1, final
    tree = ast.parse(final)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert name in functions and "second" in functions
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
        for node in ast.walk(functions["second"])
    ), "The existing input function must never become the shared call target"


def test_initial_name_allocation_and_later_reuse_keep_borrower_bindings(tmp_path: Path) -> None:
    """The initial allocator sees all analyzed files, and reuse retains its fresh name."""
    root, output = tmp_path / "input", tmp_path / "output"
    package = _package(root, 3)
    third = package / "c.py"
    third.write_text(third.read_text().replace("(n):", "(n, __extracted_func_0=None):"))
    expected = _runtime(root, 3)
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    results, reason = engine.refactor_directory_to_fixed_point(
        str(root), str(output), progress="none"
    )
    assert results and reason == "fixed_point"
    assert _runtime(output, 3) == expected
    (helper,) = _helpers(output / "pkg")
    assert helper.name != "__extracted_func_0"
    assert "__extracted_func_0=None" in (output / "pkg" / "c.py").read_text()
    assert any(
        "in place of its restatement" in desc for _, descs in results.values() for desc in descs
    )


def _staged_proposal(tmp_path: Path, helper_source: str, caller_source: str):
    """Build the narrow local proof fixture inside a stage; broad engine tests above generate it normally."""
    origin, stage = tmp_path / "original", tmp_path / "stage"
    origin.mkdir()
    stage.mkdir()
    for root in (origin, stage):
        (root / "pyproject.toml").write_text("[project]\nname='test'\nversion='0'\n")
    (origin / "module.py").write_text(caller_source)
    path = stage / "module.py"
    path.write_text(helper_source + "\n" + caller_source)
    functions = FunctionIndex.build(collect_functions(analyze_scopes(parse_modules([str(path)]))))
    helper = next(
        item.node for item in functions.functions if item.node.name == "__extracted_func_0"
    )
    caller = next(item.node for item in functions.functions if item.node.name == "caller")
    assert isinstance(helper, ast.FunctionDef)
    new = copy.deepcopy(helper)
    new.name = "__extracted_func"
    replacements = [
        Replacement(
            line_range=(
                function.body[0].lineno,
                function.body[-1].end_lineno or function.body[-1].lineno,
            ),
            node=ast.Return(
                value=ast.Call(
                    func=ast.Name(id=new.name, ctx=ast.Load()),
                    args=[ast.Name(id="n", ctx=ast.Load())],
                    keywords=[],
                )
            ),
            file_path=str(path),
        )
        for function in (helper, caller)
    ]
    proposal = RefactoringProposal(str(path), new, replacements, "proof fixture", 1)
    engine = UnificationRefactorEngine(min_lines=3)
    engine._output_origin = (origin, stage)
    return engine, proposal, functions


@pytest.mark.parametrize(
    "helper_source, caller_source, accepted",
    [
        ("def __extracted_func_0(n):\n" + BODY, "def caller(n):\n" + BODY, True),
        ("def __extracted_func_0(n=1):\n" + BODY, "def caller(n):\n" + BODY, False),
        ("@decorate\ndef __extracted_func_0(n):\n" + BODY, "def caller(n):\n" + BODY, False),
        ("def __extracted_func_0(*, n):\n" + BODY, "def caller(n):\n" + BODY, False),
        (
            "class Host:\n    def __extracted_func_0(n):\n"
            + "".join("    " + line + "\n" for line in BODY.splitlines()),
            "def caller(n):\n" + BODY,
            False,
        ),
        (
            "def __extracted_func_0(n):\n" + BODY,
            "def caller(n, __extracted_func_0):\n" + BODY,
            False,
        ),
    ],
    ids=["plain", "default", "decorator", "keyword-only", "receiver", "shadowed"],
)
def test_reuse_requires_a_plain_unshadowed_module_helper(
    tmp_path: Path, helper_source: str, caller_source: str, accepted: bool
) -> None:
    """The identity-call argument is valid only for the undecorated, unbound positional function."""
    engine, proposal, functions = _staged_proposal(tmp_path, helper_source, caller_source)
    before = ast.dump(proposal.extracted_function), [
        ast.dump(item.node) for item in proposal.replacements
    ]
    reused = engine._reusing_generated_helper(proposal, functions)
    assert (reused is not None) is accepted
    assert before == (
        ast.dump(proposal.extracted_function),
        [ast.dump(item.node) for item in proposal.replacements],
    )
    if reused is not None:
        assert reused.reused_function is not None
        assert len(reused.replacements) == 1
        assert "__extracted_func_0(n)" in ast.unparse(reused.replacements[0].node)


@pytest.mark.parametrize("same_body", [False, True])
def test_only_equivalent_generated_helpers_are_protected_from_further_splitting(
    tmp_path: Path, same_body: bool
) -> None:
    """Different residual computations may share a block; equivalent whole helpers gain nothing."""
    helper_source = "def __extracted_func_0(n):\n" + BODY
    caller_source = "def caller(n):\n" + (
        BODY if same_body else BODY.replace("return acc", "return acc + 1")
    )
    engine, proposal, _ = _staged_proposal(tmp_path, helper_source, caller_source)
    path = Path(proposal.file_path)
    path.write_text(path.read_text().replace("def caller(n)", "def __extracted_func_1(n)"))
    functions = FunctionIndex.build(collect_functions(analyze_scopes(parse_modules([str(path)]))))
    # Extract just the three computations. Both helpers still print and
    # return afterward, so neither site is itself a whole-body forwarder.
    proposal.replacements = [
        dataclasses.replace(
            item,
            line_range=(item.line_range[0], item.line_range[0] + 2),
            node=ast.parse("acc, msg = __extracted_func(n)").body[0],
        )
        for item in proposal.replacements
    ]
    refused = engine._helper_reduced_to_forwarder(proposal, functions)
    assert (refused is not None) is same_body


def test_reuse_itself_checks_a_new_borrowers_bindings(tmp_path: Path) -> None:
    """Even a separately constructed proposal may not import over a borrower's existing name."""
    engine, proposal, _ = _staged_proposal(
        tmp_path, "def __extracted_func_0(n):\n" + BODY, "def caller(n):\n" + BODY
    )
    borrower = Path(proposal.file_path).with_name("borrower.py")
    borrower.write_text("def caller(n, __extracted_func_0=None):\n" + BODY)
    functions = FunctionIndex.build(
        collect_functions(analyze_scopes(parse_modules([proposal.file_path, str(borrower)])))
    )
    function = functions.in_file(str(borrower))[0].node
    proposal.replacements[1] = dataclasses.replace(
        proposal.replacements[1],
        file_path=str(borrower),
        line_range=(
            function.body[0].lineno,
            function.body[-1].end_lineno or function.body[-1].lineno,
        ),
    )
    assert engine._reusing_generated_helper(proposal, functions) is None

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

"""Synthetic helper parameters preserve positional calls and declared Python floors."""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import io
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_generated_helper_reuse import BODY, _helpers, _package, _runtime, _staged_proposal
from tests.test_helpers import function_def
from towel.type_inference import CheckSuccess, MypyInferrer
from towel.unification.extractor import HygienicExtractor
from towel.unification.instantiation import instantiation_mismatch
from towel.unification.placement import HelperPlacement
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.substitution import Substitution


def test_synthetic_parameters_are_positional_only_and_free_names_follow() -> None:
    block = ast.parse("answer = value + 7\nreturn answer\n").body
    substitution = Substitution()
    substitution.add_mapping(0, ast.Constant(value=7), "__param_0")
    helper, order = HygienicExtractor().extract_function(
        template_block=block,
        substitution=substitution,
        free_variables={"value"},
        enclosing_names=set(),
        is_value_producing=True,
    )
    assert [arg.arg for arg in helper.args.posonlyargs] == ["__param_0"]
    assert [arg.arg for arg in helper.args.args] == ["value"]
    assert order == {"__param_0": 0, "value": 1}
    generated = HygienicExtractor().generate_call(
        function_name=helper.name,
        block_idx=0,
        substitution=substitution,
        param_order=order,
        free_variables={"value"},
        is_value_producing=True,
    )
    assert (
        instantiation_mismatch(
            helper,
            generated,
            block,
            {},
            {},
            site_function_names=frozenset(),
            preamble_length=0,
            returns_variables=False,
        )
        is None
    )
    rejection = instantiation_mismatch(
        helper,
        ast.parse("return extracted_function(__param_0=7, value=value)").body[0],
        block,
        {},
        {},
        site_function_names=frozenset(),
        preamble_length=0,
        returns_variables=False,
    )
    assert rejection == "arity"


@pytest.mark.parametrize("name", ["parameter", "_param_0"])
def test_explicit_nonsynthetic_substitution_names_keep_ordinary_parameters(name: str) -> None:
    substitution = Substitution()
    substitution.add_mapping(0, ast.Constant(value=7), name)
    helper, _ = HygienicExtractor().extract_function(
        template_block=ast.parse("return 7 + value").body,
        substitution=substitution,
        free_variables={"value"},
        enclosing_names=set(),
        is_value_producing=True,
    )
    assert not helper.args.posonlyargs
    assert [arg.arg for arg in helper.args.args] == [name, "value"]


@pytest.mark.parametrize("receiver", ["self", "cls"])
@pytest.mark.parametrize("already_present", [False, True])
def test_method_receiver_precedes_positional_only_synthetic_parameters(
    receiver: str, already_present: bool
) -> None:
    remaining = f", {receiver}: object" if already_present else ""
    helper = function_def(
        f"def helper(__param_0: int, /, value: int{remaining}):\n    return value\n"
    )
    HelperPlacement._ensure_leading_param(helper, receiver)
    assert [arg.arg for arg in helper.args.posonlyargs] == [receiver, "__param_0"]
    assert [arg.arg for arg in helper.args.args] == ["value"]
    assert (helper.args.posonlyargs[0].annotation is not None) == already_present
    ast.parse(ast.unparse(helper))


SOURCE = """def first(value: int) -> int:
    print("first")
    answer = value * 2
    print(answer)
    return answer

def second(value: int) -> int:
    print("second")
    answer = value * 2
    print(answer)
    return answer
"""


def _observed(source: str) -> tuple[object, str]:
    namespace: dict[str, object] = {}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(
            compile(source + "\nobserved = (first(3), second(4))\n", "program.py", "exec"),
            namespace,
        )
    return namespace["observed"], output.getvalue()


@pytest.mark.parametrize(
    ("target", "expected_slash"), [(None, False), ("3.7", False), ("3.8", True), ("3.12", True)]
)
@pytest.mark.parametrize("annotate", [False, True])
def test_engine_respects_declared_python_floor_and_keeps_call_results(
    tmp_path: Path, target: str | None, expected_slash: bool, annotate: bool
) -> None:
    declaration = f'requires-python = ">={target}"\n' if target else ""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0"\n' + declaration
    )
    path = tmp_path / "program.py"
    path.write_text(SOURCE)
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, annotate_helpers=annotate
    )
    proposals = engine.analyze_file(str(path))
    assert proposals
    proposal_dump = ast.dump(proposals[0].extracted_function, include_attributes=True)
    result = engine.apply_refactoring(str(path), proposals[0])
    assert ast.dump(proposals[0].extracted_function, include_attributes=True) == proposal_dump
    helpers = [
        node
        for node in ast.parse(result).body
        if isinstance(node, ast.FunctionDef) and node.name not in {"first", "second"}
    ]
    assert len(helpers) == 1
    assert bool(helpers[0].args.posonlyargs) == expected_slash
    assert _observed(result) == _observed(SOURCE)
    if not expected_slash:
        ast.parse(result, feature_version=(3, 7))
    else:
        checked = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "ruff",
                "check",
                "--isolated",
                "--select",
                "PYI063",
                "--stdin-filename",
                str(path),
                "-",
            ],
            input=result,
            text=True,
            capture_output=True,
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("method_kind", ["instance", "classmethod", "staticmethod"])
def test_typed_method_helpers_keep_receiver_dispatch_with_synthetic_inputs(
    tmp_path: Path, method_kind: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="probe"\nversion="0"\nrequires-python=">=3.8"\n'
        "[tool.mypy]\nstrict=true\n"
    )
    decorator = "" if method_kind == "instance" else f"    @{method_kind}\n"
    receiver = "self" if method_kind == "instance" else "cls"
    parameters = "value: int" if method_kind == "staticmethod" else f"{receiver}, value: int"
    operand = "3" if method_kind == "staticmethod" else f"{receiver}.base"
    source = "class Box:\n    base: int = 3\n"
    for name in ["first", "second", "third"]:
        source += (
            decorator
            + f"    def {name}({parameters}) -> int:\n"
            + f'        print("{name}")\n'
            + f"        answer = value * {operand}\n"
            + "        print(answer)\n        return answer\n"
        )
    path = tmp_path / "program.py"
    path.write_text(source)
    oracle = MypyInferrer()
    try:
        assert oracle.check(str(path), source) == CheckSuccess()
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        proposals = engine.analyze_file(str(path))
        assert proposals
        proposal = proposals[0]
        assert len(proposal.replacements) == 3
        assert proposal.extracted_function.args.posonlyargs
        result = engine.apply_refactoring(str(path), proposal)
        assert oracle.check(str(path), result) == CheckSuccess()
    finally:
        oracle.close()
    helpers = [
        node
        for node in ast.walk(ast.parse(result))
        if isinstance(node, ast.FunctionDef) and node.name not in {"first", "second", "third"}
    ]
    assert len(helpers) == 1 and helpers[0].args.posonlyargs
    if method_kind != "staticmethod":
        assert helpers[0].args.posonlyargs[0].arg == receiver
    observed = []
    for program in [source, result]:
        namespace: dict[str, object] = {}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(
                compile(
                    program
                    + "\nbox = Box()\nobserved = (box.first(2), box.second(4), box.third(5))\n",
                    str(path),
                    "exec",
                ),
                namespace,
            )
        observed.append((namespace["observed"], output.getvalue()))
    assert observed[0] == observed[1]
    checked = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--select",
            "PYI063",
            "--stdin-filename",
            str(path),
            "-",
        ],
        input=result,
        text=True,
        capture_output=True,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize(("target", "expected_slash"), [("3.7", False), ("3.8", True)])
def test_cross_module_repeated_passes_preserve_binding_and_origin_target(
    tmp_path: Path, target: str, expected_slash: bool
) -> None:
    root, output = tmp_path / "input", tmp_path / "output"
    package = _package(root, 3)
    configuration = root / "pyproject.toml"
    configuration.write_text(configuration.read_text() + f'requires-python = ">={target}"\n')
    for multiplier, name in enumerate("abc", 2):
        path = package / f"{name}.py"
        path.write_text(path.read_text().replace("n * 2", f"n * {multiplier}"))
    expected = _runtime(root, 3)
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    results, reason = engine.refactor_directory_to_fixed_point(
        str(root), str(output), progress="none"
    )
    assert results and reason == "fixed_point"
    helpers = _helpers(output / "pkg")
    assert helpers
    synthetic = [
        helper
        for helper in helpers
        if any(
            argument.arg.startswith("__param_")
            for argument in (*helper.args.posonlyargs, *helper.args.args)
        )
    ]
    assert synthetic
    assert all(bool(helper.args.posonlyargs) == expected_slash for helper in synthetic)
    assert _runtime(output, 3) == expected
    for path in (output / "pkg").glob("*.py"):
        ast.parse(path.read_text(), feature_version=(3, 8) if expected_slash else (3, 7))


def test_reusing_an_existing_synthetic_named_parameter_keeps_provider_signature(
    tmp_path: Path,
) -> None:
    helper = "def __extracted_func_0(__param_0):\n" + BODY.replace("n * 2", "__param_0 * 2")
    caller = "def caller(n):\n" + BODY
    engine, proposal, functions = _staged_proposal(tmp_path, helper, caller)
    # The shared fixture spells every provider argument n; this provider's
    # own replacement must read its actual parameter before reuse can be proved.
    proposal = dataclasses.replace(
        proposal,
        replacements=[
            dataclasses.replace(
                proposal.replacements[0],
                node=ast.parse(f"return {proposal.extracted_function.name}(__param_0)").body[0],
            ),
            *proposal.replacements[1:],
        ],
    )
    reused = engine._reusing_generated_helper(proposal, functions)
    assert reused is not None and reused.reused_function is not None
    path = Path(reused.file_path)
    before = function_def(helper)
    result = engine.apply_refactoring(str(path), reused)
    provider = next(
        node
        for node in ast.parse(result).body
        if isinstance(node, ast.FunctionDef) and node.name == before.name
    )
    assert ast.dump(provider, include_attributes=False) == ast.dump(
        before, include_attributes=False
    )
    assert not provider.args.posonlyargs

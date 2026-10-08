# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Ordinary literal identities survive compiler-pool and runtime-construction boundaries."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from types import FunctionType
from typing import Optional

import pytest
import towel

from towel.diagnostics import Settings
from towel.unification.compiler_literals import pooled_literal_parts, retained_literal_pool
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.unifier import Unifier


@dataclass(frozen=True)
class _Observation:
    result: str
    literal_type: str
    repeated_identity: bool
    nested_identity: Optional[bool]


def _observe(source: str, *, literal_input: object = None) -> tuple[_Observation, ...]:
    namespace: dict[str, object] = {}
    exec(compile(source, "observed.py", "exec"), namespace)
    observations = []
    for name in ("f", "g"):
        function = namespace[name]
        assert isinstance(function, FunctionType)
        if literal_input is None:
            one: object = function([1])
            two: object = function([1])
        else:
            one = function(1, literal_input)
            two = function(1, literal_input)
        assert isinstance(one, tuple) and isinstance(two, tuple)
        literal: object = one[0]
        repeated: object = two[0]
        nested = None
        if isinstance(literal, tuple) and isinstance(repeated, tuple) and len(literal) > 1:
            if isinstance(literal[1], tuple):
                nested = literal[1] is repeated[1]
        observations.append(
            _Observation(repr(one), type(literal).__name__, literal is repeated, nested)
        )
    return tuple(observations)


def _dry(original: Path, output: Path, *, cross_module: bool = False, typed: bool = False) -> str:
    command = [
        sys.executable,
        "-B",
        "-m",
        "towel.cli",
        "dry",
        str(original),
        str(output),
        "--no-interactive",
        "--no-types",
        "--no-format",
        "--progress",
        "none",
    ]
    if typed:
        command.remove("--no-types")
        command.remove("--no-format")
    if cross_module:
        command.append("--cross-module")
    runtime = Path(towel.__file__).resolve().parents[1]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "TOWEL_WORKERS": "1", "PYTHONPATH": str(runtime)},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    "first,second",
    [
        ("(1000000, 1000000)", "(1000001, 1000001)"),
        ("(1000000, 1000002)", "(1000001, 1000003)"),
        ("(1000000, (1000000,))", "(1000001, (1000001,))"),
        ("1000000 + 1", "1000000 + 2"),
        ("True + 1000000", "False + 1000000"),
        ("'noninterned label ' + 'one'", "'noninterned label ' + 'two'"),
        ("b'noninterned label ' + b'one'", "b'noninterned label ' + b'two'"),
        ("-1000000", "-1000001"),
        ("-1000000.25", "-1000001.25"),
        ("('noninterned label',) + (1000000,)", "('noninterned label',) + (1000001,)"),
        ("(1000000,) * 2", "(1000001,) * 2"),
        ("__debug__ + 1000000", "__debug__ + 1000001"),
        ("-0.0", "-1.0"),
        ("1e309", "1000000.25"),
        ("(1e309,-0.0)", "(1000000.25,0.0)"),
    ],
)
def test_complete_cli_preserves_repeated_pooled_literal_identity(
    tmp_path: Path,
    first: str,
    second: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="pool-control"\nversion="0"\n')
    source = "\n".join(
        f"def {name}(value):\n    literal = {literal}\n    subtotal = sum(value) + 11\n    total = subtotal * 13\n    return literal, total\n"
        for name, literal in (("f", first), ("g", second))
    )
    original = project / "input.py"
    original.write_text(source)
    before = _observe(source)
    assert all(observation.repeated_identity for observation in before)
    output = tmp_path / "output.py"
    report = _dry(original, output)
    assert "Applied 1 refactoring" in report
    generated = output.read_text() if output.exists() else source
    assert _observe(generated) == before
    assert original.read_text() == source


def test_identical_pooled_descendant_is_not_replaced_by_an_outside_structural_mapping() -> None:
    first = ast.parse(
        "literal = ((1000000 + 1),)\nmarker = 1000000 + 1\ntotal = value + 11\nreturn literal, total, marker"
    ).body
    second = ast.parse(
        "literal = ((1000000 + 1),)\nmarker = value\ntotal = value + 11\nreturn literal, total, marker"
    ).body
    assert Unifier().unify_blocks([first, second], [{}, {}]) is None


def test_whole_pooled_root_can_still_be_passed_from_its_original_caller(tmp_path: Path) -> None:
    source = "\n".join(
        f"def {name}(value, literal_input):\n    literal = {literal}\n    subtotal = value + 11\n    total = subtotal * 13\n    return literal, total\n"
        for name, literal in (("f", "(1000000, 1000000)"), ("g", "literal_input"))
    )
    original = tmp_path / "input.py"
    original.write_text(source)
    output = tmp_path / "output.py"
    report = _dry(original, output)
    assert "Applied 1 refactoring" in report
    external = (1000007, 1000008)
    assert _observe(output.read_text(), literal_input=external) == _observe(
        source, literal_input=external
    )


def test_dynamic_tuple_still_has_useful_parameterization(tmp_path: Path) -> None:
    source = "\n".join(
        f"def {name}(value):\n    literal = (value, {literal})\n    subtotal = sum(value) + 11\n    total = subtotal * 13\n    return literal, total\n"
        for name, literal in (("f", "1000000"), ("g", "1000001"))
    )
    original = tmp_path / "input.py"
    original.write_text(source)
    output = tmp_path / "output.py"
    report = _dry(original, output)
    assert "Applied 1 refactoring" in report
    before = _observe(source)
    assert all(not item.repeated_identity for item in before)
    assert _observe(output.read_text()) == before


def _cross_observe(project: Path) -> str:
    expression = "import a,b; print(a.f(1)[0] is b.g(1)[0]); print(a.f(1)[0] is a.outside); print(b.g(1)[0] is b.outside)"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            f"import sys; sys.path.insert(0, {str(project)!r}); " + expression,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=project,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    "literal",
    [
        "1000000",
        "1000000.25",
        "'noninterned label with spaces'",
        "b'noninterned label with spaces'",
        "(1000000, 1000000)",
        "(1000000, (1000000,))",
        "1000000 + 1",
        "(True, None)",
        "-0.0",
    ],
)
def test_complete_cross_module_cli_preserves_common_literal_and_outside_correlations(
    tmp_path: Path,
    literal: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="pool-cross"\nversion="0"\n')
    body = "outside = {literal}\ndef {name}(value):\n    literal = {literal}\n    subtotal = value + 11\n    total = subtotal * {factor}\n    return literal, total\n"
    (project / "a.py").write_text(body.format(literal=literal, name="f", factor=5))
    (project / "b.py").write_text("import a\n" + body.format(literal=literal, name="g", factor=7))
    before = _cross_observe(project)
    assert before == "False\nTrue\nTrue\n"
    output = tmp_path / "output"
    _dry(project, output, cross_module=True)
    assert _cross_observe(output) == before


@pytest.mark.parametrize("singletons", [False, True])
def test_cross_module_callers_keep_useful_fully_parameterized_or_singleton_literals(
    tmp_path: Path,
    singletons: bool,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="pool-positive"\nversion="0"\n')
    body = "outside = {literal}\ndef {name}(value):\n    literal = {literal}\n    subtotal = value + {offset}\n    total = subtotal * {factor}\n    return literal, total\n"
    for file, name, literal, offset, factor in (
        ("a.py", "f", "1000000", 5, 7),
        ("b.py", "g", "1000001", 6, 8),
    ):
        source = body.format(
            literal="None" if singletons else literal, name=name, offset=offset, factor=factor
        )
        (project / file).write_text(("import a\n" if file == "b.py" else "") + source)
    before = _cross_observe(project)
    output = tmp_path / "output"
    report = _dry(project, output, cross_module=True)
    assert "Applied 2 refactoring" in report
    assert _cross_observe(output) == before


def test_runtime_literal_facts_ignore_metadata_targets_and_slices() -> None:
    assert not pooled_literal_parts(
        ast.parse("def f(value: tuple[str, str]) -> tuple[str, str]: pass").body
    )
    assert not pooled_literal_parts(ast.parse("values[0:2] = values[1:3]").body)
    assert not pooled_literal_parts(ast.parse("target = (left, right)").body)
    annotation_only = ast.parse("literal: Literal[1000000, 1000001] = value").body
    assert not pooled_literal_parts(annotation_only)
    assert not retained_literal_pool(annotation_only)
    first = ast.parse("literal: Literal[1, 2] = value + 1").body
    second = ast.parse("literal: Literal[1, 2] = value + 2").body
    # The separate existing constant-consistency guard counts annotations;
    # pool protection itself must not pin their ignored literal descendants.
    assert not pooled_literal_parts(first) and not pooled_literal_parts(second)
    metadata = "Literal[1000000 + 1, 1000001 + 2]"
    first = ast.parse(f"literal: {metadata} = 1000000 + 1").body
    second = ast.parse(f"literal: {metadata} = value").body
    assert Unifier().unify_blocks([first, second], [{}, {}]) is not None
    assert (
        Unifier().unify_blocks(
            [
                ast.parse("match value:\n case 1000000: pass").body,
                ast.parse("match value:\n case 1000001: pass").body,
            ],
            [{}, {}],
        )
        is None
    )
    assert (
        Unifier().unify_blocks(
            [ast.parse("return f'one {value}'").body, ast.parse("return f'two {value}'").body],
            [{}, {}],
        )
        is None
    )


def test_literal_membership_keeps_ordinary_hash_and_equality_effects(tmp_path: Path) -> None:
    modulus = sys.hash_info.modulus
    source = "\n".join(
        f"def {name}(value):\n    matched = value in {{{left}, {right}}}\n    subtotal = 11\n    total = subtotal * 13\n    return matched, total\n"
        for name, left, right in (("f", modulus, modulus * 2), ("g", modulus * 3, modulus * 4))
    )

    class Probe:
        def __init__(self) -> None:
            self.events: list[str] = []

        def __hash__(self) -> int:
            self.events.append("hash")
            return 0

        def __eq__(self, other: object) -> bool:
            self.events.append(repr(other))
            return False

    def effects(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
        namespace: dict[str, object] = {}
        exec(text, namespace)
        result = []
        for name in ("f", "g"):
            function = namespace[name]
            assert isinstance(function, FunctionType)
            probe = Probe()
            value: object = function(probe)
            result.append((repr(value), tuple(probe.events)))
        return tuple(result)

    original = tmp_path / "input.py"
    original.write_text(source)
    output = tmp_path / "output.py"
    before = effects(source)
    _dry(original, output)
    generated = output.read_text() if output.exists() else source
    assert effects(generated) == before


def test_literal_carrier_preserves_same_module_and_cross_module_cluster_correlations(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="pool-cluster"\nversion="0"\n')
    body = "def {name}(value):\n    literal = (1000000, 1000000)\n    subtotal = value + 11\n    total = subtotal * {factor}\n    return literal, total\n"
    (project / "a.py").write_text(
        body.format(name="f", factor=5) + body.format(name="f_again", factor=7)
    )
    (project / "b.py").write_text("import a\n" + body.format(name="g", factor=9))
    observer = "import a,b; print(a.f(1)[0] is a.f_again(1)[0]); print(a.f(1)[0] is b.g(1)[0]); print(a.f(1), a.f_again(1), b.g(1))"

    def observe(project: Path) -> str:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys; sys.path.insert(0,{str(project)!r}); " + observer,
            ],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    before = observe(project)
    assert before.startswith("True\nFalse\n")
    engine = UnificationRefactorEngine(
        cross_module_helpers=True, settings=Settings.from_environ({"TOWEL_WORKERS": "1"})
    )
    proposals = engine.analyze_files(
        [str(project / "a.py"), str(project / "b.py")], progress="none"
    )
    assert len(proposals) == 1
    assert len(proposals[0].replacements) == 2
    output = tmp_path / "output"
    report = _dry(project, output, cross_module=True)
    assert "Applied 1 refactoring(s) across 1 file(s)" in report
    assert observe(output) == before


@pytest.mark.parametrize(
    "literal",
    [
        "1e309 - 1e309",
        "0.0 * 1e309",
        "1e309 / 1e309",
        "1e308 * 1e308 - 1e308 * 1e308",
        "1e309j - 1e309j",
        "0.0 * 1e309j",
        "1e309j / 1e309j",
        "(" + str(10**308) + " / 1 * 10) - (" + str(10**308) + " / 1 * 10)",
        "(2 ** -1 * " + str(10**308) + " * 100) - (2 ** -1 * " + str(10**308) + " * 100)",
        "(" + "2**-1" + " * (2**100)" * 11 + ") - (" + "2**-1" + " * (2**100)" * 11 + ")",
        "(1e309 - 1e309,)",
        "(1, (1e309 - 1e309,))",
    ],
)
def test_complete_cli_preserves_distinct_nan_compiler_occurrences(
    tmp_path: Path, literal: str
) -> None:
    source = "\n".join(
        f"def {name}(value):\n    first = {literal}\n    second = {literal}\n"
        f"    subtotal = value + {factor}\n    total = subtotal * 11\n    return first, second, total\n"
        for name, factor in (("f", 5), ("g", 7))
    )
    observer = "print(f(1)[0] is g(1)[0]); print(f(1)[0] is f(1)[1]); print([f(1)[0]] == [g(1)[0]])"

    def observe(text: str) -> str:
        run = subprocess.run(
            [sys.executable, "-I", "-B", "-c", text + "\n" + observer],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run.returncode == 0, run.stderr
        return run.stdout

    before = observe(source)
    assert before == "False\nFalse\nFalse\n"
    original = tmp_path / "input.py"
    original.write_text(source)
    output = tmp_path / "output.py"
    _dry(original, output)
    generated = output.read_text() if output.exists() else source
    assert observe(generated) == before


def test_default_typed_carrier_preserves_float_operand_and_outside_identity(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="typed-pool"\nversion="0"\nrequires-python=">=3.9"\n'
        "[tool.mypy]\nstrict=true\n"
    )
    for module, function, factor in (("a", "f", 5), ("b", "g", 7)):
        prefix = "import a\n" if module == "b" else ""
        (project / f"{module}.py").write_text(
            prefix + f"outside = 1000000.25\ndef {function}(value: float) -> tuple[float, float]:\n"
            "    literal = 1000000.25\n    subtotal = value + literal\n"
            f"    total = subtotal * literal\n    return literal, total + {factor}\n"
        )

    def observe(root: Path) -> str:
        script = """import a,b
expected = None
events = []
class Probe(float):
    def __add__(self, other):
        events.append(other is expected)
        return Probe(float(self) + float(other))
    def __mul__(self, other):
        events.append(other is expected)
        return float(self) * float(other)
for function, outside in ((a.f,a.outside),(b.g,b.outside)):
    expected = outside
    literal, total = function(Probe(1))
    print(literal is outside, type(literal).__name__, total)
print(a.f(1)[0] is b.g(1)[0], events)
"""
        run = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys; sys.path.insert(0,{str(root)!r});\n" + script,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run.returncode == 0, run.stderr
        return run.stdout

    before = observe(project)
    assert before.endswith("False [True, True, True, True]\n")
    output = tmp_path / "output"
    report = _dry(project, output, cross_module=True, typed=True)
    assert "Applied 2 refactoring(s) across 2 file(s)" in report
    assert observe(output) == before
    helpers = [
        node
        for path in output.glob("*.py")
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func")
    ]
    assert len(helpers) == 1
    helper = helpers[0]
    parameters = helper.args.posonlyargs + helper.args.args
    assert len(parameters) <= 5
    assert all(parameter.annotation is not None for parameter in parameters)
    checked = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-incremental", str(output)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_default_typed_heterogeneous_carrier_keeps_precise_selectors(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="typed-heterogeneous-pool"\nversion="0"\nrequires-python=">=3.9"\n'
        "[tool.mypy]\nstrict=true\n"
    )
    declarations = (
        "outside_int = 1000000\noutside_float = 1000000.25\n"
        "outside_str = 'a label with spaces'\noutside_bytes = b'a byte label'\n"
        "outside_tuple = (1000002,1000003)\n"
    )
    for module, function, factor in (("a", "f", 5), ("b", "g", 7)):
        prefix = "import a\n" if module == "b" else ""
        (project / f"{module}.py").write_text(
            prefix
            + declarations
            + f"def {function}(value: int) -> tuple[int,float,str,bytes,tuple[int,int],int]:\n"
            "    number = 1000000\n    fraction = 1000000.25\n"
            "    label = 'a label with spaces'\n    data = b'a byte label'\n"
            "    samples = (1000002,1000003)\n"
            f"    subtotal = value + {factor}\n    total = subtotal * 11\n"
            "    return number,fraction,label,data,samples,total\n"
        )
    (project / "consumer.py").write_text(
        "from a import f\nfrom b import g\n"
        "first: tuple[int,float,str,bytes,tuple[int,int],int] = f(1)\n"
        "second: tuple[int,float,str,bytes,tuple[int,int],int] = g(1)\n"
    )

    def observe(root: Path) -> str:
        script = """import a,b
names = ('outside_int','outside_float','outside_str','outside_bytes','outside_tuple')
for module, function in ((a,a.f),(b,b.g)):
    values = function(1)
    print(tuple(type(value).__name__ for value in values), values[-1])
    print(tuple(value is getattr(module,name) for value,name in zip(values,names)))
print(tuple(left is right for left,right in zip(a.f(1)[:5],b.g(1)[:5])))
"""
        run = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys;sys.path.insert(0,{str(root)!r});\n" + script,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run.returncode == 0, run.stderr
        return run.stdout

    before = observe(project)
    assert before.count("(True, True, True, True, True)") == 2
    assert before.endswith("(False, False, False, False, False)\n")
    output = tmp_path / "output"
    report = _dry(project, output, cross_module=True, typed=True)
    assert "Applied 2 refactoring(s) across 2 file(s)" in report
    assert observe(output) == before
    helpers = [
        node
        for path in output.glob("*.py")
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func")
    ]
    assert len(helpers) == 1
    helper = helpers[0]
    parameters = helper.args.posonlyargs + helper.args.args
    assert len(parameters) <= 5
    carrier = next(
        argument
        for argument in parameters
        if argument.annotation is not None and "bytes" in ast.unparse(argument.annotation)
    )
    annotation = carrier.annotation
    assert annotation is not None
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        annotation = ast.parse(annotation.value, mode="eval").body
    assert isinstance(annotation, ast.Subscript) and isinstance(annotation.slice, ast.Tuple)
    assert [ast.unparse(element) for element in annotation.slice.elts] == [
        "int",
        "float",
        "str",
        "bytes",
        "tuple[int, int]",
    ]
    for checker, arguments in (
        ("mypy", ["--strict", "--no-incremental", str(output)]),
        ("pyright", [str(output)]),
    ):
        pytest.importorskip(checker)
        for checked_root in (project, output):
            checked_arguments = (
                ["--strict", "--no-incremental", "."] if checker == "mypy" else ["."]
            )
            checked = subprocess.run(
                [sys.executable, "-m", checker, *checked_arguments],
                cwd=checked_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert checked.returncode == 0, checked.stdout + checked.stderr


def test_whole_root_thunk_preserves_unused_branch_and_exception_order(tmp_path: Path) -> None:
    source = "\n".join(
        f"def {name}(value):\n    print('start')\n    if value:\n        literal = +{label!r}\n"
        "    else:\n        literal = 'safe'\n"
        f"    subtotal = value + {factor}\n    total = subtotal * 11\n    return literal,total\n"
        for name, label, factor in (("f", "unsupported a", 5), ("g", "unsupported b", 7))
    )
    observer = """for function in (f,g):
    for value in (0,1):
        try:
            print(function(value))
        except TypeError as error:
            print(type(error).__name__,error.args)
"""

    def observe(text: str) -> str:
        run = subprocess.run(
            [sys.executable, "-I", "-B", "-c", text + "\n" + observer],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run.returncode == 0, run.stderr
        return run.stdout

    before = observe(source)
    assert before.count("start\n") == 4 and before.count("TypeError") == 2
    original = tmp_path / "input.py"
    original.write_text(source)
    output = tmp_path / "output.py"
    report = _dry(original, output)
    assert "Applied 1 refactoring" in report
    assert observe(output.read_text()) == before

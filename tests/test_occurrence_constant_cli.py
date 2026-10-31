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

"""Complete-CLI occurrence vectors preserve actual operand identities and effects."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import towel


@dataclass(frozen=True)
class _Case:
    name: str
    rows: tuple[tuple[str, ...], ...]
    common: str = "2"
    formatting: str = ""


CASES = (
    _Case("offset_and_shared_two", (("1",), ("2",))),
    _Case("repeated_vector", (("1", "1"), ("2", "2"))),
    _Case("permuted_vectors", (("1", "2"), ("2", "1"))),
    _Case("three_callers", (("1", "2", "3"), ("2", "3", "1"), ("3", "1", "2"))),
    _Case("bool_and_int_vectors", (("True", "1"), ("False", "0"))),
    _Case("float_and_int_vectors", (("1.0", "1"), ("2.0", "2"))),
    _Case("signed_zero_vectors", (("-0.0", "0.0"), ("-1.0", "1.0"))),
    _Case("wide_int_offsets", ((str(10**80),), (str(10**80 + 1),)), str(10**80)),
)

DRIVER = """import gc
import importlib

EVENTS = []
MODE = 'ok'
NEXT = 0
OFFSETS = ()
COMMON = None
SPEC = None
OFFSET_INDEX = 0

def operand(value):
    return (type(value).__name__, repr(value))

class Result(float):
    def __new__(cls, value):
        global NEXT
        result = float.__new__(cls, value)
        result.serial = NEXT
        NEXT += 1
        EVENTS.append(('new-result', result.serial))
        return result
    def __add__(self, other):
        EVENTS.append(('result-add', self.serial, operand(other)))
        if MODE == 'add-error':
            raise ValueError('addition failed')
        return Result(float(self) + float(other))
    def __radd__(self, other):
        EVENTS.append(('result-radd', self.serial, operand(other), other is OFFSETS[0]))
        if MODE == 'add-error':
            raise ValueError('addition failed')
        return Result(float(other) + float(self))
    def __mul__(self, other):
        EVENTS.append(('result-mul', self.serial, operand(other)))
        if MODE == 'mul-error':
            raise ValueError('multiply failed')
        return Result(float(self) * float(other))
    def __repr__(self):
        EVENTS.append(('repr', self.serial))
        if MODE == 'repr-error':
            raise ValueError('repr failed')
        return float.__repr__(self)
    def __str__(self):
        EVENTS.append(('str', self.serial))
        if MODE == 'str-error':
            raise ValueError('str failed')
        return float.__repr__(self)
    def __format__(self, spec):
        EVENTS.append(('format', self.serial, spec, spec is SPEC))
        if MODE == 'format-error':
            raise ValueError('format failed')
        return float.__format__(self, spec)
    def __del__(self):
        EVENTS.append(('drop-result', self.serial))

class Value(int):
    def __add__(self, other):
        global OFFSET_INDEX
        expected = OFFSETS[OFFSET_INDEX]
        EVENTS.append(('input-add', operand(other), other is expected))
        OFFSET_INDEX += 1
        if MODE == 'add-error':
            raise ValueError('addition failed')
        return Result(int(self) + float(other))
    def __mul__(self, other):
        EVENTS.append(('input-mul', operand(other), other is COMMON))
        if MODE == 'mul-error':
            raise ValueError('multiply failed')
        return Result(int(self) * float(other))
    def __format__(self, spec):
        EVENTS.append(('input-format', spec, spec is SPEC))
        if MODE == 'format-error':
            raise ValueError('format failed')
        return 'value:' + spec
    def __del__(self):
        EVENTS.append(('drop-input', int(self)))

modules = [importlib.import_module(name) for name, _, _ in FUNCTIONS]
print('common-aliases', [[left.COMMON is right.COMMON for right in modules] for left in modules])
for module_name, function_name, index in FUNCTIONS:
    module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    OFFSETS = getattr(module, 'EXPECTED' + str(index))
    COMMON = module.COMMON
    SPEC = module.SPEC
    for given in (-2, 0, 5):
        returned = None
        try:
            returned = function(given)
            print(function_name, 'plain', given, type(returned).__name__, repr(returned))
        except Exception as error:
            print(function_name, 'plain', given, type(error).__name__, str(error))
        finally:
            del returned
    for MODE in ('ok', 'add-error', 'mul-error', 'repr-error', 'str-error', 'format-error'):
        EVENTS.clear()
        NEXT = 0
        OFFSET_INDEX = 0
        returned = None
        try:
            returned = function(Value(3))
            print(function_name, MODE, 'value', type(returned).__name__, repr(returned))
        except Exception as error:
            print(function_name, MODE, 'error', type(error).__name__, str(error))
        finally:
            del returned
        gc.collect()
        print(EVENTS)
"""


def _body(case: _Case, row: tuple[str, ...]) -> str:
    if case.formatting:
        expression = f"x + {row[0]} + x * {case.common}"
        if case.formatting == "nested_width":
            text = f'f"[{{x:>{{{row[0]} + x * {case.common}}}}}]"'
        else:
            suffix = {
                "plain": "",
                "repr": "!r",
                "str": "!s",
                "ascii": "!a",
                "spec": ":>20",
                "repr_spec": "!r:>20",
            }[case.formatting]
            text = f'f"head: {{{expression}{suffix}}} tail"'
        return (
            f"    text = {text}\n"
            "    upper = text.upper()\n"
            "    lower = text.lower()\n"
            "    combined = upper + lower\n"
            "    return text, upper, lower, combined\n"
        )
    lines = [f"    value0 = x + {row[0]}", f"    scale = x * {case.common}"]
    lines.extend(f"    value{index} = x + {literal}" for index, literal in enumerate(row[1:], 1))
    lines.append("    total = value0 + scale")
    lines.extend(f"    total = total + value{index}" for index in range(1, len(row)))
    # Keep enough original work for the actual CLI path, even for a single column.
    lines.extend(["    checked = total + 11", "    return checked"])
    return "\n".join(lines) + "\n"


def _sources(case: _Case, cross_module: bool) -> tuple[dict[str, str], str]:
    sources: dict[str, str] = {}
    functions = []
    for index, row in enumerate(case.rows):
        module = chr(ord("a") + index) if cross_module else "m"
        name = f"f{index}"
        prefix = "import a\n" if cross_module and index else ""
        expected = ", ".join(row) + ("," if len(row) == 1 else "")
        definitions = f"EXPECTED{index} = ({expected})\n"
        if module + ".py" not in sources:
            definitions = prefix + f"COMMON = {case.common}\nSPEC = '>20'\n" + definitions
        annotation = "tuple[str, str, str, str]" if case.formatting else "float"
        definitions += f"def {name}(x: float) -> {annotation}:\n" + _body(case, row) + "\n"
        sources[module + ".py"] = sources.get(module + ".py", "") + definitions
        functions.append((module, name, index))
    return sources, "FUNCTIONS = " + repr(functions) + "\n" + DRIVER


def _observe(project: Path, output: Path, program: str) -> tuple[int, str, str]:
    output.mkdir()
    original: dict[str, bytes] = {}
    for path in project.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(project))
        original[relative] = path.read_bytes()
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original[relative])
    driver = output / "observe.py"
    assert not driver.exists()
    driver.write_text(program)
    ran = subprocess.run(
        [sys.executable, "-B", str(driver)],
        cwd=output,
        env={
            **os.environ,
            "PYTHONPATH": str(output),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert not list(output.rglob("*.pyc"))
    assert all((output / relative).read_bytes() == source for relative, source in original.items())
    return ran.returncode, ran.stdout, ran.stderr


def _dry(project: Path, output: Path, *, cross_module: bool, memo: bool = True) -> int:
    original = {
        str(path.relative_to(project)): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file() and path.suffix in {".py", ".toml"}
    }
    runtime = Path(towel.__file__).resolve().parents[1]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(runtime)!r});"
        "sys.argv=['towel',*sys.argv[1:]];"
    )
    if memo:
        bootstrap += "runpy.run_module('towel.cli',run_name='__main__')"
    else:
        bootstrap += (
            "from towel.unification.bounded_cache import memoization_disabled\n"
            "with memoization_disabled():\n"
            "    runpy.run_module('towel.cli',run_name='__main__')\n"
        )
    command = [
        sys.executable,
        "-B",
        "-c",
        bootstrap,
        "dry",
        str(project),
        str(output),
        "--no-interactive",
        "--min-lines",
        "3",
        "--progress",
        "none",
    ]
    if cross_module:
        command.append("--cross-module")
    ran = subprocess.run(
        command,
        cwd=project.parent,
        env={**os.environ, "TOWEL_WORKERS": "1", "PYTHONHASHSEED": "0"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    (output.parent / (output.name + ".cli.log")).write_text(ran.stdout + ran.stderr)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert all((project / path).read_bytes() == value for path, value in original.items())
    applied = sum(int(count) for count in re.findall(r"Applied (\d+) refactoring\(s\)", ran.stdout))
    changed = any(
        (output / path).read_bytes() != value
        for path, value in original.items()
        if path.endswith(".py")
    )
    assert changed == (applied > 0), ran.stdout
    return applied


def _project(root: Path, sources: dict[str, str]) -> Path:
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname="occurrence-cli"\nversion="0"\n[tool.mypy]\nstrict=true\n'
    )
    for name, source in sources.items():
        (root / name).write_text(source)
    return root


def _assert_moved_and_bounded(output: Path, names: tuple[str, ...]) -> None:
    functions = [
        node
        for path in output.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.FunctionDef)
    ]
    helpers = [node for node in functions if node.name.startswith("__extracted_func_")]
    assert helpers
    assert all(len(node.args.args) + len(node.args.posonlyargs) <= 5 for node in helpers)
    # A positive must recover the differing arithmetic, not merely a common suffix.
    callers = [node for node in functions if node.name in names]
    assert len(callers) == len(names)
    assert all(
        not any(isinstance(child, ast.BinOp) for child in ast.walk(node)) for node in callers
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_complete_typed_formatted_cli_recovers_occurrence_vectors(
    tmp_path: Path, case: _Case
) -> None:
    sources, program = _sources(case, False)
    project = _project(tmp_path / "input", sources)
    before = _observe(project, tmp_path / "observed-before", program)
    output = tmp_path / "output"
    assert _dry(project, output, cross_module=False) > 0
    _assert_moved_and_bounded(output, tuple(f"f{index}" for index in range(len(case.rows))))
    assert _observe(output, tmp_path / "observed-after", program) == before
    assert before[0] == 0


@pytest.mark.parametrize("common", ["1000000", "1000000.25"])
@pytest.mark.parametrize("cross_module", [False, True], ids=["same-module", "cross-module"])
def test_retained_operand_keeps_its_caller_pool_identity(
    tmp_path: Path, common: str, cross_module: bool
) -> None:
    other = "1000001" if common == "1000000" else "1000001.25"
    case = _Case("pooled_operand", ((common,), (other,)), common)
    sources, program = _sources(case, cross_module)
    project = _project(tmp_path / "input", sources)
    before = _observe(project, tmp_path / "observed-before", program)
    output = tmp_path / "output"
    assert _dry(project, output, cross_module=cross_module) >= (2 if cross_module else 1)
    _assert_moved_and_bounded(output, ("f0", "f1"))
    assert _observe(output, tmp_path / "observed-after", program) == before
    assert before[0] == 0
    events = [ast.literal_eval(line) for line in before[1].splitlines() if line.startswith("[")]
    multiplications = [event for trace in events for event in trace if event[0] == "input-mul"]
    assert multiplications and all(event[-1] is True for event in multiplications)


@pytest.mark.parametrize(
    "formatting", ["plain", "repr", "str", "ascii", "spec", "repr_spec", "nested_width"]
)
def test_evaluated_fstring_constants_recover_without_changing_format_effects(
    tmp_path: Path, formatting: str
) -> None:
    case = _Case("formatted_offsets", (("1",), ("2",)), formatting=formatting)
    sources, program = _sources(case, False)
    project = _project(tmp_path / "input", sources)
    before = _observe(project, tmp_path / "observed-before", program)
    output = tmp_path / "output"
    assert _dry(project, output, cross_module=False) > 0
    _assert_moved_and_bounded(output, ("f0", "f1"))
    assert _observe(output, tmp_path / "observed-after", program) == before
    assert before[0] == 0


def test_cli_memo_disabled_and_reparsed_copy_preserve_occurrence_behavior(tmp_path: Path) -> None:
    case = _Case("copied_permutation", (("1", "2"), ("2", "1")))
    sources, program = _sources(case, False)
    outputs = []
    for index, memo in enumerate((True, False, True)):
        project = _project(tmp_path / f"input-{index}", sources.copy())
        before = _observe(project, tmp_path / f"observed-before-{index}", program)
        output = tmp_path / f"output-{index}"
        assert _dry(project, output, cross_module=False, memo=memo) > 0
        _assert_moved_and_bounded(output, ("f0", "f1"))
        observed = _observe(output, tmp_path / f"observed-after-{index}", program)
        assert observed == before and before[0] == 0
        outputs.append(ast.dump(ast.parse((output / "m.py").read_text()), include_attributes=False))
    assert outputs[0] == outputs[1] == outputs[2]


@pytest.mark.parametrize("expression", ["OFFSET", "supply()"], ids=["module-name", "call"])
def test_mixed_constant_expression_boundary_preserves_effects_and_failures(
    tmp_path: Path, expression: str
) -> None:
    source = (
        "OFFSET = 2\nEVENTS: list[object] = []\nFAIL = False\n"
        "def supply() -> int:\n"
        "    EVENTS.append('supply')\n"
        "    if FAIL:\n"
        "        raise ValueError('supplier failed')\n"
        "    return OFFSET\n\n"
    )
    for index, offset in enumerate(("1", expression)):
        source += (
            f"def f{index}(x: int) -> int:\n"
            f"    first = x + {offset}\n"
            "    scale = x * 2\n"
            "    total = first + scale\n"
            "    checked = total + 11\n"
            "    return checked\n\n"
        )
    program = """import gc
import m
MODE = 'ok'
class Value(int):
    def __add__(self, other):
        m.EVENTS.append(('add', type(other).__name__, repr(other)))
        if MODE == 'add-error':
            raise ValueError('operand failed')
        return int(self) + other
    def __mul__(self, other):
        m.EVENTS.append(('mul', type(other).__name__, repr(other)))
        if MODE == 'mul-error':
            raise ValueError('multiplication failed')
        return int(self) * other
    def __del__(self):
        m.EVENTS.append('drop-input')
for function in (m.f0, m.f1):
    for m.OFFSET in (1, 2, 1000000):
        for m.FAIL in (False, True):
            for MODE in ('ok', 'add-error', 'mul-error'):
                m.EVENTS.clear()
                try:
                    print(function.__name__, m.OFFSET, m.FAIL, MODE, function(Value(3)))
                except Exception as error:
                    print(function.__name__, m.OFFSET, m.FAIL, MODE, type(error).__name__, str(error))
                gc.collect()
                print(m.EVENTS)
"""
    project = _project(tmp_path / "input", {"m.py": source})
    before = _observe(project, tmp_path / "observed-before", program)
    output = tmp_path / "output"
    applied = _dry(project, output, cross_module=False)
    if expression == "OFFSET":
        # Mutable module loads are a deliberate boundary of the occurrence-only recovery.
        assert applied == 0
    else:
        assert applied > 0
        _assert_moved_and_bounded(output, ("f0", "f1"))
    assert _observe(output, tmp_path / "observed-after", program) == before
    assert before[0] == 0 and "operand failed" in before[1]
    if expression == "supply()":
        assert "supplier failed" in before[1] and "'supply'" in before[1]

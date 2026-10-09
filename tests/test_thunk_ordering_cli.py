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

"""Actual CLI ordering preserves stores, exceptions, identities and owned lifetimes."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import towel
from towel.unification.structural_memo import load_substitution, store_substitution
from towel.unification.substitution import Substitution
from towel.unification.thunk_inlining import (
    inline_leading_thunks,
    reorder_eligible_thunk_parameters,
)


@dataclass(frozen=True)
class OrderingCase:
    name: str
    statements: tuple[str, ...]
    deferred: bool = False
    signature: str = "d"
    definitions: str = ""
    refusal: str = ""


_CASES = (
    OrderingCase("pair", ("d[~1] = +1", "d[~2] = +2")),
    OrderingCase("cluster", ("d[~1] = +1", "d[~2] = +2", "d[~3] = +3")),
    OrderingCase("invalid_key", ("d[~1] = +1", "d[~1.0] = +2")),
    OrderingCase("invalid_rhs", ("d[~1] = +1", "d[~2] = ~1.0")),
    OrderingCase("two_errors", ("d[~1] = +1", "d[~1.0] = ~'invalid'"), True),
    OrderingCase(
        "pooled_identity",
        ("d[~1000000] = +1000000", "d[~1000001] = +1000001"),
        definitions="KEYS = (~1000000, ~1000001)\nVALUES = (+1000000, +1000001)\n",
    ),
    OrderingCase(
        "signature", ("d[~1] = +1", "d[~2] = +2"), signature="d, /, unused=None, *, note=None"
    ),
    OrderingCase("unknown_result", ("d[~1] = value(1)", "d[~2] = value(2)"), True),
    OrderingCase("duplicated", ("d[~1] = +1 + +1", "d[~2] = +2 + +2"), True),
    OrderingCase(
        "conditional",
        ("d[~1] = (+1 if flag else +2)", "d[~2] = (+3 if flag else +4)"),
        True,
        "d, flag",
        refusal="ownership_parameter_budget",
    ),
    OrderingCase("property", ("d.target[~1] = +1", "d.target[~1.0] = +2"), True),
    OrderingCase("callback", ("d.prepare()\n    d[~1] = +1", "d.prepare()\n    d[~2] = +2"), True),
    OrderingCase(
        "module_reads",
        ("d[~KEY_LEFT] = +VALUE_LEFT", "d[~KEY_RIGHT] = +VALUE_RIGHT"),
        True,
        definitions="KEY_LEFT = 1\nKEY_RIGHT = 2\nVALUE_LEFT = 1\nVALUE_RIGHT = 2\n",
        refusal="module_data_lookup",
    ),
    OrderingCase("unsafe_cluster", ("d[~1] = +1", "d[~2] = +2", "d[~3] = value(3)"), True),
    OrderingCase("unbound", ("d[~MISSING_LEFT] = +1", "d[~MISSING_RIGHT] = +2"), True),
)

_TAIL = "    marker = (1, 2, 3, 4, 5, 6, 7, 8)\n    same = (marker, marker)\n    return d, same\n"
_MODES = ("ok", "store-error", "property-error", "prepare-error", "value-error", "false", "rebind")
_DRIVER = r"""import gc
import json
import py_compile
import sys

if sys.argv[4] == 'warm':
    py_compile.compile(sys.argv[1] + '/case.py', doraise=True)
sys.path.insert(0, sys.argv[1])
import case
EVENTS = []
MODE = sys.argv[2]

class Token:
    def __init__(self, label):
        self.label = label
        EVENTS.append(('new', label))
    def __repr__(self):
        return 'Token(' + self.label + ')'
    def __bool__(self):
        EVENTS.append(('bool', self.label))
        return MODE != 'false'
    def __del__(self):
        EVENTS.append(('del', self.label))

class Destination:
    def __init__(self, label='destination'):
        self.label = label
        EVENTS.append(('new', label))
    def __setitem__(self, key, value):
        EVENTS.append(('store', type(key).__name__, repr(key), type(value).__name__, repr(value),
                       tuple(key is x for x in getattr(case, 'KEYS', ())),
                       tuple(value is x for x in getattr(case, 'VALUES', ()))))
        if MODE == 'store-error':
            raise ArithmeticError('store')
    @property
    def target(self):
        EVENTS.append(('target',))
        if MODE == 'property-error':
            raise ArithmeticError('target')
        return Destination('child')
    def prepare(self):
        EVENTS.append(('prepare',))
        if MODE == 'prepare-error':
            raise ArithmeticError('prepare')
    def __del__(self):
        EVENTS.append(('del', self.label))

def value(number):
    EVENTS.append(('value', number))
    if MODE == 'value-error':
        raise ArithmeticError('value')
    return Token('value-' + str(number))

case.value = value
if MODE == 'rebind':
    case.KEY_LEFT = 11
    case.KEY_RIGHT = 22
    case.VALUE_LEFT = 33
    case.VALUE_RIGHT = 44
for name in json.loads(sys.argv[3]):
    EVENTS.clear()
    try:
        function = getattr(case, name)
        if sys.argv[5] == 'signature':
            result = function(Destination(), Token('unused'), note=Token('note'))
        elif sys.argv[5] == 'conditional':
            result = function(Destination(), Token('flag'))
        else:
            result = function(Destination())
        EVENTS.append(('return', result[1][0] is result[1][1], result[1][0]))
        del result
    except Exception as error:
        EVENTS.append(('exception', type(error).__name__, str(error)))
    gc.collect()
    print(json.dumps([name, EVENTS]))
"""


def _environment() -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "TOWEL_WORKERS": "1",
    }


def _source(case: OrderingCase) -> str:
    return case.definitions + "\n".join(
        f"def {name}({case.signature}):\n    {statement}\n{_TAIL}"
        for name, statement in zip(("left", "right", "third"), case.statements)
    )


def _extract(
    tmp_path: Path, case: OrderingCase, *, uncached: bool = False, budget: int = 5
) -> tuple[Path, Path, str]:
    project = tmp_path / ("uncached" if uncached else "cached")
    project.mkdir()
    output = tmp_path / (project.name + "-output")
    source = _source(case)
    original = project / "case.py"
    original.write_text(source)
    configuration = project / "pyproject.toml"
    config = '[project]\nname="ordering-cli-control"\nversion="0.0.0"\nrequires-python=">=3.11"\n'
    configuration.write_text(config)
    runtime = Path(towel.__file__).resolve().parents[1]
    bootstrap = (
        "import runpy,sys;" + f"sys.path.insert(0,{str(runtime)!r});"
        "sys.argv=['towel',*sys.argv[1:]];"
    )
    if uncached:
        bootstrap += (
            "from towel.unification.bounded_cache import memoization_disabled;"
            "context=memoization_disabled();context.__enter__();"
        )
    bootstrap += "runpy.run_module('towel.cli',run_name='__main__')"
    ran = subprocess.run(
        [
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
            "--max-parameters",
            str(budget),
            "--progress",
            "none",
        ],
        cwd=tmp_path,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    (tmp_path / (project.name + "-cli.log")).write_text(ran.stdout + ran.stderr)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert original.read_text() == source
    assert configuration.read_text() == config
    transformed = (output / "case.py").read_text()
    if case.refusal:
        assert case.refusal in ran.stdout
        assert transformed == source
    elif budget == 5:
        assert sum(map(int, re.findall(r"Applied (\d+) refactoring\(s\)", ran.stdout))) > 0
        assert transformed != source
    return project, output, transformed


def _helpers(source: str) -> list[ast.FunctionDef]:
    return [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func_")
    ]


def _thunks(helper: ast.FunctionDef) -> set[str]:
    parameters = {argument.arg for argument in helper.args.posonlyargs + helper.args.args}
    return {
        node.func.id
        for node in ast.walk(helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in parameters
        and not node.args
        and not node.keywords
    }


def _observe(
    driver: Path, project: Path, case: OrderingCase, mode: str, cache: str
) -> tuple[int, str, str]:
    names = '["left", "right", "third"]' if len(case.statements) == 3 else '["left", "right"]'
    ran = subprocess.run(
        [sys.executable, "-B", str(driver), str(project), mode, names, cache, case.name],
        cwd=driver.parent,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert ran.returncode == 0 and ran.stderr == "", ran.stderr
    assert len(ran.stdout.splitlines()) == len(case.statements)
    return ran.returncode, ran.stdout, ran.stderr


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
def test_ordered_cli_arguments_preserve_complete_observations(
    tmp_path: Path, case: OrderingCase
) -> None:
    project, output, transformed = _extract(tmp_path, case)
    helpers = _helpers(transformed)
    store_helpers = [
        helper
        for helper in helpers
        if any(
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Subscript) for target in node.targets)
            for node in ast.walk(helper)
        )
    ]
    if not case.refusal:
        assert store_helpers, "the actual extraction must include the assignment"
    if case.refusal:
        assert not helpers
    elif case.deferred:
        if case.name == "unsafe_cluster":
            original_third = ast.parse(_source(case)).body[-1]
            written_third = ast.parse(transformed).body[-1]
            assert ast.dump(written_third) == ast.dump(original_third)
        else:
            assert any(_thunks(helper) for helper in store_helpers)
    else:
        assert len(store_helpers) == 1
        helper = store_helpers[0]
        assert not _thunks(helper)
        assert [argument.arg for argument in helper.args.posonlyargs[:2]] == [
            "__param_1",
            "__param_0",
        ]
        calls = [
            node
            for node in ast.walk(ast.parse(transformed))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == helper.name
        ]
        assert len(calls) == len(case.statements)
        assert all(
            not isinstance(argument, ast.Lambda) for call in calls for argument in call.args[:2]
        )
    if case.name == "unsafe_cluster":
        optimized = [helper for helper in store_helpers if not _thunks(helper)]
        assert len(optimized) == 1
        callers = {
            function.name
            for function in ast.parse(transformed).body
            if isinstance(function, ast.FunctionDef)
            and any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == optimized[0].name
                for node in ast.walk(function)
            )
        }
        assert callers == {"left", "right"}
    for helper in helpers:
        assert len(helper.args.posonlyargs) + len(helper.args.args) <= 5
    before_functions = {
        node.name: node
        for node in ast.parse(_source(case)).body
        if isinstance(node, ast.FunctionDef)
    }
    after_functions = {
        node.name: node for node in ast.parse(transformed).body if isinstance(node, ast.FunctionDef)
    }
    assert all(
        ast.dump(before.args) == ast.dump(after_functions[name].args)
        for name, before in before_functions.items()
    )
    driver = tmp_path / "observe.py"
    driver.write_text(_DRIVER)
    records: list[str] = []
    caches = ("cold", "warm") if case.name == "pooled_identity" else ("cold",)
    for cache in caches:
        for mode in _MODES:
            before = _observe(driver, project, case, mode, cache)
            after = _observe(driver, output, case, mode, cache)
            records.append(repr((cache, mode, before, after)))
            assert before == after, (case.name, cache, mode, before, after)
    (tmp_path / "observations.txt").write_text("\n".join(records) + "\n")


def test_memoization_does_not_change_ordered_helpers(tmp_path: Path) -> None:
    case = _CASES[1]
    _, _, cached = _extract(tmp_path, case)
    _, _, uncached = _extract(tmp_path, case, uncached=True)
    assert ast.dump(ast.parse(cached)) == ast.dump(ast.parse(uncached))
    assert all(not _thunks(helper) for helper in _helpers(cached))


def test_argument_budget_includes_the_ownership_handoff(tmp_path: Path) -> None:
    _, _, transformed = _extract(tmp_path, _CASES[0], budget=3)
    helpers = _helpers(transformed)
    assert all(len(helper.args.posonlyargs) + len(helper.args.args) <= 3 for helper in helpers)
    assert not any(
        isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
        for helper in helpers
        for node in ast.walk(helper)
    )


def test_rehydrated_substitutions_keep_source_nodes_and_ordered_results() -> None:
    blocks = [list(ast.parse(source).body) for source in ("d[~1] = +1", "d[~2] = +2")]
    substitution = Substitution()
    for name, attribute in (("key", "target"), ("value", "value")):
        expressions: list[tuple[int, ast.AST]] = []
        for index, block in enumerate(blocks):
            assignment = block[0]
            assert isinstance(assignment, ast.Assign)
            if attribute == "target":
                target = assignment.targets[0]
                assert isinstance(target, ast.Subscript)
                expression = target.slice
            else:
                expression = assignment.value
            expressions.append((index, expression))
        substitution.param_expressions[name] = expressions
        substitution.function_params[name] = []
        substitution.params_used_as_callee.add(name)
    stored = store_substitution(substitution, blocks, [{}, {}])
    original_dumps = tuple(
        ast.dump(node, include_attributes=True) for block in blocks for node in block
    )
    reparsed = copy.deepcopy(blocks)
    first, renames = load_substitution(stored, reparsed)
    second, _ = load_substitution(stored, reparsed)
    helper = ast.parse("def helper(key, value, /, d):\n    d[key()] = value()\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    order = {"key": 0, "value": 1, "d": 2}
    ordered = reorder_eligible_thunk_parameters(helper, first, order)
    assert ordered == {"value": 0, "key": 1, "d": 2}
    assert inline_leading_thunks(helper, first, ordered) == {"key", "value"}
    assert first.inlined_parameters == {"key", "value"}
    assert second.inlined_parameters == set()
    assert store_substitution(second, reparsed, renames) == stored
    assert order == {"key": 0, "value": 1, "d": 2}
    assert (
        tuple(ast.dump(node, include_attributes=True) for block in blocks for node in block)
        == original_dumps
    )
    assert (
        tuple(ast.dump(node, include_attributes=True) for block in reparsed for node in block)
        == original_dumps
    )
    assert all(original is not loaded for original, loaded in zip(blocks[0], reparsed[0]))

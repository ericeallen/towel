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

"""Branch-local CLI extraction preserves lexical bindings and ordinary lifetimes."""

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
class BranchCase:
    name: str
    first: str
    second: str
    useful: bool
    signature: str = ""
    definitions: str = "FLAG = True\nINNER = True\nanswer = 999\n"
    third: str | None = None


def _arithmetic(addend: int, indent: str = "        ", answer: str = "answer") -> str:
    return (
        f"{indent}subtotal = 20\n{indent}doubled = subtotal * 2\n"
        f"{indent}{answer} = doubled + {addend}\n"
    )


def _branch(addend: int) -> str:
    return "    if FLAG:\n" + _arithmetic(addend) + "    return answer\n"


def _getter_budget(count: int) -> BranchCase:
    definitions = (
        "FLAG = True\nINNER = True\nanswer = 999\ndef consume(*values: object) -> None:\n    pass\n"
    )
    for side, offset in (("one", 0), ("two", 10)):
        for index in range(count):
            definitions += f"def g_{side}_{index}() -> int:\n    return {index + offset + 2}\n"
    bodies: list[str] = []
    for side in ("one", "two"):
        extra = f" + g_{side}_5()" if count == 6 else ""
        bodies.append(
            f"    if FLAG:\n        subtotal = g_{side}_0() + g_{side}_1()\n"
            f"        doubled = subtotal * g_{side}_2()\n"
            f"        answer = doubled + g_{side}_3() + g_{side}_4(){extra}\n"
            "    return answer\n"
        )
    return BranchCase(
        "five_arguments" if count == 5 else "six_arguments",
        bodies[0],
        bodies[1],
        count == 5,
        definitions=definitions,
    )


_CASES = (
    _getter_budget(5),
    _getter_budget(6),
    BranchCase(
        "additional_unsafe_caller",
        _branch(3),
        _branch(7),
        True,
        third="    answer = make('prior')\n" + _branch(11),
    ),
    BranchCase(
        "continuation",
        _branch(3),
        _branch(7).replace(
            "    return answer\n", "    print('second', answer)\n    return answer * 2\n"
        ),
        True,
    ),
    BranchCase(
        "nested_if",
        "    if FLAG:\n        if INNER:\n"
        + _arithmetic(3, "            ")
        + "    return answer\n",
        "    if FLAG:\n        if INNER:\n"
        + _arithmetic(7, "            ")
        + "    return answer\n",
        True,
    ),
    BranchCase(
        "both_arms",
        "    if FLAG:\n        if INNER:\n            subtotal = 20\n        else:\n            subtotal = 30\n        doubled = subtotal * 2\n        answer = doubled + 3\n    return answer\n",
        "    if FLAG:\n        if INNER:\n            subtotal = 20\n        else:\n            subtotal = 30\n        doubled = subtotal * 2\n        answer = doubled + 7\n    return answer\n",
        True,
    ),
    BranchCase(
        "callback_condition",
        "    if active():\n" + _arithmetic(3) + "    consume(answer)\n    return answer\n",
        "    if active():\n" + _arithmetic(7) + "    consume(answer)\n    return answer\n",
        True,
    ),
    BranchCase(
        "renamed_outputs",
        _branch(3),
        "    if FLAG:\n        part = 20\n        twice = part * 2\n        final = twice + 7\n    return final\n",
        True,
    ),
    BranchCase(
        "owned_arithmetic_inputs",
        "    if FLAG:\n        subtotal = a + b\n        doubled = subtotal * c\n        answer = doubled + 3\n    return answer\n",
        "    if FLAG:\n        subtotal = a + b\n        doubled = subtotal * c\n        answer = doubled + 7\n    return answer\n",
        False,
        "a: int, b: int, c: int",
        definitions="FLAG = True\nINNER = True\nanswer = 999\ndef consume(*values: object) -> None:\n    pass\n",
    ),
    BranchCase(
        "owned_arithmetic_four",
        "    if FLAG:\n        subtotal = a + b\n        doubled = subtotal * c\n        answer = doubled + d + 3\n    return answer\n",
        "    if FLAG:\n        subtotal = a + b\n        doubled = subtotal * c\n        answer = doubled + d + 7\n    return answer\n",
        False,
        "a: int, b: int, c: int, d: int",
        definitions="FLAG = True\nINNER = True\nanswer = 999\ndef consume(*values: object) -> None:\n    pass\n",
    ),
    BranchCase(
        "earlier_read",
        "    consume(answer)\n" + _branch(3),
        "    consume(answer)\n" + _branch(7),
        False,
    ),
    BranchCase(
        "prior_binding",
        "    answer = make('prior')\n" + _branch(3),
        "    answer = make('prior')\n" + _branch(7),
        False,
    ),
    BranchCase("parameter_output", _branch(3), _branch(7), False, "answer"),
    BranchCase(
        "delete_output",
        "    if FLAG:\n        subtotal = 20\n        answer = subtotal + 3\n        del answer\n    return answer\n",
        "    if FLAG:\n        subtotal = 20\n        answer = subtotal + 7\n        del answer\n    return answer\n",
        False,
    ),
    BranchCase(
        "partial_initialization",
        "    if FLAG:\n        subtotal = 20\n        doubled = subtotal * 2\n        if INNER:\n            answer = doubled + 3\n    return answer\n",
        "    if FLAG:\n        subtotal = 20\n        doubled = subtotal * 2\n        if INNER:\n            answer = doubled + 7\n    return answer\n",
        False,
    ),
    BranchCase(
        "closure_capture",
        "    def reader():\n        return answer\n"
        + _branch(3).replace("return answer", "return reader()"),
        "    def reader():\n        return answer\n"
        + _branch(7).replace("return answer", "return reader()"),
        False,
    ),
    BranchCase(
        "return_in_block",
        "    if FLAG:\n        subtotal = 20\n        answer = subtotal + 3\n        return answer\n    return answer\n",
        "    if FLAG:\n        subtotal = 20\n        answer = subtotal + 7\n        return answer\n    return answer\n",
        False,
    ),
    BranchCase(
        "raise_in_block",
        "    if FLAG:\n        subtotal = 20\n        answer = subtotal + 3\n        raise ValueError(answer)\n    return answer\n",
        "    if FLAG:\n        subtotal = 20\n        answer = subtotal + 7\n        raise ValueError(answer)\n    return answer\n",
        False,
    ),
    BranchCase(
        "sibling_binding",
        "    if INNER:\n        answer = make('sibling')\n" + _branch(3),
        "    if INNER:\n        answer = make('sibling')\n" + _branch(7),
        False,
    ),
    BranchCase(
        "opaque_factories",
        "    if FLAG:\n        first = make('first')\n        second = make('second')\n        answer = combine(first, second, 3)\n    consume(first, second, answer)\n    return first, second, answer\n",
        "    if FLAG:\n        first = make('first')\n        second = make('second')\n        answer = combine(first, second, 7)\n    consume(first, second, answer)\n    return first, second, answer\n",
        False,
    ),
    BranchCase(
        "opaque_aliases",
        "    if FLAG:\n        first = make('first')\n        alias = first\n        answer = combine(first, alias, 3)\n    return first, alias, answer\n",
        "    if FLAG:\n        first = make('first')\n        alias = first\n        answer = combine(first, alias, 7)\n    return first, alias, answer\n",
        False,
    ),
    BranchCase(
        "property_outputs",
        "    if FLAG:\n        first = make('first')\n        second = first.item\n        answer = combine(first, second, 3)\n    return first, second, answer\n",
        "    if FLAG:\n        first = make('first')\n        second = first.item\n        answer = combine(first, second, 7)\n    return first, second, answer\n",
        False,
    ),
)

_MODES = (
    "true",
    "false",
    "inner-false",
    "condition-error",
    "consume-error",
    "first-error",
    "second-error",
    "property-error",
    "operator-error",
    "aliases",
)
_DRIVER = r"""import gc
import json
import sys
sys.path.insert(0, sys.argv[1])
import case
EVENTS = []
MODE = sys.argv[2]

class Flag:
    def __init__(self, name):
        self.name = name
    def __bool__(self):
        EVENTS.append(('condition', self.name))
        if MODE == 'condition-error':
            raise ArithmeticError('condition-' + self.name)
        return MODE != 'false' and not (MODE == 'inner-false' and self.name == 'inner')

class Value:
    def __init__(self, label):
        self.label = label
        EVENTS.append(('new', label))
    def __repr__(self):
        return 'Value(' + self.label + ')'
    @property
    def item(self):
        EVENTS.append(('property', self.label))
        if MODE == 'property-error':
            raise ArithmeticError('property')
        return Value('item')
    def __del__(self):
        EVENTS.append(('del', self.label))

class Number(int):
    def __new__(cls, number, label):
        obj = int.__new__(cls, number)
        obj.label = label
        EVENTS.append(('new', label))
        return obj
    def __add__(self, other):
        EVENTS.append(('add', self.label, repr(other)))
        if MODE in ('operator-error', 'first-error'):
            raise ArithmeticError('operator')
        return Number(int(self) + int(other), self.label + '+')
    def __mul__(self, other):
        EVENTS.append(('mul', self.label, repr(other)))
        if MODE in ('operator-error', 'second-error'):
            raise ArithmeticError('operator')
        return Number(int(self) * int(other), self.label + '*')
    def __del__(self):
        EVENTS.append(('del', self.label))

def active():
    return bool(case.FLAG)

def make(label):
    EVENTS.append(('make', label))
    if MODE == label + '-error':
        raise ArithmeticError(label)
    return Value(label)

def combine(first, second, number):
    EVENTS.append(('combine', first.label, second.label, number, first is second))
    if MODE == 'operator-error':
        raise ArithmeticError('combine')
    if MODE == 'aliases':
        return first
    return Value('answer-' + str(number))

def consume(*values):
    EVENTS.append(('consume', tuple((type(value).__name__, repr(value)) for value in values)))
    if MODE == 'consume-error':
        raise ArithmeticError('consume')

class Manager:
    def __enter__(self):
        EVENTS.append(('enter',))
    def __exit__(self, kind, value, traceback):
        EVENTS.append(('exit', None if kind is None else kind.__name__))

case.FLAG = Flag('outer')
case.INNER = Flag('inner')
case.active = active
case.make = make
case.combine = combine
case.consume = consume
case.manager = Manager
if sys.argv[3] in ('five_arguments', 'six_arguments'):
    count = 5 if sys.argv[3] == 'five_arguments' else 6
    for side, offset in (('one', 0), ('two', 10)):
        for index in range(count):
            def getter(side=side, index=index, offset=offset):
                EVENTS.append(('getter', side, index))
                if (MODE == 'first-error' and index == 0) or (MODE == 'second-error' and index == 1):
                    raise ArithmeticError('getter-' + str(index))
                return Number(index + offset + 2, side + '-' + str(index))
            setattr(case, 'g_' + side + '_' + str(index), getter)
for name in (('first', 'second', 'third') if sys.argv[3] == 'additional_unsafe_caller' else ('first', 'second')):
    EVENTS.clear()
    try:
        function = getattr(case, name)
        if sys.argv[3] == 'owned_arithmetic_inputs':
            result = function(Number(1, 'a'), Number(2, 'b'), Number(3, 'c'))
        elif sys.argv[3] == 'owned_arithmetic_four':
            result = function(Number(1, 'a'), Number(2, 'b'), Number(3, 'c'), Number(4, 'd'))
        elif sys.argv[3] == 'parameter_output':
            result = function(Value('parameter'))
        else:
            result = function()
        if isinstance(result, tuple):
            EVENTS.append(('return', tuple((type(value).__name__, repr(value)) for value in result),
                           tuple(value is other for value in result for other in result)))
        else:
            EVENTS.append(('return', type(result).__name__, repr(result)))
        del result
    except Exception as error:
        EVENTS.append(('exception', type(error).__name__, str(error)))
    gc.collect()
    print(json.dumps([name, EVENTS]))
"""


def _source(case: BranchCase) -> str:
    before, after = case.second.rsplit("    return ", 1)
    second = before + "    consume('second-continuation')\n    return " + after
    source = (
        case.definitions
        + f"\ndef first({case.signature}):\n{case.first}\ndef second({case.signature}):\n{second}"
    )
    if case.third is not None:
        source += f"\ndef third({case.signature}):\n{case.third}"
    return source


def _environment() -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "TOWEL_WORKERS": "1",
    }


def _extract(tmp_path: Path, case: BranchCase, uncached: bool = False) -> tuple[Path, Path, str]:
    source = tmp_path / ("uncached" if uncached else "source")
    source.mkdir()
    output = tmp_path / (source.name + "-output")
    content = _source(case)
    original = source / "case.py"
    original.write_text(content)
    config = '[project]\nname="if-fresh-bindings"\nversion="0.0.0"\nrequires-python=">=3.11"\n'
    (source / "pyproject.toml").write_text(config)
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
            str(source),
            str(output),
            "--no-interactive",
            "--min-lines",
            "3",
            "--progress",
            "none",
        ],
        cwd=tmp_path,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    (tmp_path / (source.name + "-cli.log")).write_text(ran.stdout + ran.stderr)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert original.read_text() == content
    assert (source / "pyproject.toml").read_text() == config
    return source, output, (output / "case.py").read_text()


def _observe(driver: Path, project: Path, case: BranchCase, mode: str) -> tuple[int, str, str]:
    ran = subprocess.run(
        [sys.executable, "-B", str(driver), str(project), mode, case.name],
        cwd=driver.parent,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert ran.returncode == 0 and not ran.stderr, ran.stderr
    assert sum(
        line.startswith(('["first",', '["second",', '["third",'))
        for line in ran.stdout.splitlines()
    ) == (3 if case.third is not None else 2)
    return ran.returncode, ran.stdout, ran.stderr


def _helper_calls_inside_if(module: ast.Module, case: BranchCase) -> None:
    helpers = {
        node.name
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func_")
    }
    for helper in (
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name in helpers
    ):
        assert not any(
            isinstance(node, ast.Name) and node.id in ("FLAG", "active")
            for node in ast.walk(helper)
        ), "the original outer condition must remain in the caller"
    for function in module.body:
        if not isinstance(function, ast.FunctionDef) or function.name not in ("first", "second"):
            continue
        parents = {
            child: parent for parent in ast.walk(function) for child in ast.iter_child_nodes(parent)
        }
        calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in helpers
        ]
        assert calls
        for call in calls:
            cursor: ast.AST = call
            ancestors: list[ast.AST] = []
            while cursor in parents:
                cursor = parents[cursor]
                ancestors.append(cursor)
            assert sum(isinstance(node, ast.If) for node in ancestors) >= (
                2 if case.name == "nested_if" else 1
            )
            assert not any(
                isinstance(node, (ast.For, ast.While, ast.Try, ast.With, ast.Match))
                for node in ancestors
            )
            assert isinstance(
                parents[call], ast.Assign
            ), "normal continuation must receive fresh local outputs"


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
def test_if_bindings_keep_lexical_errors_and_full_observations(
    tmp_path: Path, case: BranchCase
) -> None:
    original, output, transformed = _extract(tmp_path, case)
    driver = tmp_path / "observe.py"
    driver.write_text(_DRIVER)
    records: list[str] = []
    for mode in _MODES:
        before = _observe(driver, original, case, mode)
        after = _observe(driver, output, case, mode)
        records.append(repr((mode, before, after)))
        assert before == after, (case.name, mode, before, after)
    (tmp_path / "observations.txt").write_text("\n".join(records) + "\n")
    if case.useful:
        assert (
            sum(
                map(
                    int,
                    re.findall(
                        r"Applied (\d+) refactoring\(s\)", (tmp_path / "source-cli.log").read_text()
                    ),
                )
            )
            > 0
        )
        assert transformed != _source(case)
        _helper_calls_inside_if(ast.parse(transformed), case)
    elif case.name in ("opaque_factories", "opaque_aliases", "property_outputs"):
        if ast.dump(ast.parse(transformed)) != ast.dump(ast.parse(_source(case))):
            _helper_calls_inside_if(ast.parse(transformed), case)
    else:
        assert ast.dump(ast.parse(transformed)) == ast.dump(ast.parse(_source(case)))
    functions = {
        node.name: node for node in ast.parse(transformed).body if isinstance(node, ast.FunctionDef)
    }
    assert all(
        ast.dump(functions[name].args) == ast.dump(node.args)
        for node in ast.parse(_source(case)).body
        if isinstance(node, ast.FunctionDef)
        for name in (node.name,)
    )
    if case.third is not None:
        helper_names = {name for name in functions if name.startswith("__extracted_func_")}
        for branch in (node for node in ast.walk(functions["third"]) if isinstance(node, ast.If)):
            assert not any(
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in helper_names
                and any(
                    isinstance(name, ast.Name) and name.id == "answer"
                    for target in node.targets
                    for name in ast.walk(target)
                )
                for node in ast.walk(branch)
            )
    helpers = [node for name, node in functions.items() if name.startswith("__extracted_func_")]
    assert all(len(node.args.posonlyargs) + len(node.args.args) <= 5 for node in helpers)
    if case.name == "five_arguments":
        assert any(len(node.args.posonlyargs) + len(node.args.args) == 5 for node in helpers)


@pytest.mark.parametrize(
    "name,prefix,suffix,indent",
    [
        ("loop", "    for item in (1,):\n", "", "        "),
        ("try", "    try:\n", "    finally:\n        consume('finally')\n", "        "),
        ("with", "    with manager():\n", "", "        "),
        ("match", "    match 1:\n        case 1:\n", "", "            "),
    ],
)
def test_unsupported_ancestors_keep_the_existing_binding_guard(
    tmp_path: Path, name: str, prefix: str, suffix: str, indent: str
) -> None:
    case = BranchCase(
        name,
        prefix
        + indent
        + "if FLAG:\n"
        + _arithmetic(3, indent + "    ")
        + suffix
        + "    return answer\n",
        prefix
        + indent
        + "if FLAG:\n"
        + _arithmetic(7, indent + "    ")
        + suffix
        + "    return answer\n",
        False,
    )
    test_if_bindings_keep_lexical_errors_and_full_observations(tmp_path, case)


def test_cached_and_uncached_branch_helpers_agree(tmp_path: Path) -> None:
    _, _, cached = _extract(tmp_path, next(case for case in _CASES if case.name == "continuation"))
    _, _, uncached = _extract(
        tmp_path, next(case for case in _CASES if case.name == "continuation"), uncached=True
    )
    assert cached != _source(next(case for case in _CASES if case.name == "continuation"))
    assert ast.dump(ast.parse(cached)) == ast.dump(ast.parse(uncached))


def test_source_certificates_survive_reparse_and_frozen_substitution_loading() -> None:
    import copy
    from towel.unification.if_binding_transfer import (
        IfTransferSite,
        certify_if_binding_transfer,
        verify_if_binding_transfer,
    )
    from towel.unification.models import source_digest_of
    from towel.unification.structural_memo import load_substitution, store_substitution
    from towel.unification.substitution import Substitution

    source = _source(next(case for case in _CASES if case.name == "continuation"))
    module = ast.parse(source)
    original_dump = ast.dump(module, include_attributes=True)
    functions = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in ("first", "second")
    ]
    blocks: list[list[ast.stmt]] = []
    for function in functions:
        branch = function.body[0]
        assert isinstance(branch, ast.If)
        blocks.append(branch.body)
    function = functions[0]
    nodes = blocks[0]
    site = IfTransferSite(
        source_digest_of(source),
        (function.lineno, function.col_offset),
        (nodes[0].lineno, nodes[0].col_offset, len(nodes)),
    )
    certificate = certify_if_binding_transfer(
        source, module, function, nodes, site, ("doubled", "answer")
    )
    assert certificate is not None
    substitution = Substitution()
    expressions: list[tuple[int, ast.AST]] = []
    for index, block in enumerate(blocks):
        assignment = block[-1]
        assert isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.BinOp)
        expressions.append((index, assignment.value.right))
    substitution.param_expressions["addend"] = expressions
    stored = store_substitution(substitution, blocks, [{}, {}])
    for fresh in (copy.deepcopy(module), ast.parse(source)):
        fresh_functions = [node for node in fresh.body if isinstance(node, ast.FunctionDef)]
        fresh_blocks: list[list[ast.stmt]] = []
        for fresh_function in fresh_functions:
            branch = fresh_function.body[0]
            assert isinstance(branch, ast.If)
            fresh_blocks.append(branch.body)
        assert verify_if_binding_transfer(certificate, source, fresh)
        loaded, renames = load_substitution(stored, fresh_blocks)
        assert store_substitution(loaded, fresh_blocks, renames) == stored
        assert all(
            original is not replacement and ast.dump(original) == ast.dump(replacement)
            for (_, original), (_, replacement) in zip(
                expressions, loaded.param_expressions["addend"]
            )
        )
        loaded.param_expressions.clear()
        again, _ = load_substitution(stored, fresh_blocks)
        assert "addend" in again.param_expressions
        assert ast.dump(fresh, include_attributes=True) == original_dump
    assert ast.dump(module, include_attributes=True) == original_dump


def test_certificates_reject_foreign_nodes_and_changed_source() -> None:
    import copy
    from dataclasses import replace
    from towel.unification.if_binding_transfer import (
        IfTransferSite,
        certify_if_binding_transfer,
        verify_if_binding_transfer,
    )
    from towel.unification.models import source_digest_of

    source = _source(next(case for case in _CASES if case.name == "continuation"))
    module = ast.parse(source)
    function = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "first"
    )
    branch = function.body[0]
    assert isinstance(branch, ast.If)
    nodes = branch.body
    site = IfTransferSite(
        source_digest_of(source),
        (function.lineno, function.col_offset),
        (nodes[0].lineno, nodes[0].col_offset, len(nodes)),
    )
    certificate = certify_if_binding_transfer(
        source, module, function, nodes, site, ("doubled", "answer")
    )
    assert certificate is not None
    assert (
        certify_if_binding_transfer(
            source, module, function, copy.deepcopy(nodes), site, ("doubled", "answer")
        )
        is None
    )
    assert (
        certify_if_binding_transfer(source, module, function, nodes[::2], site, ("answer",)) is None
    )
    assert not verify_if_binding_transfer(certificate, "\n" + source, ast.parse("\n" + source))
    assert not verify_if_binding_transfer(
        replace(certificate, site=replace(site, module_digest="0" * 64)), source, module
    )
    altered = copy.deepcopy(module)
    altered_function = next(
        node for node in altered.body if isinstance(node, ast.FunctionDef) and node.name == "first"
    )
    altered_branch = altered_function.body[0]
    assert isinstance(altered_branch, ast.If)
    altered_branch.body.reverse()
    assert not verify_if_binding_transfer(certificate, source, altered)


def test_frozen_certificate_does_not_retain_original_ast_owners() -> None:
    import gc
    import weakref
    from towel.unification.if_binding_transfer import IfTransferSite, certify_if_binding_transfer
    from towel.unification.models import source_digest_of

    source = _source(next(case for case in _CASES if case.name == "continuation"))
    module = ast.parse(source)
    function = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "first"
    )
    branch = function.body[0]
    assert isinstance(branch, ast.If)
    nodes = branch.body
    site = IfTransferSite(
        source_digest_of(source),
        (function.lineno, function.col_offset),
        (nodes[0].lineno, nodes[0].col_offset, len(nodes)),
    )
    certificate = certify_if_binding_transfer(
        source, module, function, nodes, site, ("doubled", "answer")
    )
    assert certificate is not None
    owners = tuple(weakref.ref(node) for node in (module, function, branch, *nodes))
    del module, function, branch, nodes
    gc.collect()
    assert all(owner() is None for owner in owners)
    assert certificate.returned_names == ("doubled", "answer")

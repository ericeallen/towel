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

"""Redirect review 2: mutation primitives may escape through any expression."""

import ast
from pathlib import Path

import pytest

from tests.test_callback_parameters import run_script
from tests.test_shape_review import dry
from towel.unification.rebinding import rebinding_reason

# The middle line differs in shape from its neighbors: polish refuses a helper
# holding a run of same-shaped independent statements. It still prints 4.
STATEMENT_PAIR = """def total(value):
    print(value + 1)
    print(value * 2)
    print(value + 3)

def report(value):
    print("before")
    print(value + 1)
    print(value * 2)
    print(value + 3)
    print("after", value)

"""

ESCAPES = {
    "argument": 'def mutate(setter, module):\n    setter(module, "total", REPLACEMENT)\nmutate(PRIMITIVE, mod)\n',
    "keyword": 'def mutate(module, setter):\n    setter(module, "total", REPLACEMENT)\nmutate(mod, setter=PRIMITIVE)\n',
    "default": 'def mutate(module, setter=PRIMITIVE):\n    setter(module, "total", REPLACEMENT)\nmutate(mod)\n',
    "keyword_default": 'def mutate(module, *, setter=PRIMITIVE):\n    setter(module, "total", REPLACEMENT)\nmutate(mod)\n',
    "return": 'def primitive():\n    return PRIMITIVE\nprimitive()(mod, "total", REPLACEMENT)\n',
    "container": '[PRIMITIVE][0](mod, "total", REPLACEMENT)\n',
}


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("escape", list(ESCAPES))
@pytest.mark.parametrize("primitive", ["setattr", "delattr"])
def test_escaped_mutator_preserves_statement_duplicate(
    tmp_path: Path, strict: bool, escape: str, primitive: str
) -> None:
    """Redirect review 2: argument/default variants also break statement redirects."""
    mutation = ESCAPES[escape].replace("PRIMITIVE", primitive)
    mutation = (
        mutation.replace("REPLACEMENT", "lambda value: print(-999)")
        if primitive == "setattr"
        else mutation.replace(", REPLACEMENT", "")
    )
    source = tmp_path / "input.py"
    source.write_text(STATEMENT_PAIR + "import __main__ as mod\n" + mutation + "report(2)\n")
    output = tmp_path / "output.py"
    before = run_script(source)
    assert before == (0, "before\n3\n4\n5\nafter 2\n", "")
    dry(source, output, strict, "--complete-scope", "--no-format")
    assert run_script(output) == before
    assert "def __extracted_func_" in output.read_text()


@pytest.mark.parametrize(
    "reference",
    [
        "setattr",
        "delattr",
        "builtins.setattr",
        "builtins.delattr",
        "setter",
        "deleter",
        "mock.patch",
        "patcher",
        "patcher.object",
        "mod.__setattr__",
        "mod.__delattr__",
    ],
)
@pytest.mark.parametrize(
    "escape",
    [
        "consume(REFERENCE)",
        "consume(setter=REFERENCE)",
        "def consume(setter=REFERENCE): pass",
        "def consume(*, setter=REFERENCE): pass",
        "def consume(): return REFERENCE",
        "mutators = [REFERENCE]",
        "mutators = {'setter': REFERENCE}",
        "mutator = REFERENCE if flag else fallback",
        "def consume(): yield REFERENCE",
        "consume(*(REFERENCE,))",
        "consume(**{'setter': REFERENCE})",
        'REFERENCE.__call__(mod, "total", replacement)',
    ],
)
def test_mutation_reference_escape_is_located(reference: str, escape: str) -> None:
    """Redirect review 2: aliases and nested expressions cannot hide a mutation primitive."""
    source = (
        STATEMENT_PAIR
        + "import builtins, unittest.mock as mock\n"
        + "from builtins import setattr as setter, delattr as deleter\n"
        + "from unittest.mock import patch as patcher\n"
        + escape.replace("REFERENCE", reference)
        + "\n"
    )
    target = ast.parse(source).body[0]
    assert isinstance(target, ast.FunctionDef)
    reason = rebinding_reason({"mod.py": source}, "mod.py", target)
    assert reason is not None and "mod.py:" in reason and "may rebind" in reason


@pytest.mark.parametrize("primitive", ["setattr", "delattr", "setter", "deleter"])
@pytest.mark.parametrize(
    "receiver", ["module", "default_module", "global_module", "holder.module", "get_module()"]
)
def test_unknown_mutation_receiver_blocks_redirect(primitive: str, receiver: str) -> None:
    """Redirect ruling 3: unknown object identity never proves a mutation harmless."""
    arguments = f'{receiver}, "total"'
    if primitive in ("setattr", "setter"):
        arguments += ", replacement"
    source = (
        STATEMENT_PAIR
        + "from builtins import setattr as setter, delattr as deleter\n"
        + "def mutate(module, default_module=global_module):\n"
        + f"    {primitive}({arguments})\n"
    )
    target = ast.parse(source).body[0]
    assert isinstance(target, ast.FunctionDef)
    assert rebinding_reason({"mod.py": source}, "mod.py", target) is not None


@pytest.mark.parametrize("strict", [False, True])
def test_clean_statement_redirect_remains_available(tmp_path: Path, strict: bool) -> None:
    """Redirect review 2: declining an escaped primitive must not disable clean statements."""
    source, output = tmp_path / "input.py", tmp_path / "output.py"
    source.write_text(STATEMENT_PAIR + "report(2)\n")
    dry(source, output, strict, "--complete-scope", "--no-format")
    assert "    total(value)\n" in output.read_text()
    assert "def __extracted_func_" not in output.read_text()
    assert run_script(output) == run_script(source)


@pytest.mark.parametrize(
    "call",
    [
        'setattr(mod, "other", replacement)',
        'delattr(mod, "other")',
        'setter(mod, "other", replacement)',
        'mock.patch.object(mod, "other", replacement)',
        'mock.patch("mod.other", replacement)',
    ],
)
def test_direct_mutation_of_another_name_does_not_block_redirect(call: str) -> None:
    """Redirect review 2: a direct call with a known unrelated name does not escape."""
    source = (
        STATEMENT_PAIR
        + "import unittest.mock as mock\nfrom builtins import setattr as setter\n"
        + call
        + "\n"
    )
    target = ast.parse(source).body[0]
    assert isinstance(target, ast.FunctionDef)
    assert rebinding_reason({"mod.py": source}, "mod.py", target) is None


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("primitive", ["setattr", "delattr"])
@pytest.mark.parametrize("alias", [False, True])
def test_unrelated_import_cannot_hide_an_escaped_mutation_alias(
    tmp_path: Path, strict: bool, primitive: str, alias: bool
) -> None:
    """Redirect review 2: aliases in separate scopes cannot erase a possible mutator."""
    arguments = 'module, "total"'
    if primitive == "setattr":
        arguments += ", lambda value: print(-999)"
    name = "setter" if alias else primitive
    imported = f"from builtins import {primitive} as setter\n" if alias else ""
    source, output = tmp_path / "input.py", tmp_path / "output.py"
    source.write_text(
        STATEMENT_PAIR
        + imported
        + f"def mutate(module, fn={name}):\n"
        + f"    fn({arguments})\n"
        + f"def unrelated():\n    from math import sin as {name}\n    return {name}(0)\n"
        + "import __main__ as mod\nmutate(mod)\nreport(2)\n"
    )
    before = run_script(source)
    assert before == (0, "before\n3\n4\n5\nafter 2\n", "")
    dry(source, output, strict, "--complete-scope", "--no-format")
    assert run_script(output) == before

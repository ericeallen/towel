# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Captured builtin mappings affect free reads and implicit compiler operations.

Imports use a frame's import hook without an explicit name load. Comprehensions
inline in Python 3.12+, whereas generator and nested function bodies capture the
module's current mapping when their functions are created. The first iterable
and definition headers still execute in the original caller's frame.
"""

from __future__ import annotations

import ast
import dis
import sys
from pathlib import Path
import textwrap
from types import CodeType

import pytest

from tests.test_parameterize_builtins import _refactor, _run, _write
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import builtin_mapping_reads, module_resolved_names


def _reads(body: str, *, parameters: str = "data", prefix: str = "") -> frozenset[str]:
    source = (
        prefix
        + "__builtins__ = {}\n"
        + f"def sample({parameters}):\n"
        + textwrap.indent(textwrap.dedent(body).strip() + "\n", "    ")
    )
    tree = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "sample"
    )
    return builtin_mapping_reads(function, function.body, analyzer, source)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("return custom(data)", {"custom"}),
        ("return __class__(data)", {"__class__"}),
        ("global __class__\nreturn __class__(data)", {"__class__"}),
        ("return len(data)", {"len"}),
        ("global custom\ncustom += data[0]\nreturn custom", {"custom"}),
        ("return __debug__", set()),
        ("return [custom for custom in data]", set()),
        ("return [item for item in custom(data)]", {"custom"}),
        ("return [custom(item) for item in data]", {"custom"}),
        ("return {custom(item) for item in data}", {"custom"}),
        ("return {item: custom(item) for item in data}", {"custom"}),
        ("return [custom for custom in custom(data)]", {"custom"}),
        ("return [1 for custom.value in data]", {"custom"}),
        ("return {1 for custom[0] in data}", {"custom"}),
        ("return {1: 2 for data[custom] in data}", {"custom"}),
        ("return (1 for custom[0] in data)", set()),
        ("return [(left, right) for left in data for right in custom(left)]", {"custom"}),
        ("return [item for item in data if custom(item)]", {"custom"}),
        ("return (custom(item) for item in data)", set()),
        ("return (item for item in data if custom(item))", set()),
        ("return ((left, right) for left in data for right in custom(left))", set()),
        ("return (item for item in custom(data))", {"custom"}),
        ("return lambda: custom(data)", set()),
        ("return lambda value=custom(data): value", {"custom"}),
        ("return lambda: (lambda value=custom(data): value)()", set()),
        ("return lambda value=(lambda inner=custom(data): inner)(): value", {"custom"}),
        ("def inner(value):\n    return custom(value)\nreturn inner", set()),
        ("def inner(value=custom(data)):\n    return value\nreturn inner", {"custom"}),
        ("def inner(*, value=custom(data)):\n    return value\nreturn inner", {"custom"}),
        ("async def inner(value=custom(data)):\n    return value\nreturn inner", {"custom"}),
        ("@custom(data)\ndef inner(value):\n    return value\nreturn inner", {"custom"}),
        ("def inner(value: custom(data)):\n    return value\nreturn inner", {"custom"}),
        ("def inner(value) -> custom(data):\n    return value\nreturn inner", {"custom"}),
        ("def inner():\n    import provider\nreturn inner", set()),
        ("import provider\nreturn provider", {"__import__"}),
        ("from provider import value\nreturn value", {"__import__"}),
        ("class Inner:\n    value = custom(data)\nreturn Inner", {"__build_class__"}),
        ("class Inner(custom(data)):\n    pass\nreturn Inner", {"__build_class__", "custom"}),
        ("value: custom(data)\nreturn data[0]", set()),
        ("value: custom(data) = data[0]\nreturn value", set()),
        ("assert data[0]\nreturn data[0]", set()),
        ('return f"{data[0]!r}"', set()),
    ],
)
def test_direct_mapping_reads_follow_compiler_frame_timing(body: str, expected: set[str]) -> None:
    assert _reads(body) == frozenset(expected)


def test_module_bound_names_can_still_fall_back_to_a_captured_mapping() -> None:
    assert _reads("return custom(data)", prefix="custom = None\n") == frozenset({"custom"})


def test_local_parameters_keep_their_ordinary_read_behavior() -> None:
    assert _reads("return custom(data)", parameters="data, custom") == frozenset()


def test_postponed_nested_annotations_do_not_read_the_callers_mapping() -> None:
    assert (
        _reads(
            "def inner(value: custom(data)) -> custom(data):\n    return value\nreturn inner",
            prefix="from __future__ import annotations\n",
        )
        == frozenset()
    )


@pytest.mark.parametrize(
    ("definition", "module_read"),
    [
        ("def sample():\n    return __class__", True),
        ("def sample():\n    global __class__\n    return __class__", True),
        ("class Owner:\n    def sample(self):\n        return __class__", False),
        (
            "class Owner:\n    def sample(self):\n        global __class__\n        return __class__",
            True,
        ),
        (
            "class Owner:\n    global __class__\n    def sample(self):\n        return __class__",
            False,
        ),
        (
            "class Owner:\n    def outer(self):\n        def sample():\n            return __class__\n        return sample",
            False,
        ),
        (
            "class Owner:\n    def outer(self):\n        global __class__\n        def sample():\n            return __class__\n        return sample",
            True,
        ),
        (
            "class Owner:\n    def outer(self):\n        __class__ = 1\n        def sample():\n            return __class__\n        return sample",
            False,
        ),
        (
            "def outer():\n    __class__ = 1\n    class Owner:\n        def sample(self):\n            return __class__\n    return Owner",
            False,
        ),
        (
            "class Owner:\n    def sample(self):\n        nonlocal __class__\n        return __class__",
            False,
        ),
        (
            "def outer():\n    __class__ = 1\n    def sample():\n        nonlocal __class__\n        return __class__\n    return sample",
            False,
        ),
    ],
)
def test_class_spelling_is_exempt_only_when_the_compiler_resolves_a_lexical_cell(
    definition: str, module_read: bool
) -> None:
    source = "__builtins__ = {}\n" + definition + "\n"
    tree = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "sample"
    )
    pending = [compile(source, "class-cell-fixture", "exec")]
    matching: list[CodeType] = []
    while pending:
        code = pending.pop()
        if code.co_name == "sample":
            matching.append(code)
        pending.extend(value for value in code.co_consts if isinstance(value, CodeType))
    assert len(matching) == 1
    instructions = list(dis.get_instructions(matching[0]))
    opcode = "LOAD_GLOBAL" if module_read else "LOAD_DEREF"
    assert any(
        instruction.opname == opcode and instruction.argval == "__class__"
        for instruction in instructions
    )
    expected = frozenset({"__class__"}) if module_read else frozenset()
    assert module_resolved_names(function, analyzer, {"__class__"}, exact=True) == expected
    assert builtin_mapping_reads(function, function.body, analyzer, source) == expected


@pytest.mark.parametrize("operation", ["import provider", "from provider import value"])
@pytest.mark.parametrize("shadow", ["local", "module"])
def test_import_opcode_ignores_lexical_import_hook_shadowing(operation: str, shadow: str) -> None:
    parameters = "data, __import__" if shadow == "local" else "data"
    prefix = "__import__ = None\n" if shadow == "module" else ""
    assert _reads(operation, parameters=parameters, prefix=prefix) == frozenset({"__import__"})


@pytest.mark.parametrize("shadow", ["local", "module"])
def test_build_class_opcode_ignores_lexical_build_class_shadowing(shadow: str) -> None:
    parameters = "data, __build_class__" if shadow == "local" else "data"
    prefix = "__build_class__ = None\n" if shadow == "module" else ""
    assert _reads("class Inner:\n    pass", parameters=parameters, prefix=prefix) == frozenset(
        {"__build_class__"}
    )


@pytest.mark.parametrize(
    ("body", "opcode"),
    [
        ("import provider", "IMPORT_NAME"),
        ("from provider import value", "IMPORT_NAME"),
        ("class Inner:\n    pass", "LOAD_BUILD_CLASS"),
        ("assert False", "LOAD_ASSERTION_ERROR"),
    ],
)
def test_implicit_mapping_operations_have_no_explicit_hook_name_load(
    body: str, opcode: str
) -> None:
    module = compile("def sample():\n" + textwrap.indent(body, "    "), "fixture", "exec")
    function = next(value for value in module.co_consts if isinstance(value, CodeType))
    instructions = list(dis.get_instructions(function))
    assert any(instruction.opname == opcode for instruction in instructions)
    assert not any(
        instruction.opname in {"LOAD_NAME", "LOAD_GLOBAL"}
        and instruction.argval in {"__import__", "__build_class__", "AssertionError"}
        for instruction in instructions
    )


_TAIL = "    y = x + 2\n    z = y * 3\n    return z\n"


def _module(body: str, first_mapping: str, second_mapping: str, prefix: str = "") -> str:
    return (
        prefix
        + "def unrelated():\n    pass\n"
        + f"__builtins__ = {first_mapping}\ndef first(data):\n"
        + textwrap.indent(textwrap.dedent(body).strip() + "\n", "    ")
        + _TAIL
        + f"__builtins__ = {second_mapping}\ndef second(data):\n"
        + textwrap.indent(textwrap.dedent(body).strip() + "\n", "    ")
        + _TAIL
        + "print(first([1]), second([1]))\n"
    )


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    ("body", "first_custom", "second_custom"),
    [
        ("x = custom(data)", "lambda data: 100", "lambda data: 200"),
        ("x = (lambda value=custom(data): value)()", "lambda data: 100", "lambda data: 200"),
        (
            "values = [custom(item) for item in data]\nx = values[0]",
            "lambda data: 100",
            "lambda data: 200",
        ),
        (
            "values = [item for item in custom(data)]\nx = values[0]",
            "lambda data: [100]",
            "lambda data: [200]",
        ),
        (
            "x = 0\nfor item in (value for value in custom(data)):\n    x += item",
            "lambda data: [100]",
            "lambda data: [200]",
        ),
    ],
)
def test_custom_keys_and_eager_expressions_refuse_without_changing_source_or_execution(
    tmp_path: Path, flag: bool, body: str, first_custom: str, second_custom: str
) -> None:
    source = _module(body, '{"custom": ' + first_custom + "}", '{"custom": ' + second_custom + "}")
    path = tmp_path / "sample.py"
    _write(tmp_path, {"sample.py": source})
    before_bytes = path.read_bytes()
    before = _run(tmp_path, "sample.py")
    # Python 3.11 compiles the list body as a nested function; 3.12+ inlines it.
    # The refactoring policy must remain safe for both execution models.
    nested_list_body = sys.version_info < (3, 12) and body.startswith("values = [custom(item)")
    assert before == ("606 606\n" if nested_list_body else "306 606\n")
    applied, declined = _refactor(path, parameterize_builtins=flag)
    assert applied == 0
    assert declined.get("builtin_mapping_replaced", 0) > 0
    assert path.read_bytes() == before_bytes
    assert _run(tmp_path, "sample.py") == before


@pytest.mark.parametrize("flag", [False, True])
def test_plain_function_class_spelling_keeps_its_captured_mapping(
    tmp_path: Path, flag: bool
) -> None:
    source = _module(
        "x = __class__(data)",
        '{"__class__": lambda data: 100}',
        '{"__class__": lambda data: 200}',
    )
    path = tmp_path / "sample.py"
    _write(tmp_path, {"sample.py": source})
    before = path.read_bytes()
    assert _run(tmp_path, "sample.py") == "306 606\n"
    applied, declined = _refactor(path, parameterize_builtins=flag)
    assert applied == 0
    assert declined.get("builtin_mapping_replaced", 0) > 0
    assert path.read_bytes() == before
    assert _run(tmp_path, "sample.py") == "306 606\n"


@pytest.mark.parametrize("flag", [False, True])
def test_comprehension_store_targets_keep_their_captured_mapping(
    tmp_path: Path, flag: bool
) -> None:
    source = _module(
        "values = [1 for custom[0] in data]\nx = values[0]",
        '{"custom": first_holder}',
        '{"custom": second_holder}',
        "first_holder = [0]\nsecond_holder = [0]\n",
    )
    path = tmp_path / "sample.py"
    _write(tmp_path, {"sample.py": source})
    before = path.read_bytes()
    assert _run(tmp_path, "sample.py") == "9 9\n"
    applied, declined = _refactor(path, parameterize_builtins=flag)
    assert applied == 0
    assert declined.get("builtin_mapping_replaced", 0) > 0
    assert path.read_bytes() == before
    assert _run(tmp_path, "sample.py") == "9 9\n"


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    "body", ["import provider\nx = provider.value", "from provider import value\nx = value"]
)
def test_actual_import_hooks_stay_in_the_caller_with_its_captured_mapping(
    tmp_path: Path, flag: bool, body: str
) -> None:
    prefix = """class FirstProvider:
    value = 100
class SecondProvider:
    value = 200
def first_import(*args, **kwargs):
    return FirstProvider
def second_import(*args, **kwargs):
    return SecondProvider
"""
    source = _module(body, '{"__import__": first_import}', '{"__import__": second_import}', prefix)
    path = tmp_path / "sample.py"
    _write(tmp_path, {"sample.py": source})
    before = path.read_bytes()
    assert _run(tmp_path, "sample.py") == "306 606\n"
    applied, declined = _refactor(path, parameterize_builtins=flag)
    assert applied == 0
    assert declined.get("builtin_mapping_replaced", 0) > 0
    assert path.read_bytes() == before
    assert _run(tmp_path, "sample.py") == "306 606\n"


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("x = 0\nfor item in (custom(value) for value in data):\n    x += item", "606 606\n"),
        ("values = [item + 1 for item in data]\nx = values[0]", "12 12\n"),
        ("x = data[0] + __debug__", "12 12\n"),
        ('value = f"{data[0]!r}"\nx = data[0]', "9 9\n"),
    ],
)
def test_safe_frame_timing_and_fixed_interpreter_operations_still_extract(
    tmp_path: Path, flag: bool, body: str, expected: str
) -> None:
    source = _module(body, '{"custom": lambda data: 100}', '{"custom": lambda data: 200}')
    path = tmp_path / "sample.py"
    _write(tmp_path, {"sample.py": source})
    assert _run(tmp_path, "sample.py") == expected
    applied, _ = _refactor(path, parameterize_builtins=flag)
    assert applied == 1
    assert "__extracted_func_" in path.read_text()
    assert _run(tmp_path, "sample.py") == expected

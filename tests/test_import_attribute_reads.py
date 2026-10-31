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

"""Attribute targets do not ask whether an import provides a previous value."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Sequence

import pytest

from tests.test_names_typed_any import requires_pyright
from towel import type_baseline
from towel.type_baseline import ImportProbes, import_probes, imports_typed_as_any
from towel.type_inference import CheckSuccess, PyrightOracle


def probes(source: str, path: str = "/project/example.py") -> ImportProbes:
    result = import_probes(path, source)
    assert result is not None
    return result


@pytest.mark.parametrize(
    "statement",
    [
        "module.payload = value",
        "module.payload = module.other = value",
        "module.payload, other = values",
        "del module.payload",
        "module.payload: int",
        "module.payload: int = value",
        "for module.payload in values:\n    pass",
        "with resource() as module.payload:\n    pass",
        "values = [None for module.payload in items]",
    ],
)
def test_a_plain_target_does_not_question_the_imported_attribute(statement: str) -> None:
    result = probes("import example as module\n" + statement + "\n")
    assert not any(q.kind == "attribute" for q in result.questions)
    module = next(q for q in result.questions if q.kind == "module")
    assert imports_typed_as_any(result, [{module.key: "Any"}], "/project/example.py")


@pytest.mark.parametrize(
    ("statement", "attributes"),
    [
        ("module.payload += value", {"payload"}),
        ("module.payload **= value", {"payload"}),
        ("module.payload: module.Annotation", {"Annotation"}),
        ("module.payload = module.original", {"original"}),
        ("module.child.payload = value", {"child"}),
        ("del module.child.payload", {"child"}),
        ("module.child.payload: int", {"child"}),
        ("module.child.payload += value", {"child"}),
        ("module.payload[index] = value", {"payload"}),
        ("module.payload[module.index] = value", {"payload", "index"}),
        ("del module.payload[index]", {"payload"}),
        ("def mutate():\n    module.payload += value", {"payload"}),
    ],
)
def test_real_attribute_reads_in_targets_and_expressions_remain_questions(
    statement: str, attributes: set[str]
) -> None:
    result = probes("import example as module\n" + statement + "\n")
    assert {q.subject for q in result.questions if q.kind == "attribute"} == {
        f'"{name}" of module "example"' for name in attributes
    }


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("module.child.payload = value", set()),
        ("del module.child.payload", set()),
        ("module.child.payload: int", set()),
        ("module.child.payload += value", {"payload"}),
        ("module.child.payload[index] = value", {"payload"}),
    ],
)
def test_a_dotted_import_questions_only_reads_beyond_its_imported_path(
    statement: str, expected: set[str]
) -> None:
    result = probes("import module.child\n" + statement + "\n")
    assert {q.subject for q in result.questions if q.kind == "attribute"} == {
        f'"{name}" of module "module.child"' for name in expected
    }


def test_writes_do_not_consume_the_bounded_attribute_question_budget() -> None:
    result = probes(
        "import example as module\n"
        "module.first = 1\nmodule.second = 2\nmodule.third = 3\n"
        "value = module.payload\n"
    )
    questions = [q for q in result.questions if q.kind == "attribute"]
    assert [q.subject for q in questions] == ['"payload" of module "example"']
    assert imports_typed_as_any(result, [{questions[0].key: "Unknown"}], "/project/example.py")


def test_read_facts_are_immutable_and_leave_the_ast_unchanged() -> None:
    tree = ast.parse("module.target = module.value\nmodule.augmented += 1\n")
    before = ast.dump(tree, include_attributes=True)
    reads = type_baseline._attribute_reads(tuple(ast.walk(tree)))
    assert isinstance(reads, tuple)
    assert {node.attr for node in reads} == {"value", "augmented"}
    assert ast.dump(tree, include_attributes=True) == before
    changed = ast.parse("module.target = 1\nmodule.augmented = 1\n")
    assert type_baseline._attribute_reads(tuple(ast.walk(changed))) == ()


@pytest.mark.parametrize("import_count", [1, 40])
def test_attribute_read_inventory_is_computed_once_per_source(
    monkeypatch: pytest.MonkeyPatch, import_count: int
) -> None:
    calls = 0
    original = type_baseline._attribute_reads

    def reads(nodes: Sequence[ast.AST]) -> tuple[ast.Attribute, ...]:
        nonlocal calls
        calls += 1
        return original(nodes)

    monkeypatch.setattr(type_baseline, "_attribute_reads", reads)
    imports = "".join(f"import example as module_{index}\n" for index in range(import_count))
    result = probes(imports + "module_0.target = 1\nvalue = module_0.payload\n")
    assert any(q.subject == '"payload" of module "example"' for q in result.questions)
    assert calls == 1


@requires_pyright
@pytest.mark.parametrize("augmented", [False, True])
def test_actual_checker_distinguishes_a_write_from_a_read_before_write(
    tmp_path: Path, augmented: bool
) -> None:
    (tmp_path / "pyrightconfig.json").write_text(
        '{"pythonVersion":"3.12","typeCheckingMode":"standard"}'
    )
    source = (
        "import doctest\n"
        + ("doctest.compile += 1" if augmented else "doctest.compile = compile")
        + "  # type: ignore[attr-defined]\n"
    )
    path = tmp_path / "example.py"
    path.write_text(source)
    result = probes(source, str(path))
    oracle = PyrightOracle(language_server=False)
    try:
        checked = oracle.check_project({str(path): source})
        answers = oracle.reveal(result.requests)
    finally:
        oracle.close()
    assert isinstance(checked, CheckSuccess) and not checked.errors
    findings = imports_typed_as_any(result, [answers], str(path))
    assert bool(findings) == augmented

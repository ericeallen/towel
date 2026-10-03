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

"""A sibling attribute write does not replace an imported submodule's path."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.test_names_typed_any import requires_pyright
from towel import type_baseline
from towel.type_baseline import ImportQuestion, import_probes, imports_typed_as_any
from towel.type_inference import CheckSuccess, PyrightOracle, RevealRequest


def child_question(source: str) -> ImportQuestion:
    probes = import_probes("/project/example.py", source)
    assert probes is not None
    return next(q for q in probes.questions if q.subject == '"child" of module "package"')


@pytest.mark.parametrize(
    "statement",
    [
        "package.other = value",
        "del package.other",
        "package.other += value",
        "package.other.member = value",
        "def configure():\n    package.other = value",
        "def configure():\n    global package\n    package.other = value",
        "if enabled:\n    package.other = value",
        "def configure(package):\n    package.child = value",
    ],
)
def test_unrelated_attribute_writes_leave_the_submodule_context_available(statement: str) -> None:
    question = child_question(
        "import package\nimport package.child\n" + statement + "\nvalue = package.child\n"
    )
    assert question.attribute_context is not None
    assert question.attribute_context.expected == 'Module("package.child")'


@pytest.mark.parametrize(
    "statement",
    [
        "package = replacement",
        "del package",
        "package.child = replacement",
        "del package.child",
        "package.child += replacement",
        "package.child.member = replacement",
        "del package.child.member",
        "package.child: object",
        "def configure():\n    global package\n    package = replacement",
        "def configure():\n    global package\n    import replacement as package",
        "def configure():\n    package.child = replacement",
        "if enabled:\n    package.child.member = replacement",
        "for package.child in replacements:\n    pass",
        "values = [None for package.child in replacements]",
        "from replacement import *",
    ],
)
def test_root_or_overlapping_path_writes_still_block_the_submodule_context(statement: str) -> None:
    source = "import package\nimport package.child\n" + statement + "\nvalue = package.child\n"
    question = child_question(source)
    assert question.attribute_context is None
    probes = import_probes("/project/example.py", source)
    assert probes is not None
    assert imports_typed_as_any(probes, [{question.key: "Unknown"}], "/project/example.py")


@pytest.mark.parametrize(
    ("written", "available"),
    [("package.child", False), ("package.child.grand", False), ("package.child.other", True)],
)
def test_a_nested_module_path_checks_its_full_prefix(written: str, available: bool) -> None:
    probes = import_probes(
        "/project/example.py",
        "import package.child\nimport package.child.grand\n"
        f"{written} = replacement\nvalue = package.child.grand\n",
    )
    assert probes is not None
    question = next(q for q in probes.questions if q.subject == '"grand" of module "package.child"')
    assert (question.attribute_context is not None) == available


@pytest.mark.parametrize(
    "source",
    [
        "import package\nif enabled:\n    import package.child\nvalue = package.child\n",
        "import package\nvalue = package.child\nimport package.child\n",
        "def earlier(): return package.child\nimport package\nimport package.child\n",
    ],
)
def test_a_sibling_write_does_not_relax_import_or_read_order(source: str) -> None:
    assert child_question(source + "package.other = value\n").attribute_context is None


def test_attribute_writes_keep_the_alias_replacement_rule_conservative() -> None:
    probes = import_probes(
        "/project/example.py",
        "import math as namespace\nimport statistics as namespace\n"
        "namespace.other = value\nresult = namespace.mean([1.0])\n",
    )
    assert probes is not None
    assert any(q.subject == '"mean" of module "math"' for q in probes.questions)


def test_attribute_write_paths_are_immutable_and_separate_from_root_bindings() -> None:
    tree = ast.parse("import package\npackage.branch.member = value\n")
    original = ast.dump(tree, include_attributes=True)
    facts = type_baseline._attribute_bindings(tree, tuple(ast.walk(tree)))
    assert facts is not None
    assert facts.attribute_writes == frozenset({("package", "branch", "member")})
    assert "package" not in facts.module_writes
    assert ast.dump(tree, include_attributes=True) == original


@requires_pyright
@pytest.mark.parametrize(
    "statement",
    [
        "collections.namedtuple = collections.namedtuple",
        "collections.extra = 1  # type: ignore[attr-defined]",
        "def configure() -> None:\n    collections.extra = 1  # type: ignore[attr-defined]",
    ],
)
def test_actual_checker_identifies_the_module_despite_a_sibling_write(
    tmp_path: Path, statement: str
) -> None:
    (tmp_path / "pyrightconfig.json").write_text(
        '{"pythonVersion":"3.12","typeCheckingMode":"standard"}'
    )
    source = (
        "import collections\nimport collections.abc\n" + statement + "\nvalue = collections.abc\n"
    )
    path = str(tmp_path / "example.py")
    Path(path).write_text(source)
    probes = import_probes(path, source)
    assert probes is not None
    question = next(q for q in probes.questions if q.subject == '"abc" of module "collections"')
    original = RevealRequest(path, source, len(source.splitlines()), "", ("collections.abc",))
    oracle = PyrightOracle(language_server=False)
    try:
        checked = oracle.check_project({path: source})
        answers = oracle.reveal(probes.requests)
        original_answers = oracle.reveal([original])
    finally:
        oracle.close()
    assert isinstance(checked, CheckSuccess) and not checked.errors
    assert original_answers[path, original.line, 0] == 'Module("collections.abc")'
    assert answers[question.key] == "Unknown"
    assert question.attribute_context is not None
    assert answers[question.attribute_context.key] == 'Module("collections.abc")'
    assert imports_typed_as_any(probes, [answers], path) == ()

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

"""An imported object must be asked about in its lexical and typing context."""

from pathlib import Path

import pytest

from tests.test_names_typed_any import requires_pyright
from towel.type_baseline import ImportProbes, import_probes, imports_typed_as_any
from towel.type_inference import CheckSuccess, PyrightOracle


def probes(source: str, path: str = "/project/example.py") -> ImportProbes:
    result = import_probes(path, source)
    assert result is not None
    return result


@pytest.mark.parametrize(
    "body",
    [
        "def read(module): return module.payload",
        "def read():\n    module = object()\n    return module.payload",
        "read = lambda module: module.payload",
        "value = [module.payload for module in objects]",
        "value = {module.payload for module in objects}",
        "value = {module.payload: 1 for module in objects}",
        "value = (module.payload for module in objects)",
        "def outer(module):\n    def read(): return module.payload\n    return read",
        "def outer(module):\n    def read():\n        nonlocal module\n        return module.payload\n    return read",
    ],
)
def test_a_shadowing_local_attribute_is_not_a_module_question(body: str) -> None:
    result = probes("import example as module\n" + body + "\n")
    assert not any(q.kind == "attribute" for q in result.questions)
    module_question = next(q for q in result.questions if q.kind == "module")
    assert imports_typed_as_any(result, [{module_question.key: "Any"}], "/project/example.py")


@pytest.mark.parametrize(
    "body",
    [
        "def read(): return module.payload",
        "def read():\n    global module\n    return module.payload",
        "def read(module=module.payload): return module",
        "def read(module: module.payload): pass",
        "class C(module.payload): pass",
        "class C:\n    module = other\n    def read(self): return module.payload",
        "value = [module for module in module.payload]",
        "value = [module.payload for item in objects]",
        "read = lambda: module.payload",
        "class C:\n    value = module.payload\n    module = other",
    ],
)
def test_real_outer_module_reads_remain_questions(body: str) -> None:
    result = probes("import example as module\n" + body + "\n")
    assert any(q.subject == '"payload" of module "example"' for q in result.questions)


def test_separate_import_scopes_keep_only_their_own_attributes() -> None:
    result = probes(
        "import outer as module\n"
        "value = module.first\n"
        "def read():\n"
        "    import inner as module\n"
        "    return module.second\n"
    )
    assert {(q.line, q.subject) for q in result.questions if q.kind == "attribute"} == {
        (1, '"first" of module "outer"'),
        (4, '"second" of module "inner"'),
    }


def test_a_different_member_of_the_module_is_not_a_self_value_use() -> None:
    result = probes(
        "import typing_extensions as te\n"
        "class P(te.Protocol): pass\n"
        "class C:\n    def clone(self) -> te.Self: return self\n"
    )
    assert any(q.subject.startswith('"Self"') and q.self_context for q in result.questions)


@pytest.mark.parametrize(
    "extra",
    [
        "value = te.Self",
        "alias = te",
        "te.Self = other",
        "value: Meta[int, te.Self]",
        "value: Meta[int, consume(te.Self)]",
        "value: te.Self.attribute",
    ],
)
def test_value_or_metadata_use_does_not_identify_the_import(extra: str) -> None:
    result = probes(
        "import typing_extensions as te\n"
        "from typing import Annotated as Meta\n"
        "class P(te.Protocol): pass\n"
        "class C:\n    def clone(self) -> te.Self: return self\n" + extra + "\n"
    )
    question = next(q for q in result.questions if q.self_context is not None)
    assert imports_typed_as_any(result, [{question.key: "Unknown"}], "/project/example.py")


@requires_pyright
@pytest.mark.parametrize(
    "constructor",
    [
        ("import collections.abc as cabc", "cabc.Callable"),
        ("from collections.abc import Callable as Callback", "Callback"),
        ("import typing as t", "t.Callable"),
    ],
)
def test_real_callable_annotations_and_protocol_base_keep_typed_self(
    tmp_path: Path, constructor: tuple[str, str]
) -> None:
    (tmp_path / "pyrightconfig.json").write_text('{"pythonVersion":"3.10"}')
    imported, name = constructor
    source = (
        "from __future__ import annotations\n" + imported + "\n"
        "import typing_extensions as te\n"
        "class P(te.Protocol): pass\n"
        "class C:\n"
        f"    callback: {name}[[te.Self], None] | None = None\n"
        "    def clone(self) -> te.Self: return self\n"
    )
    path = tmp_path / "example.py"
    path.write_text(source)
    result = probes(source, str(path))
    oracle = PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(result.requests)
    finally:
        oracle.close()
    assert imports_typed_as_any(result, [answers], str(path)) == ()


@requires_pyright
def test_import_identity_does_not_accept_invalid_annotated_metadata(tmp_path: Path) -> None:
    (tmp_path / "pyrightconfig.json").write_text('{"pythonVersion":"3.10"}')
    source = (
        "from typing import Annotated as Meta\n"
        "import typing_extensions as te\n"
        "def need_int(value: int) -> str: return str(value)\n"
        "class C:\n    value: Meta[int, need_int(te.Self)]\n"
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
    assert imports_typed_as_any(result, [answers], str(path)) == ()
    assert isinstance(checked, CheckSuccess)
    assert any("reportArgumentType" in error.message for error in checked.errors)


@requires_pyright
@pytest.mark.parametrize(
    "declared",
    [
        "class Self: pass",
        "class Self: pass\nSelf = Self()",
        "from typing import TypeVar\nSelf = TypeVar('Self')",
    ],
)
@pytest.mark.parametrize(
    "imported, expression", [("from forms import Self", "Self"), ("import forms", "forms.Self")]
)
def test_a_custom_self_symbol_is_not_the_contextual_typing_form(
    tmp_path: Path, declared: str, imported: str, expression: str
) -> None:
    (tmp_path / "pyrightconfig.json").write_text('{"pythonVersion":"3.10"}')
    (tmp_path / "forms.py").write_text(declared + "\n")
    source = f"{imported}\nclass C:\n    value: {expression}\n"
    path = tmp_path / "example.py"
    path.write_text(source)
    result = probes(source, str(path))
    oracle = PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(result.requests)
    finally:
        oracle.close()
    question = next(question for question in result.questions if question.self_context)
    assert question.self_context is not None
    key, expected = question.self_context
    assert answers.get(key) != expected
    # The current checker can type these custom objects. If a checker instead
    # reports Unknown, their spelling must not discharge that real finding.
    assert any(
        '"Self"' in finding.message
        for finding in imports_typed_as_any(
            result, [{**answers, question.key: "Unknown"}], str(path)
        )
    )


@requires_pyright
def test_import_identity_does_not_accept_self_as_an_ordinary_int_value(tmp_path: Path) -> None:
    (tmp_path / "pyrightconfig.json").write_text('{"pythonVersion":"3.10"}')
    source = (
        "import typing_extensions as te\n"
        "def need_int(value: int) -> None: pass\n"
        "need_int(te.Self)\n"
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
    assert imports_typed_as_any(result, [answers], str(path)) == ()
    assert isinstance(checked, CheckSuccess)
    assert any("reportArgumentType" in error.message for error in checked.errors)

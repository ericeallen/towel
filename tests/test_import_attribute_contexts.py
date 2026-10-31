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

"""Submodule visibility belongs to the program's binding, not a fresh import alias."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from towel.type_baseline import ImportProbes, ImportQuestion, import_probes, imports_typed_as_any
from towel.type_inference import MypyInferrer, PyrightOracle, RevealRequest

IMPORTS = "import collections\nimport collections.abc\n"


def _abc_question(source: str) -> tuple[ImportProbes, ImportQuestion]:
    probes = import_probes("/project/example.py", source)
    assert probes is not None
    question = next(q for q in probes.questions if q.subject == '"abc" of module "collections"')
    return probes, question


@pytest.mark.parametrize(
    "body",
    [
        "value = collections.abc\n",
        "class C(collections.abc.MutableMapping):\n    pass\n",
        "def f():\n    return collections.abc\n",
        "if True:\n    value = collections.abc\n",
        "if True: value = collections.abc\n",
        "def f(): return collections.abc\n",
        "class C:\n    value = collections.abc\n",
        "value = 0; other = collections.abc\n",
        "value = lambda: collections.abc\n",
        "value = [collections.abc for x in ()]\n",
        "value = True and collections.abc\n",
        "value = collections.abc if True else None\n",
        "value = ((other := 0), collections.abc)\n",
        "if value:\n    pass\nelif isinstance(value, collections.abc.Mapping):\n    pass\n",
    ],
)
def test_post_import_site_uses_the_original_binding_for_later_reads(body: str) -> None:
    probes, question = _abc_question(IMPORTS + body)
    assert question.attribute_context is not None
    context = question.attribute_context
    assert context.expected == 'Module("collections.abc")'
    path, line, index = context.key
    request = next(r for r in probes.requests if r.file_path == path and r.line == line)
    assert request.expressions[index] == "collections.abc"
    assert not request.indent
    statement = next(node for node in ast.parse(request.source).body if node.lineno == line)
    assert ast.dump(statement) == ast.dump(ast.parse(body).body[0])


@pytest.mark.parametrize(
    "source",
    [
        "import collections\nvalue = collections.abc\nimport collections.abc\n",
        "def f(): return collections.abc\n" + IMPORTS,
        "import collections\nvalue = 0\nimport collections.abc\nvalue = collections.abc\n",
        "if True:\n    import collections\n    import collections.abc\n    value = collections.abc\n",
        "def f():\n    import collections\n    import collections.abc\n    return collections.abc\n",
        "import collections; import collections.abc; value = collections.abc\n",
        "import collections\nimport collections.abc as child\nvalue = collections.abc\n",
        "import collections\ndef load():\n    import collections.abc\n"
        "def read():\n    return collections.abc\n",
    ],
)
def test_uncertain_import_order_or_binding_keeps_the_original_probe(source: str) -> None:
    probes, question = _abc_question(source)
    assert question.attribute_context is None
    assert imports_typed_as_any(probes, [{question.key: "Unknown"}], "/project/example.py")


@pytest.mark.parametrize(
    "binding",
    [
        "collections = other",
        "del collections",
        "collections.abc = other",
        "del collections.abc",
        "import math as collections",
        "import collections",
        "from elsewhere import collections",
        "from elsewhere import *",
        "def collections(): pass",
        "class collections: pass",
        "try: pass\nexcept Exception as collections: pass",
        "match other:\n    case collections:\n        pass",
    ],
)
def test_competing_bindings_do_not_supply_evidence_for_the_import(binding: str) -> None:
    _, question = _abc_question(IMPORTS + "value = collections.abc\n" + binding + "\n")
    assert question.attribute_context is None


@pytest.mark.parametrize(
    "binding",
    [
        "def f():\n    import collections",
        "def f():\n    import collections.abc",
        "def f(collections): pass",
        "def f():\n    global collections",
    ],
)
def test_unrelated_locals_and_declarations_do_not_write_the_module_binding(binding: str) -> None:
    _, question = _abc_question(IMPORTS + "value = collections.abc\n" + binding + "\n")
    assert question.attribute_context is not None
    assert question.attribute_context.expected == 'Module("collections.abc")'


def test_original_binding_needs_exact_evidence_from_the_same_checker() -> None:
    probes, question = _abc_question(
        IMPORTS + "value = collections.abc\ndef f():\n    return collections.abc\n"
    )
    assert question.attribute_context is not None
    key, expected = question.attribute_context.key, question.attribute_context.expected
    blind = {question.key: "Unknown"}
    known = {**blind, key: expected}
    assert imports_typed_as_any(probes, [known], "/project/example.py") == ()
    for answer in (None, "Any", "Unknown", 'Module("elsewhere")', "types.ModuleType"):
        incomplete = dict(blind)
        if answer is not None:
            incomplete[key] = answer
        assert imports_typed_as_any(probes, [incomplete], "/project/example.py")
    assert imports_typed_as_any(probes, [known, blind], "/project/example.py")
    assert imports_typed_as_any(probes, [blind, {key: expected}], "/project/example.py")
    module = next(q for q in probes.questions if q.kind == "module")
    assert imports_typed_as_any(probes, [{**known, module.key: "Any"}], "/project/example.py")


def test_normal_attributes_do_not_gain_unrelated_module_questions() -> None:
    probes = import_probes("/project/example.py", "import os\nvalue = os.sep\n")
    assert probes is not None
    assert len(probes.requests) == 1
    assert all(q.attribute_context is None for q in probes.questions)


@pytest.mark.skipif(importlib.util.find_spec("pyright") is None, reason="pyright absent")
@pytest.mark.parametrize("reverse", [False, True])
def test_known_submodule_is_read_through_its_actual_binding(tmp_path: Path, reverse: bool) -> None:
    (tmp_path / "pyrightconfig.json").write_text(
        '{"pythonVersion":"3.12","typeCheckingMode":"standard"}'
    )
    imports = ["import collections", "import collections.abc"]
    if reverse:
        imports.reverse()
    source = "\n".join(imports) + "\nclass Cache(collections.abc.MutableMapping):\n    pass\n"
    path = tmp_path / "example.py"
    path.write_text(source)
    probes = import_probes(str(path), source)
    assert probes is not None
    oracle = PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(probes.requests)
    finally:
        oracle.close()
    assert imports_typed_as_any(probes, [answers], str(path)) == ()


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
def test_missing_submodule_remains_blind(tmp_path: Path, checker: str) -> None:
    if importlib.util.find_spec(checker) is None:
        pytest.skip(f"{checker} absent")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mypy]\nignore_missing_imports = true\n"
        '[tool.pyright]\npythonVersion = "3.12"\nreportMissingImports = "none"\n'
    )
    source = (
        "import collections\nimport collections.towel_absent_child\n"
        "def f():\n    return collections.towel_absent_child\n"
    )
    path = tmp_path / "example.py"
    path.write_text(source)
    probes = import_probes(str(path), source)
    assert probes is not None
    oracle = MypyInferrer() if checker == "mypy" else PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(probes.requests)
    finally:
        oracle.close()
    assert imports_typed_as_any(probes, [answers], str(path))


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
def test_local_packages_aliases_and_read_contexts_remain_checked(
    tmp_path: Path, checker: str
) -> None:
    if importlib.util.find_spec(checker) is None:
        pytest.skip(f"{checker} absent")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mypy]\nignore_missing_imports = true\n"
        '[tool.pyright]\npythonVersion = "3.12"\nreportMissingImports = "none"\n'
    )
    package = tmp_path / "probe_parent"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "child.py").write_text("value: int = 1\n")
    imports = "import probe_parent\nimport probe_parent.child\n"
    sources = {
        "ordinary": imports + "value = probe_parent.child\n",
        "conditional": imports + "if True:\n    value = probe_parent.child\n",
        "deferred": imports + "def f(): return probe_parent.child\n",
        "elif": imports + "if False:\n    pass\nelif probe_parent.child.value:\n    pass\n",
        "one_line": imports + "if True: value = probe_parent.child\n",
        "dotted_alias": "import probe_parent.child as child\nvalue = child.value\n",
        "coexisting_contexts": "from typing import Self\n"
        + imports
        + "class Node:\n    child: Self\n    value = probe_parent.child\n"
        + "def f():\n    import math\n    return math.sqrt(1)\n",
        "relative": "from . import child\nvalue = child.value\n",
    }
    requests: list[RevealRequest] = []
    plans = []
    for name, source in sources.items():
        path = package / f"{name}.py"
        path.write_text(source)
        probes = import_probes(str(path), source)
        assert probes is not None
        plans.append((path, probes))
        requests.extend(probes.requests)
    oracle = MypyInferrer() if checker == "mypy" else PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(requests)
    finally:
        oracle.close()
    for path, probes in plans:
        assert imports_typed_as_any(probes, [answers], str(path)) == (), (path, answers)
        for question in probes.questions:
            if checker == "pyright" and question.attribute_context is not None:
                context = question.attribute_context
                assert answers.get(context.key) == context.expected


@pytest.mark.skipif(importlib.util.find_spec("pyright") is None, reason="pyright absent")
@pytest.mark.parametrize(
    "source",
    [
        "import collections\nimport collections.abc as child\nvalue = collections.abc\n",
        "import collections\ndef load():\n    import collections.abc\n"
        "def read():\n    return collections.abc\n",
    ],
)
def test_a_companion_alias_or_sibling_scope_does_not_prove_visibility(
    tmp_path: Path, source: str
) -> None:
    path = tmp_path / "example.py"
    path.write_text(source)
    probes = import_probes(str(path), source)
    assert probes is not None
    question = next(q for q in probes.questions if q.subject == '"abc" of module "collections"')
    assert question.attribute_context is None
    oracle = PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(probes.requests)
    finally:
        oracle.close()
    assert answers[question.key] == "Unknown"
    assert imports_typed_as_any(probes, [answers], str(path))

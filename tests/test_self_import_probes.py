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

"""A contextual typing form is not an untyped import merely because it is not a value."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
from typing import Dict

import pytest

from tests.test_names_typed_any import TWINS, requires_pyright
from towel.type_baseline import ImportProbes, import_probes, imports_typed_as_any
from towel.type_inference import PyrightOracle, RevealKey
from towel.unification.refactor_engine import UnificationRefactorEngine


def _probes(source: str, path: str = "/project/example.py") -> ImportProbes:
    result = import_probes(path, source)
    assert result is not None
    return result


def _unknown_answers(probes: ImportProbes) -> Dict[RevealKey, str]:
    return {
        q.key: 'Module("typing")' if q.kind == "module" else "Unknown" for q in probes.questions
    }


@pytest.mark.parametrize(
    "use",
    [
        "value = Self",
        "def f(value=Self): pass",
        "@Self\ndef f(): pass",
        "class C(Self): pass",
        "value: Annotated[int, consume(Self)]",
        "value: Annotated[int, Self]",
        "from typing import Annotated as Meta\nvalue: Meta[int, Self]",
        "from typing import Annotated as list\nvalue: list[int, Self]",
        "value: list[Meta[int, Self]]",
        "value: UnknownConstructor[Self]",
        "value: (lambda: Self)",
        "value: (Self if condition else object)",
        "value: Self.attribute",
        "value: Self | 1",
        "value: Self | True",
        "Self = object",
        "del Self",
        "def f(Self): pass",
        "def Self(): pass",
        "class Self: pass",
        "try:\n    pass\nexcept Exception as Self:\n    pass",
        "match value:\n    case Self:\n        pass",
        "match value:\n    case {'x': x, **Self}:\n        pass",
        "from elsewhere import Self",
        "from elsewhere import *",
        "def f():\n    global Self",
        "values = [Self for Self in others]",
    ],
)
def test_value_reads_or_competing_bindings_keep_the_ordinary_probe(use: str) -> None:
    probes = _probes("from typing import Self\n" + use + "\n")
    assert all(q.self_context is None for q in probes.questions)
    assert imports_typed_as_any(probes, [_unknown_answers(probes)], "/project/example.py")


def test_contextual_evidence_must_be_exact_and_come_from_the_same_checker() -> None:
    probes = _probes("from typing import Self\nclass C:\n    def clone(self) -> Self: ...\n")
    question = next(q for q in probes.questions if q.self_context is not None)
    assert question.self_context is not None
    key, expected = question.self_context
    unknown = _unknown_answers(probes)
    identified = {**unknown, key: expected}
    assert imports_typed_as_any(probes, [identified], "/project/example.py") == ()
    for wrong in ("Unknown", "Any", "type[Other]", "type[Self@Other]", ""):
        assert imports_typed_as_any(probes, [{**unknown, key: wrong}], "/project/example.py")
    assert imports_typed_as_any(probes, [identified, unknown], "/project/example.py")
    assert imports_typed_as_any(probes, [unknown, {key: expected}], "/project/example.py")


@pytest.mark.parametrize(
    "annotation", ["Self", "Self | None", "list[Self]", "dict[str, tuple[Self, ...]]", "type[Self]"]
)
def test_plain_union_and_builtin_container_annotations_have_contextual_probes(
    annotation: str,
) -> None:
    probes = _probes(f"from typing import Self\nclass C:\n    value: {annotation}\n")
    assert any(q.self_context is not None for q in probes.questions)


def test_fresh_context_names_and_physical_lines_preserve_later_imports() -> None:
    source = """from __future__ import annotations
_towel_self_context_1 = 0
from typing import Self
class C:
    def clone(self) -> Self: ...
def factory():
    from typing_extensions import Self as Receiver
    class D:
        def clone(self) -> Receiver: ...
    import missing_after
    return D
"""
    probes = _probes(source)
    augmented = probes.requests[0].source
    compile(augmented, "<probed>", "exec")
    contexts = [q.self_context for q in probes.questions if q.self_context is not None]
    assert len(contexts) == 2
    assert any("_towel_self_context_1_]" in expected for _, expected in contexts)
    for request in probes.requests:
        assert request.source == augmented
        line = augmented.splitlines()[request.line - 1]
        assert line.startswith(request.indent)
        assert line.strip() == "pass" or line.lstrip().startswith(("from ", "import "))
    missing = next(q for q in probes.questions if q.subject == 'module "missing_after"')
    assert missing.line == 10
    assert imports_typed_as_any(probes, [{missing.key: "Any"}], "/project/example.py")


def test_context_name_avoids_normalized_unicode_identifiers() -> None:
    source = "class _towel_self_context_１: pass\nfrom typing import Self\n"
    probes = _probes(source)
    tree = ast.parse(probes.requests[0].source)
    classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    assert len(classes) == len(set(classes)) == 2
    assert "_towel_self_context_1" in classes


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type parameter syntax requires Python 3.12")
@pytest.mark.parametrize("parameter", ["Self", "*Self", "**Self"])
def test_type_parameter_bindings_prevent_the_contextual_exception(parameter: str) -> None:
    source = f"from typing import Self\nclass C[{parameter}]: pass\n"
    probes = _probes(source)
    assert all(q.self_context is None for q in probes.questions)
    assert imports_typed_as_any(probes, [_unknown_answers(probes)], "/project/example.py")


@pytest.mark.parametrize("module,bound", [("typing", "Self"), ("typing_extensions", "Receiver")])
@requires_pyright
def test_real_checker_identifies_self_without_losing_missing_imports(
    tmp_path: Path, module: str, bound: str
) -> None:
    (tmp_path / "pyrightconfig.json").write_text(
        '{"pythonVersion":"3.12","typeCheckingMode":"strict","reportMissingImports":"none"}'
    )
    path = tmp_path / "example.py"
    source = f"""from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from {module} import Self as {bound}
class C:
    def clone(self) -> {bound}:
        return self
from towel_missing_self_provider import Self as Missing
class D:
    def clone(self) -> Missing: ...
"""
    path.write_text(source)
    probes = _probes(source, str(path))
    oracle = PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(probes.requests)
    finally:
        oracle.close()
    found = imports_typed_as_any(probes, [answers], str(path))
    assert len(found) == 1, (found, answers)
    assert '"Self" imported from "towel_missing_self_provider" as Unknown' in found[0].message
    assert found[0].line == 8
    for question in probes.questions:
        if question.self_context is not None and question.line == 4:
            key, expected = question.self_context
            assert answers[question.key] == "Unknown"
            assert answers[key] == expected


@requires_pyright
@pytest.mark.parametrize(
    "declared",
    ["from absent import Self", "Self: object = undefined", "from typing import Any\nSelf: Any"],
)
def test_shadow_typing_module_gets_no_contextual_exemption(tmp_path: Path, declared: str) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "typing.py").write_text(declared + "\n")
    (tmp_path / "pyrightconfig.json").write_text('{"reportMissingImports":"none"}')
    path = package / "example.py"
    source = "from .typing import Self\nclass C:\n    def clone(self) -> Self: ...\n"
    path.write_text(source)
    probes = _probes(source, str(path))
    oracle = PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(probes.requests)
    finally:
        oracle.close()
    for q in probes.questions:
        if q.self_context is not None:
            key, expected = q.self_context
            assert answers.get(key) != expected
    if declared.startswith("from absent"):
        assert imports_typed_as_any(probes, [answers], str(path))


@requires_pyright
def test_a_target_without_self_support_keeps_its_unknown_import(tmp_path: Path) -> None:
    (tmp_path / "pyrightconfig.json").write_text('{"pythonVersion":"3.10"}')
    path = tmp_path / "example.py"
    source = "from typing import Self\nclass C:\n    def clone(self) -> Self: ...\n"
    path.write_text(source)
    probes = _probes(source, str(path))
    oracle = PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(probes.requests)
    finally:
        oracle.close()
    found = imports_typed_as_any(probes, [answers], str(path))
    assert len(found) == 1
    assert '"Self" imported from "typing" as Unknown' in found[0].message


@requires_pyright
def test_default_typed_extraction_preserves_a_file_with_self_annotations(tmp_path: Path) -> None:
    (tmp_path / "pyrightconfig.json").write_text(
        '{"pythonVersion":"3.12","typeCheckingMode":"strict"}'
    )
    source = """from typing import Self
class Example:
    def clone(self) -> Self:
        return self
""" + TWINS
    path = tmp_path / "example.py"
    path.write_text(source)
    oracle = PyrightOracle(language_server=False)
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
        results, _ = engine.refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), progress="none"
        )
        assert results
        assert not engine._type_names_any
    finally:
        oracle.close()
    rewritten = path.read_text()
    assert "__extracted_func_" in rewritten
    assert "_towel_self_context_" not in rewritten
    assert ast.parse(rewritten)
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    exec(source, before)
    exec(rewritten, after)
    for name in ("first", "second"):
        original, changed = before[name], after[name]
        assert callable(original) and callable(changed)
        assert [original(value) for value in (-3, 0, 4)] == [changed(value) for value in (-3, 0, 4)]


@pytest.mark.parametrize(
    "statement,bound",
    [
        ("import typing", "typing"),
        ("import typing as types", "types"),
        ("import pkg.types", "pkg.types"),
    ],
)
def test_qualified_proof_belongs_only_to_the_self_attribute(statement: str, bound: str) -> None:
    probes = _probes(f"{statement}\nclass C:\n    value: list[{bound}.Self | None]\n")
    question = next(q for q in probes.questions if q.self_context is not None)
    assert question.kind == "attribute" and question.subject.startswith('"Self" of module')
    assert sum(q.self_context is not None for q in probes.questions) == 1
    assert question.self_context is not None
    key, expected = question.self_context
    answers = {**_unknown_answers(probes), key: expected}
    assert imports_typed_as_any(probes, [answers], "/project/example.py") == ()
    module = next(q for q in probes.questions if q.kind == "module")
    assert module.self_context is None
    assert imports_typed_as_any(probes, [{**answers, module.key: "Any"}], "/project/example.py")


@pytest.mark.parametrize(
    "use",
    [
        "value = types.Self",
        "types = other",
        "types.Self = other",
        "del types.Self",
        "alias = types",
        "def f(types): pass",
        "value: Annotated[int, types.Self]",
        "value: Meta[int, types.Self]",
        "value: (lambda: types.Self)",
        "value: (types.Self if condition else object)",
        "value: types.Self.attribute",
    ],
)
def test_qualified_value_uses_and_writes_keep_the_ordinary_probe(use: str) -> None:
    probes = _probes("import typing as types\nclass C:\n    value: types.Self\n" + use + "\n")
    assert all(q.self_context is None for q in probes.questions)
    assert imports_typed_as_any(probes, [_unknown_answers(probes)], "/project/example.py")


@requires_pyright
@pytest.mark.parametrize(
    "statement,bound", [("import typing", "typing"), ("import typing as types", "types")]
)
def test_real_checker_identifies_qualified_self(tmp_path: Path, statement: str, bound: str) -> None:
    (tmp_path / "pyrightconfig.json").write_text('{"pythonVersion":"3.12"}')
    path = tmp_path / "example.py"
    source = f"{statement}\nclass C:\n    def clone(self) -> {bound}.Self:\n        return self\n"
    path.write_text(source)
    probes = _probes(source, str(path))
    oracle = PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(probes.requests)
    finally:
        oracle.close()
    assert imports_typed_as_any(probes, [answers], str(path)) == ()
    question = next(q for q in probes.questions if q.self_context is not None)
    assert question.self_context is not None
    key, expected = question.self_context
    assert answers[question.key] == "Unknown" and answers[key] == expected

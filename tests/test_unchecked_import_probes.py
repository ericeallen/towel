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

"""An unchecked body makes even a known module Any; resolve that import in context."""

from __future__ import annotations

import ast
import importlib.util
import shutil
from pathlib import Path
from typing import Dict, Tuple

import pytest

from towel.type_baseline import ImportProbes, import_probes, imports_typed_as_any
from towel.type_inference import MypyInferrer, PyrightOracle
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
requires_pyright = pytest.mark.skipif(
    shutil.which("pyright") is None and importlib.util.find_spec("pyright") is None,
    reason="pyright absent",
)


def _probes(source: str, path: str = "/project/example.py") -> ImportProbes:
    result = import_probes(path, source)
    assert result is not None
    return result


@pytest.mark.parametrize("response", [None, "Any", "Unknown", 'Module("warnings")', "object"])
def test_only_the_same_checkers_exact_module_answer_can_resolve_any(response: str | None) -> None:
    probes = _probes("def f():\n    import warnings\n")
    question = probes.questions[0]
    assert question.module_context is not None
    answers: Dict[Tuple[str, int, int], str] = {question.key: "Any"}
    if response is not None:
        answers[question.module_context] = response
    assert imports_typed_as_any(probes, [answers], "/project/example.py")
    known = {**answers, question.module_context: "types.ModuleType"}
    assert imports_typed_as_any(probes, [known], "/project/example.py") == ()
    assert imports_typed_as_any(probes, [known, answers], "/project/example.py")
    assert imports_typed_as_any(
        probes,
        [{question.key: "Any"}, {question.module_context: "types.ModuleType"}],
        "/project/example.py",
    )


@pytest.mark.parametrize(
    ("header", "context"),
    [
        ("def f(value):", True),
        ("async def f(value):", True),
        ("def f(self):", True),
        ("def f(value, /):", True),
        ("@decorate\ndef f(value):", True),
        ("def f(value: int, /):", False),
        ("def f(*, value: int):", False),
        ("def f(*values: int):", False),
        ("def f(**values: int):", False),
        ("def f(self: object):", False),
        ("def f(value) -> None:", False),
    ],
)
def test_function_headers_only_identify_where_a_context_may_be_needed(
    header: str, context: bool
) -> None:
    probes = _probes(header + "\n    import warnings\n")
    assert (probes.questions[0].module_context is not None) == context


def test_context_names_are_hygienic_under_unicode_identifier_normalization() -> None:
    probes = _probes("def _towel_import_context_０(): pass\ndef f():\n    import warnings\n")
    tree = ast.parse(probes.requests[0].source)
    names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    assert names.count("_towel_import_context_0") == 1
    assert "_towel_import_context_0_" in names


def test_both_contexts_keep_independent_physical_lines_and_indentation() -> None:
    source = """def f():
    import typing
    class C:
        def clone(self) -> typing.Self:
            return self
    from math import ceil
"""
    probes = _probes(source)
    texts = {request.source for request in probes.requests}
    assert len(texts) == 1
    text = texts.pop()
    compile(text, "<probes>", "exec")
    questions = probes.questions
    self_question = next(q for q in questions if q.self_context is not None)
    module_question = next(q for q in questions if q.line == 2 and q.kind == "module")
    assert self_question.self_context is not None
    assert module_question.module_context is not None
    assert self_question.self_context[0] != module_question.module_context
    for request in probes.requests:
        assert text.splitlines()[request.line - 1].startswith(request.indent)


def _configuration(root: Path, extra_mypy: str = "") -> None:
    (root / "pyproject.toml").write_text(
        '[tool.mypy]\nignore_missing_imports = true\npython_version = "3.12"\nplatform = "linux"\n'
        + extra_mypy
        + '\n[tool.pyright]\npythonVersion = "3.12"\npythonPlatform = "Linux"\nreportMissingImports = "none"\n'
    )


@pytest.mark.parametrize(
    "checker",
    [pytest.param("mypy", marks=requires_mypy), pytest.param("pyright", marks=requires_pyright)],
)
@pytest.mark.parametrize(
    "case",
    [
        "known",
        "missing",
        "relative",
        "relative_missing",
        "local_shadow",
        "stdlib_shadow",
        "nested_typed",
        "unreachable",
        "self_and_module",
    ],
)
def test_context_preserves_resolution_scope_and_reachability(
    tmp_path: Path, checker: str, case: str
) -> None:
    _configuration(tmp_path)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "provider.py").write_text("value: int = 1\n")
    sources = {
        "known": 'def f(value):\n    import warnings\n    from math import ceil, exp\n    from math import log as ln\n    warnings.warn("example")\n',
        "missing": "def f(value):\n    import towel_absent_context_provider as p\n    return p.value\n",
        "relative": "def f(value):\n    from .provider import value as result\n    return result\n",
        "relative_missing": "def f(value):\n    from .absent import value as result\n    return result\n",
        "local_shadow": "def f(value):\n    import local_provider as warnings\n    return warnings.value\n",
        "stdlib_shadow": "def f(value):\n    import warnings\n    return warnings.value\n",
        "nested_typed": "def outer():\n    async def inner(value: int) -> None:\n        import warnings\n        warnings.warn('known')\n",
        "unreachable": "import sys\nif sys.platform == 'win32':\n    def f():\n        import towel_absent_context_provider as p\n        return p.value\n",
        "self_and_module": "def f():\n    import typing\n    class C:\n        def clone(self) -> typing.Self:\n            return self\n",
    }
    if case == "local_shadow":
        (tmp_path / "local_provider.py").write_text("value: str = 'local provider'\n")
    if case == "stdlib_shadow":
        (tmp_path / "warnings.py").write_text("value: str = 'local provider'\n")
    path = package / "example.py"
    path.write_text(sources[case])
    probes = _probes(sources[case], str(path))
    oracle = MypyInferrer() if checker == "mypy" else PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(probes.requests)
    finally:
        oracle.close()
    found = imports_typed_as_any(probes, [answers], str(path))
    # Pyright resolves stdlib warnings ahead of this local provider; its
    # Unknown attribute must still refuse, even though the module is known.
    blind = case in {"missing", "relative_missing"} or (
        case == "stdlib_shadow" and checker == "pyright"
    )
    assert bool(found) == blind, (found, answers)
    if checker == "mypy" and case == "known":
        for question in probes.questions:
            if question.kind == "module":
                assert question.module_context is not None
                assert answers[question.key] == "Any"
                assert answers[question.module_context] == "types.ModuleType"
    if case == "unreachable":
        assert all(
            q.key not in answers and q.module_context not in answers
            for q in probes.questions
            if q.line == 4
        )
    if checker == "pyright" and case == "self_and_module":
        question = next(q for q in probes.questions if q.self_context is not None)
        assert question.self_context is not None
        key, expected = question.self_context
        assert answers[question.key] == "Unknown"
        assert answers[key] == expected


@requires_mypy
def test_a_local_module_the_project_leaves_untyped_is_still_refused(tmp_path: Path) -> None:
    _configuration(
        tmp_path, '\n[[tool.mypy.overrides]]\nmodule = "warnings"\nfollow_imports = "skip"\n'
    )
    (tmp_path / "warnings.py").write_text("value: int = 1\n")
    source = "def f():\n    import warnings\n    return warnings.value\n"
    path = tmp_path / "example.py"
    path.write_text(source)
    probes = _probes(source, str(path))
    oracle = MypyInferrer()
    try:
        answers = oracle.reveal(probes.requests)
    finally:
        oracle.close()
    module = next(q for q in probes.questions if q.kind == "module")
    assert module.module_context is not None
    assert answers[module.module_context] == "Any"
    assert imports_typed_as_any(probes, [answers], str(path))


@requires_mypy
def test_unchecked_known_import_does_not_block_other_typed_functions(tmp_path: Path) -> None:
    _configuration(tmp_path)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = """def unrelated(value):
    import warnings
    return value


def first(value: int) -> int:
    one = value + 1
    two = one * 2
    three = two - 3
    return three


def second(value: int) -> int:
    one = value + 1
    two = one * 2
    three = two - 3
    return three
"""
    path = package / "example.py"
    path.write_text(source)
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=2, type_oracle=oracle)
        engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    finally:
        oracle.close()
    output = path.read_text()
    assert output != source
    assert "_towel_import_context_" not in output
    before: Dict[str, object] = {}
    after: Dict[str, object] = {}
    exec(source, before)
    exec(output, after)
    for name in ("first", "second"):
        original = before[name]
        changed = after[name]
        assert callable(original) and callable(changed)
        assert [original(n) for n in range(-3, 4)] == [changed(n) for n in range(-3, 4)]

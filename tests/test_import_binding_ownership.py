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

"""Only a binding's own reads and writes determine its import evidence."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.test_names_typed_any import requires_pyright
from towel import type_baseline
from towel.type_baseline import ImportProbes, import_probes, imports_typed_as_any
from towel.type_inference import CheckSuccess, PyrightOracle
from towel.unification.statement_facts import bindings_of


def probes(source: str, path: str = "/project/example.py") -> ImportProbes:
    result = import_probes(path, source)
    assert result is not None
    return result


@requires_pyright
@pytest.mark.parametrize(
    "source",
    [
        "import collections\nimport collections.abc\n"
        "def identity(collections: int) -> int: return collections\nvalue = collections.abc\n",
        "import collections\nimport collections.abc\n"
        "def identity() -> int:\n    collections = 1\n    return collections\n"
        "value = collections.abc\n",
        "class Local:\n    abc: int = 1\n"
        "def identity(collections: Local) -> int: return collections.abc\n"
        "import collections\nimport collections.abc\nvalue = collections.abc\n",
        "import math as namespace\nimport statistics as namespace\n"
        "value: float = namespace.mean([1.0, 2.0])\n",
        "import math as namespace\nimport math as namespace\n"
        "import statistics as namespace\nvalue: float = namespace.mean([1.0, 2.0])\n",
        "import math as namespace, statistics as namespace\n"
        "value: float = namespace.mean([1.0, 2.0])\n",
    ],
)
def test_actual_checker_keeps_unrelated_locals_and_superseded_imports_typed(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "pyrightconfig.json").write_text(
        '{"pythonVersion":"3.12","typeCheckingMode":"standard"}'
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
    assert imports_typed_as_any(result, [answers], str(path)) == ()


@pytest.mark.parametrize(
    "binding",
    [
        "def mutate():\n    global collections\n    collections = other\n",
        "def mutate():\n    global collections\n    import other as collections\n",
        "def mutate():\n    collections.abc = other\n",
        "collections.abc = other\n",
        "collections = other\n",
        "from other import *\n",
        "def mutate():\n    global collections\n    del collections\n",
        "values = [(collections := other) for item in items]\n",
        "values = [None for collections.abc in items]\n",
        "match other:\n    case [*collections]:\n        pass\n",
        "match other:\n    case {**collections}:\n        pass\n",
    ],
)
def test_real_module_writes_still_prevent_visibility_evidence(binding: str) -> None:
    result = probes(
        "import collections\nimport collections.abc\nvalue = collections.abc\n" + binding
    )
    question = next(q for q in result.questions if q.subject == '"abc" of module "collections"')
    assert question.attribute_context is None
    assert imports_typed_as_any(result, [{question.key: "Unknown"}], "/project/example.py")


@pytest.mark.parametrize(
    "source",
    [
        "def later(): return namespace.mean([1.0])\n"
        "import math as namespace\nimport statistics as namespace\n",
        "import math as namespace\nif condition:\n    import statistics as namespace\n"
        "value = namespace.mean([1.0])\n",
        "import math as namespace\nimport statistics as namespace\n"
        "if condition:\n    import math as namespace\nvalue = namespace.mean([1.0])\n",
        "import math as namespace\nimport statistics as namespace\n"
        "namespace = other\nvalue = namespace.mean([1.0])\n",
        "import math as namespace\nimport statistics as namespace\n"
        "def mutate():\n    global namespace\n    namespace = other\n"
        "value = namespace.mean([1.0])\n",
        "import math as namespace\nfrom other import *\n"
        "import statistics as namespace\nvalue = namespace.mean([1.0])\n",
        "def local():\n    import math as namespace\n    import statistics as namespace\n"
        "    return namespace.mean([1.0])\n",
        "import math as namespace; import statistics as namespace; value = namespace.mean([1.0])\n",
    ],
)
def test_uncertain_binding_or_read_order_keeps_both_import_questions(source: str) -> None:
    result = probes(source)
    assert any(q.subject == '"mean" of module "math"' for q in result.questions)


def test_superseded_module_questions_are_still_checked_for_missing_types() -> None:
    result = probes(
        "import absent as namespace\nimport statistics as namespace\n"
        "value = namespace.mean([1.0])\n"
    )
    assert not any(q.subject == '"mean" of module "absent"' for q in result.questions)
    question = next(q for q in result.questions if q.subject == 'module "absent"')
    assert imports_typed_as_any(result, [{question.key: "Any"}], "/project/example.py")


def test_new_scope_and_block_summaries_are_computed_once_per_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"blocks": 0, "scopes": 0, "bindings": 0}
    original_blocks = type_baseline._module_import_blocks
    original_scope = type_baseline._AttributeScopes._enter_scope
    original_bindings = bindings_of

    def blocks(tree: ast.Module) -> tuple[type_baseline._ModuleImportBlock, ...]:
        calls["blocks"] += 1
        return original_blocks(tree)

    def scope(visitor: type_baseline._AttributeScopes, node: ast.AST) -> None:
        calls["scopes"] += 1
        original_scope(visitor, node)

    def bindings(node: ast.AST, *, into_nested_scopes: bool) -> frozenset[str]:
        calls["bindings"] += 1
        return original_bindings(node, into_nested_scopes=into_nested_scopes)

    monkeypatch.setattr(type_baseline, "_module_import_blocks", blocks)
    monkeypatch.setattr(type_baseline._AttributeScopes, "_enter_scope", scope)
    monkeypatch.setattr(type_baseline, "bindings_of", bindings)
    observed = []
    for import_count in (1, 40):
        for name in calls:
            calls[name] = 0
        imports = "".join(f"import collections as alias_{i}\n" for i in range(import_count))
        result = probes(
            imports + "import collections\nimport collections.abc\n"
            "def outer(value):\n    def inner(value): return value\n    return inner(value)\n"
            "value = collections.abc\n"
        )
        assert result.questions
        observed.append(calls.copy())
    assert observed[0]["blocks"] == 1
    assert observed[0]["scopes"] > 0 and observed[0]["bindings"] > 0
    assert observed[0] == observed[1]

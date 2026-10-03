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

"""An inline exception handler keeps its own header's logical indentation."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import textwrap
import warnings

import pytest

from towel.reachability import PROBE, probe_plan
from towel.type_baseline import import_probes
from towel.type_inference import MypyInferrer, PyrightOracle, RevealRequest
from towel.unification.bounded_cache import memoization_disabled


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize(
    "handler",
    [
        "except: pass; pass",
        "except Exception: pass; pass",
        "except Exception as café: value = café; pass",
        "except (\n    ValueError, TypeError\n): pass; pass",
        "except \\\n    Exception: pass; pass",
        "except* Exception: pass; pass",
        "except* (\n    ValueError, TypeError\n): pass; pass",
        "except* Exception as café: value = café; pass",
        "except Exception: '''not a ;\nbody boundary'''; pass",
    ],
)
def test_inline_handlers_preserve_ast_and_distinct_statement_sites(
    depth: int, handler: str
) -> None:
    source = "try: pass\n" + handler + "\n"
    for index in range(depth):
        source = f"def outer_{index}():\n" + textwrap.indent(source, "    ")
    before = source
    compile(source, "<original>", "exec", dont_inherit=True)
    original = ast.parse(source)
    plan = probe_plan(source)
    assert plan is not None
    assert ast.dump(ast.parse(plan.text)) == ast.dump(original)
    assert source == before
    for node in ast.walk(original):
        if not isinstance(node, ast.ExceptHandler):
            continue
        sites = []
        for statement in node.body:
            source_line = source.split("\n")[statement.lineno - 1]
            column = len(source_line.encode("utf-8")[: statement.col_offset].decode("utf-8"))
            site = plan.sites[(statement.lineno, column)]
            assert site[1] == "    " * (depth + 1)
            sites.append(site)
        assert len(set(sites)) == len(node.body)
    probed = plan.text.split("\n")
    for line, indent in sorted(set(plan.sites.values()), reverse=True):
        probed.insert(line - 1, f"{indent}reveal_type({PROBE})")
    compile("\n".join(probed), "<probes>", "exec", dont_inherit=True)


def test_pyrsistent_pair_unpacking_and_return_handler_has_a_plan() -> None:
    source = (
        "class PMapItems:\n"
        "    def __contains__(self, arg):\n"
        "        try: (k,v) = arg\n"
        "        except Exception: return False\n"
        "        return k in self._map and self._map[k] == v\n"
    )
    compile(source, "<original>", "exec", dont_inherit=True)
    plan = probe_plan(source)
    assert plan is not None
    assert ast.dump(ast.parse(plan.text)) == ast.dump(ast.parse(source))
    assert "            return False" in plan.text.split("\n")


@pytest.mark.parametrize("prefix", ["except", "except*"])
def test_inline_handler_imports_have_compilable_contextual_questions(prefix: str) -> None:
    source = (
        "def f():\n"
        "\ttry: pass\n"
        f"\t{prefix} Exception: import typing; import warnings\n"
        "\tclass Node:\n"
        "\t\tdef clone(self) -> typing.Self: return self\n"
    )
    original = source
    probes = import_probes("/project/example.py", source)
    assert probes is not None
    assert any(question.self_context is not None for question in probes.questions)
    assert any(question.module_context is not None for question in probes.questions)
    assert source == original
    texts = {request.source for request in probes.requests}
    assert len(texts) == 1
    text = texts.pop()
    compile(text, "<imports>", "exec", dont_inherit=True)
    lines = text.split("\n")
    for request in sorted(probes.requests, key=lambda request: request.line, reverse=True):
        lines[request.line - 1 : request.line - 1] = [
            f"{request.indent}reveal_type({expression})" for expression in request.expressions
        ]
    compile("\n".join(lines), "<reveals>", "exec", dont_inherit=True)


@pytest.mark.parametrize("prefix", ["except", "except*"])
@pytest.mark.parametrize("expression", [r'f"\{1}"', r'"\777"', "1 is 2"])
@pytest.mark.parametrize("error_module", [None, "<string>", ""])
def test_handler_placement_preserves_native_diagnostics(
    prefix: str, expression: str, error_module: str | None
) -> None:
    source = f"def f():\n    try: pass\n    {prefix} Exception: value = {expression}\n"
    probed = (
        "reveal_type((0))\ndef f():\n    reveal_type((0))\n    try:\n"
        "        reveal_type((0))\n        pass\n"
        f"    {prefix} Exception:\n        reveal_type((0))\n        value = {expression}\n"
    )

    def observed(*, native: bool) -> tuple[bool, list[tuple[str, int, str, str]]]:
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            if error_module is not None:
                warnings.filterwarnings("error", module=error_module)
            if native:
                try:
                    ast.parse(source)
                    compile(probed, "<probes>", "exec", dont_inherit=True)
                except SyntaxError:
                    accepted = False
                else:
                    accepted = True
            else:
                accepted = probe_plan(source) is not None
        return accepted, [
            (warning.filename, warning.lineno, warning.category.__name__, str(warning.message))
            for warning in emitted
        ]

    expected = observed(native=True)
    assert observed(native=False) == expected
    assert observed(native=False) == expected
    with memoization_disabled():
        assert observed(native=False) == expected


@pytest.mark.parametrize(
    "checker",
    [
        pytest.param(
            name,
            marks=pytest.mark.skipif(
                importlib.util.find_spec(name) is None, reason=name + " absent"
            ),
        )
        for name in ("mypy", "pyright")
    ],
)
def test_checkers_keep_nonreturning_handler_siblings_unreachable(
    tmp_path: Path, checker: str
) -> None:
    source = (
        "from typing import NoReturn\n"
        "def stop() -> NoReturn:\n    raise RuntimeError\n"
        "def nonreturning() -> None:\n"
        "    try: pass\n    except Exception: stop(); pass\n"
        "def returning() -> None:\n"
        "    try: pass\n    except Exception: return; pass\n"
        "def raising() -> None:\n"
        "    try: pass\n    except* Exception: raise ValueError(\n        'halt'\n    ); pass\n"
        "def ordinary() -> None:\n"
        "    try: pass\n    except* Exception: str('value'); pass\n"
    )
    path = tmp_path / "example.py"
    path.write_text(source)
    before = path.read_bytes()
    plan = probe_plan(source)
    assert plan is not None
    requests = []
    expected = {}
    for function in ast.parse(source).body:
        if not isinstance(function, ast.FunctionDef) or function.name == "stop":
            continue
        statement = function.body[0]
        assert isinstance(statement, (ast.Try, ast.TryStar))
        body = statement.handlers[0].body
        assert len(body) == 2
        for index, sibling in enumerate(body):
            line, indent = plan.sites[(sibling.lineno, sibling.col_offset)]
            requests.append(RevealRequest(str(path), plan.text, line, indent, (PROBE,)))
            expected[(str(path), line, 0)] = index == 0 or function.name == "ordinary"
    oracle = MypyInferrer() if checker == "mypy" else PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(requests)
    finally:
        oracle.close()
    assert len(requests) == 8
    assert {key: key in answers for key in expected} == expected, answers
    assert path.read_bytes() == before

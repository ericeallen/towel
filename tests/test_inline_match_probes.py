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

"""Inline suites and semicolon siblings keep distinct, correctly placed probes."""

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


@pytest.mark.parametrize("pattern", ["[*collections]", "{**collections}", "collections"])
@pytest.mark.parametrize("depth", [0, 1, 2])
def test_inline_case_bindings_preserve_the_source_ast(pattern: str, depth: int) -> None:
    source = (
        "import collections\nimport collections.abc\nvalue = collections.abc\n"
        f"match other:\n    case {pattern}: pass\n"
    )
    for index in range(depth):
        source = f"def outer_{index}(other: object) -> None:\n" + textwrap.indent(source, "    ")
    original = source
    tree = compile(source, "<original>", "exec", ast.PyCF_ONLY_AST)
    plan = probe_plan(source)
    assert plan is not None
    assert ast.dump(ast.parse(plan.text)) == ast.dump(tree)
    assert source == original
    probes = import_probes("/project/example.py", source)
    assert probes is not None
    for request in probes.requests:
        compile(request.source, "<import-probes>", "exec", dont_inherit=True)


@pytest.mark.parametrize(
    ("source", "indent"),
    [
        ("match value:\n\tcase [*rest]: pass\n", "\t\t"),
        ("match value:\n  case [*rest]: pass\n", "      "),
        ("match value:\n    case (\n        [*rest]\n): pass\n", "        "),
        ("match value:\n    case [*rest] if (\n        allowed\n): pass\n", "        "),
        (
            "match value:\n    # case header follows a comment\n    case (\n"
            "        [*rest]  # case imaginary: pass\n    ) if (\n"
            "        allowed\n): pass; pass\n",
            "        ",
        ),
        (
            "match value:\n    case [*rest]:\n        match rest:\n"
            "            case [*inner]: pass\n",
            "                ",
        ),
        (
            "def f(value: object) -> None:\n    match value:\n        case (\n"
            "            [*rest]\n): pass\n",
            "            ",
        ),
        ("match value:\n    case [*café]: pass\n", "        "),
        ("match value:\n    case [*rest]: '''a\nmultiline body'''\n", "        "),
        ("match value:\n    case [*rest]: '''a\nmultiline body'''; pass; pass\n", "        "),
        (
            "match value:\n    case (\n        # case misleading:\n        case\n): pass\n",
            "        ",
        ),
        ("match value:\n    case \\\n        (\n            [*rest]\n): pass\n", "        "),
        ("match value:\n    case (\n        case.member\n): pass\n", "        "),
        (
            "match value:\n    case (\n        '''case misleading:\ncase literal'''\n): pass\n",
            "        ",
        ),
    ],
)
def test_logical_case_headers_determine_body_indentation(source: str, indent: str) -> None:
    compile(source, "<original>", "exec", dont_inherit=True)
    plan = probe_plan(source)
    assert plan is not None
    assert ast.dump(ast.parse(plan.text)) == ast.dump(ast.parse(source))
    moved = plan.text.splitlines()
    for statement in ast.walk(ast.parse(source)):
        if isinstance(statement, (ast.Pass, ast.Expr)):
            line = source.splitlines()[statement.lineno - 1]
            column = len(line.encode("utf-8")[: statement.col_offset].decode("utf-8"))
            at, placed_indent = plan.sites[(statement.lineno, column)]
            assert placed_indent == indent
            assert moved[at - 1].startswith(indent)
    probed = plan.text.split("\n")
    for at, placed_indent in sorted(set(plan.sites.values()), reverse=True):
        probed.insert(at - 1, f"{placed_indent}reveal_type({PROBE})")
    compile("\n".join(probed), "<probes>", "exec", dont_inherit=True)


@pytest.mark.parametrize(
    "source",
    [
        "def f():\n    '''a\nmultiline body'''; pass; pass\n",
        "def f():\n    consume(\n        value\n    ); pass\n",
        "if value: '''a\nmultiline body'''; pass\n",
        "café = '; : #'; marker = ';'; result = café  # ; ignored\n",
        "'''docstring\n; text'''; value = 1\n",
        '"docstring"; from __future__ import annotations; value = 1\n',
        "def f():\n\tvalue = ';'; other = value\n",
    ],
)
def test_semicolon_siblings_are_real_probes_outside_literals(source: str) -> None:
    original = ast.parse(source)
    compile(source, "<original>", "exec", dont_inherit=True)
    plan = probe_plan(source)
    assert plan is not None
    assert ast.dump(ast.parse(plan.text)) == ast.dump(original)
    probed = plan.text.split("\n")
    for at, indent in sorted(set(plan.sites.values()), reverse=True):
        probed.insert(at - 1, f"{indent}reveal_type({PROBE})")
    text = "\n".join(probed)
    compile(text, "<probes>", "exec", dont_inherit=True)
    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "reveal_type"
        for node in ast.walk(ast.parse(text))
    )
    assert actual == len(set(plan.sites.values()))
    for node in ast.walk(original):
        body = getattr(node, "body", ())
        if not isinstance(body, list):
            continue
        for before, after in zip(body, body[1:]):
            if before.end_lineno != after.lineno:
                continue
            places = []
            for statement in (before, after):
                line = source.splitlines()[statement.lineno - 1]
                column = len(line.encode("utf-8")[: statement.col_offset].decode("utf-8"))
                places.append((statement.lineno, column))
            if all(place in plan.sites for place in places):
                assert plan.sites[places[0]] != plan.sites[places[1]]


@pytest.mark.parametrize(
    "source",
    [
        "match value:\n    case [*rest] pass\n",
        "match value:\n    case _:\n    pass\n",
        "match value:\n    case _:\n        pass\n    case _: pass\n",
    ],
)
def test_invalid_match_syntax_does_not_gain_a_probe_plan(source: str) -> None:
    with pytest.raises(SyntaxError):
        compile(source, "<original>", "exec", dont_inherit=True)
    assert probe_plan(source) is None


@pytest.mark.parametrize(
    "checker",
    [
        pytest.param(
            "mypy",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("mypy") is None, reason="mypy absent"
            ),
        ),
        pytest.param(
            "pyright",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("pyright") is None, reason="pyright absent"
            ),
        ),
    ],
)
def test_real_checkers_answer_only_reachable_inline_case_probes(
    tmp_path: Path, checker: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mypy]\npython_version = "3.11"\nplatform = "linux"\n'
        '[tool.pyright]\npythonVersion = "3.11"\npythonPlatform = "Linux"\n'
    )
    source = (
        "import sys\n"
        "def f(value: object) -> None:\n"
        "    match value:\n"
        "        case (\n"
        "            [*rest]\n"
        "): pass\n"
        "        case {**mapping}: pass\n"
        "        case captured: pass\n"
        "    if sys.platform == 'win32':\n"
        "        match value:\n"
        "            case hidden: pass\n"
    )
    path = tmp_path / "example.py"
    path.write_text(source)
    original = path.read_bytes()
    plan = probe_plan(source)
    assert plan is not None
    requests = []
    for statement in ast.walk(ast.parse(source)):
        if isinstance(statement, ast.Pass):
            at, indent = plan.sites[(statement.lineno, statement.col_offset)]
            requests.append(RevealRequest(str(path), plan.text, at, indent, (PROBE,)))
    oracle = MypyInferrer() if checker == "mypy" else PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(requests)
    finally:
        oracle.close()
    assert len(requests) == 4
    for request in requests:
        key = (str(path), request.line, 0)
        assert (key in answers) == (len(request.indent) == 12), answers
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "expression",
    [r'f"\{1}"', r'f"\q{1}"', r'rf"{1:\q}"', r'"\777"', "1 is 2"],
)
@pytest.mark.parametrize("error_module", [None, "<string>", "<unknown>", "<probes>", ""])
def test_case_header_discovery_adds_no_native_diagnostics(
    expression: str, error_module: str | None
) -> None:
    source = f"value = {expression}\nmatch value:\n    case _: pass\n"
    probed = (
        f"reveal_type((0))\nvalue = {expression}\nreveal_type((0))\nmatch value:\n"
        "    case _:\n        reveal_type((0))\n        pass\n"
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
            "mypy",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("mypy") is None, reason="mypy absent"
            ),
        ),
        pytest.param(
            "pyright",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("pyright") is None, reason="pyright absent"
            ),
        ),
    ],
)
def test_real_checkers_distinguish_nonreturning_semicolon_siblings(
    tmp_path: Path, checker: str
) -> None:
    source = (
        "from typing import NoReturn\n"
        "def stop() -> NoReturn:\n    raise RuntimeError\n"
        "def same_line() -> None:\n    stop(); pass\n"
        "def multiline_call() -> None:\n    stop(\n    ); pass\n"
        "def raising() -> None:\n    raise ValueError(\n        'halt'\n    ); pass\n"
        "def ordinary() -> None:\n    str(\n        'value'\n    ); pass\n"
        "def asserted() -> None:\n    assert False; pass\n"
        "def matched(value: object) -> None:\n    match value:\n"
        "        case captured: stop(\n        ); pass\n"
    )
    path = tmp_path / "example.py"
    path.write_text(source)
    original = path.read_bytes()
    plan = probe_plan(source)
    assert plan is not None
    requests = []
    expected = {}
    for function in ast.parse(source).body:
        if not isinstance(function, ast.FunctionDef) or function.name == "stop":
            continue
        first = function.body[0]
        body = first.cases[0].body if isinstance(first, ast.Match) else function.body
        assert len(body) == 2
        for index, statement in enumerate(body):
            at, indent = plan.sites[(statement.lineno, statement.col_offset)]
            requests.append(RevealRequest(str(path), plan.text, at, indent, (PROBE,)))
            expected[(str(path), at, 0)] = index == 0 or function.name == "ordinary"
    oracle = MypyInferrer() if checker == "mypy" else PyrightOracle(language_server=False)
    try:
        answers = oracle.reveal(requests)
    finally:
        oracle.close()
    assert len(requests) == 12
    assert {key: key in answers for key in expected} == expected, answers
    assert path.read_bytes() == original

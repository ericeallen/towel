# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Ordinary byte identities survive source rendering and structural memo reuse."""

from __future__ import annotations

import ast
import gc
import os
from pathlib import Path
import subprocess
import sys
from types import FunctionType
import weakref

import pytest
import towel

from towel.canonical_ast import canonical_dump
from towel.diagnostics import Settings
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.compiler_literals import unsafe_byte_literal_moves
from towel.unification.refactor_engine import UnificationRefactorEngine

pytestmark = pytest.mark.skipif(
    sys.implementation.name != "cpython" or not (3, 9) <= sys.version_info[:2] <= (3, 13),
    reason="Lexical cache controls target the reviewed CPython 3.9--3.13 constructors",
)


def _token(character: str, *, escaped: bool) -> str:
    return "b'" + character + ("\\\n" * 600 if escaped else "") + "'"


def _body(name: str, literal: str, factor: int) -> str:
    return (
        f"def {name}(value):\n    literal = {literal}\n"
        f"    subtotal = value + 11\n    total = subtotal * {factor}\n"
        "    return literal, total\n"
    )


def _source(*, different: bool, escaped: bool, outside_first: bool = False) -> str:
    second = "?" if different else "!"
    outside = (
        f"outside_f={_token('!', escaped=escaped)}\n"
        f"outside_g={_token(second, escaped=escaped)}\n"
    )
    body = _body("f", _token("!", escaped=escaped and not outside_first), 5) + _body(
        "g", _token(second, escaped=escaped and not outside_first), 7
    )
    return outside + body if outside_first else body + outside


def _observe(source: str) -> tuple[tuple[str, bool, bool, bool], ...]:
    namespace: dict[str, object] = {}
    exec(compile(source, "byte_control.py", "exec"), namespace)
    result = []
    for name, outside_name in (("f", "outside_f"), ("g", "outside_g")):
        function = namespace[name]
        assert isinstance(function, FunctionType)
        first: object = function(1)
        repeated: object = function(1)
        assert isinstance(first, tuple) and isinstance(repeated, tuple)
        literal: object = first[0]
        assert isinstance(literal, bytes)
        external = b"!" if literal == b"!" else b"?"
        result.append(
            (
                repr(first),
                literal is repeated[0],
                literal is namespace[outside_name],
                literal is external,
            )
        )
    return tuple(result)


def _dry(original: Path, output: Path, *, cross: bool = False) -> str:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "TOWEL_", "DEBUG_", "COVERAGE_"))
    }
    environment.update(
        PYTHONPATH=str(Path(towel.__file__).resolve().parents[1]),
        TOWEL_WORKERS="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    command = [
        sys.executable,
        "-B",
        "-m",
        "towel.cli",
        "dry",
        str(original),
        str(output),
        "--no-types",
        "--no-format",
        "--no-interactive",
        "--progress",
        "none",
    ]
    if cross:
        command.append("--cross-module")
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30, env=environment)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@pytest.mark.parametrize("different", [False, True])
def test_cli_preserves_long_token_byte_identity_in_same_module(
    tmp_path: Path, different: bool
) -> None:
    source = _source(different=different, escaped=True)
    before = _observe(source)
    assert all(repeated and outside and not external for _, repeated, outside, external in before)
    original, output = tmp_path / "original.py", tmp_path / "output.py"
    original.write_text(source)
    _dry(original, output)
    generated = output.read_text() if output.exists() else source
    assert _observe(generated) == before
    assert original.read_text() == source


def test_cli_considers_unsafe_outside_token_before_otherwise_short_body(tmp_path: Path) -> None:
    source = _source(different=True, escaped=True, outside_first=True)
    before = _observe(source)
    assert all(outside and not external for _, _, outside, external in before)
    original, output = tmp_path / "original.py", tmp_path / "output.py"
    original.write_text(source)
    _dry(original, output)
    assert _observe(output.read_text() if output.exists() else source) == before


def test_short_byte_source_still_has_useful_cli_extraction(tmp_path: Path) -> None:
    source = _source(different=True, escaped=False)
    original, output = tmp_path / "original.py", tmp_path / "output.py"
    original.write_text(source)
    report = _dry(original, output)
    assert "Applied 1 refactoring" in report
    assert output.read_text() != source
    assert _observe(output.read_text()) == _observe(source)
    tree = ast.parse(output.read_text())
    helpers = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)} - {"f", "g"}
    assert len(helpers) == 1
    assert (
        len(
            [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in helpers
            ]
        )
        == 2
    )


def _cross_observe(project: Path) -> str:
    code = (
        "import a,b,external; print(a.f(1)[0] is a.outside); print(b.g(1)[0] is b.outside); "
        "print(a.f(1)[0] is external.bang); print(b.g(1)[0] is external.question)"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            f"import sys;sys.path.insert(0,{str(project)!r});" + code,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=project,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_cross_module_cli_keeps_differing_long_token_bytes_at_original_pools(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="byte-pool-control"\nversion="0"\n')
    (project / "a.py").write_text(
        _body("f", _token("!", escaped=True), 5) + f"outside={_token('!', escaped=True)}\n"
    )
    (project / "b.py").write_text(
        "import a\n"
        + _body("g", _token("?", escaped=True), 7)
        + f"outside={_token('?', escaped=True)}\n"
    )
    (project / "external.py").write_text("bang=b'!'\nquestion=b'?'\n")
    originals = {path.name: path.read_bytes() for path in project.iterdir()}
    before = _cross_observe(project)
    assert before == "True\nTrue\nFalse\nFalse\n"
    output = tmp_path / "output"
    _dry(project, output, cross=True)
    assert _cross_observe(output) == before
    assert {path.name: path.read_bytes() for path in project.iterdir()} == originals


def test_structural_memo_warmed_on_short_source_cannot_certify_long_token_source(
    tmp_path: Path,
) -> None:
    safe = _source(different=True, escaped=False)
    unsafe = _source(different=True, escaped=True)
    assert canonical_dump(ast.parse(safe)) == canonical_dump(ast.parse(unsafe))
    original = tmp_path / "program.py"
    original.write_text(safe)
    engine = UnificationRefactorEngine(settings=Settings.from_environ({"TOWEL_WORKERS": "1"}))
    assert engine.analyze_files([str(original)], progress="none")
    assert engine._unify_cache
    original.write_text(unsafe)
    before = _observe(unsafe)
    proposals = engine.analyze_files([str(original)], progress="none")
    assert engine.declined_pairs["literal_pool_boundary"] > 0
    for proposal in proposals:
        assert _observe(engine.apply_refactoring(str(original), proposal)) == before
    with memoization_disabled():
        uncached = engine.analyze_files([str(original)], progress="none")
    assert [
        (
            canonical_dump(proposal.extracted_function),
            tuple(
                (replacement.line_range, canonical_dump(replacement.node))
                for replacement in proposal.replacements
            ),
        )
        for proposal in uncached
    ] == [
        (
            canonical_dump(proposal.extracted_function),
            tuple(
                (replacement.line_range, canonical_dump(replacement.node))
                for replacement in proposal.replacements
            ),
        )
        for proposal in proposals
    ]
    assert original.read_text() == unsafe


def test_byte_facts_are_source_specific_weak_and_disabled_memo_preserves_verdict() -> None:
    safe = ast.parse(_source(different=True, escaped=False))
    unsafe = ast.parse(_source(different=True, escaped=True))
    assert canonical_dump(safe) == canonical_dump(unsafe)
    safe_function, unsafe_function = safe.body[0], unsafe.body[0]
    assert isinstance(safe_function, ast.FunctionDef) and isinstance(
        unsafe_function, ast.FunctionDef
    )
    assert not unsafe_byte_literal_moves(safe_function.body, safe)
    assert unsafe_byte_literal_moves(unsafe_function.body, unsafe)
    with memoization_disabled():
        assert not unsafe_byte_literal_moves(safe_function.body, safe)
        assert unsafe_byte_literal_moves(unsafe_function.body, unsafe)
    reference = weakref.ref(unsafe)
    del unsafe
    gc.collect()
    assert reference() is None, "Per-module byte facts must not retain the source tree"

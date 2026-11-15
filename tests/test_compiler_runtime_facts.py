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

"""Measured compiler facts for the Python 3.14/3.15 remediation lane.

Each identity comparison holds both independently constructed objects alive.
Comparing ids of successive temporary objects would confuse allocator reuse
with a shared compiler pool.
"""

from __future__ import annotations

import ast
from pathlib import Path
import string
import sys

import pytest

from tests.compiler_golden_profiles import PARAMETER_CELL_GOLDENS, _compiled_parameter_cells
from towel.unification import argument_ownership, compiler_literals
from towel.unification.scope_analyzer import ScopeAnalyzer


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython literal constructors")
def test_global_literal_constructor_facts() -> None:
    """Python lane: independently compiled singleton/cache families keep identity."""
    values = (
        *range(-5, 257),
        "",
        *(chr(index) for index in range(256)),
        *(character * 3 for character in string.ascii_letters + string.digits + "_"),
        "ASCII_identifier_123",
        b"",
    )
    for value in values:
        left: object = eval(compile(repr(value), "<left-literal-pool>", "eval"))
        right: object = eval(compile(repr(value), "<right-literal-pool>", "eval"))
        assert left is right, repr(value)
        assert compiler_literals.is_global_literal(value), repr(value)


@pytest.mark.parametrize("value", ["", "not interned!", "ñ multi", "𒀀 multi", "x" * 1000])
def test_exact_string_fragment_constructor_identity(value: str) -> None:
    """Python lane: passing an exact fragment through no-spec formatting retains it."""
    assert f"{value}" is value
    assert f"{value:}" is value
    assert "".join((value,)) is value
    assert format(value, "") is value
    assert compiler_literals.literal_fragment_transfer_supported()


def test_all_single_byte_constructor_and_long_token_provenance() -> None:
    """Python lane: escaped lexical atoms still bypass all 256 singleton constructors."""
    for index in range(256):
        token = "b'\\x" + f"{index:02x}" + "'"
        escaped = token[:-1] + "\\\n" * 600 + "'"
        short: object = ast.literal_eval(token)
        long: object = ast.literal_eval(escaped)
        assert isinstance(short, bytes) and isinstance(long, bytes)
        cached = (short + b"\0")[:1]
        assert short is cached, index
        assert long == short and long is not cached, index
        for spelling, unsafe in ((token, False), (escaped, True)):
            module = ast.parse(f"def f():\n    return {spelling}\n")
            function = module.body[0]
            assert isinstance(function, ast.FunctionDef)
            assert compiler_literals.unsafe_byte_literal_moves(function.body, module) == unsafe


@pytest.mark.skipif(sys.version_info < (3, 12), reason="separate comprehension frames before 3.12")
def test_original_golden_parameter_cells_remain_empty() -> None:
    """Python lane: compile the actual four originals before selecting their goldens."""
    examples = Path(__file__).resolve().parent.parent / "test_examples"
    for filename, expected in PARAMETER_CELL_GOLDENS.items():
        actual = _compiled_parameter_cells((examples / filename).read_text(), set(expected))
        assert actual == {name: frozenset() for name in expected}


@pytest.mark.parametrize("version", [(3, 14), (3, 15), (3, 99)])
def test_unreviewed_cleanup_cannot_claim_an_argument_transfer(
    monkeypatch: pytest.MonkeyPatch, version: tuple[int, int]
) -> None:
    """Python lane: changed 3.14/3.15 cleanup order invalidates the old handoff proof."""
    monkeypatch.setattr(sys, "version_info", version)
    assert not argument_ownership._supported_runtime()
    module = ast.parse("def f(value):\n    result = value + 1\n    return result\n")
    function = module.body[0]
    helper = ast.parse("def helper(value): return value + 1").body[0]
    assert isinstance(function, ast.FunctionDef) and isinstance(helper, ast.FunctionDef)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    assert (
        argument_ownership.argument_handoff_plan(
            function,
            function.body,
            analyzer,
            ast.parse("return helper(value)").body[0],
            helper,
        )
        is None
    )


def test_unknown_compiler_cannot_claim_literal_constructor_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python lane: evidence for new releases does not admit an unreviewed successor."""
    monkeypatch.setattr(sys, "version_info", (3, 99))
    assert not compiler_literals.literal_fragment_transfer_supported()
    assert not compiler_literals.is_global_literal(42)
    assert compiler_literals.is_global_literal(None)
    module = ast.parse("def f(): return b'!'")
    function = module.body[0]
    assert isinstance(function, ast.FunctionDef)
    assert compiler_literals.unsafe_byte_literal_moves(function.body, module)


def test_unreviewed_cleanup_declines_duplicate_bodies_with_a_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python lane: unsupported lifetime proofs leave real source unchanged with a reason."""
    from towel.unification.refactor_engine import UnificationRefactorEngine

    monkeypatch.setattr(sys, "version_info", (3, 14))
    source = (
        'def f(a, b):\n    local = resource("f")\n    return 11\n'
        'def g(a, b):\n    local = resource("g")\n    return 12\n'
    )
    path = tmp_path / "cleanup.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, skip_trivial_helpers=False
    )
    assert engine.analyze_file(str(path)) == []
    assert "lifetime_order_uncertified" in engine.declined_pairs
    assert path.read_text() == source


def test_native_frame_release_order_matches_the_certification_boundary() -> None:
    """Python lane: observe parameters and locals, not just compiled slot names."""
    events: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        def __del__(self) -> None:
            events.append(self.name)

    def frame(a: Resource, b: Resource) -> None:
        local1 = Resource("local1")
        local2 = Resource("local2")
        assert (a.name, b.name, local1.name, local2.name) == ("a", "b", "local1", "local2")

    frame(Resource("a"), Resource("b"))
    if argument_ownership._supported_runtime():
        assert events == ["a", "b", "local1", "local2"]
    else:
        assert events == ["local2", "local1", "b", "a"]


def test_uncertified_retained_local_order_is_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python lane: the multiple-local return proof uses the same runtime predicate."""
    from towel.unification.refactor_engine import UnificationRefactorEngine

    monkeypatch.setattr(argument_ownership, "_supported_runtime", lambda: False)
    source = (
        "def f():\n    x = resource('x')\n    y = resource('y')\n    emit('f')\n"
        "def g():\n    x = resource('x')\n    y = resource('y')\n    emit('g')\n"
    )
    path = tmp_path / "retained.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, skip_trivial_helpers=False
    )
    assert engine.analyze_file(str(path)) == []
    assert "lifetime_order_uncertified" in engine.declined_pairs
    assert path.read_text() == source

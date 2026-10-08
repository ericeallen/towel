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

"""Ownership sharing preserves exact sites, compiler context and independent reproof."""

from __future__ import annotations

import ast
import dataclasses
import gc
import hashlib
import os
import subprocess
import sys
import weakref
import warnings
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, FunctionType
from typing import Optional, Tuple

import pytest

from towel.unification import argument_ownership
from towel.unification.argument_ownership import (
    ArgumentHandoffMemo,
    argument_handoff_plan,
    verify_argument_handoff,
)
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.models import ArgumentHandoff, proposal_identity
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.diagnostics import Settings
from towel.canonical_ast import exact_dump
from towel.analysis_sources import parser_configuration

_SOURCE = "def f(value):\n    result = value + 1\n    return result\n"


@dataclass(frozen=True)
class _Inputs:
    source: str
    module: ast.Module
    function: ast.FunctionDef
    analyzer: ScopeAnalyzer
    call: ast.stmt
    helper: ast.FunctionDef
    digest: str


def _inputs(source: str = _SOURCE) -> _Inputs:
    module = ast.parse(source)
    function = next(
        node for node in ast.walk(module) if isinstance(node, ast.FunctionDef) and node.name == "f"
    )
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    call = ast.parse("return helper(value)").body[0]
    helper = ast.parse("def helper(value): return value + 1").body[0]
    assert isinstance(helper, ast.FunctionDef)
    return _Inputs(
        source,
        module,
        function,
        analyzer,
        call,
        helper,
        hashlib.sha256(source.encode()).hexdigest(),
    )


def _plan(inputs: _Inputs, memo: ArgumentHandoffMemo) -> Optional[ArgumentHandoff]:
    return argument_handoff_plan(
        inputs.function,
        inputs.function.body,
        inputs.analyzer,
        inputs.call,
        inputs.helper,
        memo=memo,
        source_digest=inputs.digest,
    )


def test_exact_certificate_hits_and_disabled_cache_preserve_the_immutable_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs()
    memo = ArgumentHandoffMemo()
    compute = argument_ownership._certify_argument_handoff
    calls = 0

    def counted(function, module, call, helper, cache, key):
        nonlocal calls
        calls += 1
        return compute(function, module, call, helper, cache, key)

    monkeypatch.setattr(argument_ownership, "_certify_argument_handoff", counted)
    first = _plan(inputs, memo)
    assert first is not None
    assert _plan(inputs, memo) is first
    assert calls == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(first, "box_name", "changed")
    with memoization_disabled():
        assert _plan(inputs, memo) == first
    assert calls == 2
    assert verify_argument_handoff(first, inputs.source, inputs.call, inputs.helper)
    assert calls == 3, "Materialization must independently re-prove even a warmed certificate"


def test_exact_generated_locations_and_observed_source_digest_are_not_merged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs()
    memo = ArgumentHandoffMemo()
    compute = argument_ownership._certify_argument_handoff
    calls = 0

    def counted(function, module, call, helper, cache, key):
        nonlocal calls
        calls += 1
        return compute(function, module, call, helper, cache, key)

    monkeypatch.setattr(argument_ownership, "_certify_argument_handoff", counted)
    first = _plan(inputs, memo)
    assert first is not None
    assert _plan(dataclasses.replace(inputs, digest="changed-source"), memo) == first
    ast.increment_lineno(inputs.call, 100)
    assert _plan(inputs, memo) == first
    ast.increment_lineno(inputs.helper, 100)
    assert _plan(inputs, memo) == first
    assert calls == 4


def test_warmed_certificate_still_checks_runtime_scope_and_whole_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs()
    memo = ArgumentHandoffMemo()
    assert _plan(inputs, memo) is not None
    supported = argument_ownership._supported_runtime
    monkeypatch.setattr(argument_ownership, "_supported_runtime", lambda: False)
    assert _plan(inputs, memo) is None
    monkeypatch.setattr(argument_ownership, "_supported_runtime", supported)
    assert (
        argument_handoff_plan(
            inputs.function,
            [ast.parse("result = value + 1").body[0], inputs.function.body[-1]],
            inputs.analyzer,
            inputs.call,
            inputs.helper,
            memo=memo,
            source_digest=inputs.digest,
        )
        is None
    )
    inputs.analyzer.analyze(ast.parse("def f(value): return value\n"))
    assert _plan(inputs, memo) is None, "A reused analyzer no longer certifies its previous module"


def test_helper_compiler_facts_repeat_only_for_the_exact_generated_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs()
    memo = ArgumentHandoffMemo()
    compile_codes = argument_ownership._compiled_codes
    helpers_compiled = 0

    def counted(module: ast.AST) -> Tuple[CodeType, ...]:
        nonlocal helpers_compiled
        if isinstance(module, ast.Module) and any(
            isinstance(node, ast.FunctionDef) and node.name == "helper" for node in module.body
        ):
            helpers_compiled += 1
        return compile_codes(module)

    monkeypatch.setattr(argument_ownership, "_compiled_codes", counted)
    assert _plan(inputs, memo) is not None
    other_call = dataclasses.replace(inputs, call=ast.parse("return helper(value + 1)").body[0])
    assert _plan(other_call, memo) is not None
    assert helpers_compiled == 1
    ast.increment_lineno(inputs.helper, 10)
    assert _plan(inputs, memo) is not None
    assert helpers_compiled == 2
    with memoization_disabled():
        assert _plan(inputs, memo) is not None
    assert helpers_compiled == 3


def test_changed_helper_cell_binding_does_not_inherit_a_fresh_thunk_permission() -> None:
    original = _inputs()
    helper = ast.parse("def helper(thunk): return thunk()").body[0]
    assert isinstance(helper, ast.FunctionDef)
    inputs = dataclasses.replace(
        original, call=ast.parse("return helper(lambda: value)").body[0], helper=helper
    )
    memo = ArgumentHandoffMemo()
    assert _plan(inputs, memo) is not None
    capturing = ast.parse(
        "def helper(thunk):\n    def inner(): return thunk()\n    return inner()\n"
    ).body[0]
    assert isinstance(capturing, ast.FunctionDef)
    assert _plan(dataclasses.replace(inputs, helper=capturing), memo) is None


def test_parser_limit_change_and_oversized_keys_compute_fresh_certificates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs()
    memo = ArgumentHandoffMemo()
    first = _plan(inputs, memo)
    assert first is not None
    previous = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(previous + 50)
        changed = _plan(inputs, memo)
        assert changed == first and changed is not first
    finally:
        sys.setrecursionlimit(previous)
    monkeypatch.setattr(ArgumentHandoffMemo, "_MAX_KEY_BYTES", 1)
    bounded = ArgumentHandoffMemo()
    one = _plan(inputs, bounded)
    two = _plan(inputs, bounded)
    assert one == two == first and one is not two


@pytest.mark.parametrize(
    "body",
    [
        "return value is 1",
        "assert (value, 1)\n    return value",
        "return (1)()",
        "return [1]['s']",
        "return (1 + 2)()",
        "return __debug__()",
    ],
    ids=[
        "identity",
        "assert-tuple",
        "literal-call",
        "literal-subscript",
        "folded-call",
        "folded-debug",
    ],
)
def test_warning_capable_helper_compilation_preserves_every_warning_and_error_policy(
    body: str,
) -> None:
    original = _inputs()
    helper = ast.parse("def helper(value):\n    " + body + "\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    inputs = dataclasses.replace(original, helper=helper)
    memo = ArgumentHandoffMemo()
    key = argument_ownership._HandoffKey(
        inputs.digest,
        exact_dump(inputs.function),
        exact_dump(inputs.call),
        exact_dump(helper),
        parser_configuration(),
    )
    for mode in ("uncached-plan", "memo-plan", "memo-helper"):
        counts = []
        for _ in range(2):
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always", SyntaxWarning)
                if mode == "uncached-plan":
                    assert _plan(inputs, ArgumentHandoffMemo(limit=0)) is not None
                elif mode == "memo-plan":
                    assert _plan(inputs, memo) is not None
                else:
                    assert memo._helper_code(helper, key) is not None
                counts.append(sum(issubclass(item.category, SyntaxWarning) for item in emitted))
        assert counts == [1, 1], (body, mode, counts)
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        assert _plan(inputs, memo) is None
        assert memo._helper_code(helper, key) is None


def test_missing_helper_expression_context_is_not_normalized_into_a_warmed_key() -> None:
    original = _inputs()
    helper = ast.parse("def helper(value): return value").body[0]
    assert isinstance(helper, ast.FunctionDef)
    inputs = dataclasses.replace(original, helper=helper)
    memo = ArgumentHandoffMemo()
    first = _plan(inputs, memo)
    assert first is not None
    value = helper.body[0]
    assert isinstance(value, ast.Return) and isinstance(value.value, ast.Name)
    del value.value.ctx
    cached = _plan(inputs, memo)
    assert cached is not first
    # Before 3.13 the compiler refuses missing contexts; newer deepcopy fills
    # constructor defaults. Either way, the exact key must compute afresh.
    assert cached == _plan(inputs, ArgumentHandoffMemo(limit=0))


@pytest.mark.parametrize("operand", ["True", "False", "(not 1)"])
def test_boolean_inversion_preserves_compiler_deprecations_and_error_policy(operand: str) -> None:
    original = _inputs()
    helper = ast.parse(f"def helper(value): return ~{operand}").body[0]
    assert isinstance(helper, ast.FunctionDef)
    assert not argument_ownership._compilation_cannot_warn(helper)
    inputs = dataclasses.replace(original, helper=helper)
    memo = ArgumentHandoffMemo()
    key = argument_ownership._HandoffKey(
        inputs.digest,
        exact_dump(inputs.function),
        exact_dump(inputs.call),
        exact_dump(helper),
        parser_configuration(),
    )
    counts = []
    for mode in ("uncached-plan", "memo-plan", "memo-helper"):
        repeated = []
        for _ in range(2):
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always", DeprecationWarning)
                if mode == "uncached-plan":
                    assert _plan(inputs, ArgumentHandoffMemo(limit=0)) is not None
                elif mode == "memo-plan":
                    assert _plan(inputs, memo) is not None
                else:
                    assert memo._helper_code(helper, key) is not None
                repeated.append(
                    sum(issubclass(item.category, DeprecationWarning) for item in emitted)
                )
        counts.append(repeated)
    expected = [1, 1] if sys.version_info >= (3, 13) else [0, 0]
    assert counts == [expected, expected, expected]
    if sys.version_info >= (3, 13):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            assert _plan(inputs, memo) == _plan(inputs, ArgumentHandoffMemo(limit=0))
            uncached = argument_ownership._helper_code(helper)
            cached = memo._helper_code(helper, key)
            assert uncached is not None and cached is not None
            # CPython suppresses the optimizer error and retains UNARY_INVERT;
            # warmed compiler facts from the "always" policy must not replace it.
            assert cached.co_code == uncached.co_code and cached.co_consts == uncached.co_consts


def test_wide_hexadecimal_body_is_certified_without_changing_integer_limits() -> None:
    source = (
        "def f(value):\n    items = list(value)\n    return len(items) + 0x1" + "0" * 4000 + "\n"
    )
    original = _inputs(source)
    helper = ast.parse(source.replace("def f(", "def helper(")).body[0]
    assert isinstance(helper, ast.FunctionDef)
    inputs = dataclasses.replace(original, helper=helper)
    previous = sys.get_int_max_str_digits()
    first = _plan(inputs, ArgumentHandoffMemo(limit=0))
    assert first is not None and _plan(inputs, ArgumentHandoffMemo()) == first
    assert sys.get_int_max_str_digits() == previous


def test_complete_cli_preserves_wide_defaults_and_explicit_call_results(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="wide-default"\nversion="0"\n')
    literal = "0x1" + "0" * 4000
    source = "\n".join(
        f"def {name}(values={literal}):\n"
        "    items = list(values)\n"
        "    subtotal = sum(items)\n"
        "    total = subtotal * 2\n"
        f"    return total + {number}\n"
        for name, number in (("f", 3), ("g", 7))
    )
    original = project / "input.py"
    original.write_text(source)
    output = tmp_path / "output.py"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "towel.cli",
            "dry",
            str(original),
            str(output),
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "TOWEL_WORKERS": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert "Applied 1 refactoring" in result.stdout
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    exec(source, before)
    exec(output.read_text(), after)
    for name, expected in (("f", 9), ("g", 13)):
        before_function = before[name]
        after_function = after[name]
        assert isinstance(before_function, FunctionType) and isinstance(
            after_function, FunctionType
        )
        assert before_function([1, 2]) == after_function([1, 2]) == expected
        assert before_function.__defaults__ == after_function.__defaults__
    assert original.read_text() == source


@pytest.mark.parametrize(
    "source, accepted",
    [
        (_SOURCE, True),
        ("_towel_arguments = 0\n" + _SOURCE, True),
        ("object = lambda: None\n" + _SOURCE, False),
        ("def f(value):\n    object = value\n    return object\n", False),
        ("def f(value):\n    def inner(): return value\n    return inner()\n", False),
    ],
    ids=["ordinary", "module-name-collision", "module-object", "local-object", "parameter-cell"],
)
def test_same_spelled_functions_in_other_binding_contexts_keep_their_own_proof(
    source: str, accepted: bool
) -> None:
    memo = ArgumentHandoffMemo()
    assert _plan(_inputs(), memo) is not None
    current = _inputs(source)
    cached = _plan(current, memo)
    assert (cached is not None) is accepted
    assert cached == _plan(current, ArgumentHandoffMemo(limit=0))
    if cached is not None and source.startswith("_towel_arguments"):
        assert cached.box_name == "_towel_arguments_1"


def test_eviction_and_source_graph_lifetime_do_not_pin_or_change_certificates() -> None:
    inputs = _inputs()
    memo = ArgumentHandoffMemo(limit=1)
    first = _plan(inputs, memo)
    assert first is not None
    changed = dataclasses.replace(inputs, call=ast.parse("return helper(value + 1)").body[0])
    second = _plan(changed, memo)
    assert second is not None and second.call_dump != first.call_dump
    assert _plan(inputs, memo) == first
    reference = weakref.ref(inputs.module)
    del inputs, changed
    gc.collect()
    assert reference() is None, "Memo values and syntax keys must not retain source ASTs"


def test_repetitive_candidates_and_materialized_source_match_without_ownership_sharing(
    tmp_path: Path,
) -> None:
    source = "\n".join(
        f"def f{index}(value):\n"
        "    values = [item + 1 for item in value]\n"
        "    subtotal = sum(values)\n"
        "    total = round(subtotal * 1.08, 2)\n"
        f"    return 'F{index} ' + str(total)\n"
        for index in range(8)
    )
    path = tmp_path / "input.py"
    path.write_text(source)
    settings = Settings.from_environ({"TOWEL_WORKERS": "1"})
    results = []
    for limit in (512, 0):
        engine = UnificationRefactorEngine(settings=settings)
        engine._argument_handoff_memo = ArgumentHandoffMemo(limit=limit)
        proposals = engine.analyze_files([str(path)], progress="none")
        assert len(proposals) == 1
        output = engine.apply_refactoring(str(path), proposals[0])
        results.append(
            (
                tuple(proposal_identity(proposal) for proposal in proposals),
                engine.declined_pairs,
                output,
            )
        )
    assert results[0] == results[1]
    assert path.read_text() == source
